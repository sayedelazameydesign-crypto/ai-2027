#!/usr/bin/env python3
"""A3 — one database, many processes.

The claim under test is not "it compiles". It is:

* a worker's existence, health and death are **durable facts in the database**,
  not in-process state;
* two real OS processes can share one SQLite file without duplicating a side
  effect;
* `SIGKILL`ing a worker mid-effect does not lose the task, does not repeat the
  external effect, and leaves exactly one live worker;
* a wave never claims more work than the agent's bulkhead can actually run.

The subprocess tests are slow (a few seconds each) but they are the whole point
of A3, so they run by default rather than behind a flag. Set
``FLEET_SKIP_SUBPROCESS=1`` to skip them on a constrained box.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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
    TaskStatus,
)
from fleet2027.store import SCHEMA_VERSION  # noqa: E402

PROBE = PACKAGE_ROOT / "scripts" / "multi_process_probe.py"
SKIP_SUBPROCESS = os.environ.get("FLEET_SKIP_SUBPROCESS") == "1"


def _probe(*args: str, timeout: float = 180.0) -> tuple[int, dict]:
    """Run the probe as a real subprocess and return (exit code, last JSON)."""
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, str(PROBE), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    payload: dict = {}
    start = completed.stdout.find("{")
    if start != -1:
        try:
            # The probe ends with a human-readable verdict; decode just the JSON
            # document and ignore the trailer.
            payload, _ = json.JSONDecoder().raw_decode(completed.stdout[start:])
        except json.JSONDecodeError:  # pragma: no cover - probe bug
            payload = {}
    return completed.returncode, payload


class WorkerRegistryTests(unittest.TestCase):
    """The registry is durable state, so every assertion survives a reopen."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / "fleet.db")
        self.store = DurableTaskStore(self.db)
        self.addCleanup(self.store.close)

    def test_schema_is_at_least_three(self) -> None:
        self.assertGreaterEqual(self.store.stats()["schema_version"], 3)
        self.assertGreaterEqual(SCHEMA_VERSION, 3)

    def test_register_is_an_upsert_not_a_duplicate(self) -> None:
        self.store.register_worker("w1", hostname="host-a", pid=11)
        self.store.register_worker("w1", hostname="host-a", pid=11)
        rows = self.store.list_workers(stale_after_s=60)
        self.assertEqual([row["worker_id"] for row in rows], ["w1"])

    def test_heartbeat_keeps_a_worker_alive_past_the_stale_window(self) -> None:
        now = 1_000.0
        self.store.register_worker("w1", now_ts=now)
        self.assertEqual(
            [row["worker_id"] for row in self.store.live_workers(stale_after_s=10, now_ts=now + 5)],
            ["w1"],
        )
        self.assertEqual(self.store.live_workers(stale_after_s=10, now_ts=now + 30), ())
        self.store.worker_heartbeat("w1", now_ts=now + 30)
        self.assertEqual(
            [row["worker_id"] for row in self.store.live_workers(stale_after_s=10, now_ts=now + 35)],
            ["w1"],
        )

    def test_deregistration_is_terminal_even_if_the_heartbeat_looks_fresh(self) -> None:
        self.store.register_worker("w1", now_ts=1_000.0)
        self.store.deregister_worker("w1")
        rows = self.store.list_workers(stale_after_s=10, now_ts=1_000.0)
        self.assertFalse(rows[0]["alive"])
        self.assertTrue(rows[0]["stopped"])

    def test_counters_are_recorded_per_worker(self) -> None:
        self.store.register_worker("w1")
        self.store.worker_heartbeat("w1", steps_kept=4)
        row = self.store.list_workers(stale_after_s=60)[0]
        self.assertEqual(row["steps_kept"], 4)

    def test_events_are_emitted_for_registration_and_stop(self) -> None:
        self.store.register_worker("w1", now_ts=1_000.0)
        self.store.deregister_worker("w1")
        kinds = {
            row["kind"]
            for row in self.store._conn.execute(
                "SELECT kind FROM audit_log WHERE kind LIKE 'worker.%'"
            )
        }
        self.assertEqual(kinds, {"worker.registered", "worker.stopped"})


class ClaimCapacityTests(unittest.TestCase):
    """A wave must never claim more than the agent can run right now."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = DurableTaskStore(str(Path(self.temp.name) / "fleet.db"))
        self.addCleanup(self.store.close)

    def _submit(self, steps: int, agent: str = "serial") -> None:
        plan = tuple(
            PlanStep(
                step_id=f"s{index}",
                agent=agent,
                action="x",
                verification=(EvidenceKind.OUTPUT_NON_EMPTY.value,),
            )
            for index in range(1, steps + 1)
        )
        self.store.submit(
            TaskSpec(task_id="t-cap", goal="capacity", steps=plan), worker="test"
        )

    def test_capacity_zero_claims_nothing(self) -> None:
        self._submit(3)
        claimed = self.store.claim_ready_steps(
            "t-cap", worker="w1", lease_seconds=30, capacities={"serial": 0}
        )
        self.assertEqual(claimed, ())

    def test_capacity_one_claims_exactly_one_and_keeps_attempts(self) -> None:
        self._submit(4)
        claimed = self.store.claim_ready_steps(
            "t-cap", worker="w1", lease_seconds=30, capacities={"serial": 1}
        )
        self.assertEqual([step.step_id for step in claimed], ["s1"])
        snapshot = self.store.get_task("t-cap")
        self.assertEqual(snapshot.steps[0].attempts, 1)
        self.assertEqual([step.attempts for step in snapshot.steps[1:]], [0, 0, 0])
        self.assertEqual(
            {step.status for step in snapshot.steps[1:]},
            {snapshot.steps[1].status.__class__.READY},
        )

    def test_unknown_agent_capacity_never_blocks_work(self) -> None:
        self._submit(2)
        claimed = self.store.claim_ready_steps(
            "t-cap", worker="w1", lease_seconds=30, capacities={"someone-else": 1}
        )
        self.assertEqual(len(claimed), 2)

    def test_claiming_updates_the_worker_row(self) -> None:
        self.store.register_worker("w1")
        self._submit(2)
        self.store.claim_ready_steps(
            "t-cap", worker="w1", lease_seconds=30, capacities={"serial": 5}
        )
        row = self.store.list_workers(stale_after_s=60)[0]
        self.assertEqual(row["steps_claimed"], 2)
        self.assertEqual(row["last_task_id"], "t-cap")


class EffectIntentTests(unittest.TestCase):
    """Write-ahead effects: an unknown outcome must never look like a no-op."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = DurableTaskStore(str(Path(self.temp.name) / "fleet.db"))
        self.addCleanup(self.store.close)

    def test_intent_then_completion_is_one_row_with_a_state(self) -> None:
        key = self.store.idempotency_key("t1", "s1")
        self.store.declare_effect_intent(key, task_id="t1", step_id="s1", worker="w1")
        self.assertEqual(self.store.effect(key)["state"], "intent")
        self.store.complete_effect(key, task_id="t1", step_id="s1", summary="charged")
        row = self.store.effect(key)
        self.assertEqual(row["state"], "completed")
        self.assertIsNotNone(row["completed_at"])
        self.assertEqual(self.store.unresolved_effect_intents("t1"), ())

    def test_old_style_record_effect_still_reads_as_completed(self) -> None:
        key = self.store.idempotency_key("t1", "s1")
        self.assertTrue(
            self.store.record_effect(key, task_id="t1", step_id="s1", summary="legacy")
        )
        self.assertEqual(self.store.effect(key)["state"], "completed")

    def test_reconcile_compensates_unknown_outcomes_and_audits_it(self) -> None:
        key = self.store.idempotency_key("t1", "s1")
        self.store.declare_effect_intent(key, task_id="t1", step_id="s1", worker="ghost")
        reconciled = self.store.reconcile_effects("t1")
        self.assertEqual([item["outcome"] for item in reconciled], ["compensated"])
        row = self.store.effect(key)
        self.assertEqual(row["state"], "compensated")
        self.assertIsNotNone(row["compensated_at"])
        self.assertEqual(self.store.unresolved_effect_intents("t1"), ())
        kinds = {
            r["kind"] for r in self.store._conn.execute(
                "SELECT kind FROM audit_log WHERE task_id = 't1'"
            )
        }
        self.assertIn("effect.reconciled", kinds)

    def test_unresolved_intents_are_reported_not_hidden(self) -> None:
        key = self.store.idempotency_key("t1", "s1")
        self.store.declare_effect_intent(key, task_id="t1", step_id="s1", worker="ghost")
        pending = self.store.unresolved_effect_intents("t1")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["step_id"], "s1")


class DeadWorkerRequeueTests(unittest.TestCase):
    """Lease-based recovery and worker-based recovery must not fight."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = DurableTaskStore(str(Path(self.temp.name) / "fleet.db"))
        self.addCleanup(self.store.close)
        plan = tuple(
            PlanStep(
                step_id=f"s{index}",
                agent="serial",
                action="x",
                verification=(EvidenceKind.OUTPUT_NON_EMPTY.value,),
            )
            for index in range(1, 4)
        )
        self.store.submit(TaskSpec(task_id="t-dead", goal="g", steps=plan), worker="test")

    def test_a_dead_owner_is_requeued_while_a_live_one_is_left_alone(self) -> None:
        now = 1_000.0
        self.store.register_worker("ghost", now_ts=now)
        self.store.register_worker("alive", now_ts=now)
        self.store.claim_ready_steps(
            "t-dead", worker="ghost", lease_seconds=3_600, now_ts=now,
            capacities={"serial": 2},
        )
        self.store.claim_ready_steps(
            "t-dead", worker="alive", lease_seconds=3_600, now_ts=now,
            capacities={"serial": 2},
        )
        # ``ghost`` stops heartbeating; ``alive`` keeps going. The claim above
        # left a long lease on both, so only the worker registry can tell them apart.
        self.store.worker_heartbeat("alive", now_ts=now + 500)
        recovered = self.store.requeue_steps_of_dead_workers(
            stale_after_s=60, now_ts=now + 500
        )
        self.assertTrue(recovered)
        self.assertEqual({item["previous_owner"] for item in recovered}, {"ghost"})
        owners = {
            row["lease_owner"]
            for row in self.store._conn.execute(
                "SELECT lease_owner FROM steps WHERE task_id = 't-dead' AND status = 'running'"
            )
        }
        self.assertEqual(owners, {"alive"})

    def test_an_owner_that_never_registered_falls_back_to_its_own_heartbeat(self) -> None:
        now = 1_000.0
        self.store.claim_ready_steps(
            "t-dead", worker="unregistered", lease_seconds=3_600, now_ts=now,
            capacities={"serial": 1},
        )
        fresh = self.store.requeue_steps_of_dead_workers(stale_after_s=60, now_ts=now + 5)
        self.assertEqual(fresh, ())
        stale = self.store.requeue_steps_of_dead_workers(stale_after_s=60, now_ts=now + 500)
        self.assertEqual({item["previous_owner"] for item in stale}, {"unregistered"})

    def test_recovery_is_recorded_in_the_audit_chain(self) -> None:
        now = 1_000.0
        self.store.register_worker("ghost", now_ts=now)
        self.store.claim_ready_steps(
            "t-dead", worker="ghost", lease_seconds=3_600, now_ts=now,
            capacities={"serial": 2},
        )
        self.store.requeue_steps_of_dead_workers(stale_after_s=60, now_ts=now + 500)
        kinds = {
            row["kind"] for row in self.store._conn.execute(
                "SELECT kind FROM audit_log WHERE task_id = 't-dead'"
            )
        }
        self.assertIn("step.recovered_dead_worker", kinds)
        ok, rows, _ = self.store.verify_audit_chain()
        self.assertTrue(ok)
        self.assertGreater(rows, 0)


class RunnerOnceTests(unittest.TestCase):
    """``run_once`` is the cross-process unit of work; it must be honest."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "ws"
        self.workspace.mkdir()
        self.store = DurableTaskStore(str(self.root / "fleet.db"))
        self.addCleanup(self.store.close)

    def _runner(self, *, serial: bool) -> FleetRunner:
        calls: list[str] = []

        def handler(ctx):
            calls.append(ctx.step_id)
            target = ctx.workspace_root / f"{ctx.step_id}.txt"
            target.write_text(f"ran {ctx.step_id}\n", encoding="utf-8")
            return StepResult(ok=True, summary="wrote", output=f"wrote {ctx.step_id}")

        registry = AgentRegistry()
        registry.register(
            AgentSpec(
                name="writer",
                role="writes one file",
                capabilities=("write",),
                produces=("text",),
                cost_tier=CostTier.LOCAL,
                max_parallel=1 if serial else 4,
                requires_evidence=(EvidenceKind.OUTPUT_NON_EMPTY.value,),
            ),
            handler,
        )
        runner = FleetRunner(
            store=self.store,
            registry=registry,
            workspace_root=self.workspace,
            config=RunnerConfig(max_worker_threads=4),
            worker_id="w-test",
        )
        runner.calls = calls  # type: ignore[attr-defined]
        return runner

    def _submit(self, steps: int) -> None:
        plan = tuple(
            PlanStep(
                step_id=f"s{index}",
                agent="writer",
                action="write",
                verification=(EvidenceKind.OUTPUT_NON_EMPTY.value,),
            )
            for index in range(1, steps + 1)
        )
        self.store.submit(TaskSpec(task_id="t1", goal="g", steps=plan), worker="test")

    def test_serial_agent_runs_one_step_per_wave_without_burning_attempts(self) -> None:
        self._submit(3)
        runner = self._runner(serial=True)
        self.assertEqual(runner.run_once("t1"), 1)
        snapshot = self.store.get_task("t1")
        self.assertEqual([step.attempts for step in snapshot.steps], [1, 0, 0])
        self.assertEqual(runner.steps_kept, 1)

    def test_parallel_agent_runs_the_whole_wave(self) -> None:
        self._submit(3)
        runner = self._runner(serial=False)
        self.assertEqual(runner.run_once("t1"), 3)

    def test_run_once_returns_zero_on_a_finished_task(self) -> None:
        self._submit(1)
        runner = self._runner(serial=False)
        runner.run_once("t1")
        self.assertEqual(self.store.get_task("t1").status, TaskStatus.SUCCEEDED)
        self.assertEqual(runner.run_once("t1"), 0)

    def test_runnable_now_distinguishes_waiting_from_done(self) -> None:
        self._submit(1)
        self.assertTrue(self.store.runnable_now("t1"))
        runner = self._runner(serial=False)
        runner.run_once("t1")
        self.assertFalse(self.store.runnable_now("t1"))
        self.assertEqual(self.store.active_task_ids(), ())


class GateRegistrationTests(unittest.TestCase):
    """The gate list is the contract the CI job and the docs describe."""

    def test_multi_process_is_a_default_gate(self) -> None:
        from gate import GATES

        names = [gate.__name__ for gate in GATES]
        self.assertIn("gate_multi_process", names)
        # The workflow and README quote this count; a silent change is a lie.
        self.assertEqual(len(GATES), 11)

    def test_opt_in_gates_are_never_in_the_default_list(self) -> None:
        from gate import GATES, OPT_IN_GATES

        default_names = {gate.__name__ for gate in GATES}
        self.assertFalse(default_names & {gate.__name__ for gate in OPT_IN_GATES.values()})
        self.assertIn("llm-live-ollama", OPT_IN_GATES)


@unittest.skipIf(SKIP_SUBPROCESS, "FLEET_SKIP_SUBPROCESS=1")
class MultiProcessTests(unittest.TestCase):
    """Real subprocesses on one SQLite file — the actual A3 claim."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _paths(self) -> list[str]:
        return [
            "--db", str(self.root / "fleet.db"),
            "--workspace", str(self.root / "ws"),
            "--ledger", str(self.root / "ledger.txt"),
        ]

    def test_two_workers_split_one_task_without_duplicating_effects(self) -> None:
        code, payload = _probe(
            "--mode", "concurrent", "--workers", "2", "--steps", "6",
            "--sleep", "0.3", *self._paths(),
        )
        self.assertEqual(code, 0, json.dumps(payload.get("checks"), indent=1))
        self.assertTrue(payload["checks"]["two_os_processes_did_work"])
        self.assertEqual(payload["ledger_lines"], 6)
        self.assertEqual(payload["duplicated_step_ids"], [])
        self.assertEqual(payload["snapshot"]["status"], "succeeded")

    def test_a_serial_agent_never_oversubscribes_its_bulkhead(self) -> None:
        code, payload = _probe(
            "--mode", "contention", "--workers", "2", "--steps", "5",
            "--sleep", "0.05", *self._paths(),
        )
        self.assertEqual(code, 0, json.dumps(payload.get("checks"), indent=1))
        self.assertTrue(payload["checks"]["no_bulkhead_saturation"])
        self.assertTrue(payload["checks"]["every_step_first_attempt"])
        self.assertEqual(payload["snapshot"]["status"], "succeeded")

    def test_a_sigkilled_worker_does_not_repeat_its_side_effect(self) -> None:
        code, payload = _probe(
            "--mode", "kill", "--steps", "4", "--sleep", "0.05",
            "--crash-window-s", "5", *self._paths(),
        )
        self.assertEqual(code, 0, json.dumps(payload.get("checks"), indent=1))
        checks = payload["checks"]
        self.assertTrue(checks["victim_really_killed"])
        self.assertTrue(checks["task_finished_by_survivor"])
        self.assertTrue(checks["the_interrupted_effect_was_recovered"])
        self.assertTrue(checks["no_duplicated_side_effects"])
        self.assertTrue(checks["no_intent_left_unresolved"])
        self.assertEqual(payload["ledger_lines_final"], 4)

    def test_the_worker_cli_exits_zero_on_a_finished_task(self) -> None:
        cli = PACKAGE_ROOT / "cli.py"
        args = [
            sys.executable, str(cli), "demo",
            "--db", str(self.root / "fleet.db"),
            "--workspace", str(self.root / "ws"),
            "--task", "cli-task",
        ]
        demo = subprocess.run(  # noqa: S603 - fixed argv, no shell
            args, capture_output=True, text=True, timeout=180
        )
        self.assertEqual(demo.returncode, 0, demo.stderr[-2000:])
        worker = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [
                sys.executable, str(cli), "worker",
                "--db", str(self.root / "fleet.db"),
                "--workspace", str(self.root / "ws"),
                "--id", "cli-worker",
                "--task", "cli-task",
                "--idle-exit-s", "0.5",
                "--expect-complete",
            ],
            capture_output=True, text=True, timeout=180,
        )
        self.assertEqual(worker.returncode, 0, worker.stdout[-2000:] + worker.stderr[-2000:])
        self.assertIn("cli-task", worker.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
