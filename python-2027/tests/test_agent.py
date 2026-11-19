from __future__ import annotations

import tempfile
from collections.abc import Sequence
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ai2027_protocol.agent import (
    AgentEvent,
    AgentSessionState,
    AgentTaskRequest,
    AutonomousAgent,
    PlannerDecision,
    SessionStore,
)
from ai2027_protocol.contracts import AIProvider, PromptRequest, ProviderKind
from ai2027_protocol.planner_engine import FreeFirstAgentPlanner
from ai2027_protocol.router import FreeFirstRouter
from ai2027_protocol.security import SecurityValidationError
from ai2027_protocol.telemetry import ObservabilityRuntime
from ai2027_protocol.tools import SandboxToolRuntime, ToolExecutor, ToolInvocation

_REPO_ROOT = Path(__file__).resolve().parents[2]


class _ScriptedJsonLlmProvider:
    kind = ProviderKind.GEMINI
    model = "gemini-scripted-agent"
    is_remote = True

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def is_configured(self) -> bool:
        return True

    async def complete(self, request: PromptRequest, *, timeout_s: float) -> str:
        idx = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        return self._responses[idx]


class _AbortOnSecondCallPlanner:
    def __init__(self, store: SessionStore, session_id: str) -> None:
        self._store = store
        self._sid = session_id

    async def decide(
        self,
        *,
        task: str,
        step_num: int,
        max_steps: int,
        history_summary: str,
        artifacts: Sequence[str],
        memory_context: str,
        tool_executor: ToolExecutor,
        preferred_provider: ProviderKind | None,
        allow_remote: bool,
    ) -> tuple[PlannerDecision, str, str, bool]:
        if step_num == 1:
            return (
                PlannerDecision(
                    thought="step 1 list",
                    plan=("list", "done"),
                    actions=(
                        ToolInvocation(tool="workspace.list_files", args={"path": "python-2027"}),
                    ),
                ),
                "deterministic",
                "stub",
                True,
            )
        self._store.request_abort(self._sid)
        return PlannerDecision(thought="never reached", final_answer="never"), "x", "y", True


async def test_offline_agent_runs_full_multistep_workflow_with_artifacts_and_approvals(
    tmp_path: Path,
) -> None:
    store = SessionStore(tmp_path / "studio")
    session = store.create_session(title="Offline Showcase")
    runtime = ObservabilityRuntime(json_logs=True)
    router = FreeFirstRouter(telemetry=runtime)
    agent = AutonomousAgent(
        workspace_root=_REPO_ROOT,
        session_store=store,
        router=router,
        telemetry=runtime,
    )

    state: AgentSessionState = await agent.run(
        session.session_id,
        AgentTaskRequest(
            task=(
                "Audit python-2027, run a Python script, build an SVG diagram, "
                "and propose a Gmail digest approval."
            ),
            max_steps=5,
            preferred_provider=ProviderKind.DETERMINISTIC,
        ),
    )
    assert state.status == "completed"
    assert state.mode == "offline-deterministic"
    assert state.simulated_llm is True
    assert state.cost_usd == 0.0
    assert len(state.steps) == 2
    paths = {a.path for a in state.artifacts}
    assert "deliverable.html" in paths
    assert "architecture.svg" in paths
    assert "analyze_task.py" in paths
    assert all(a.verified for a in state.artifacts)
    assert len(state.approvals) == 1


async def test_live_llm_agent_planner_parses_fenced_json_and_writes_artifact(
    tmp_path: Path,
) -> None:
    store = SessionStore(tmp_path / "studio")
    session = store.create_session(title="Live LLM Run")
    fake_llm: AIProvider = _ScriptedJsonLlmProvider(
        [
            '```json\n{"thought": "Write HTML deliverable", "plan": ["Build HTML"], '
            '"actions": [{"tool": "sandbox.write_artifact", '
            '"args": {"path": "live.html", "content": "<h1>Live LLM</h1>"}}], '
            '"final_answer": null}\n```',
            '{"thought": "Complete", "actions": [], "final_answer": "Done via live LLM."}',
        ]
    )
    router = FreeFirstRouter(providers=[fake_llm])
    agent = AutonomousAgent(
        workspace_root=_REPO_ROOT,
        session_store=store,
        router=router,
    )
    state = await agent.run(
        session.session_id,
        AgentTaskRequest(task="Create live HTML page", max_steps=4),
    )
    assert state.status == "completed"
    assert state.mode == "live-llm"
    assert state.simulated_llm is False
    assert state.provider == "gemini"
    assert state.final_answer == "Done via live LLM."
    assert any(a.path == "live.html" and a.verified for a in state.artifacts)


async def test_live_planner_handles_non_json_answer_and_abort(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "studio")
    s1 = store.create_session(title="Plaintext LLM")
    plain_llm = _ScriptedJsonLlmProvider(["Plain text final summary from Gemini"])
    planner = FreeFirstAgentPlanner(FreeFirstRouter(providers=[plain_llm]))
    decision, prov, _, sim = await planner.decide(
        task="Summarize",
        step_num=1,
        max_steps=3,
        history_summary="",
        artifacts=(),
        memory_context="",
        tool_executor=SandboxToolRuntime(),
        preferred_provider=None,
        allow_remote=True,
    )
    assert prov == "gemini"
    assert sim is False
    assert decision.final_answer == "Plain text final summary from Gemini"

    abort_agent = AutonomousAgent(
        workspace_root=_REPO_ROOT,
        session_store=store,
        router=FreeFirstRouter(),
        planner=_AbortOnSecondCallPlanner(store, s1.session_id),
    )
    events: list[AgentEvent] = []
    async for ev in abort_agent.stream_run(s1.session_id, AgentTaskRequest(task="run")):
        events.append(ev)
        if ev.event == "tool_result":
            store.request_abort(s1.session_id)
    assert events[-1].event == "aborted"
    reloaded = await store.load(s1.session_id)
    assert reloaded.status == "aborted"


@settings(max_examples=25)
@given(bad_id=st.sampled_from(["../etc", "a", "x" * 60, "bad/id", "bad\\id", "bad space"]))
def test_session_store_rejects_invalid_session_ids(bad_id: str) -> None:
    store = SessionStore(Path(tempfile.gettempdir()) / "ai2027_prop_test")
    with pytest.raises(SecurityValidationError):
        store.session_dir(bad_id)
