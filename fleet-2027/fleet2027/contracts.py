"""Strict, dependency-free contracts for the AI-2027 Fleet runtime.

Design philosophy (mirrors `python-2027`'s pydantic `StrictModel`):

* **Unknown fields are refused, not ignored** — a silently dropped field is a
  silent behaviour change, and silent behaviour changes are how "durable"
  systems lose work.
* **Invalid states are unrepresentable** — a step cannot be ``RUNNING`` while
  carrying a ``completed_at`` timestamp; the store enforces the same rules.
* **Every record round-trips through JSON** — a crashed process must be
  resumable by a *different* process on a *different* machine, so nothing that
  matters may live only in memory.

Python 3.10+ · stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class ContractError(ValueError):
    """Raised when a record violates a declared contract."""


class PlanError(ContractError):
    """Raised when a plan is not executable as written (cycle, missing agent…)."""


class TaskConflictError(ContractError):
    """Raised when one task id is reused for a different specification."""


# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #


class TaskStatus(str, Enum):
    """Lifecycle of one durable task."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEAD_LETTER = "dead_letter"


class StepStatus(str, Enum):
    """Lifecycle of one step inside a task."""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    RETRYING = "retrying"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    DEAD_LETTER = "dead_letter"


#: Statuses that resolve a dependency (a skipped step resolves it *and* marks
#: the task degraded, which is recorded rather than hidden).
RESOLVED_STEP_STATUSES = frozenset(
    {StepStatus.SUCCEEDED, StepStatus.SKIPPED, StepStatus.CANCELLED}
)
#: Statuses that block every dependent step.
BLOCKING_STEP_STATUSES = frozenset({StepStatus.FAILED, StepStatus.DEAD_LETTER})
#: Statuses after which a step is never executed again.
TERMINAL_STEP_STATUSES = frozenset(
    {
        StepStatus.SUCCEEDED,
        StepStatus.SKIPPED,
        StepStatus.CANCELLED,
        StepStatus.DEAD_LETTER,
        StepStatus.FAILED,
    }
)
TERMINAL_TASK_STATUSES = frozenset(
    {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.DEAD_LETTER,
    }
)


class EvidenceKind(str, Enum):
    """Named, independently re-checkable evidence requirements.

    A step that declares ``EvidenceKind.ARTIFACT_SHA256`` is not "done" because
    an agent said so; it is done when the sha256 of the file *on disk* matches
    the digest the agent claimed.
    """

    ARTIFACT_EXISTS = "artifact_exists"
    ARTIFACT_SHA256 = "artifact_sha256"
    OUTPUT_NON_EMPTY = "output_non_empty"
    EXIT_CODE_ZERO = "exit_code_zero"
    TEST_PASSED = "test_passed"
    JSON_SCHEMA = "json_schema"
    INDEPENDENT_REVIEW = "independent_review"
    NO_PENDING_COMPENSATION = "no_pending_compensation"


class CostTier(str, Enum):
    """Billing class of an agent, mirroring the repo's Free-First doctrine."""

    LOCAL = "local"
    FREE_TIER = "free_tier"
    UNKNOWN = "unknown"
    PAID = "paid"


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,126}$")


def utc_now() -> str:
    """Return the current UTC instant as an ISO-8601 string with ``Z``."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def require_safe_id(value: str, *, where: str) -> str:
    """Validate an identifier that will be used as a filename or SQL key."""
    if not isinstance(value, str) or not _SAFE_ID.match(value):
        raise ContractError(f"{where}: unsafe identifier {value!r}")
    return value


def canonical_json(payload: Any) -> str:
    """Serialise ``payload`` deterministically so hashes are reproducible."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(raw: bytes) -> str:
    """Return the hex sha256 of ``raw``."""
    return hashlib.sha256(raw).hexdigest()


def sha256_text(text: str) -> str:
    """Return the hex sha256 of the UTF-8 encoding of ``text``."""
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: str) -> str:
    """Return the hex sha256 of a file, streaming so large files stay cheap."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:  # noqa: PTH123 - explicit for clarity
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def strict_object(
    raw: Mapping[str, Any],
    *,
    where: str,
    required: Mapping[str, Any],
    optional: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a raw mapping against declared fields, refusing anything extra.

    ``required``/``optional`` map field name → ``(types...)`` tuple. ``bool`` is
    rejected where ``int`` is expected and vice versa, because a JSON ``true``
    silently accepted as ``1`` is a bug factory.
    """
    if not isinstance(raw, Mapping):
        raise ContractError(f"{where}: expected an object, got {type(raw).__name__}")
    optional = optional or {}
    unknown = sorted(set(raw) - set(required) - set(optional))
    if unknown:
        raise ContractError(f"{where}: unknown field(s) {unknown}")
    missing = sorted(set(required) - set(raw))
    if missing:
        raise ContractError(f"{where}: missing field(s) {missing}")
    out: dict[str, Any] = {}
    for name, expected in {**required, **optional}.items():
        if name not in raw:
            continue
        value = raw[name]
        if isinstance(expected, tuple):
            types: tuple[type, ...] = expected
        else:  # pragma: no cover - defensive, callers pass tuples
            types = (expected,)
        _check_type(value, types, where=f"{where}.{name}")
        out[name] = value
    return out


def _check_type(value: Any, types: tuple[type, ...], *, where: str) -> None:
    """Reject ``bool`` where a number is expected and enforce the declared type."""
    if isinstance(value, bool) and bool not in types:
        raise ContractError(f"{where}: expected {types}, got bool")
    if not isinstance(value, types):
        raise ContractError(
            f"{where}: expected {', '.join(t.__name__ for t in types)}, "
            f"got {type(value).__name__}"
        )


def _tuple_of_str(value: Any, *, where: str) -> tuple[str, ...]:
    """Normalise a JSON array of strings into a tuple, refusing non-strings."""
    if isinstance(value, str):
        raise ContractError(f"{where}: expected an array of strings, got a bare string")
    if not isinstance(value, Iterable):
        raise ContractError(f"{where}: expected an array of strings")
    items = tuple(value)
    for item in items:
        if not isinstance(item, str) or not item:
            raise ContractError(f"{where}: array contains a non-string entry {item!r}")
    return items


# --------------------------------------------------------------------------- #
# Agent specification
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AgentSpec:
    """Declared capability contract of one specialist agent.

    ``produces`` names the artifact kinds the agent is allowed to claim; the
    verifier checks those claims against disk. ``fallbacks`` powers graceful
    degradation: if this agent's circuit breaker is open, the fleet either
    substitutes a declared fallback or marks the step ``skipped`` (non-critical)
    / fails closed (critical).
    """

    name: str
    role: str
    capabilities: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    cost_tier: CostTier = CostTier.LOCAL
    timeout_s: float = 30.0
    max_attempts: int = 2
    idempotent: bool = True
    requires_evidence: tuple[str, ...] = (EvidenceKind.OUTPUT_NON_EMPTY.value,)
    fallbacks: tuple[str, ...] = ()
    max_parallel: int = 2

    def __post_init__(self) -> None:
        """Validate the declaration at construction time."""
        require_safe_id(self.name, where="AgentSpec.name")
        if not self.role:
            raise ContractError("AgentSpec.role must not be empty")
        if self.timeout_s <= 0:
            raise ContractError("AgentSpec.timeout_s must be > 0")
        if not 1 <= self.max_attempts <= 10:
            raise ContractError("AgentSpec.max_attempts must be within 1..10")
        if self.max_parallel < 1:
            raise ContractError("AgentSpec.max_parallel must be >= 1")
        for item in self.requires_evidence:
            EvidenceKind(item)  # raises ValueError on an unknown requirement

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe mapping."""
        return {
            "name": self.name,
            "role": self.role,
            "capabilities": list(self.capabilities),
            "produces": list(self.produces),
            "cost_tier": self.cost_tier.value,
            "timeout_s": self.timeout_s,
            "max_attempts": self.max_attempts,
            "idempotent": self.idempotent,
            "requires_evidence": list(self.requires_evidence),
            "fallbacks": list(self.fallbacks),
            "max_parallel": self.max_parallel,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> AgentSpec:
        """Rebuild a spec from its serialised form, refusing unknown fields."""
        data = strict_object(
            raw,
            where="AgentSpec",
            required={
                "name": (str,),
                "role": (str,),
            },
            optional={
                "capabilities": (list,),
                "produces": (list,),
                "cost_tier": (str,),
                "timeout_s": (int, float),
                "max_attempts": (int,),
                "idempotent": (bool,),
                "requires_evidence": (list,),
                "fallbacks": (list,),
                "max_parallel": (int,),
            },
        )
        return cls(
            name=data["name"],
            role=data["role"],
            capabilities=_tuple_of_str(data.get("capabilities", ()), where="capabilities"),
            produces=_tuple_of_str(data.get("produces", ()), where="produces"),
            cost_tier=CostTier(data.get("cost_tier", CostTier.LOCAL.value)),
            timeout_s=float(data.get("timeout_s", 30.0)),
            max_attempts=int(data.get("max_attempts", 2)),
            idempotent=bool(data.get("idempotent", True)),
            requires_evidence=_tuple_of_str(
                data.get("requires_evidence", (EvidenceKind.OUTPUT_NON_EMPTY.value,)),
                where="requires_evidence",
            ),
            fallbacks=_tuple_of_str(data.get("fallbacks", ()), where="fallbacks"),
            max_parallel=int(data.get("max_parallel", 2)),
        )


# --------------------------------------------------------------------------- #
# Plan
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PlanStep:
    """One node of the task DAG.

    ``depends_on`` names other step ids; execution order is derived, never
    hard-coded, so a plan can be parallelised without rewriting it.
    """

    step_id: str
    agent: str
    action: str
    depends_on: tuple[str, ...] = ()
    inputs: Mapping[str, Any] = field(default_factory=dict)
    critical: bool = True
    max_attempts: int | None = None
    verification: tuple[str, ...] = ()
    timeout_s: float | None = None

    def __post_init__(self) -> None:
        """Validate identifiers, dependencies and evidence names."""
        require_safe_id(self.step_id, where="PlanStep.step_id")
        require_safe_id(self.agent, where="PlanStep.agent")
        if not self.action:
            raise ContractError("PlanStep.action must not be empty")
        if self.step_id in self.depends_on:
            raise PlanError(f"step {self.step_id!r} depends on itself")
        if self.max_attempts is not None and not 1 <= self.max_attempts <= 10:
            raise ContractError("PlanStep.max_attempts must be within 1..10")
        if self.timeout_s is not None and self.timeout_s <= 0:
            raise ContractError("PlanStep.timeout_s must be > 0")
        for item in self.verification:
            EvidenceKind(item)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe mapping."""
        return {
            "step_id": self.step_id,
            "agent": self.agent,
            "action": self.action,
            "depends_on": list(self.depends_on),
            "inputs": dict(self.inputs),
            "critical": self.critical,
            "max_attempts": self.max_attempts,
            "verification": list(self.verification),
            "timeout_s": self.timeout_s,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> PlanStep:
        """Rebuild a step from its serialised form."""
        data = strict_object(
            raw,
            where="PlanStep",
            required={"step_id": (str,), "agent": (str,), "action": (str,)},
            optional={
                "depends_on": (list,),
                "inputs": (dict,),
                "critical": (bool,),
                "max_attempts": (int, type(None)),
                "verification": (list,),
                "timeout_s": (int, float, type(None)),
            },
        )
        timeout = data.get("timeout_s")
        return cls(
            step_id=data["step_id"],
            agent=data["agent"],
            action=data["action"],
            depends_on=_tuple_of_str(data.get("depends_on", ()), where="depends_on"),
            inputs=dict(data.get("inputs", {})),
            critical=bool(data.get("critical", True)),
            max_attempts=data.get("max_attempts"),
            verification=_tuple_of_str(data.get("verification", ()), where="verification"),
            timeout_s=float(timeout) if timeout is not None else None,
        )


@dataclass(frozen=True)
class TaskSpec:
    """Immutable description of one unit of durable work.

    The specification is hashed; re-submitting the same id with a different
    hash raises :class:`TaskConflictError` instead of silently mutating work
    that may already be half-executed.
    """

    task_id: str
    goal: str
    steps: tuple[PlanStep, ...]
    created_by: str = "cli"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate the plan structurally before it can ever be persisted."""
        require_safe_id(self.task_id, where="TaskSpec.task_id")
        if not self.goal.strip():
            raise ContractError("TaskSpec.goal must not be empty")
        if not self.steps:
            raise ContractError("TaskSpec.steps must not be empty")
        ids = [step.step_id for step in self.steps]
        if len(set(ids)) != len(ids):
            raise PlanError(f"duplicate step ids in task {self.task_id!r}")
        known = set(ids)
        for step in self.steps:
            unknown = sorted(set(step.depends_on) - known)
            if unknown:
                raise PlanError(
                    f"step {step.step_id!r} depends on unknown step(s) {unknown}"
                )
        _assert_acyclic(self.steps)

    def spec_hash(self) -> str:
        """Stable content hash used for idempotent submission and conflict checks."""
        return sha256_text(canonical_json(self.to_dict()))

    def to_dict(self) -> dict[str, Any]:
        """Serialise the whole specification deterministically."""
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "created_by": self.created_by,
            "metadata": dict(self.metadata),
            "steps": [step.to_dict() for step in self.steps],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> TaskSpec:
        """Rebuild a task specification from JSON."""
        data = strict_object(
            raw,
            where="TaskSpec",
            required={"task_id": (str,), "goal": (str,), "steps": (list,)},
            optional={"created_by": (str,), "metadata": (dict,)},
        )
        steps = tuple(PlanStep.from_dict(item) for item in data["steps"])
        return cls(
            task_id=data["task_id"],
            goal=data["goal"],
            steps=steps,
            created_by=data.get("created_by", "cli"),
            metadata=dict(data.get("metadata", {})),
        )


def _assert_acyclic(steps: Sequence[PlanStep]) -> None:
    """Run an iterative DFS; a cycle is a plan bug, never a runtime surprise."""
    graph = {step.step_id: step.depends_on for step in steps}
    white, grey, black = 0, 1, 2
    colour = dict.fromkeys(graph, white)
    for root in graph:
        if colour[root] != white:
            continue
        stack: list[tuple[str, int]] = [(root, 0)]
        while stack:
            node, index = stack.pop()
            if index == 0:
                colour[node] = grey
            deps = graph[node]
            if index < len(deps):
                stack.append((node, index + 1))
                dep = deps[index]
                state = colour[dep]
                if state == grey:
                    raise PlanError(f"dependency cycle detected at {node!r} -> {dep!r}")
                if state == white:
                    stack.append((dep, 0))
            else:
                colour[node] = black


# --------------------------------------------------------------------------- #
# Runtime records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ArtifactRef:
    """A produced file plus the digest needed to re-verify it later."""

    path: str
    sha256: str
    bytes: int = 0
    kind: str = "file"

    def __post_init__(self) -> None:
        """Refuse obviously malformed digests early."""
        if len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256):
            raise ContractError(f"ArtifactRef.sha256 is not a hex sha256: {self.sha256!r}")
        if self.bytes < 0:
            raise ContractError("ArtifactRef.bytes must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe mapping."""
        return {
            "path": self.path,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ArtifactRef:
        """Rebuild from JSON."""
        data = strict_object(
            raw,
            where="ArtifactRef",
            required={"path": (str,), "sha256": (str,)},
            optional={"bytes": (int,), "kind": (str,)},
        )
        return cls(
            path=data["path"],
            sha256=data["sha256"],
            bytes=int(data.get("bytes", 0)),
            kind=data.get("kind", "file"),
        )

    @classmethod
    def from_file(
        cls, path: str, *, kind: str = "file", record_path: str | None = None
    ) -> ArtifactRef:
        """Hash a real file on disk and describe it.

        ``path`` is where the file actually is (absolute is fine); ``record_path``
        is what gets persisted and must be workspace-relative so the verifier can
        re-hash the artifact on a machine that shares no absolute paths.
        """
        return cls(
            path=record_path or path,
            sha256=sha256_file(path),
            bytes=os.path.getsize(path),
            kind=kind,
        )


@dataclass(frozen=True)
class StepResult:
    """What an agent handler returns; the verifier decides whether to trust it."""

    ok: bool
    summary: str = ""
    output: str = ""
    artifacts: tuple[ArtifactRef, ...] = ()
    evidence: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None
    cost_usd: float = 0.0
    duration_ms: float = 0.0

    def __post_init__(self) -> None:
        """Keep the success/failure invariant honest."""
        if self.ok and self.error:
            raise ContractError("StepResult cannot be ok=True and carry an error")
        if not self.ok and not self.error:
            raise ContractError("StepResult ok=False must carry an error string")
        if self.cost_usd < 0:
            raise ContractError("StepResult.cost_usd must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe mapping."""
        return {
            "ok": self.ok,
            "summary": self.summary,
            "output": self.output,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "evidence": dict(self.evidence),
            "error": self.error,
            "cost_usd": self.cost_usd,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class VerificationReport:
    """Independent verdict for one step, computed from disk rather than trust."""

    ok: bool
    checks: Mapping[str, bool]
    failures: tuple[str, ...] = ()
    verifier: str = "independent"

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe mapping."""
        return {
            "ok": self.ok,
            "checks": dict(self.checks),
            "failures": list(self.failures),
            "verifier": self.verifier,
        }


@dataclass(frozen=True)
class StepSnapshot:
    """Read-only view of one persisted step."""

    task_id: str
    step_id: str
    ordinal: int
    agent: str
    action: str
    depends_on: tuple[str, ...]
    status: StepStatus
    attempts: int
    max_attempts: int
    critical: bool
    idempotency_key: str
    verification: tuple[str, ...]
    cost_usd: float
    summary: str | None = None
    error: str | None = None
    completed_at: str | None = None


@dataclass(frozen=True)
class TaskSnapshot:
    """Read-only view of one persisted task."""

    task_id: str
    goal: str
    status: TaskStatus
    spec_sha256: str
    created_at: str
    updated_at: str
    lease_owner: str | None
    degraded: bool
    steps: tuple[StepSnapshot, ...]

    def counts(self) -> dict[str, int]:
        """Return step counts per status — the honest progress meter."""
        result: dict[str, int] = {}
        for step in self.steps:
            result[step.status.value] = result.get(step.status.value, 0) + 1
        return result

    def pending_steps(self) -> tuple[StepSnapshot, ...]:
        """Steps that still need execution or retry."""
        live = {StepStatus.PENDING, StepStatus.READY, StepStatus.RETRYING, StepStatus.RUNNING}
        return tuple(step for step in self.steps if step.status in live)
