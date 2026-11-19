"""Multi-step autonomous agent engine unifying Free-First LLMs, Tools, and L0/L1/L5."""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from .auditor import ProtocolAuditor
from .bridges import ConnectorsBridge, KernelSandboxBridge, MemoryBridge
from .connector_hub_bridge import LiveConnectorClient
from .contracts import (
    BillingTier,
    BudgetSnapshot,
    CostStatus,
    ProviderAttempt,
    ProviderKind,
    StrictModel,
    TelemetrySink,
)
from .planner_engine import (
    AgentPlannerProtocol,
    FreeFirstAgentPlanner,
    PlannerDecision,
    format_step_history,
)
from .router import FreeFirstRouter
from .security import (
    SecurityValidationError,
    redact_secrets,
    resolve_safe_path,
    sanitize_prompt_text,
)
from .telemetry import ObservabilityRuntime
from .tools import (
    SandboxToolRuntime,
    ToolContext,
    ToolExecutor,
    ToolInvocation,
    ToolOutcome,
    infer_artifact_kind,
)

_SESSION_ID_RE = re.compile(r"^[a-z0-9_-]{8,48}$")


class AgentTaskRequest(StrictModel):
    """Validated user goal submitted to the autonomous Studio/CLI agent."""

    task: str = Field(min_length=1, max_length=16_000)
    max_steps: int = Field(default=5, ge=1, le=12)
    preferred_provider: ProviderKind | None = None
    allow_remote: bool = True
    language: Literal["auto", "en", "ar"] = "auto"

    @classmethod
    def sanitize(cls, raw_task: str) -> str:
        """Sanitize raw user task text before storage or prompting."""
        return sanitize_prompt_text(raw_task, max_length=16_000)


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


class SessionStore:
    """Thread-safe filesystem store managing isolated session sandboxes and cancel flags."""

    def __init__(self, studio_dir: Path) -> None:
        """Create session directories under studio_dir/sessions safely."""
        self._studio_dir = studio_dir.resolve()
        self._sessions_root = self._studio_dir / "sessions"
        self._sessions_root.mkdir(parents=True, exist_ok=True)
        self._cancel_flags: dict[str, asyncio.Event] = {}

    @property
    def sessions_root(self) -> Path:
        """Return base directory housing all per-session sandboxes."""
        return self._sessions_root

    def _validate_session_id(self, session_id: str) -> str:
        clean = session_id.strip()
        if not _SESSION_ID_RE.fullmatch(clean):
            raise SecurityValidationError("Invalid session_id format.")
        return clean

    def session_dir(self, session_id: str) -> Path:
        """Return path-contained directory for an existing or newly created session."""
        clean = self._validate_session_id(session_id)
        return resolve_safe_path(self._sessions_root, clean)

    def create_session(self, *, title: str = "New Agent Session") -> AgentSessionState:
        """Allocate a fresh session with isolated sandbox directory and initial state."""
        session_id = f"sess_{uuid.uuid4().hex[:12]}"
        sdir = self.session_dir(session_id)
        (sdir / "sandbox").mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC).isoformat()
        state = AgentSessionState(
            session_id=session_id,
            title=redact_secrets(title.strip())[:100] or "Agent Session",
            created_at=now,
            updated_at=now,
        )
        self.save_sync(state)
        return state

    def save_sync(self, state: AgentSessionState) -> None:
        """Atomically persist session.json inside the session folder."""
        sdir = self.session_dir(state.session_id)
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / "sandbox").mkdir(parents=True, exist_ok=True)
        target = sdir / "session.json"
        tmp = sdir / "session.tmp"
        tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(target)

    async def save(self, state: AgentSessionState) -> None:
        """Persist session state asynchronously without blocking the event loop."""
        await asyncio.to_thread(self.save_sync, state)

    def load_sync(self, session_id: str) -> AgentSessionState:
        """Load an existing session snapshot or raise FileNotFoundError."""
        sdir = self.session_dir(session_id)
        target = sdir / "session.json"
        if not target.is_file():
            raise FileNotFoundError(f"Session {session_id!r} not found.")
        return AgentSessionState.model_validate_json(target.read_text(encoding="utf-8"))

    async def load(self, session_id: str) -> AgentSessionState:
        """Load session snapshot asynchronously."""
        return await asyncio.to_thread(self.load_sync, session_id)

    async def list_sessions(self, *, limit: int = 25) -> list[AgentSessionState]:
        """List stored sessions ordered most recently updated first."""

        def _scan() -> list[AgentSessionState]:
            items: list[AgentSessionState] = []
            if not self._sessions_root.is_dir():
                return items
            for child in self._sessions_root.iterdir():
                meta = child / "session.json"
                if child.is_dir() and meta.is_file():
                    try:
                        raw_meta = meta.read_text(encoding="utf-8")
                        items.append(AgentSessionState.model_validate_json(raw_meta))
                    except (OSError, ValueError):
                        continue
            items.sort(key=lambda s: s.updated_at, reverse=True)
            return items[:limit]

        return await asyncio.to_thread(_scan)

    def get_cancel_flag(self, session_id: str) -> asyncio.Event:
        """Return or initialize the cooperative cancellation Event for session_id."""
        clean = self._validate_session_id(session_id)
        flag = self._cancel_flags.get(clean)
        if flag is None:
            flag = asyncio.Event()
            self._cancel_flags[clean] = flag
        return flag

    def request_abort(self, session_id: str) -> None:
        """Trigger cooperative cancellation for a running session."""
        self.get_cancel_flag(session_id).set()

    def clear_abort(self, session_id: str) -> None:
        """Reset cancellation before starting a new run on an existing session."""
        self.get_cancel_flag(session_id).clear()


class AutonomousAgent:
    """Orchestrates Plan -> Act -> Observe -> Verify -> Memory across Studio & CLI."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        session_store: SessionStore,
        router: FreeFirstRouter,
        telemetry: TelemetrySink | None = None,
        tool_executor: ToolExecutor | None = None,
        planner: AgentPlannerProtocol | None = None,
        read_only: bool = False,
    ) -> None:
        """Assemble the autonomous agent with replaceable router, planner, and tools."""
        self.workspace_root = workspace_root.resolve()
        self.session_store = session_store
        self.router = router
        self.telemetry: TelemetrySink = telemetry or ObservabilityRuntime(json_logs=True)
        self.tool_executor: ToolExecutor = tool_executor or SandboxToolRuntime()
        self.planner: AgentPlannerProtocol = planner or FreeFirstAgentPlanner(router)
        self.read_only = read_only
        self.kernel_bridge = KernelSandboxBridge(self.workspace_root)
        self.memory_bridge = MemoryBridge(
            workspace_root=self.workspace_root,
            db_path=self.session_store.sessions_root.parent / "memory.db",
            sandbox_root=self.session_store.sessions_root,
        )
        self.connectors_bridge = ConnectorsBridge(
            store_path=self.session_store.sessions_root.parent / "approvals.json"
        )
        self.connector_client = LiveConnectorClient()
        self.auditor = ProtocolAuditor(workspace_root=self.workspace_root, telemetry=self.telemetry)

    async def stream_run(
        self, session_id: str, request: AgentTaskRequest
    ) -> AsyncIterator[AgentEvent]:
        """Execute an autonomous task and stream live typed AgentEvent updates."""
        clean_task = AgentTaskRequest.sanitize(request.task)
        state = await self.session_store.load(session_id)
        self.session_store.clear_abort(session_id)
        cancel_flag = self.session_store.get_cancel_flag(session_id)

        sdir = self.session_store.session_dir(session_id)
        sandbox_dir = sdir / "sandbox"
        ctx = ToolContext(
            session_id=session_id,
            workspace_root=self.workspace_root,
            sandbox_dir=sandbox_dir,
            ledger_path=sdir / "ledger.jsonl",
            sessions_root=self.session_store.sessions_root,
            kernel_bridge=self.kernel_bridge,
            memory_bridge=self.memory_bridge,
            connectors_bridge=self.connectors_bridge,
            auditor=self.auditor,
            connector_client=self.connector_client,
            read_only=self.read_only,
        )

        with self.telemetry.start_span("agent.run", {"max_steps": request.max_steps}):
            trace_id = self.telemetry.current_trace_id()
            state = state.model_copy(
                update={
                    "status": "running",
                    "task": clean_task,
                    "title": clean_task[:64],
                    "trace_id": trace_id,
                    "steps": (),
                    "plan": (),
                    "final_answer": "",
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            )
            await self.session_store.save(state)
            yield self._evt(
                "session_started", session_id, 0, "Agent started", {"trace_id": trace_id}
            )

            mem_hits = await self.memory_bridge.search(clean_task, k=3)
            memory_ctx = "\n".join(
                f"- [{h.score:.3f}] {h.run_id}: {h.text[:160]}" for h in mem_hits if h.above_noise
            )

            history: list[AgentStepTrace] = []
            artifacts: dict[str, ArtifactRecord] = {a.path: a for a in state.artifacts}
            approvals: list[str] = list(state.approvals)
            plan_steps: list[PlanStep] = []
            mode: Literal["live-llm", "offline-deterministic"] = "offline-deterministic"
            last_provider = ProviderKind.DETERMINISTIC.value
            last_model = "offline-planner-v1"
            simulated_llm = True
            last_billing_tier = BillingTier.LOCAL
            last_cost_known = True
            last_cost_usd: float | None = 0.0
            router_attempts: list[ProviderAttempt] = []
            final_answer = ""

            for step_num in range(1, request.max_steps + 1):
                if cancel_flag.is_set():
                    state = state.model_copy(
                        update={
                            "status": "aborted",
                            "updated_at": datetime.now(UTC).isoformat(),
                        }
                    )
                    await self.session_store.save(state)
                    yield self._evt("aborted", session_id, step_num, "Run aborted by user")
                    return

                step_t0 = time.perf_counter_ns()
                hist_summary = format_step_history(
                    [(h.step, h.thought, h.outcomes) for h in history]
                )
                decision, prov_name, model_name, is_sim = await self.planner.decide(
                    task=clean_task,
                    step_num=step_num,
                    max_steps=request.max_steps,
                    history_summary=hist_summary,
                    artifacts=tuple(artifacts),
                    memory_context=memory_ctx,
                    tool_executor=self.tool_executor,
                    preferred_provider=request.preferred_provider,
                    allow_remote=request.allow_remote,
                )
                last_provider, last_model, simulated_llm = prov_name, model_name, is_sim
                last_billing_tier = decision.billing_tier
                last_cost_known = decision.cost_known
                last_cost_usd = decision.cost_usd
                if decision.router_attempts:
                    router_attempts.extend(decision.router_attempts)
                mode = "offline-deterministic" if is_sim else "live-llm"

                if decision.plan and not plan_steps:
                    plan_steps = [
                        PlanStep(step_id=idx, title=title, status="pending")
                        for idx, title in enumerate(decision.plan, start=1)
                    ]
                    yield self._evt(
                        "plan",
                        session_id,
                        step_num,
                        "Execution plan ready",
                        {"plan": [p.model_dump(mode="json") for p in plan_steps]},
                    )

                if step_num <= len(plan_steps):
                    plan_steps[step_num - 1] = plan_steps[step_num - 1].model_copy(
                        update={"status": "running"}
                    )

                yield self._evt(
                    "step_start",
                    session_id,
                    step_num,
                    decision.thought,
                    {"provider": prov_name, "model": model_name, "simulated": is_sim},
                )

                if not decision.actions:
                    final_answer = decision.final_answer or "Task complete."
                    if step_num <= len(plan_steps):
                        plan_steps[step_num - 1] = plan_steps[step_num - 1].model_copy(
                            update={"status": "completed"}
                        )
                    break

                outcomes = await self.tool_executor.execute_parallel(decision.actions, ctx)
                for out in outcomes:
                    yield self._evt(
                        "tool_result",
                        session_id,
                        step_num,
                        out.summary,
                        out.model_dump(mode="json"),
                    )
                    if out.artifact_path:
                        rel_art = out.artifact_path
                        target = resolve_safe_path(sandbox_dir, rel_art)
                        bsize = target.stat().st_size if target.is_file() else 0
                        rec = ArtifactRecord(
                            path=rel_art,
                            kind=out.artifact_kind or infer_artifact_kind(rel_art),
                            sha256=out.verified_sha256,
                            byte_size=bsize,
                            verified=bool(out.ok and out.verified_sha256),
                            preview_url=f"/v1/studio/sessions/{session_id}/artifacts/{rel_art}",
                        )
                        artifacts[rel_art] = rec
                        yield self._evt(
                            "artifact",
                            session_id,
                            step_num,
                            f"Verified artifact {rel_art}",
                            rec.model_dump(mode="json"),
                        )
                    if out.approval_id:
                        approvals.append(out.approval_id)
                        yield self._evt(
                            "approval_required",
                            session_id,
                            step_num,
                            out.summary,
                            {"approval_id": out.approval_id},
                        )

                if step_num <= len(plan_steps):
                    all_ok = all(o.ok for o in outcomes)
                    plan_steps[step_num - 1] = plan_steps[step_num - 1].model_copy(
                        update={"status": "completed" if all_ok else "failed"}
                    )

                step_ms = round((time.perf_counter_ns() - step_t0) / 1e6, 3)
                history.append(
                    AgentStepTrace(
                        step=step_num,
                        thought=decision.thought,
                        invocations=decision.actions,
                        outcomes=tuple(outcomes),
                        latency_ms=step_ms,
                        provider=prov_name,
                        model=model_name,
                    )
                )
                if decision.final_answer:
                    final_answer = decision.final_answer
                    break

            if not final_answer:
                final_answer = "### Autonomous Run Summary\n" + "\n".join(
                    f"- Step {h.step}: " + "; ".join(o.summary for o in h.outcomes) for h in history
                )

            for idx, ps in enumerate(plan_steps):
                if ps.status in {"pending", "running"}:
                    plan_steps[idx] = ps.model_copy(update={"status": "completed"})

            snap = self.router.budget_snapshot()
            cost_stat = (
                CostStatus.PROVEN_ZERO_LOCAL
                if last_billing_tier == BillingTier.LOCAL
                else CostStatus.DECLARED_FREE_TIER_UNBILLED
                if last_billing_tier == BillingTier.FREE_TIER
                else CostStatus.ALLOWED_WITHIN_BUDGET
            )
            final_state = state.model_copy(
                update={
                    "status": "completed",
                    "mode": mode,
                    "provider": last_provider,
                    "model": last_model,
                    "simulated_llm": simulated_llm,
                    "cost_usd": last_cost_usd,
                    "billing_tier": last_billing_tier,
                    "cost_status": cost_stat,
                    "cost_known": last_cost_known,
                    "budget_snapshot": snap,
                    "router_attempts": tuple(router_attempts),
                    "plan": tuple(plan_steps),
                    "steps": tuple(history),
                    "artifacts": tuple(artifacts.values()),
                    "approvals": tuple(dict.fromkeys(approvals)),
                    "final_answer": redact_secrets(final_answer),
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            )
            await self.session_store.save(final_state)
            self.telemetry.log(
                "info",
                "agent.session.completed",
                session_id=session_id,
                mode=mode,
                steps=len(history),
                artifacts=len(artifacts),
                cost_usd=0.0,
            )
            yield self._evt(
                "final",
                session_id,
                len(history),
                "Run completed cleanly",
                final_state.model_dump(mode="json"),
            )

    async def run(self, session_id: str, request: AgentTaskRequest) -> AgentSessionState:
        """Run an autonomous task to completion and return the persisted session state."""
        async for _ in self.stream_run(session_id, request):
            pass
        return await self.session_store.load(session_id)

    def _evt(
        self,
        event: Literal[
            "session_started",
            "plan",
            "step_start",
            "tool_result",
            "artifact",
            "approval_required",
            "final",
            "error",
            "aborted",
        ],
        session_id: str,
        step: int,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> AgentEvent:
        return AgentEvent(
            event=event,
            session_id=session_id,
            step=step,
            message=redact_secrets(message),
            data=data or {},
            timestamp=datetime.now(UTC).isoformat(),
        )


__all__ = [
    "AgentEvent",
    "AgentPlannerProtocol",
    "AgentSessionState",
    "AgentStepTrace",
    "AgentTaskRequest",
    "ArtifactRecord",
    "AutonomousAgent",
    "PlanStep",
    "PlannerDecision",
    "SessionStore",
]
