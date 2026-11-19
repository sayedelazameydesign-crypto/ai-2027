"""Checkpoint/resume contract of the real autonomous agent (in-process)."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from ai2027_protocol.agent import (
    AgentResumeError,
    AgentTaskRequest,
    AutonomousAgent,
    SessionStore,
)
from ai2027_protocol.contracts import ToolInvocation
from ai2027_protocol.planner_engine import PlannerDecision
from ai2027_protocol.router import FreeFirstRouter
from ai2027_protocol.tools import ToolContext, ToolOutcome, ToolSpec

TASK = "resume contract test task"


class _Planner:
    """Deterministic one-tool-call-per-step planner."""

    async def decide(self, **kwargs: Any) -> tuple[PlannerDecision, str, str, bool]:
        step_num = int(kwargs["step_num"])
        return (
            PlannerDecision(
                thought=f"step {step_num}",
                plan=("one", "two", "three"),
                actions=(ToolInvocation(tool="gate-step", args={"step": step_num}),),
            ),
            "deterministic",
            "resume-planner-v1",
            True,
        )


class _Executor:
    """Records executed steps; can fail once to simulate a mid-task crash."""

    def __init__(self, *, fail_on_step: int = 0) -> None:
        self.executed: list[int] = []
        self._fail_on_step = fail_on_step

    def specs(self) -> tuple[ToolSpec, ...]:
        return (
            ToolSpec(
                name="gate-step",
                description="step recorder",
                read_only=False,
                parameters_hint="step:int",
            ),
        )

    async def execute(self, invocation: ToolInvocation, ctx: ToolContext) -> ToolOutcome:
        step = int(invocation.args["step"])
        self.executed.append(step)
        if self._fail_on_step and step == self._fail_on_step:
            raise RuntimeError("simulated hard crash inside the step")
        return ToolOutcome(tool="gate-step", ok=True, summary=f"step {step} ok")

    async def execute_parallel(
        self, invocations: Sequence[ToolInvocation], ctx: ToolContext
    ) -> list[ToolOutcome]:
        return [await self.execute(item, ctx) for item in invocations]


def _agent(studio: Path, executor: _Executor) -> tuple[AutonomousAgent, SessionStore]:
    store = SessionStore(studio)
    agent = AutonomousAgent(
        workspace_root=studio / "workspace",
        session_store=store,
        router=FreeFirstRouter(providers=()),
        planner=_Planner(),
        tool_executor=executor,
        read_only=False,
    )
    return agent, store


async def test_checkpoint_is_written_after_every_completed_step(tmp_path: Path) -> None:
    studio = tmp_path / "studio"
    executor = _Executor(fail_on_step=2)
    agent, store = _agent(studio, executor)
    session = store.create_session(title=TASK)

    with pytest.raises(RuntimeError, match="simulated hard crash"):
        await agent.run(session.session_id, AgentTaskRequest(task=TASK, max_steps=3))

    persisted = await store.load(session.session_id)
    assert persisted.status == "running"
    assert persisted.checkpoint_step == 1
    assert persisted.checkpoint_at
    assert [step.step for step in persisted.steps] == [1]


async def test_resume_continues_from_checkpoint_without_replaying(tmp_path: Path) -> None:
    studio = tmp_path / "studio"
    crashing, store = _agent(studio, _Executor(fail_on_step=2))
    session = store.create_session(title=TASK)
    with pytest.raises(RuntimeError):
        await crashing.run(session.session_id, AgentTaskRequest(task=TASK, max_steps=3))

    # A fresh agent + store on the same directory: the process boundary is real.
    resumed_executor = _Executor()
    resuming, fresh_store = _agent(studio, resumed_executor)
    state = await resuming.run(
        session.session_id,
        AgentTaskRequest(task=TASK, max_steps=3, resume=True),
    )

    assert resumed_executor.executed == [2, 3]
    assert state.status == "completed"
    assert [step.step for step in state.steps] == [1, 2, 3]
    assert state.checkpoint_step == 3
    assert all(plan.status == "completed" for plan in state.plan)


async def test_resume_events_report_the_checkpoint(tmp_path: Path) -> None:
    studio = tmp_path / "studio"
    crashing, store = _agent(studio, _Executor(fail_on_step=2))
    session = store.create_session(title=TASK)
    with pytest.raises(RuntimeError):
        await crashing.run(session.session_id, AgentTaskRequest(task=TASK, max_steps=3))

    resuming, _ = _agent(studio, _Executor())
    events = [
        event
        async for event in resuming.stream_run(
            session.session_id, AgentTaskRequest(task=TASK, max_steps=3, resume=True)
        )
    ]
    resumed = [event for event in events if event.event == "session_resumed"]
    assert len(resumed) == 1
    assert resumed[0].data["resume_from_step"] == 1
    assert resumed[0].data["next_step"] == 2


async def test_resume_is_refused_for_a_completed_session(tmp_path: Path) -> None:
    studio = tmp_path / "studio"
    agent, store = _agent(studio, _Executor())
    session = store.create_session(title=TASK)
    await agent.run(session.session_id, AgentTaskRequest(task=TASK, max_steps=2))

    with pytest.raises(AgentResumeError, match="not 'running'"):
        await agent.run(session.session_id, AgentTaskRequest(task=TASK, max_steps=2, resume=True))


async def test_resume_is_refused_when_the_task_text_differs(tmp_path: Path) -> None:
    studio = tmp_path / "studio"
    crashing, store = _agent(studio, _Executor(fail_on_step=2))
    session = store.create_session(title=TASK)
    with pytest.raises(RuntimeError):
        await crashing.run(session.session_id, AgentTaskRequest(task=TASK, max_steps=3))

    resuming, _ = _agent(studio, _Executor())
    with pytest.raises(AgentResumeError, match="differs from the checkpointed run"):
        await resuming.run(
            session.session_id,
            AgentTaskRequest(task="a completely different task", max_steps=3, resume=True),
        )


async def test_resume_is_refused_without_a_checkpoint(tmp_path: Path) -> None:
    studio = tmp_path / "studio"
    agent, store = _agent(studio, _Executor())
    session = store.create_session(title=TASK)
    # A run that died before finishing its first step: running, but nothing to resume.
    store.save_sync(session.model_copy(update={"status": "running", "task": TASK}))

    with pytest.raises(AgentResumeError, match="no checkpointed step"):
        await agent.run(session.session_id, AgentTaskRequest(task=TASK, max_steps=2, resume=True))


def test_cli_resume_requires_a_named_session(tmp_path: Path) -> None:
    from ai2027_protocol.cli import main as cli_main

    assert cli_main(["--root", str(tmp_path), "agent", "--resume", "sess_missing01"]) == 2
    assert cli_main(["--root", str(tmp_path), "agent"]) == 2


def test_cli_reports_a_refused_resume_instead_of_guessing(tmp_path: Path) -> None:
    from ai2027_protocol.cli import main as cli_main

    studio = tmp_path / ".ai2027_studio"
    agent, store = _agent(studio, _Executor())
    session = store.create_session(title=TASK)
    asyncio.run(agent.run(session.session_id, AgentTaskRequest(task=TASK, max_steps=2)))

    # A completed session cannot be resumed; the CLI must exit 2, not fabricate progress.
    assert cli_main(["--root", str(tmp_path), "agent", "--resume", session.session_id]) == 2


async def test_fresh_runs_ignore_old_checkpoints(tmp_path: Path) -> None:
    studio = tmp_path / "studio"
    crashing, store = _agent(studio, _Executor(fail_on_step=2))
    session = store.create_session(title=TASK)
    with pytest.raises(RuntimeError):
        await crashing.run(session.session_id, AgentTaskRequest(task=TASK, max_steps=3))

    executor = _Executor()
    fresh, _ = _agent(studio, executor)
    state = await fresh.run(session.session_id, AgentTaskRequest(task=TASK, max_steps=3))
    assert executor.executed == [1, 2, 3]
    assert state.status == "completed"
