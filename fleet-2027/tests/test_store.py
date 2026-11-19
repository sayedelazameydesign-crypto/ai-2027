"""Durability tests against a real SQLite file: claims, retries, DLQ, audit.

Run: python3 -m pytest fleet-2027/tests -q
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from fleet2027 import (  # noqa: E402
    DurableTaskStore,
    PlanStep,
    StepResult,
    StepStatus,
    StoreError,
    TaskConflictError,
    TaskSpec,
    TaskStatus,
    VerificationReport,
)
from fleet2027.contracts import ContractError  # noqa: E402


def _plan(task_id: str = "t1", *, steps: tuple[PlanStep, ...] | None = None) -> TaskSpec:
    if steps is None:
        steps = (
            PlanStep(step_id="a", agent="w", action="one"),
            PlanStep(step_id="b", agent="w", action="two", depends_on=("a",)),
        )
    return TaskSpec(task_id=task_id, goal="goal", steps=steps)


def _ok(summary: str = "done") -> StepResult:
    return StepResult(ok=True, summary=summary, output="payload")


def _verified() -> VerificationReport:
    return VerificationReport(ok=True, checks={"output_non_empty": True})


class StoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "fleet.db")
        self.store = DurableTaskStore(self.db)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()


class SubmissionTests(StoreTestCase):
    def test_submit_is_idempotent_for_identical_specs(self) -> None:
        self.assertTrue(self.store.submit(_plan()))
        self.assertFalse(self.store.submit(_plan()))
        self.assertEqual(self.store.stats()["tasks"], {"queued": 1})

    def test_submit_refuses_a_different_spec_under_the_same_id(self) -> None:
        self.store.submit(_plan())
        with self.assertRaises(TaskConflictError):
            self.store.submit(_plan(steps=(PlanStep(step_id="z", agent="w", action="x"),)))

    def test_initial_dependency_states(self) -> None:
        self.store.submit(_plan())
        snapshot = self.store.get_task("t1")
        states = {step.step_id: step.status for step in snapshot.steps}
        self.assertEqual(states["a"], StepStatus.READY)
        self.assertEqual(states["b"], StepStatus.PENDING)

    def test_no_attempt_row_exists_before_the_first_claim(self) -> None:
        self.store.submit(_plan())
        count = self.store._conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
        self.assertEqual(count, 0)


class ClaimTests(StoreTestCase):
    def test_claim_advances_the_attempt_counter_once(self) -> None:
        self.store.submit(_plan())
        first = self.store.claim_ready_steps("t1", worker="w1", lease_seconds=30)
        self.assertEqual([s.step_id for s in first], ["a"])
        self.assertEqual(first[0].attempts, 1)
        again = self.store.claim_ready_steps("t1", worker="w2", lease_seconds=30)
        self.assertEqual(again, ())  # already leased by w1

    def test_two_concurrent_workers_cannot_both_claim_a_step(self) -> None:
        self.store.submit(_plan())
        results: list[tuple[str, ...]] = []
        barrier = threading.Barrier(2)

        def contender(worker: str) -> None:
            barrier.wait()
            claimed = self.store.claim_ready_steps("t1", worker=worker, lease_seconds=30)
            results.append(tuple(s.step_id for s in claimed))

        threads = [threading.Thread(target=contender, args=(f"w{i}",)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        winners = [r for r in results if r]
        self.assertEqual(len(winners), 1, f"exactly one worker may win, saw {results}")

    def test_dependencies_gate_the_next_wave(self) -> None:
        self.store.submit(_plan())
        claimed = self.store.claim_ready_steps("t1", worker="w1", lease_seconds=30)
        self.store.record_success(
            "t1", claimed[0].step_id, worker="w1", result=_ok(), verification=_verified()
        )
        second = self.store.claim_ready_steps("t1", worker="w1", lease_seconds=30)
        self.assertEqual([s.step_id for s in second], ["b"])

    def test_a_lost_lease_makes_the_commit_refused(self) -> None:
        self.store.submit(_plan())
        self.store.claim_ready_steps("t1", worker="w1", lease_seconds=30)
        committed = self.store.record_success(
            "t1", "a", worker="impostor", result=_ok(), verification=_verified()
        )
        self.assertFalse(committed)
        self.assertEqual(self.store.get_task("t1").steps[0].status, StepStatus.RUNNING)


class RetryAndDeadLetterTests(StoreTestCase):
    def test_retry_backoff_is_respected(self) -> None:
        self.store.submit(_plan(steps=(PlanStep(step_id="a", agent="w", action="x", max_attempts=3),)))
        self.store.claim_ready_steps("t1", worker="w1", lease_seconds=30)
        self.store.record_failure(
            "t1", "a", worker="w1", error="transient", will_retry=True, backoff_s=60
        )
        self.assertEqual(self.store.get_task("t1").steps[0].status, StepStatus.RETRYING)
        early = self.store.claim_ready_steps("t1", worker="w1", lease_seconds=30)
        self.assertEqual(early, (), "a step inside its backoff must not be re-claimed")
        self.assertIsNotNone(self.store.next_attempt_delay("t1"))
        late = self.store.claim_ready_steps("t1", worker="w1", lease_seconds=30, now_ts=time.time() + 61)
        self.assertEqual([s.step_id for s in late], ["a"])
        self.assertEqual(late[0].attempts, 2)

    def test_exhausted_attempts_land_in_the_dead_letter_queue(self) -> None:
        self.store.submit(_plan(steps=(PlanStep(step_id="a", agent="w", action="x", max_attempts=2),)))
        for attempt in range(2):
            self.store.claim_ready_steps("t1", worker="w1", lease_seconds=30, now_ts=time.time() + attempt * 90)
            self.store.record_failure(
                "t1", "a", worker="w1", error=f"boom {attempt}", will_retry=True, backoff_s=0
            )
        snapshot = self.store.get_task("t1")
        self.assertEqual(snapshot.steps[0].status, StepStatus.DEAD_LETTER)
        self.assertEqual(snapshot.status, TaskStatus.DEAD_LETTER)
        letters = self.store.list_dead_letters()
        self.assertEqual(len(letters), 1)
        self.assertIn("boom", letters[0]["reason"])

    def test_requeue_resets_the_attempt_budget(self) -> None:
        self.store.submit(_plan(steps=(PlanStep(step_id="a", agent="w", action="x", max_attempts=1),)))
        self.store.claim_ready_steps("t1", worker="w1", lease_seconds=30)
        self.store.record_failure("t1", "a", worker="w1", error="boom", will_retry=False)
        self.assertEqual(self.store.get_task("t1").status, TaskStatus.DEAD_LETTER)
        self.store.requeue_dead_letter("t1", "a")
        snapshot = self.store.get_task("t1")
        self.assertEqual(snapshot.steps[0].status, StepStatus.RETRYING)
        self.assertEqual(snapshot.steps[0].attempts, 0)
        self.assertEqual(self.store.list_dead_letters(), ())

    def test_non_critical_failure_degrades_instead_of_failing(self) -> None:
        steps = (
            PlanStep(step_id="a", agent="w", action="x"),
            PlanStep(
                step_id="opt", agent="w", action="optional", depends_on=("a",), critical=False
            ),
        )
        self.store.submit(_plan(steps=steps))
        claimed = self.store.claim_ready_steps("t1", worker="w1", lease_seconds=30)
        self.store.record_success(
            "t1", claimed[0].step_id, worker="w1", result=_ok(), verification=_verified()
        )
        self.store.claim_ready_steps("t1", worker="w1", lease_seconds=30)
        self.store.record_failure("t1", "opt", worker="w1", error="nope", will_retry=False)
        snapshot = self.store.get_task("t1")
        self.assertEqual(snapshot.status, TaskStatus.SUCCEEDED)
        self.assertTrue(snapshot.degraded, "a lost non-critical step must be recorded")

    def test_critical_failure_fails_the_task_and_blocks_dependents(self) -> None:
        self.store.submit(_plan())
        self.store.claim_ready_steps("t1", worker="w1", lease_seconds=30)
        self.store.record_failure("t1", "a", worker="w1", error="boom", will_retry=False)
        snapshot = self.store.get_task("t1")
        self.assertEqual(snapshot.status, TaskStatus.FAILED)
        self.assertEqual(snapshot.steps[1].status, StepStatus.FAILED)
        self.assertIn("blocked by a failed dependency", snapshot.steps[1].error or "")


class EffectTests(StoreTestCase):
    def test_an_effect_is_recorded_exactly_once(self) -> None:
        self.store.submit(_plan())
        self.assertTrue(
            self.store.record_effect("k1", task_id="t1", step_id="a", summary="charge once")
        )
        self.assertFalse(self.store.record_effect("k1", task_id="t1", step_id="a"))
        self.assertEqual(self.store.effect("k1")["replay_count"], 1)

    def test_a_duplicate_success_is_refused_by_the_unique_index(self) -> None:
        self.store.submit(_plan())
        self.store.claim_ready_steps("t1", worker="w1", lease_seconds=30)
        self.assertTrue(
            self.store.record_success(
                "t1", "a", worker="w1", result=_ok(), verification=_verified()
            )
        )
        # Re-claiming a succeeded step is impossible, so the ledger is the guard.
        key = self.store.idempotency_key("t1", "a")
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.transaction() as conn:
                conn.execute(
                    "INSERT INTO effects(idempotency_key, task_id, step_id, summary, recorded_at)"
                    " VALUES(?,?,?,?,?)",
                    (key, "t1", "a", "duplicate", "now"),
                )


class RecoveryTests(StoreTestCase):
    def test_expired_leases_are_requeued_with_the_attempt_recorded(self) -> None:
        self.store.submit(_plan())
        self.store.claim_ready_steps("t1", worker="w1", lease_seconds=1)
        self.assertEqual(self.store.recover_expired_leases(lease_seconds=3600), ())
        recovered = self.store.recover_expired_leases(
            lease_seconds=1, now_ts=time.time() + 5
        )
        self.assertEqual([r["step_id"] for r in recovered], ["a"])
        self.assertEqual(self.store.get_task("t1").steps[0].status, StepStatus.RETRYING)
        row = self.store._conn.execute(
            "SELECT outcome FROM attempts WHERE attempt_no = 1"
        ).fetchone()
        self.assertEqual(row["outcome"], "lost_lease")

    def test_a_fresh_lease_is_not_stolen(self) -> None:
        self.store.submit(_plan())
        self.store.claim_ready_steps("t1", worker="w1", lease_seconds=300)
        self.assertEqual(self.store.recover_expired_leases(lease_seconds=300), ())

    def test_forced_recovery_is_audited(self) -> None:
        self.store.submit(_plan())
        self.store.claim_ready_steps("t1", worker="dead-worker", lease_seconds=300)
        recovered = self.store.force_recover_running_steps(reason="SIGKILL test")
        self.assertEqual(len(recovered), 1)
        kinds = [
            row["kind"]
            for row in self.store._conn.execute("SELECT kind FROM audit_log ORDER BY seq")
        ]
        self.assertIn("recovery.forced", kinds)
        self.assertIn("step.recovered", kinds)


class CancelTests(StoreTestCase):
    def test_cancel_refuses_late_writes(self) -> None:
        self.store.submit(_plan())
        self.store.claim_ready_steps("t1", worker="w1", lease_seconds=30)
        snapshot = self.store.cancel_task("t1", reason="operator changed their mind")
        self.assertEqual(snapshot.status, TaskStatus.CANCELLED)
        self.assertFalse(
            self.store.record_success(
                "t1", "a", worker="w1", result=_ok(), verification=_verified()
            )
        )

    def test_cancel_of_a_finished_task_is_refused(self) -> None:
        self.store.submit(_plan(steps=(PlanStep(step_id="a", agent="w", action="x"),)))
        self.store.claim_ready_steps("t1", worker="w1", lease_seconds=30)
        self.store.record_success("t1", "a", worker="w1", result=_ok(), verification=_verified())
        with self.assertRaises(StoreError):
            self.store.cancel_task("t1")


class AuditTests(StoreTestCase):
    def test_the_chain_verifies_and_tampering_is_detected(self) -> None:
        self.store.submit(_plan())
        self.store.claim_ready_steps("t1", worker="w1", lease_seconds=30)
        ok, rows, problem = self.store.verify_audit_chain()
        self.assertTrue(ok, problem)
        self.assertGreaterEqual(rows, 2)
        self.store._conn.execute(
            "UPDATE audit_log SET payload_json = replace(payload_json, 'w1', 'w2') WHERE seq = 2"
        )
        ok, _, problem = self.store.verify_audit_chain()
        self.assertFalse(ok)
        self.assertIn("digest mismatch", problem or "")

    def test_integrity_report_states_its_limits(self) -> None:
        self.store.submit(_plan())
        report = self.store.verify_integrity()
        self.assertTrue(report["ok"])
        self.assertTrue(report["limitations"])
        self.assertFalse(report["running_steps_without_heartbeat"])

    def test_schema_version_is_recorded(self) -> None:
        self.assertEqual(self.store.stats()["schema_version"], 1)


class CheckpointTests(StoreTestCase):
    def test_checkpoint_digest_round_trips(self) -> None:
        self.store.submit(_plan())
        claimed = self.store.claim_ready_steps("t1", worker="w1", lease_seconds=30)
        self.store.record_success(
            "t1", claimed[0].step_id, worker="w1", result=_ok(), verification=_verified()
        )
        checkpoint = self.store.checkpoint("t1", note="after step a")
        latest = self.store.last_checkpoint("t1")
        self.assertIsNotNone(latest)
        self.assertEqual(latest["sha256"], checkpoint["sha256"])
        self.assertTrue(latest["integrity_ok"])
        self.assertEqual(latest["completed_steps"], 1)
        self.assertEqual(latest["total_steps"], 2)

    def test_resume_seed_export_import_preserves_completed_work(self) -> None:
        self.store.submit(_plan())
        claimed = self.store.claim_ready_steps("t1", worker="w1", lease_seconds=30)
        self.store.record_success(
            "t1", claimed[0].step_id, worker="w1", result=_ok(), verification=_verified()
        )
        seed = self.store.export_resume_seed("t1")
        with tempfile.TemporaryDirectory() as other:
            with DurableTaskStore(str(Path(other) / "new.db")) as fresh:
                spec = fresh.import_resume_seed(seed)
                self.assertEqual(spec.task_id, "t1")
                self.assertEqual(fresh.get_task("t1").steps[0].status, StepStatus.SUCCEEDED)
                self.assertEqual(fresh.get_task("t1").steps[1].status, StepStatus.READY)

    def test_a_tampered_seed_is_refused(self) -> None:
        self.store.submit(_plan())
        seed = self.store.export_resume_seed("t1")
        seed["task"]["goal"] = "something else entirely"
        with self.assertRaises(ContractError):
            self.store.import_resume_seed(seed)

    def test_terminal_tasks_cannot_be_exported(self) -> None:
        self.store.submit(_plan(steps=(PlanStep(step_id="a", agent="w", action="x"),)))
        self.store.claim_ready_steps("t1", worker="w1", lease_seconds=30)
        self.store.record_success("t1", "a", worker="w1", result=_ok(), verification=_verified())
        with self.assertRaises(StoreError):
            self.store.export_resume_seed("t1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
