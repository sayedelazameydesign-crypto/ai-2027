"""Fleet-2027 — cooperative, durable, evidence-verified agent fleet runtime.

Where this sits in the AI-2027 architecture
-------------------------------------------
The repository's own gap audit records ``agent-fleet`` as **absent**: a single
``AutonomousAgent`` plans and executes, sessions are persisted for *resume*, and
nothing coordinates cooperating specialists. Fleet-2027 is the missing layer,
built to the same house rules as its neighbours:

* **stdlib only** (``sqlite3``, ``threading``, ``hashlib``) — no new dependency,
  so ``scripts/ci_guard.py``'s zero-dependency doctrine is untouched.
* **evidence-first** — a step succeeds only after an independent verifier
  re-derives its evidence from disk; an agent's ``ok=True`` is a claim, not a
  result.
* **Free-First** — agent declarations carry explicit cost tiers and the registry
  refuses a fleet with no local/free-tier agent.
* **no silent progress** — every transition is one SQL ``UPDATE`` whose row
  count is checked, and every transition is appended to a hash-chained audit log.

Quick start
-----------
    import sys; sys.path.insert(0, "fleet-2027")
    from fleet2027 import (
        AgentRegistry, AgentSpec, DurableTaskStore, FleetRunner,
        PlanStep, TaskSpec,
    )

    registry = AgentRegistry()
    registry.register(
        AgentSpec(name="writer", role="writes files", produces=("report",)),
        lambda ctx: ...,
    )
    spec = TaskSpec(
        task_id="demo-1",
        goal="produce a report",
        steps=(PlanStep(step_id="s1", agent="writer", action="write"),),
    )
    with DurableTaskStore("/tmp/fleet.db") as store:
        report = FleetRunner(store, registry).submit_and_run(spec)
        print(report.status)

CLI
---
    python3 fleet-2027/cli.py demo --db /tmp/fleet.db
    python3 fleet-2027/cli.py plan --db /tmp/fleet.db --spec spec.json
    python3 fleet-2027/cli.py run --db /tmp/fleet.db --task demo-1
    python3 fleet-2027/cli.py recover --db /tmp/fleet.db
    python3 fleet-2027/cli.py status --db /tmp/fleet.db
"""

from __future__ import annotations

from .contracts import (
    BLOCKING_STEP_STATUSES,
    RESOLVED_STEP_STATUSES,
    TERMINAL_STEP_STATUSES,
    TERMINAL_TASK_STATUSES,
    AgentSpec,
    ArtifactRef,
    ContractError,
    CostTier,
    EvidenceKind,
    PlanError,
    PlanStep,
    StepResult,
    StepSnapshot,
    StepStatus,
    TaskConflictError,
    TaskSnapshot,
    TaskSpec,
    TaskStatus,
    VerificationReport,
    canonical_json,
    sha256_file,
    sha256_text,
    utc_now,
)
from .registry import (
    AgentRegistrationError,
    AgentRegistry,
    Bulkhead,
    CircuitBreaker,
    HandlerContext,
    build_reviewer,
    cost_guard,
)
from .runner import (
    FleetError,
    FleetRunner,
    RunnerConfig,
    StepFailure,
    TaskRunReport,
    ensure_runnable,
    summarize_snapshot,
    task_from_steps,
)
from .store import (
    DurableTaskStore,
    LeaseLostError,
    StoreError,
    TransactionError,
)
from .verifier import (
    PathEscape,
    VerificationPolicy,
    aggregate_report,
    resolve_artifact,
    verify_step,
)

__version__ = "0.1.0"

__all__ = [
    "AgentRegistrationError",
    "AgentRegistry",
    "AgentSpec",
    "ArtifactRef",
    "BLOCKING_STEP_STATUSES",
    "Bulkhead",
    "CircuitBreaker",
    "ContractError",
    "CostTier",
    "DurableTaskStore",
    "EvidenceKind",
    "FleetError",
    "FleetRunner",
    "HandlerContext",
    "LeaseLostError",
    "PathEscape",
    "PlanError",
    "PlanStep",
    "RESOLVED_STEP_STATUSES",
    "RunnerConfig",
    "StoreError",
    "StepFailure",
    "StepResult",
    "StepSnapshot",
    "StepStatus",
    "TERMINAL_STEP_STATUSES",
    "TERMINAL_TASK_STATUSES",
    "TaskConflictError",
    "TaskRunReport",
    "TaskSnapshot",
    "TaskSpec",
    "TaskStatus",
    "TransactionError",
    "VerificationPolicy",
    "VerificationReport",
    "__version__",
    "aggregate_report",
    "build_reviewer",
    "canonical_json",
    "cost_guard",
    "ensure_runnable",
    "resolve_artifact",
    "sha256_file",
    "sha256_text",
    "summarize_snapshot",
    "task_from_steps",
    "utc_now",
    "verify_step",
]
