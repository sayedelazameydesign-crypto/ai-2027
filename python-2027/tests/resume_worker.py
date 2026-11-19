#!/usr/bin/env python3
"""Child process used by ``scripts/persistent_task_gate.py``.

It runs the **real** ``AutonomousAgent`` loop with a deterministic planner and a
tool executor that journals every attempt/completion to a log file. In ``start``
mode it SIGKILLs **itself** in the middle of a step (after the previous step was
checkpointed, before this one completes), which is the only honest way to prove
that resume does not restart from step 1. In ``resume`` mode it continues the
same session from its checkpoint.

Run (never imported by the package):

    python tests/resume_worker.py --mode start  --studio-dir DIR --log LOG ...
    python tests/resume_worker.py --mode resume --studio-dir DIR --log LOG ...

Python 3.11+.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import signal
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(_SRC))

from ai2027_protocol.agent import (  # noqa: E402  (path set above)
    AgentSessionState,
    AgentTaskRequest,
    AutonomousAgent,
    SessionStore,
)
from ai2027_protocol.contracts import ToolInvocation  # noqa: E402
from ai2027_protocol.planner_engine import PlannerDecision  # noqa: E402
from ai2027_protocol.router import FreeFirstRouter  # noqa: E402
from ai2027_protocol.tools import ToolContext, ToolOutcome, ToolSpec  # noqa: E402


class GatePlanner:
    """Deterministic planner: one journaled tool call per step, no LLM."""

    async def decide(self, **kwargs: Any) -> tuple[PlannerDecision, str, str, bool]:
        """Return the fixed decision for the requested step number."""
        step_num = int(kwargs["step_num"])
        plan = tuple(f"gate-step-{idx}" for idx in range(1, 5))
        return (
            PlannerDecision(
                thought=f"gate step {step_num}",
                plan=plan,
                actions=(ToolInvocation(tool="gate-step", args={"step": step_num}),),
            ),
            "deterministic",
            "gate-planner-v1",
            True,
        )


class GateExecutor:
    """Journals attempt/completion lines and can SIGKILL itself mid-step."""

    def __init__(self, *, log_path: Path, crash_on_step: int) -> None:
        self._log_path = log_path
        self._crash_on_step = crash_on_step

    def specs(self) -> tuple[ToolSpec, ...]:
        """Return the single tool descriptor this executor understands."""
        return (
            ToolSpec(
                name="gate-step",
                description="Journal one gate step.",
                read_only=False,
                parameters_hint="step:int",
            ),
        )

    def _append(self, line: str) -> None:
        with self._log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    async def execute(self, invocation: ToolInvocation, ctx: ToolContext) -> ToolOutcome:
        """Journal the attempt, optionally die, then complete and write an artifact."""
        step = int(invocation.args.get("step", 0))
        self._append(f"attempt:{step}")
        if self._crash_on_step and step == self._crash_on_step:
            self._append(f"crash:{step}")
            os.kill(os.getpid(), signal.SIGKILL)

        rel = f"step-{step}.txt"
        target = ctx.sandbox_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        body = f"gate step {step} completed\n"
        target.write_text(body, encoding="utf-8")
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        self._append(f"done:{step}:{digest}")
        return ToolOutcome(
            tool="gate-step",
            ok=True,
            summary=f"gate step {step} done",
            artifact_path=rel,
            verified_sha256=digest,
            artifact_kind="text",
        )

    async def execute_parallel(
        self, invocations: Sequence[ToolInvocation], ctx: ToolContext
    ) -> list[ToolOutcome]:
        """Execute the batch sequentially; the gate needs a deterministic order."""
        return [await self.execute(item, ctx) for item in invocations]


def _build_agent(
    studio_dir: Path, *, log_path: Path, crash_on_step: int
) -> tuple[AutonomousAgent, SessionStore]:
    workspace = studio_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    store = SessionStore(studio_dir)
    agent = AutonomousAgent(
        workspace_root=workspace,
        session_store=store,
        router=FreeFirstRouter(providers=()),
        planner=GatePlanner(),
        tool_executor=GateExecutor(log_path=log_path, crash_on_step=crash_on_step),
        read_only=False,
    )
    return agent, store


async def _run(args: argparse.Namespace) -> int:
    studio_dir = Path(args.studio_dir)
    log_path = Path(args.log)
    session_file = Path(args.session_file)

    if args.mode == "start":
        agent, store = _build_agent(studio_dir, log_path=log_path, crash_on_step=args.crash_on_step)
        state = store.create_session(title="PAP persistent-task gate")
        await asyncio.to_thread(session_file.write_text, state.session_id, encoding="utf-8")
        await agent.run(
            state.session_id,
            AgentTaskRequest(task=args.task, max_steps=args.max_steps, allow_remote=False),
        )
        final: AgentSessionState = await store.load(state.session_id)
        print(f"completed status={final.status} steps={len(final.steps)}")
        return 0

    if args.mode == "resume":
        session_id = (await asyncio.to_thread(session_file.read_text, encoding="utf-8")).strip()
        agent, store = _build_agent(studio_dir, log_path=log_path, crash_on_step=0)
        state = await agent.run(
            session_id,
            AgentTaskRequest(
                task=args.task, max_steps=args.max_steps, allow_remote=False, resume=True
            ),
        )
        print(f"resumed status={state.status} steps={len(state.steps)}")
        return 0

    raise SystemExit(f"unknown mode {args.mode!r}")


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run one gate phase."""
    parser = argparse.ArgumentParser(description="PAP persistent-task gate worker.")
    parser.add_argument("--mode", choices=("start", "resume"), required=True)
    parser.add_argument("--studio-dir", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--session-file", required=True)
    parser.add_argument("--task", default="persistent task gate run")
    parser.add_argument("--max-steps", type=int, default=4)
    parser.add_argument("--crash-on-step", type=int, default=0)
    return asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
