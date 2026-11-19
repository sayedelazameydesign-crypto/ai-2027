#!/usr/bin/env python3
"""Out-of-process worker used by the durability gate to make crashes real.

The gate runs this script twice:

``phase1``
    A slow, *effectful* agent runs in a **child process** under the runner. The
    gate ``SIGKILL``s that process in the middle of the step (no cleanup, no
    ``finally``, no graceful shutdown), then asserts the database still reports
    ``running`` with a stale heartbeat and that the recorded attempt history is
    intact.

``phase2``
    A brand-new process opens the same database, requeues the expired lease,
    resumes, and finishes. The gate asserts:

    * the resumed step's first attempt number is **2** (the lost attempt was
      recorded, not forgotten),
    * the side effect happened **exactly once** (idempotency ledger),
    * every step that was finished in phase 1 was still ``succeeded`` afterwards — the
      crash did not silently recompute completed work,
    * the audit chain still verifies.

The agent writes one line per attempt to ``effects.log`` and uses
``store.record_effect`` before writing, so a duplicate execution would be
visible as two lines.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from fleet2027 import (  # noqa: E402
    AgentRegistry,
    AgentSpec,
    ArtifactRef,
    CostTier,
    DurableTaskStore,
    EvidenceKind,
    FleetRunner,
    HandlerContext,
    PlanStep,
    RunnerConfig,
    StepResult,
    TaskSpec,
    TaskStatus,
)


def _quick_agent(ctx: HandlerContext) -> StepResult:
    """A fast step that must survive the crash as already-verified work."""
    target = ctx.workspace_root / "crash" / ctx.task_id / "first.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("first step finished before the crash\n", encoding="utf-8")
    return StepResult(
        ok=True,
        summary="first step written",
        output="first.txt",
        artifacts=(
            ArtifactRef.from_file(
                str(target),
                kind="document",
                record_path=target.relative_to(ctx.workspace_root).as_posix(),
            ),
        ),
    )


def _slow_effectful_agent(ctx: HandlerContext) -> StepResult:
    """Sleep past the point where the gate kills us, then write exactly once."""
    log = ctx.workspace_root / "crash" / "effects.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    fresh = ctx.store.record_effect(
        ctx.idempotency_key, task_id=ctx.task_id, step_id=ctx.step_id, summary="slow effect"
    )
    with log.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "task": ctx.task_id,
                    "step": ctx.step_id,
                    "attempt": ctx.attempt,
                    "fresh_effect": fresh,
                    "at": time.time(),
                }
            )
            + "\n"
        )
    time.sleep(float(ctx.inputs.get("sleep_s", 30.0)))
    target = ctx.workspace_root / "crash" / ctx.task_id / "slow.txt"
    target.write_text(f"slow step finished on attempt {ctx.attempt}\n", encoding="utf-8")
    return StepResult(
        ok=True,
        summary="slow effect applied once",
        output="slow.txt",
        artifacts=(
            ArtifactRef.from_file(
                str(target),
                kind="document",
                record_path=target.relative_to(ctx.workspace_root).as_posix(),
            ),
        ),
    )


def _registry() -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(
        AgentSpec(
            name="quick",
            role="finish fast",
            cost_tier=CostTier.LOCAL,
            requires_evidence=(EvidenceKind.ARTIFACT_SHA256.value,),
        ),
        _quick_agent,
    )
    registry.register(
        AgentSpec(
            name="slow",
            role="apply an effect slowly",
            cost_tier=CostTier.LOCAL,
            timeout_s=600.0,
            max_attempts=3,
            requires_evidence=(EvidenceKind.ARTIFACT_SHA256.value,),
        ),
        _slow_effectful_agent,
    )
    return registry


def _spec(task_id: str, *, sleep_s: float) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        goal="prove a SIGKILL cannot lose or duplicate work",
        steps=(
            PlanStep(
                step_id="first",
                agent="quick",
                action="write first.txt",
                verification=(EvidenceKind.ARTIFACT_SHA256.value,),
            ),
            PlanStep(
                step_id="slow",
                agent="slow",
                action="sleep then write slow.txt",
                depends_on=("first",),
                verification=(EvidenceKind.ARTIFACT_SHA256.value,),
                inputs={"sleep_s": sleep_s},
            ),
        ),
        created_by="crash-gate",
    )


def phase1(db: str, workspace: str, task_id: str, sleep_s: float) -> int:
    """Run until the gate kills us. Returns only if it did not."""
    ws = Path(workspace)
    ws.mkdir(parents=True, exist_ok=True)
    with DurableTaskStore(db) as store:
        runner = FleetRunner(
            store,
            _registry(),
            workspace_root=ws,
            config=RunnerConfig(heartbeat_interval_s=1.0, max_backoff_wait_s=5.0),
        )
        marker = ws / "crash" / "phase1.pid"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(os.getpid()), encoding="utf-8")
        report = runner.submit_and_run(_spec(task_id, sleep_s=sleep_s))
        print(json.dumps({"phase": 1, "status": report.status.value}))
    return 0


def phase2(db: str, workspace: str, task_id: str, *, force: bool = False, lease_s: float = 6.0) -> int:
    """Fresh process: recover the expired lease and finish the task.

    Two recovery paths are supported, both real:

    * default — **wait out the lease** and let ``recover`` requeue the step on
      its own. This proves the automatic path works with no privileged call.
    * ``force`` — skip the wait and assert the previous owner is dead, which is
      the ``SIGKILL`` case; the assertion is written to the audit chain.
    """
    ws = Path(workspace)
    with DurableTaskStore(db) as store:
        runner = FleetRunner(
            store,
            _registry(),
            workspace_root=ws,
            config=RunnerConfig(
                heartbeat_interval_s=1.0, max_backoff_wait_s=5.0, lease_seconds=lease_s
            ),
        )
        before = store.get_task(task_id)
        if force:
            recovered = runner.recover(force=True, reason="SIGKILL gate: previous owner is dead")
        else:
            import time as _time
            deadline = _time.time() + lease_s + 15
            recovered = runner.recover()
            while not recovered and _time.time() < deadline:
                _time.sleep(0.5)
                recovered = runner.recover()
        after_recover = store.get_task(task_id)
        report = runner.run(task_id)
        final = store.get_task(task_id)
        attempts = {
            row["step_id"]: [r["attempt_no"] for r in
                             store._conn.execute(
                                 "SELECT attempt_no FROM attempts WHERE task_id = ? AND step_id = ?"
                                 " ORDER BY attempt_no", (task_id, row["step_id"]))
                             ]
            for row in [{"step_id": s.step_id} for s in final.steps]
        }
        print(
            json.dumps(
                {
                    "phase": 2,
                    "status_before": before.status.value,
                    "recovered": recovered,
                    "status_after_recover": after_recover.status.value,
                    "status_final": final.status.value,
                    "degraded": final.degraded,
                    "steps": [
                        {
                            "step_id": step.step_id,
                            "status": step.status.value,
                            "attempts": step.attempts,
                            "attempt_numbers": attempts.get(step.step_id, []),
                        }
                        for step in final.steps
                    ],
                    "report": {
                        "executed": report.executed,
                        "verified": report.verified,
                        "failed": report.failed,
                    },
                }
            )
        )
        if final.status is not TaskStatus.SUCCEEDED:
            return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run one phase."""
    parser = argparse.ArgumentParser(prog="crash_worker")
    parser.add_argument("phase", choices=["phase1", "phase2"])
    parser.add_argument("--db", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--sleep", type=float, default=30.0)
    parser.add_argument("--force", action="store_true", help="assert the previous owner died")
    parser.add_argument("--lease", type=float, default=6.0)
    args = parser.parse_args(argv)
    if args.phase == "phase1":
        return phase1(args.db, args.workspace, args.task, args.sleep)
    return phase2(args.db, args.workspace, args.task, force=args.force, lease_s=args.lease)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
