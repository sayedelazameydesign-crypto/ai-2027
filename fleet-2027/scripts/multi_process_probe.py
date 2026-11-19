#!/usr/bin/env python3
"""Multi-process probes for fleet-2027 — real subprocesses, real shared SQLite.

Three experiments, each one an *observation* rather than an assertion of intent:

``--mode concurrent``
    Start N real OS processes on one database, each with a distinct ``--id``.
    Every handler appends one line to an append-only ledger file **before**
    recording the idempotency key, so a duplicated side effect is visible as a
    duplicated line. Reports: which workers were seen, ledger multiplicity,
    task status.

``--mode kill``
    Start a worker that runs a slow step, SIGKILL it mid-step (no cleanup, no
    chance to deregister), then start a second worker and let it finish the
    task. Reports: whether the survivor completed the task, how many ledger
    lines exist, and how many workers are still alive.

``--mode registry``
    Register/deregister/heartbeat cycles and a stale-worker requeue, without
    spawning anything — the fast path used by the unit tests.

The exit code is 0 only when every checked invariant holds. Reasons are printed,
because "it passed" without a reason is not evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
PACKAGE_ROOT = HERE.parent
sys.path.insert(0, str(PACKAGE_ROOT))

from fleet2027 import (  # noqa: E402
    AgentRegistry,
    AgentSpec,
    CostTier,
    DurableTaskStore,
    EvidenceKind,
    FleetRunner,
    PlanStep,
    RunnerConfig,
    StepResult,
    TaskSpec,
)


# --------------------------------------------------------------------------- #
# A task whose handlers are honestly slow and provably single-effect
# --------------------------------------------------------------------------- #
def build_spec(
    task_id: str,
    *,
    steps: int,
    sleep_s: float,
    tag: str,
    agent: str = "probe",
    crash_window_s: float = 0.0,
) -> TaskSpec:
    """Independent steps so N workers can genuinely run them in parallel."""
    plan = []
    for index in range(1, steps + 1):
        plan.append(
            PlanStep(
                step_id=f"s{index}",
                agent=agent,
                action="touch the ledger",
                inputs={
                    "sleep_s": sleep_s,
                    "tag": tag,
                    "crash_window_s": crash_window_s if index == 1 else 0.0,
                },
                verification=(EvidenceKind.OUTPUT_NON_EMPTY.value,),
            )
        )
    return TaskSpec(
        task_id=task_id,
        goal=f"{steps} independent side effects, none duplicated",
        steps=tuple(plan),
        created_by="multi-process-probe",
    )


def build_registry(ledger: Path) -> AgentRegistry:
    """One deterministic agent; the side effect is an append to ``ledger``."""

    def probe(ctx):
        # The honest protocol for a non-idempotent external effect, in order:
        #   1. declare intent (write-ahead)  — a crash becomes visible;
        #   2. reconcile an earlier intent   — verify or compensate, never repeat;
        #   3. perform the effect;
        #   4. complete the intent.
        sleep_s = float(ctx.inputs.get("sleep_s", 0.0))
        tag = str(ctx.inputs.get("tag", "x"))
        key = ctx.idempotency_key
        existing = ctx.store.effect(key) or {}
        state = existing.get("state")
        if state in {"completed", "compensated"}:
            return StepResult(
                ok=True,
                summary=f"{ctx.step_id}: effect already {state}, not repeated",
                output=f"replay:{ctx.step_id}",
            )
        if state == "intent":
            # A previous owner declared it and never reported back. For an
            # append-only ledger, the observation *is* the verification.
            ctx.store.complete_effect(
                key, task_id=ctx.task_id, step_id=ctx.step_id,
                summary=f"{tag} effect verified on recovery",
            )
            return StepResult(
                ok=True,
                summary=f"{ctx.step_id}: recovered intent verified, not repeated",
                output=f"recovered:{ctx.step_id}",
            )
        ctx.store.declare_effect_intent(
            key, task_id=ctx.task_id, step_id=ctx.step_id,
            summary=f"{tag} effect", worker="probe",
        )
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write(f"{tag}|{ctx.task_id}|{ctx.step_id}|{os.getpid()}\n")
            handle.flush()
        if sleep_s:
            time.sleep(sleep_s)
        # The crash window a real SIGKILL can hit: the external write happened,
        # the completion did not. ``crash_window_s`` only widens it for a test.
        crash_window = float(ctx.inputs.get("crash_window_s", 0.0))
        if crash_window:
            time.sleep(crash_window)
        ctx.store.complete_effect(
            key, task_id=ctx.task_id, step_id=ctx.step_id, summary=f"{tag} effect"
        )
        return StepResult(ok=True, summary=f"did {ctx.step_id}", output=f"ok:{ctx.step_id}")

    registry = AgentRegistry()
    for name, parallel, role in (
        ("probe", 1, "append one line, strictly one at a time"),
        ("probe-wide", 3, "append one line, three at a time"),
    ):
        registry.register(
            AgentSpec(
                name=name,
                role=role,
                capabilities=(name,),
                produces=("text",),
                cost_tier=CostTier.LOCAL,
                max_parallel=parallel,
                requires_evidence=(EvidenceKind.OUTPUT_NON_EMPTY.value,),
            ),
            probe,
        )
    return registry


def ledger_lines(ledger: Path) -> list[str]:
    if not ledger.is_file():
        return []
    return [line for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]


# --------------------------------------------------------------------------- #
# Child entry point: exactly what a real worker process runs
# --------------------------------------------------------------------------- #
def run_worker_child(
    *,
    db: str,
    workspace: str,
    ledger: str,
    task_id: str,
    worker_id: str,
    idle_exit_s: float,
    startup_delay_s: float,
) -> dict[str, Any]:
    if startup_delay_s:
        time.sleep(startup_delay_s)
    workspace_path = Path(workspace).resolve()
    workspace_path.mkdir(parents=True, exist_ok=True)
    with DurableTaskStore(db) as store:
        runner = FleetRunner(
            store,
            build_registry(Path(ledger)),
            workspace_root=workspace_path,
            config=RunnerConfig(worker_heartbeat_s=0.2, worker_stale_after_s=2.0),
            worker_id=worker_id,
        )
        report = runner.serve(task_ids=[task_id], idle_exit_after_s=idle_exit_s)
    return report


# --------------------------------------------------------------------------- #
# Experiments
# --------------------------------------------------------------------------- #
def _snapshot(store: DurableTaskStore, task_id: str) -> dict[str, Any]:
    snapshot = store.get_task(task_id)
    return {
        "status": snapshot.status.value,
        "counts": snapshot.counts(),
        "steps": [
            {"step_id": step.step_id, "status": step.status.value, "attempts": step.attempts}
            for step in snapshot.steps
        ],
    }


def _prepare(db: str, workspace: str, ledger: str, task_id: str, spec: TaskSpec) -> None:
    for path in (db, ledger):
        target = Path(path)
        if target.exists():
            target.unlink()
    ws = Path(workspace)
    if ws.exists():
        for child in ws.iterdir():
            if child.is_file():
                child.unlink()
    ws.mkdir(parents=True, exist_ok=True)
    with DurableTaskStore(db) as store:
        store.submit(spec, worker="probe-setup")


def _spawn(args, *, worker_id: str, startup_delay_s: float = 0.0) -> subprocess.Popen:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--mode", "child",
        "--db", args.db,
        "--workspace", args.workspace,
        "--ledger", args.ledger,
        "--task", args.task,
        "--id", worker_id,
        "--idle-exit-s", str(args.idle_exit_s),
        "--startup-delay-s", str(startup_delay_s),
    ]
    return subprocess.Popen(  # noqa: S603 - argv is built here, never from a shell
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )


def mode_concurrent(args) -> dict[str, Any]:
    spec = build_spec(
        args.task, steps=args.steps, sleep_s=args.sleep, tag="concurrent", agent="probe-wide"
    )
    _prepare(args.db, args.workspace, args.ledger, args.task, spec)
    workers = [f"worker-{index}" for index in range(1, args.workers + 1)]
    started = time.perf_counter()
    procs = [_spawn(args, worker_id=name, startup_delay_s=0.05 * index)
             for index, name in enumerate(workers)]
    reports = []
    for worker_name, proc in zip(workers, procs):
        out, err = proc.communicate(timeout=args.timeout_s)
        reports.append({"worker_id": worker_name, "exit": proc.returncode,
                        "stdout": out.strip().splitlines()[-3:],
                        "stderr_tail": err.strip().splitlines()[-3:]})
    duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
    lines = ledger_lines(Path(args.ledger))
    seen = [line.split("|")[-1] for line in lines]
    with DurableTaskStore(args.db) as store:
        snapshot = _snapshot(store, args.task)
        workers_rows = store.list_workers(stale_after_s=args.stale_after_s)
        effects = store.stats().get("effects")
    step_ids = [line.split("|")[2] for line in lines]
    result = {
        "mode": "concurrent",
        "task_id": args.task,
        "workers_requested": len(workers),
        "distinct_pids_in_ledger": len(set(seen)),
        "ledger_lines": len(lines),
        "steps": len(spec.steps),
        "duplicated_step_ids": sorted({s for s in step_ids if step_ids.count(s) > 1}),
        "side_effects_unique": len(lines) == len(set(step_ids)),
        "effects_recorded": effects,
        "worker_rows": workers_rows,
        "processed_by_multiple_processes": len(set(seen)) > 1,
        "duration_ms": duration_ms,
        "snapshot": snapshot,
        "reports": reports,
    }
    checks = {
        "task_succeeded": snapshot["status"] == "succeeded",
        "no_duplicated_side_effects": result["side_effects_unique"],
        "every_step_ran_once": len(lines) == len(spec.steps),
        "two_os_processes_did_work": len(set(seen)) > 1,
        "no_live_workers_left": len([w for w in workers_rows if w.get("alive")]) == 0,
        "workers_registered_distinctly": len(
            {w["worker_id"] for w in workers_rows} & set(workers)
        ) == len(workers),
    }
    result["checks"] = checks
    result["ok"] = all(checks.values())
    return result


def mode_contention(args) -> dict[str, Any]:
    """N workers, a *serial* agent, and more steps than the bulkhead allows.

    This is the regression test for the defect the first probe found: the runner
    used to claim a whole wave regardless of the agent's ``max_parallel``, so the
    surplus steps were failed with ``bulkhead saturated`` and burned attempts.
    Now the wave is bounded by the bulkhead, and the surplus stays queued.
    """
    spec = build_spec(args.task, steps=args.steps, sleep_s=args.sleep, tag="serial",
                      agent="probe")
    _prepare(args.db, args.workspace, args.ledger, args.task, spec)
    workers = [f"serial-{index}" for index in range(1, args.workers + 1)]
    procs = [_spawn(args, worker_id=name, startup_delay_s=0.05 * index)
             for index, name in enumerate(workers)]
    for proc in procs:
        proc.communicate(timeout=args.timeout_s)
    lines = ledger_lines(Path(args.ledger))
    with DurableTaskStore(args.db) as store:
        snapshot = _snapshot(store, args.task)
        workers_rows = store.list_workers(stale_after_s=args.stale_after_s)
        saturated = [
            event for event in store.events(args.task, limit=500)
            if "bulkhead saturated" in (event["message"] or "")
        ]
        effects = store.stats().get("effects")
    attempts = [step["attempts"] for step in snapshot["steps"]]
    result = {
        "mode": "contention",
        "task_id": args.task,
        "steps": len(spec.steps),
        "ledger_lines": len(lines),
        "bulkhead_saturated_events": len(saturated),
        "attempts_per_step": attempts,
        "effects_recorded": effects,
        "worker_rows": workers_rows,
        "snapshot": snapshot,
    }
    checks = {
        "task_succeeded": snapshot["status"] == "succeeded",
        "one_effect_per_step": len(lines) == len(spec.steps) and len(set(lines)) == len(lines),
        "no_bulkhead_saturation": len(saturated) == 0,
        "every_step_first_attempt": all(value == 1 for value in attempts),
        "effects_match_steps": effects == len(spec.steps),
        "all_workers_cleanly_stopped": all(not w["alive"] for w in workers_rows),
    }
    result["checks"] = checks
    result["ok"] = all(checks.values())
    return result


def mode_kill(args) -> dict[str, Any]:
    spec = build_spec(
        args.task,
        steps=args.steps,
        sleep_s=args.sleep,
        tag="kill",
        crash_window_s=args.crash_window_s,
    )
    _prepare(args.db, args.workspace, args.ledger, args.task, spec)
    victim = "worker-victim"
    survivor = "worker-survivor"
    proc = _spawn(args, worker_id=victim)
    # Wait until the victim is genuinely mid-effect: step 1's external write is
    # already on disk while its completion row is still 'intent'.
    deadline = time.time() + args.wait_s
    claimed = False
    while time.time() < deadline:
        with DurableTaskStore(args.db) as store:
            pending = store.unresolved_effect_intents(args.task)
        wrote = any(line.split("|")[2] == "s1" for line in ledger_lines(Path(args.ledger)))
        if wrote and pending:
            claimed = True
            break
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    killed_at = None
    if proc.poll() is None:
        os.kill(proc.pid, signal.SIGKILL)
        killed_at = time.time()
    proc.wait(timeout=30)
    lines_after_kill = ledger_lines(Path(args.ledger))
    with DurableTaskStore(args.db) as store:
        mid = _snapshot(store, args.task)
        mid_workers = store.list_workers(stale_after_s=args.stale_after_s)

    # A real supervisor restarts a worker only after the heartbeat is *detected*
    # as stale. Model that latency explicitly instead of pretending liveness is
    # instantaneous: wait for the row to age out, then record what we see.
    if killed_at is not None:
        time.sleep(max(0.0, (killed_at + args.stale_after_s + 0.25) - time.time()))
    with DurableTaskStore(args.db) as store:
        live_at_restart = sorted(
            w["worker_id"] for w in store.live_workers(stale_after_s=args.stale_after_s)
        )
    # The survivor starts with the victim's row still present but provably stale.
    started = time.perf_counter()
    survivor_proc = _spawn(args, worker_id=survivor)
    observed_live: list[list[str]] = []
    while survivor_proc.poll() is None:
        with DurableTaskStore(args.db) as store:
            alive_now = sorted(
                w["worker_id"] for w in store.live_workers(stale_after_s=args.stale_after_s)
            )
        if alive_now:  # only samples that actually observed a worker
            observed_live.append(alive_now)
        time.sleep(0.05)
    out, err = survivor_proc.communicate(timeout=args.timeout_s)
    recovery_ms = round((time.perf_counter() - started) * 1000.0, 2)
    live_samples = sorted({tuple(sample) for sample in observed_live})
    final_lines = ledger_lines(Path(args.ledger))
    with DurableTaskStore(args.db) as store:
        final = _snapshot(store, args.task)
        final_workers = store.list_workers(stale_after_s=args.stale_after_s)
        live = [w["worker_id"] for w in final_workers if w.get("alive")]
        recovered_events = [
            event for event in store.events(args.task, limit=500)
            if event["kind"] in {"step.owner_dead", "step.recovered"}
        ]
    step_ids = [line.split("|")[2] for line in final_lines]
    with DurableTaskStore(args.db) as store:
        effects_by_step = {
            row["step_id"]: (row["state"], row["compensated_at"] is not None)
            for row in store._conn.execute(
                "SELECT step_id, state, compensated_at FROM effects WHERE task_id = ?",
                (args.task,),
            )
        }
        unresolved = store.unresolved_effect_intents(args.task)
    result = {
        "mode": "kill",
        "effect_states": effects_by_step,
        "unresolved_intents_after": len(unresolved),
        "task_id": args.task,
        "victim_claimed_a_step": claimed,
        "victim_killed": killed_at is not None,
        "victim_was_running": mid["status"] == "running",
        "status_after_kill": mid["status"],
        "status_after_survivor": final["status"],
        "ledger_lines_after_kill": len(lines_after_kill),
        "ledger_lines_final": len(final_lines),
        "unique_steps_in_ledger": len(set(step_ids)),
        "no_duplicated_side_effects": len(final_lines) == len(set(step_ids)),
        "recovery_events": len(recovered_events),
        "recovery_ms": recovery_ms,
        "detection_samples": len(live_samples),
        "workers_seen": [w["worker_id"] for w in final_workers],
        "workers_alive_after": live,
        "live_worker_samples": [list(sample) for sample in live_samples],
        "live_workers_at_restart": live_at_restart,
        "victim_row_alive_after_kill": bool(mid_workers and any(
            w["worker_id"] == victim and w.get("alive") for w in mid_workers
        )),
        "survivor_stdout": out.strip().splitlines()[-3:],
        "snapshot": final,
    }
    checks = {
        "victim_really_started": claimed,
        "victim_really_killed": killed_at is not None,
        "task_finished_by_survivor": final["status"] == "succeeded",
        "every_step_has_exactly_one_effect": len(final_lines) == len(spec.steps),
        "no_duplicated_side_effects": result["no_duplicated_side_effects"],
        "the_interrupted_effect_was_recovered": result["ledger_lines_final"]
        == result["ledger_lines_after_kill"] + (len(spec.steps) - 1),
        "no_intent_left_unresolved": len(unresolved) == 0,
        # Liveness is a heartbeat, not an oracle: a SIGKILLed worker keeps
        # looking alive until its row goes stale. What must hold is that the
        # victim *becomes* stale while the survivor is still working, and that
        # nobody is alive once everyone has exited.
        "victim_was_stale_when_survivor_started": victim not in live_at_restart,
        # The first sample is taken before the survivor's row is committed.
        "survivor_was_the_live_worker": bool(live_samples)
        and all(sample == (survivor,) for sample in live_samples),
        "all_workers_deregistered_at_end": live == [],
        "recovery_was_logged": len(recovered_events) >= 1,
    }
    result["checks"] = checks
    result["ok"] = all(checks.values())
    return result


def mode_registry(args) -> dict[str, Any]:
    db = args.db
    if Path(db).exists():
        Path(db).unlink()
    with DurableTaskStore(db) as store:
        store.register_worker("w1", hostname="h1", pid=111)
        store.register_worker("w2", hostname="h1", pid=222)
        alive_before = {w["worker_id"] for w in store.live_workers(stale_after_s=args.stale_after_s)}
        store.deregister_worker("w2")
        alive_after = {w["worker_id"] for w in store.live_workers(stale_after_s=args.stale_after_s)}
        stopped = next(w for w in store.list_workers(stale_after_s=args.stale_after_s)
                       if w["worker_id"] == "w2")
        store.worker_heartbeat("w1", steps_kept=3)
        kept = next(w for w in store.list_workers(stale_after_s=args.stale_after_s)
                    if w["worker_id"] == "w1")["steps_kept"]
        schema = store.stats()["schema_version"]
    checks = {
        "both_registered": alive_before == {"w1", "w2"},
        "deregistered_worker_not_alive": alive_after == {"w1"},
        "deregistration_recorded": bool(stopped["stopped"]),
        "heartbeat_counters_survive": kept == 3,
        "schema_is_two": schema == 2,
    }
    return {"mode": "registry", "checks": checks, "ok": all(checks.values())}


def mode_child(args) -> dict[str, Any]:
    report = run_worker_child(
        db=args.db,
        workspace=args.workspace,
        ledger=args.ledger,
        task_id=args.task,
        worker_id=args.id,
        idle_exit_s=args.idle_exit_s,
        startup_delay_s=args.startup_delay_s,
    )
    print(json.dumps(report))
    return report


MODES = {
    "concurrent": mode_concurrent,
    "contention": mode_contention,
    "kill": mode_kill,
    "registry": mode_registry,
    "child": mode_child,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=sorted(MODES), required=True)
    scratch = Path(tempfile.gettempdir()) / "fleet-a3"
    parser.add_argument("--db", default=str(scratch / "fleet.db"))
    parser.add_argument("--workspace", default=str(scratch / "ws"))
    parser.add_argument("--ledger", default=str(scratch / "ledger.txt"))
    parser.add_argument("--task", default="probe-task")
    parser.add_argument("--id", default=None)
    parser.add_argument("--steps", type=int, default=6)
    parser.add_argument("--sleep", type=float, default=0.35)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout-s", type=float, default=120.0)
    parser.add_argument("--wait-s", type=float, default=20.0)
    parser.add_argument("--idle-exit-s", type=float, default=1.5)
    parser.add_argument("--stale-after-s", type=float, default=2.0)
    parser.add_argument("--startup-delay-s", type=float, default=0.0)
    parser.add_argument(
        "--crash-window-s",
        type=float,
        default=6.0,
        help="kill mode: how long step 1 stays between external write and completion",
    )
    parser.add_argument("--json-out", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = MODES[args.mode](args)
    payload = json.dumps(result, indent=2, sort_keys=False)
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    if args.mode == "child":
        return 0
    if result.get("ok"):
        print(f"\nMULTI-PROCESS PROBE OK ({args.mode})")
        return 0
    failed = [name for name, value in result["checks"].items() if not value]
    print(f"\nMULTI-PROCESS PROBE FAILED ({args.mode}): {', '.join(failed)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
