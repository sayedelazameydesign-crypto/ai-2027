"""Persisted session models, kept apart from the agent that drives them.

``SessionStore`` needs :class:`AgentSessionState` to load and list sessions, and
``agent.py`` needs the store: putting the models here is what keeps that
dependency graph acyclic instead of a local-import workaround.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from .contracts import (
    BillingTier,
    BudgetSnapshot,
    CostStatus,
    ProviderAttempt,
    StrictModel,
    ToolInvocation,
)
from .tools import ToolOutcome


class PlanStep(StrictModel):
    """One trackable step in the agent's execution checklist."""

    step_id: int = Field(ge=1)
    title: str
    status: Literal["pending", "running", "completed", "failed"] = "pending"


class AgentStepTrace(StrictModel):
    """Recorded thought, tool calls, and verified observations for one loop step."""

    step: int = Field(ge=1)
    thought: str
    invocations: tuple[ToolInvocation, ...] = ()
    outcomes: tuple[ToolOutcome, ...] = ()
    latency_ms: float = Field(default=0.0, ge=0.0)
    provider: str = "deterministic"
    model: str = "offline-planner-v1"


class ArtifactRecord(StrictModel):
    """Verified deliverable stored in a session's isolated L0 sandbox."""

    path: str
    kind: str
    sha256: str | None = None
    byte_size: int = 0
    verified: bool = False
    preview_url: str


class AgentEvent(StrictModel):
    """Real-time SSE event emitted during an autonomous agent execution."""

    event: Literal[
        "session_started",
        "session_resumed",
        "plan",
        "step_start",
        "tool_result",
        "artifact",
        "approval_required",
        "final",
        "error",
        "aborted",
    ]
    session_id: str
    step: int = 0
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: str


class AgentSessionState(StrictModel):
    """Full persisted snapshot for one interactive Studio/CLI agent session."""

    session_id: str
    title: str
    created_at: str
    updated_at: str
    status: Literal["idle", "running", "completed", "aborted", "failed"] = "idle"
    mode: Literal["live-llm", "offline-deterministic"] = "offline-deterministic"
    provider: str = "deterministic"
    model: str = "offline-planner-v1"
    simulated_llm: bool = True
    cost_usd: float | None = Field(default=0.0, ge=0.0)
    billing_tier: BillingTier = BillingTier.LOCAL
    cost_status: CostStatus = CostStatus.PROVEN_ZERO_LOCAL
    cost_known: bool = True
    budget_snapshot: BudgetSnapshot | None = None
    router_attempts: tuple[ProviderAttempt, ...] = ()
    trace_id: str = ""
    task: str = ""
    plan: tuple[PlanStep, ...] = ()
    steps: tuple[AgentStepTrace, ...] = ()
    artifacts: tuple[ArtifactRecord, ...] = ()
    approvals: tuple[str, ...] = ()
    final_answer: str = ""
    checkpoint_step: int = Field(default=0, ge=0)
    checkpoint_at: str = ""
