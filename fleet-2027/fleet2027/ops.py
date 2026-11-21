"""Local AWR-Ops intake and approval workflow for Fleet.

This module records requests and supplied approval decisions, but deliberately
does not itself submit a runnable ``TaskSpec`` to the fleet, invoke a runner,
touch a connector, or access the network. ``approver_id`` is an unverified
claim: Fleet currently has no trusted individual identity/permission source, so
the store's atomic queue-admission gate rejects every AWR-linked task. Workflow
rows share the existing Fleet SQLite database, write transaction, canonical
JSON hash primitive, and audit chain.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from .contracts import (
    ContractError,
    TaskConflictError,
    TaskSpec,
    canonical_json,
    require_safe_id,
    sha256_text,
    strict_object,
    utc_now,
)
from .store import DurableTaskStore, StoreError

_CONTRACT_VERSION = 1
_VALID_RISKS = frozenset({"low", "medium", "high", "critical"})


class OpsTransitionError(StoreError):
    """Raised when an AWR state transition is not permitted."""


class StaleProposalError(OpsTransitionError):
    """Raised when a proposal or approval no longer matches the stored request."""


class OpsIntegrityError(StoreError):
    """Raised when persisted AWR content no longer matches its recorded digest."""


def _text(value: Any, *, where: str) -> str:
    """Require non-blank text without silently normalising submitted content."""
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{where} must be a non-blank string")
    return value


def _text_tuple(value: Any, *, where: str) -> tuple[str, ...]:
    """Validate and freeze a JSON-style sequence of meaningful text values."""
    if isinstance(value, (str, bytes)) or not isinstance(value, (tuple, list)):
        raise ContractError(f"{where} must be a sequence of strings")
    items = tuple(value)
    for index, item in enumerate(items):
        _text(item, where=f"{where}[{index}]")
    return items


def _require_sha256(value: Any, *, where: str) -> str:
    """Validate the exact lowercase hexadecimal digest format used by Fleet."""
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ContractError(f"{where} must be a lowercase SHA-256 digest")
    return value


def _utc_timestamp(value: Any, *, where: str) -> str:
    """Require an ISO-8601 timestamp with an explicit zero UTC offset."""
    if not isinstance(value, str) or not value:
        raise ContractError(f"{where} must be an ISO-8601 UTC timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ContractError(f"{where} must be an ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ContractError(f"{where} must include a zero UTC offset")
    return value


def _canonical_payload(payload: Mapping[str, Any], *, where: str) -> str:
    """Serialise a strict JSON object using Fleet's canonical representation."""
    try:
        # ``canonical_json`` is the shared byte format. Validate standard JSON
        # first so NaN/Infinity cannot enter a supposedly portable fingerprint.
        json.dumps(payload, ensure_ascii=False, allow_nan=False)
        return canonical_json(payload)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{where} must contain only finite JSON values") from exc


def _payload_sha256(payload: Mapping[str, Any], *, where: str) -> str:
    """Hash a contract with the existing kernel-backed Fleet helper."""
    return sha256_text(_canonical_payload(payload, where=where))


def _decode_object(raw: Any, *, where: str) -> dict[str, Any]:
    """Decode stored JSON and fail closed on malformed or non-object data."""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise OpsIntegrityError(f"stored {where} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise OpsIntegrityError(f"stored {where} is not a JSON object")
    return payload


@dataclass(frozen=True)
class AwrTaskRequest:
    """A locally received request, with facts and emotional signals kept distinct."""

    task_id: str
    goal: str
    requested_by: str
    facts: tuple[str, ...] = ()
    requirements: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    sentiment_signals: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate identifiers and preserve each signal category independently."""
        require_safe_id(self.task_id, where="AwrTaskRequest.task_id")
        require_safe_id(self.requested_by, where="AwrTaskRequest.requested_by")
        _text(self.goal, where="AwrTaskRequest.goal")
        for field_name in ("facts", "requirements", "constraints", "sentiment_signals"):
            object.__setattr__(
                self,
                field_name,
                _text_tuple(getattr(self, field_name), where=f"AwrTaskRequest.{field_name}"),
            )

    def to_dict(self) -> dict[str, Any]:
        """Return the complete versioned request contract."""
        return {
            "contract_version": _CONTRACT_VERSION,
            "task_id": self.task_id,
            "goal": self.goal,
            "requested_by": self.requested_by,
            "facts": list(self.facts),
            "requirements": list(self.requirements),
            "constraints": list(self.constraints),
            "sentiment_signals": list(self.sentiment_signals),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> AwrTaskRequest:
        """Rebuild the request, refusing unknown fields and future versions."""
        data = strict_object(
            raw,
            where="AwrTaskRequest",
            required={
                "contract_version": (int,),
                "task_id": (str,),
                "goal": (str,),
                "requested_by": (str,),
                "facts": (list,),
                "requirements": (list,),
                "constraints": (list,),
                "sentiment_signals": (list,),
            },
        )
        if data["contract_version"] != _CONTRACT_VERSION:
            raise ContractError(
                f"unsupported AwrTaskRequest version {data['contract_version']}"
            )
        return cls(
            task_id=data["task_id"],
            goal=data["goal"],
            requested_by=data["requested_by"],
            facts=tuple(data["facts"]),
            requirements=tuple(data["requirements"]),
            constraints=tuple(data["constraints"]),
            sentiment_signals=tuple(data["sentiment_signals"]),
        )

    @property
    def request_sha256(self) -> str:
        """Stable digest used to detect a changed request under the same task ID."""
        return _payload_sha256(self.to_dict(), where="AwrTaskRequest")


@dataclass(frozen=True)
class AwrProposal:
    """A reviewable execution proposal whose full content is fingerprinted."""

    task_id: str
    request_sha256: str
    summary: str
    task_spec: TaskSpec
    acceptance_criteria: tuple[str, ...]
    created_by: str
    risk_level: str
    rollback_plan: str
    affected_paths: tuple[str, ...] = ()
    non_goals: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    risk_notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate the proposal and ensure its runnable plan has the same ID."""
        require_safe_id(self.task_id, where="AwrProposal.task_id")
        require_safe_id(self.created_by, where="AwrProposal.created_by")
        _require_sha256(self.request_sha256, where="AwrProposal.request_sha256")
        _text(self.summary, where="AwrProposal.summary")
        _text(self.rollback_plan, where="AwrProposal.rollback_plan")
        if not isinstance(self.task_spec, TaskSpec):
            raise ContractError("AwrProposal.task_spec must be a TaskSpec")
        if self.task_spec.task_id != self.task_id:
            raise ContractError("AwrProposal.task_id must match TaskSpec.task_id")
        if not isinstance(self.risk_level, str) or self.risk_level not in _VALID_RISKS:
            raise ContractError(f"unknown AwrProposal risk level {self.risk_level!r}")
        for field_name in (
            "acceptance_criteria",
            "affected_paths",
            "non_goals",
            "assumptions",
            "unknowns",
            "risk_notes",
        ):
            values = _text_tuple(
                getattr(self, field_name), where=f"AwrProposal.{field_name}"
            )
            object.__setattr__(self, field_name, values)
        if not self.acceptance_criteria:
            raise ContractError("AwrProposal.acceptance_criteria must not be empty")
        # Confirm that nested TaskSpec metadata is also portable JSON before this
        # object can be fingerprinted or persisted.
        _canonical_payload(self.to_dict(), where="AwrProposal")

    def to_dict(self) -> dict[str, Any]:
        """Return every review and execution-affecting field in one object."""
        return {
            "contract_version": _CONTRACT_VERSION,
            "task_id": self.task_id,
            "request_sha256": self.request_sha256,
            "summary": self.summary,
            "task_spec": self.task_spec.to_dict(),
            "acceptance_criteria": list(self.acceptance_criteria),
            "created_by": self.created_by,
            "risk_level": self.risk_level,
            "rollback_plan": self.rollback_plan,
            "affected_paths": list(self.affected_paths),
            "non_goals": list(self.non_goals),
            "assumptions": list(self.assumptions),
            "unknowns": list(self.unknowns),
            "risk_notes": list(self.risk_notes),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> AwrProposal:
        """Rebuild a proposal, refusing unknown fields and malformed task specs."""
        data = strict_object(
            raw,
            where="AwrProposal",
            required={
                "contract_version": (int,),
                "task_id": (str,),
                "request_sha256": (str,),
                "summary": (str,),
                "task_spec": (dict,),
                "acceptance_criteria": (list,),
                "created_by": (str,),
                "risk_level": (str,),
                "rollback_plan": (str,),
                "affected_paths": (list,),
                "non_goals": (list,),
                "assumptions": (list,),
                "unknowns": (list,),
                "risk_notes": (list,),
            },
        )
        if data["contract_version"] != _CONTRACT_VERSION:
            raise ContractError(f"unsupported AwrProposal version {data['contract_version']}")
        return cls(
            task_id=data["task_id"],
            request_sha256=data["request_sha256"],
            summary=data["summary"],
            task_spec=TaskSpec.from_dict(data["task_spec"]),
            acceptance_criteria=tuple(data["acceptance_criteria"]),
            created_by=data["created_by"],
            risk_level=data["risk_level"],
            rollback_plan=data["rollback_plan"],
            affected_paths=tuple(data["affected_paths"]),
            non_goals=tuple(data["non_goals"]),
            assumptions=tuple(data["assumptions"]),
            unknowns=tuple(data["unknowns"]),
            risk_notes=tuple(data["risk_notes"]),
        )

    @property
    def proposal_sha256(self) -> str:
        """Digest every proposal field, including the full Fleet execution spec."""
        return _payload_sha256(self.to_dict(), where="AwrProposal")


@dataclass(frozen=True)
class AwrApproval:
    """A supplied decision claim bound to a task and proposal digest.

    ``approver_id`` is recorded as supplied and is not authenticated by Fleet.
    """

    task_id: str
    proposal_sha256: str
    approved: bool
    approver_id: str
    decided_at: str

    def __post_init__(self) -> None:
        """Refuse anonymous, malformed, or non-human-asserted decisions."""
        require_safe_id(self.task_id, where="AwrApproval.task_id")
        require_safe_id(self.approver_id, where="AwrApproval.approver_id")
        _require_sha256(self.proposal_sha256, where="AwrApproval.proposal_sha256")
        if type(self.approved) is not bool:
            raise ContractError("AwrApproval.approved must be a bool")
        _utc_timestamp(self.decided_at, where="AwrApproval.decided_at")

    def to_dict(self) -> dict[str, Any]:
        """Serialise the exact decision needed for durable review."""
        return {
            "contract_version": _CONTRACT_VERSION,
            "task_id": self.task_id,
            "proposal_sha256": self.proposal_sha256,
            "approved": self.approved,
            "approver_id": self.approver_id,
            "decided_at": self.decided_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> AwrApproval:
        """Rebuild a decision, refusing unknown fields and missing identity."""
        data = strict_object(
            raw,
            where="AwrApproval",
            required={
                "contract_version": (int,),
                "task_id": (str,),
                "proposal_sha256": (str,),
                "approved": (bool,),
                "approver_id": (str,),
                "decided_at": (str,),
            },
        )
        if data["contract_version"] != _CONTRACT_VERSION:
            raise ContractError(f"unsupported AwrApproval version {data['contract_version']}")
        return cls(
            task_id=data["task_id"],
            proposal_sha256=data["proposal_sha256"],
            approved=data["approved"],
            approver_id=data["approver_id"],
            decided_at=data["decided_at"],
        )


class OpsState(str, Enum):
    """Non-runnable lifecycle of a local AWR request."""

    REQUESTED = "requested"
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"


_ALLOWED_TRANSITIONS: dict[OpsState, frozenset[OpsState]] = {
    OpsState.REQUESTED: frozenset({OpsState.PROPOSED}),
    # The self-loop denotes a new proposal revision, not a repeated identical
    # write. AwrOps treats an identical fingerprint as an idempotent no-op.
    OpsState.PROPOSED: frozenset(
        {OpsState.PROPOSED, OpsState.APPROVED, OpsState.REJECTED}
    ),
    # A changed proposal returns to review and invalidates the former approval.
    OpsState.APPROVED: frozenset({OpsState.PROPOSED}),
    OpsState.REJECTED: frozenset({OpsState.PROPOSED}),
}


@dataclass(frozen=True)
class TransitionDecision:
    """Pure result of checking one requested workflow transition."""

    from_state: OpsState
    to_state: OpsState
    allowed: bool
    reason: str

    def __post_init__(self) -> None:
        """Keep the result consistent with the single transition table."""
        if not isinstance(self.from_state, OpsState) or not isinstance(self.to_state, OpsState):
            raise ContractError("TransitionDecision states must be OpsState values")
        expected = self.to_state in _ALLOWED_TRANSITIONS[self.from_state]
        if type(self.allowed) is not bool or self.allowed != expected:
            raise ContractError("TransitionDecision.allowed does not match the transition table")
        _text(self.reason, where="TransitionDecision.reason")


def decide_transition(from_state: OpsState, to_state: OpsState) -> TransitionDecision:
    """Return whether a transition is allowed without mutating any state."""
    try:
        source = OpsState(from_state)
        target = OpsState(to_state)
    except (TypeError, ValueError) as exc:
        raise ContractError("unknown AWR state") from exc
    allowed = target in _ALLOWED_TRANSITIONS[source]
    if allowed:
        reason = "transition is allowed"
    else:
        reason = f"transition from {source.value} to {target.value} is not allowed"
    return TransitionDecision(source, target, allowed, reason)


@dataclass(frozen=True)
class AwrProposalRevision:
    """One immutable proposal revision retained for later audit."""

    revision: int
    proposal_sha256: str
    proposal: AwrProposal
    created_at: str


@dataclass(frozen=True)
class AwrTaskSnapshot:
    """Verified current view of one AWR workflow."""

    request: AwrTaskRequest
    request_sha256: str
    state: OpsState
    proposal: AwrProposal | None
    proposal_sha256: str | None
    approval: AwrApproval | None
    created_at: str
    updated_at: str
    last_checked_at: str

    @property
    def approved_for_current_proposal(self) -> bool:
        """True when the stored claim names the current fingerprint.

        This does not verify the approver's identity or authorize execution.
        """
        return (
            self.state is OpsState.APPROVED
            and self.approval is not None
            and self.proposal_sha256 == self.approval.proposal_sha256
            and self.approval.approved
        )


class AwrOps:
    """Persist local requests/proposals/decisions through the existing Fleet store."""

    def __init__(self, store: DurableTaskStore) -> None:
        """Bind the workflow to an already-open Fleet database, never a new DB."""
        if not isinstance(store, DurableTaskStore):
            raise TypeError("AwrOps requires an existing DurableTaskStore")
        self.store = store

    def create_request(self, request: AwrTaskRequest, *, now: str | None = None) -> bool:
        """Create one non-runnable request; repeated stable IDs are idempotent.

        The same ID with changed content is a conflict. The primary key and
        ``BEGIN IMMEDIATE`` transaction serialize concurrent local/process retries.
        """
        at = _utc_timestamp(now or utc_now(), where="now")
        request_json = _canonical_payload(request.to_dict(), where="AwrTaskRequest")
        request_sha = sha256_text(request_json)
        with self.store.transaction() as conn:
            queued = conn.execute(
                "SELECT 1 FROM tasks WHERE task_id = ?", (request.task_id,)
            ).fetchone()
            if queued is not None:
                raise OpsTransitionError(
                    f"Fleet task {request.task_id!r} is already queued; "
                    "an AWR workflow cannot claim an existing execution"
                )
            existing = conn.execute(
                "SELECT request_json, request_sha256 FROM awr_ops_workflows WHERE task_id = ?",
                (request.task_id,),
            ).fetchone()
            if existing is not None:
                existing_request = self._decode_request(
                    existing["request_json"],
                    expected_task_id=request.task_id,
                    expected_sha256=existing["request_sha256"],
                )
                if existing_request.request_sha256 != request_sha:
                    raise TaskConflictError(
                        f"AWR task {request.task_id!r} already exists with "
                        "different request content"
                    )
                conn.execute(
                    "UPDATE awr_ops_workflows SET last_checked_at = ? WHERE task_id = ?",
                    (at, request.task_id),
                )
                return False
            conn.execute(
                "INSERT INTO awr_ops_workflows(task_id, request_json, request_sha256, state,"
                " current_proposal_sha256, approval_json, created_at, updated_at, last_checked_at)"
                " VALUES(?, ?, ?, ?, NULL, NULL, ?, ?, ?)",
                (
                    request.task_id,
                    request_json,
                    request_sha,
                    OpsState.REQUESTED.value,
                    at,
                    at,
                    at,
                ),
            )
            self.store.append_audit_in_transaction(
                conn,
                kind="awr.requested",
                task_id=request.task_id,
                payload={"request_sha256": request_sha, "requested_by": request.requested_by},
                at=at,
            )
        return True

    def submit_proposal(self, proposal: AwrProposal, *, now: str | None = None) -> bool:
        """Persist a proposal revision and move the request into human review.

        A changed proposal after approval clears that approval in the same
        transaction. An identical proposal is a no-op and leaves valid approval
        intact. Proposal bodies are stored as immutable revisions; audit rows
        contain only IDs, hashes, actor IDs and state changes.
        """
        at = _utc_timestamp(now or utc_now(), where="now")
        proposal_json = _canonical_payload(proposal.to_dict(), where="AwrProposal")
        proposal_sha = sha256_text(proposal_json)
        with self.store.transaction() as conn:
            snapshot = self._select_snapshot(conn, proposal.task_id)
            if snapshot is None:
                raise StoreError(f"unknown AWR task {proposal.task_id!r}")
            if proposal.request_sha256 != snapshot.request_sha256:
                raise StaleProposalError(
                    "proposal was prepared for a different request fingerprint"
                )

            current_sha = snapshot.proposal_sha256
            if current_sha == proposal_sha and snapshot.state in {
                OpsState.PROPOSED,
                OpsState.APPROVED,
            }:
                conn.execute(
                    "UPDATE awr_ops_workflows SET last_checked_at = ? WHERE task_id = ?",
                    (at, proposal.task_id),
                )
                return False

            decision = decide_transition(snapshot.state, OpsState.PROPOSED)
            if not decision.allowed:
                raise OpsTransitionError(decision.reason)

            old_approval = snapshot.approval
            revision_row = conn.execute(
                "SELECT revision, proposal_json FROM awr_ops_proposals "
                "WHERE task_id = ? AND proposal_sha256 = ?",
                (proposal.task_id, proposal_sha),
            ).fetchone()
            if revision_row is None:
                revision = int(
                    conn.execute(
                        "SELECT COALESCE(MAX(revision), 0) + 1 FROM awr_ops_proposals "
                        "WHERE task_id = ?",
                        (proposal.task_id,),
                    ).fetchone()[0]
                )
                conn.execute(
                    "INSERT INTO awr_ops_proposals(task_id, revision, proposal_sha256,"
                    " proposal_json, created_at) VALUES(?, ?, ?, ?, ?)",
                    (proposal.task_id, revision, proposal_sha, proposal_json, at),
                )
            else:
                revision = int(revision_row["revision"])
                if revision_row["proposal_json"] != proposal_json:
                    raise OpsIntegrityError(
                        "proposal digest collision or corrupted proposal history"
                    )

            if old_approval is not None and snapshot.state is OpsState.APPROVED:
                self.store.append_audit_in_transaction(
                    conn,
                    kind="awr.approval.invalidated",
                    task_id=proposal.task_id,
                    payload={
                        "old_proposal_sha256": old_approval.proposal_sha256,
                        "new_proposal_sha256": proposal_sha,
                    },
                    at=at,
                )

            cursor = conn.execute(
                "UPDATE awr_ops_workflows SET state = ?, current_proposal_sha256 = ?,"
                " approval_json = NULL, updated_at = ?, last_checked_at = ? WHERE task_id = ?",
                (
                    OpsState.PROPOSED.value,
                    proposal_sha,
                    at,
                    at,
                    proposal.task_id,
                ),
            )
            if cursor.rowcount != 1:
                raise StoreError(f"AWR task {proposal.task_id!r} disappeared during proposal save")
            payload: dict[str, Any] = {
                "proposal_sha256": proposal_sha,
                "proposed_by": proposal.created_by,
                "revision": revision,
            }
            if current_sha is not None:
                payload["previous_proposal_sha256"] = current_sha
            self.store.append_audit_in_transaction(
                conn,
                kind="awr.proposal.submitted",
                task_id=proposal.task_id,
                payload=payload,
                at=at,
            )
        return True

    def record_approval(self, approval: AwrApproval) -> bool:
        """Record a supplied decision claim for the current proposal only.

        This stores ``approver_id`` for review but does not authenticate the
        person or grant Fleet execution authority.
        """
        at = approval.decided_at
        approval_json = _canonical_payload(approval.to_dict(), where="AwrApproval")
        with self.store.transaction() as conn:
            snapshot = self._select_snapshot(conn, approval.task_id)
            if snapshot is None:
                raise StoreError(f"unknown AWR task {approval.task_id!r}")
            if snapshot.proposal_sha256 is None:
                raise OpsTransitionError("a proposal must exist before a decision")
            if approval.proposal_sha256 != snapshot.proposal_sha256:
                raise StaleProposalError("approval fingerprint does not match the current proposal")

            target = OpsState.APPROVED if approval.approved else OpsState.REJECTED
            if snapshot.state is target and snapshot.approval == approval:
                conn.execute(
                    "UPDATE awr_ops_workflows SET last_checked_at = ? WHERE task_id = ?",
                    (at, approval.task_id),
                )
                return False
            decision = decide_transition(snapshot.state, target)
            if not decision.allowed:
                raise OpsTransitionError(decision.reason)

            cursor = conn.execute(
                "UPDATE awr_ops_workflows SET state = ?, approval_json = ?,"
                " updated_at = ?, last_checked_at = ? WHERE task_id = ?",
                (target.value, approval_json, at, at, approval.task_id),
            )
            if cursor.rowcount != 1:
                raise StoreError(f"AWR task {approval.task_id!r} disappeared during approval")
            self.store.append_audit_in_transaction(
                conn,
                kind="awr.approval.granted" if approval.approved else "awr.approval.denied",
                task_id=approval.task_id,
                payload={
                    "proposal_sha256": approval.proposal_sha256,
                    "approver_id": approval.approver_id,
                },
                at=at,
            )
        return True

    def get_task(self, task_id: str) -> AwrTaskSnapshot:
        """Return a typed snapshot after rechecking request/proposal fingerprints."""
        require_safe_id(task_id, where="AwrOps.task_id")
        with self.store.read_transaction() as conn:
            snapshot = self._select_snapshot(conn, task_id)
        if snapshot is None:
            raise StoreError(f"unknown AWR task {task_id!r}")
        return snapshot

    def proposal_history(self, task_id: str) -> tuple[AwrProposalRevision, ...]:
        """Return immutable proposal revisions in their recorded order."""
        require_safe_id(task_id, where="AwrOps.task_id")
        with self.store.read_transaction() as conn:
            current = self._select_snapshot(conn, task_id)
            if current is None:
                raise StoreError(f"unknown AWR task {task_id!r}")
            rows = conn.execute(
                "SELECT revision, proposal_sha256, proposal_json, created_at "
                "FROM awr_ops_proposals WHERE task_id = ? ORDER BY revision",
                (task_id,),
            ).fetchall()
        revisions: list[AwrProposalRevision] = []
        for expected_revision, row in enumerate(rows, start=1):
            try:
                proposal = AwrProposal.from_dict(
                    _decode_object(row["proposal_json"], where="proposal revision")
                )
            except (ContractError, TypeError, ValueError) as exc:
                raise OpsIntegrityError("stored proposal revision violates its contract") from exc
            digest = proposal.proposal_sha256
            if (
                int(row["revision"]) != expected_revision
                or digest != row["proposal_sha256"]
                or proposal.task_id != task_id
                or proposal.request_sha256 != current.request_sha256
            ):
                raise OpsIntegrityError("stored proposal revision failed integrity checks")
            _utc_timestamp(row["created_at"], where="proposal revision created_at")
            revisions.append(
                AwrProposalRevision(
                    revision=expected_revision,
                    proposal_sha256=digest,
                    proposal=proposal,
                    created_at=row["created_at"],
                )
            )
        return tuple(revisions)

    def _select_snapshot(
        self, conn: sqlite3.Connection, task_id: str
    ) -> AwrTaskSnapshot | None:
        """Read and validate one workflow plus its current immutable proposal."""
        row = conn.execute(
            "SELECT w.*, p.revision AS proposal_revision,"
            " p.proposal_sha256 AS stored_proposal_sha256, p.proposal_json "
            "FROM awr_ops_workflows AS w LEFT JOIN awr_ops_proposals AS p"
            " ON p.task_id = w.task_id AND p.proposal_sha256 = w.current_proposal_sha256"
            " WHERE w.task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            return None

        request = self._decode_request(
            row["request_json"],
            expected_task_id=task_id,
            expected_sha256=row["request_sha256"],
        )
        try:
            state = OpsState(row["state"])
        except (TypeError, ValueError) as exc:
            raise OpsIntegrityError("stored AWR state is unknown") from exc

        current_sha = row["current_proposal_sha256"]
        proposal: AwrProposal | None = None
        if current_sha is None:
            if row["proposal_json"] is not None or row["stored_proposal_sha256"] is not None:
                raise OpsIntegrityError(
                    "proposal row exists without a current proposal fingerprint"
                )
        else:
            try:
                _require_sha256(current_sha, where="stored current proposal fingerprint")
                proposal = AwrProposal.from_dict(
                    _decode_object(row["proposal_json"], where="current proposal")
                )
            except (ContractError, TypeError, ValueError) as exc:
                raise OpsIntegrityError("stored current proposal violates its contract") from exc
            if (
                row["stored_proposal_sha256"] != current_sha
                or proposal.proposal_sha256 != current_sha
                or proposal.task_id != task_id
                or proposal.request_sha256 != request.request_sha256
            ):
                raise OpsIntegrityError("stored current proposal failed fingerprint checks")

        approval: AwrApproval | None = None
        if row["approval_json"] is not None:
            try:
                approval = AwrApproval.from_dict(
                    _decode_object(row["approval_json"], where="approval")
                )
            except ContractError as exc:
                raise OpsIntegrityError("stored approval violates its contract") from exc
            if (
                approval.task_id != task_id
                or approval.proposal_sha256 != current_sha
                or approval.approved != (state is OpsState.APPROVED)
            ):
                raise OpsIntegrityError(
                    "stored approval is not bound to the current workflow state"
                )

        if state is OpsState.REQUESTED and (proposal is not None or approval is not None):
            raise OpsIntegrityError("requested state cannot contain a proposal or approval")
        if state is OpsState.PROPOSED and (proposal is None or approval is not None):
            raise OpsIntegrityError("proposed state requires a proposal and no active approval")
        if state in {OpsState.APPROVED, OpsState.REJECTED} and (
            proposal is None or approval is None
        ):
            raise OpsIntegrityError("decided state requires its current proposal and decision")

        for column in ("created_at", "updated_at", "last_checked_at"):
            _utc_timestamp(row[column], where=f"stored {column}")
        return AwrTaskSnapshot(
            request=request,
            request_sha256=request.request_sha256,
            state=state,
            proposal=proposal,
            proposal_sha256=current_sha,
            approval=approval,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_checked_at=row["last_checked_at"],
        )

    @staticmethod
    def _decode_request(
        raw: Any,
        *,
        expected_task_id: str,
        expected_sha256: str,
    ) -> AwrTaskRequest:
        """Parse a stored request and prove it matches the stable row identity."""
        try:
            request = AwrTaskRequest.from_dict(_decode_object(raw, where="request"))
            _require_sha256(expected_sha256, where="stored request fingerprint")
        except ContractError as exc:
            raise OpsIntegrityError("stored request violates its contract") from exc
        if request.task_id != expected_task_id or request.request_sha256 != expected_sha256:
            raise OpsIntegrityError("stored request failed identity or fingerprint checks")
        return request
