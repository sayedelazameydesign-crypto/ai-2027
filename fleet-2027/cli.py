#!/usr/bin/env python3
"""Command-line surface for the Fleet-2027 durable runtime.

Every command prints what it actually did. There is no "done ✓" without a
state change behind it, and a non-zero exit code means the work is not done:

    0  the task reached ``succeeded`` (possibly degraded — stated explicitly)
    2  the task is terminal but not successful (failed / dead_letter / cancelled)
    3  the task is not finished yet (the caller should resume it later)
    1  a usage or integrity error

Usage
-----
    python3 fleet-2027/cli.py demo    --db /tmp/fleet.db --workspace /tmp/ws
    python3 fleet-2027/cli.py plan    --db /tmp/fleet.db --spec spec.json
    python3 fleet-2027/cli.py run     --db /tmp/fleet.db --task demo-1 [--resume]
    python3 fleet-2027/cli.py status  --db /tmp/fleet.db [--task demo-1] [--json]
    python3 fleet-2027/cli.py recover --db /tmp/fleet.db
    python3 fleet-2027/cli.py verify  --db /tmp/fleet.db
    python3 fleet-2027/cli.py dlq     --db /tmp/fleet.db [--requeue demo-1:step]
    python3 fleet-2027/cli.py export  --db /tmp/fleet.db --task demo-1 --out seed.json
    python3 fleet-2027/cli.py import  --db /tmp/fleet.db --seed seed.json
    python3 fleet-2027/cli.py cancel  --db /tmp/fleet.db --task demo-1 --reason "..."
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from fleet2027 import (  # noqa: E402
    AgentRegistry,
    DurableTaskStore,
    FleetRunner,
    RunnerConfig,
    TaskRunReport,
    TaskSpec,
    TaskStatus,
    build_reviewer,
    examples,  # noqa: E402
    summarize_snapshot,
    utc_now,
)

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_FAILED = 2
EXIT_INCOMPLETE = 3


def _demo_registry() -> AgentRegistry:
    return examples.build_demo_registry()


def _runner(store: DurableTaskStore, workspace: Path, *, quiet: bool = False) -> FleetRunner:
    registry = _demo_registry()

    def sink(event: dict) -> None:  # pragma: no cover - trivial printer
        if quiet:
            return
        print(f"  · {event['kind']}: {event['message']}", file=sys.stderr)

    reviewer = build_reviewer(registry, reviewer_agent="reviewer")
    return FleetRunner(
        store,
        registry,
        workspace_root=workspace,
        reviewer=reviewer,
        config=RunnerConfig(on_event=sink),
    )


def _exit_for(status: TaskStatus) -> int:
    if status is TaskStatus.SUCCEEDED:
        return EXIT_OK
    if status in {TaskStatus.FAILED, TaskStatus.DEAD_LETTER, TaskStatus.CANCELLED}:
        return EXIT_FAILED
    return EXIT_INCOMPLETE


def _print_task(store: DurableTaskStore, task_id: str) -> None:
    snapshot = store.get_task(task_id)
    print(summarize_snapshot(snapshot))
    for step in snapshot.steps:
        detail = step.summary or step.error or ""
        print(
            f"  {step.step_id:<16} {step.status.value:<12} "
            f"attempts={step.attempts}/{step.max_attempts} {detail[:80]}"
        )
    print(f"  spec_sha256={snapshot.spec_sha256[:16]}…  updated={snapshot.updated_at}")


def _print_report(report: TaskRunReport) -> None:
    print(examples.summarize_evidence(report.to_dict()))
    for step_id, verification in sorted(report.verifications.items()):
        verdict = "VERIFIED" if verification.ok else "REFUSED"
        print(f"  {step_id:<16} {verdict}  checks={len(verification.checks)}")
        if not verification.ok:
            for failure in verification.failures[:3]:
                print(f"    ✗ {failure}")


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def cmd_demo(args: argparse.Namespace) -> int:
    """Run the five-agent demo task end to end and report honestly."""
    workspace = Path(args.workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    task_id = args.task or f"demo-{utc_now().replace(':', '').replace('.', '')[:15]}"
    spec = examples.build_demo_task(task_id)
    with DurableTaskStore(args.db) as store:
        runner = _runner(store, workspace)
        print(f"fleet-2027 demo · db={args.db} · workspace={workspace}")
        print("agents:", ", ".join(_demo_registry().names))
        print(
            "note: these agents are deterministic local programs (real files, real "
            "compile + probe). No LLM is called in this demo."
        )
        report = runner.submit_and_run(spec)
        _print_report(report)
        _print_task(store, task_id)
        integrity = store.verify_integrity()
        print(f"integrity: ok={integrity['ok']} audit_rows={integrity['audit_rows']}")
        return _exit_for(report.status)


def cmd_plan(args: argparse.Namespace) -> int:
    """Persist a task specification from JSON without executing it."""
    raw = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    spec = TaskSpec.from_dict(raw)
    with DurableTaskStore(args.db) as store:
        created = store.submit(spec)
        print(
            f"{'created' if created else 'already present (identical spec)'}: "
            f"{spec.task_id} · {len(spec.steps)} step(s) · "
            f"spec_sha256={spec.spec_hash()[:16]}…"
        )
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    """Execute (or resume) a persisted task."""
    workspace = Path(args.workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    with DurableTaskStore(args.db) as store:
        runner = _runner(store, workspace)
        if args.resume:
            recovered = runner.recover()
            if recovered:
                for item in recovered:
                    print(
                        f"  requeued {item['task_id']}/{item['step_id']} "
                        f"(previous owner {item['previous_owner']})"
                    )
        report = runner.run(args.task)
        _print_report(report)
        _print_task(store, args.task)
        return _exit_for(report.status)


def cmd_recover(args: argparse.Namespace) -> int:
    """Requeue every expired lease; prints exactly what moved."""
    with DurableTaskStore(args.db) as store:
        runner = _runner(store, Path(args.workspace).resolve(), quiet=True)
        recovered = runner.recover()
        if not recovered:
            print("nothing to recover: no expired lease was found")
            return EXIT_OK
        for item in recovered:
            print(
                f"requeued {item['task_id']}/{item['step_id']} "
                f"(attempt {item['attempts']} recorded as lost)"
            )
        print(f"{len(recovered)} step(s) are runnable again; run `run --task <id>`")
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    """Print task state, step state and store counters."""
    with DurableTaskStore(args.db) as store:
        if args.task:
            if args.json:
                snapshot = store.get_task(args.task)
                print(
                    json.dumps(
                        {
                            "task_id": snapshot.task_id,
                            "status": snapshot.status.value,
                            "degraded": snapshot.degraded,
                            "counts": snapshot.counts(),
                            "steps": [
                                {
                                    "step_id": step.step_id,
                                    "status": step.status.value,
                                    "attempts": step.attempts,
                                    "summary": step.summary,
                                    "error": step.error,
                                }
                                for step in snapshot.steps
                            ],
                        },
                        indent=2,
                    )
                )
            else:
                _print_task(store, args.task)
            return EXIT_OK
        stats = store.stats()
        print(json.dumps(stats, indent=2, sort_keys=True))
        for snapshot in store.list_tasks():
            print(summarize_snapshot(snapshot))
    return EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    """Re-derive store integrity and print every step's verification verdict."""
    with DurableTaskStore(args.db) as store:
        integrity = store.verify_integrity()
        print(json.dumps(integrity, indent=2, sort_keys=True))
        tasks = store.list_tasks()
        unsigned = 0
        for snapshot in tasks:
            for step in snapshot.steps:
                if step.status.value != "succeeded":
                    continue
                result = store.step_result(snapshot.task_id, step.step_id)
                report = (result or {}).get("verification") or {}
                if not report.get("ok"):
                    unsigned += 1
                    print(
                        f"UNVERIFIED {snapshot.task_id}/{step.step_id}: "
                        f"{report.get('failures') or 'no verification record'}"
                    )
        print(
            f"integrity_ok={integrity['ok']} "
            f"unverified_succeeded_steps={unsigned} tasks={len(tasks)}"
        )
        return EXIT_OK if integrity["ok"] and unsigned == 0 else EXIT_FAILED


def cmd_dlq(args: argparse.Namespace) -> int:
    """List (and optionally requeue) dead-lettered steps."""
    with DurableTaskStore(args.db) as store:
        if args.requeue:
            task_id, _, step_id = args.requeue.partition(":")
            if not step_id:
                print("--requeue expects <task_id>:<step_id>", file=sys.stderr)
                return EXIT_USAGE
            snapshot = store.requeue_dead_letter(task_id, step_id)
            print(f"requeued {task_id}/{step_id} → task is {snapshot.status.value}")
            return EXIT_OK
        entries = store.list_dead_letters()
        if not entries:
            print("dead-letter queue is empty")
            return EXIT_OK
        for entry in entries:
            print(
                f"{entry['task_id']}/{entry['step_id']} · attempts={entry['attempts']} · "
                f"{entry['reason'][:100]}"
            )
    return EXIT_OK


def cmd_export(args: argparse.Namespace) -> int:
    """Write a hash-verified resume seed to a file."""
    with DurableTaskStore(args.db) as store:
        seed = store.export_resume_seed(args.task)
        Path(args.out).write_text(json.dumps(seed, indent=2), encoding="utf-8")
        print(f"seed written to {args.out} sha256={seed['sha256'][:16]}…")
    return EXIT_OK


def cmd_import(args: argparse.Namespace) -> int:
    """Verify and import a resume seed (refuses a tampered file)."""
    seed = json.loads(Path(args.seed).read_text(encoding="utf-8"))
    with DurableTaskStore(args.db) as store:
        spec = store.import_resume_seed(seed)
        print(f"imported {spec.task_id} · {len(spec.steps)} step(s) · completed work preserved")
    return EXIT_OK


def cmd_cancel(args: argparse.Namespace) -> int:
    """Cancel a live task; late writes from workers are then refused."""
    with DurableTaskStore(args.db) as store:
        snapshot = store.cancel_task(args.task, reason=args.reason)
        print(summarize_snapshot(snapshot))
    return EXIT_FAILED


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="fleet-2027",
        description="Durable, evidence-verified agent fleet runtime (stdlib only).",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default="fleet-2027/fleet.db", help="SQLite database path")
    common.add_argument(
        "--workspace", default=".", help="workspace root that artifacts must stay inside"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", parents=[common], help="run the deterministic demo fleet")
    demo.add_argument("--task", default=None, help="task id (defaults to a timestamp)")
    demo.set_defaults(func=cmd_demo)

    plan = sub.add_parser("plan", parents=[common], help="persist a task spec from JSON")
    plan.add_argument("--spec", required=True)
    plan.set_defaults(func=cmd_plan)

    run = sub.add_parser("run", parents=[common], help="execute or resume a task")
    run.add_argument("--task", required=True)
    run.add_argument("--resume", action="store_true", help="requeue expired leases first")
    run.set_defaults(func=cmd_run)

    recover = sub.add_parser("recover", parents=[common], help="requeue expired leases")
    recover.set_defaults(func=cmd_recover)

    status = sub.add_parser("status", parents=[common], help="print store/task state")
    status.add_argument("--task", default=None)
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    verify = sub.add_parser("verify", parents=[common], help="integrity + verification audit")
    verify.set_defaults(func=cmd_verify)

    dlq = sub.add_parser("dlq", parents=[common], help="list or requeue dead letters")
    dlq.add_argument("--requeue", default=None, metavar="TASK:STEP")
    dlq.set_defaults(func=cmd_dlq)

    export = sub.add_parser("export", parents=[common], help="export a resume seed")
    export.add_argument("--task", required=True)
    export.add_argument("--out", required=True)
    export.set_defaults(func=cmd_export)

    imp = sub.add_parser("import", parents=[common], help="import a resume seed")
    imp.add_argument("--seed", required=True)
    imp.set_defaults(func=cmd_import)

    cancel = sub.add_parser("cancel", parents=[common], help="cancel a live task")
    cancel.add_argument("--task", required=True)
    cancel.add_argument("--reason", default="cancelled by operator")
    cancel.set_defaults(func=cmd_cancel)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report, do not traceback
        print(f"fleet-2027: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
