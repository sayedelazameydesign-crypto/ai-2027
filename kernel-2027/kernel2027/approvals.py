"""A single approval decision, so "ask a human" means the same thing everywhere.

The audit found approval logic in 44 files across 4 engines, each with its own
idea of what "approved" means. Approval is a security boundary: it must be
fail-closed, auditable and *the same* in every engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .contracts import KernelError, utc_now


class ApprovalState(str, Enum):
    """Lifecycle of one approval request."""

    PENDING = "pending"
    GRANTED = "granted"
    DENIED = "denied"
    EXPIRED = "expired"


@dataclass(frozen=True)
class ApprovalRequest:
    """What is being asked, by whom, and how risky it is."""

    action: str
    reason: str = ""
    risk: str = "medium"
    requested_by: str = "engine"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Reject an unnamed action: an audit trail of "something" is useless."""
        if not self.action.strip():
            raise KernelError("ApprovalRequest.action must not be blank")
        if self.risk not in {"low", "medium", "high", "critical"}:
            raise KernelError(f"unknown risk level {self.risk!r}")


@dataclass(frozen=True)
class ApprovalDecision:
    """The verdict, who gave it, and why — always serialisable for the audit log."""

    state: ApprovalState
    approved: bool
    decided_by: str = ""
    note: str = ""
    decided_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise for persistence."""
        return {
            "state": self.state.value,
            "approved": self.approved,
            "decided_by": self.decided_by,
            "note": self.note,
            "decided_at": self.decided_at,
        }


#: Risk levels that may never be auto-approved, regardless of policy.
NEVER_AUTO: frozenset[str] = frozenset({"critical"})


def decide(
    request: ApprovalRequest,
    *,
    auto_allow: frozenset[str] = frozenset(),
    granted: bool | None = None,
    decided_by: str = "policy",
    note: str = "",
) -> ApprovalDecision:
    """Resolve a request **fail-closed**.

    The rules, in order:
    1. an explicit denial always wins;
    2. ``critical`` risk can never be auto-approved, even if allow-listed;
    3. an allow-listed action may be auto-approved;
    4. anything else stays ``PENDING`` — not approved. Absence of an answer is
       never consent, which is the bug the per-engine copies kept shipping.
    """
    if granted is False:
        return ApprovalDecision(ApprovalState.DENIED, False, decided_by, note, utc_now())
    if granted is True and request.risk not in NEVER_AUTO:
        return ApprovalDecision(ApprovalState.GRANTED, True, decided_by, note, utc_now())
    if request.risk in NEVER_AUTO:
        return ApprovalDecision(
            ApprovalState.PENDING, False, "", "critical risk requires a human", utc_now()
        )
    if request.action in auto_allow:
        return ApprovalDecision(
            ApprovalState.GRANTED, True, "policy:auto_allow", note or "listed as safe", utc_now()
        )
    return ApprovalDecision(ApprovalState.PENDING, False, "", note, utc_now())


def require_approval(decision: ApprovalDecision, *, action: str) -> None:
    """Raise unless the decision is an explicit approval (fail-closed helper)."""
    if not decision.approved:
        raise PermissionError(
            f"action {action!r} is not approved (state={decision.state.value}"
            f"{', note=' + decision.note if decision.note else ''})"
        )
