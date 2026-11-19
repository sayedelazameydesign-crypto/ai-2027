"""Real crash test: a worker process is SIGKILLed mid-step, then resumed.

This is the test that turns "durable" from an adjective into a measurement. It
runs the out-of-process worker, kills it with ``SIGKILL`` (no cleanup, no
``finally``, no chance to release a lease), then asserts from the database:

1. the in-flight step is still ``running`` and its lease is detectable as stale,
2. the *finished* step is still ``succeeded`` — the crash did not roll it back,
3. a fresh process requeues exactly one step,
4. the resumed step's first attempt number is **2**, so the lost attempt is
   recorded rather than forgotten,
5. the side effect was applied **exactly once** (two log lines but only one
   ``fresh_effect: true``),
6. the audit chain still verifies.

POSIX only (it needs ``SIGKILL``); the test skips cleanly elsewhere.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from fleet2027 import DurableTaskStore, StepStatus, TaskStatus  # noqa: E402

WORKER = ROOT / "crash_worker.py"


@unittest.skipUnless(hasattr(signal, "SIGKILL"), "SIGKILL is required for this gate")
class CrashRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.db = str(base / "fleet.db")
        self.ws = base / "ws"
        self.ws.mkdir()
        self.log = self.ws / "crash" / "effects.log"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _wait_for(self, predicate, timeout: float, description: str) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        raise AssertionError(f"timed out waiting for {description}")

    def test_sigkill_mid_step_then_resume_finishes_without_duplicating_work(self) -> None:
        phase1 = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [
                sys.executable,
                str(WORKER),
                "phase1",
                "--db",
                self.db,
                "--workspace",
                str(self.ws),
                "--task",
                "crash-1",
                "--sleep",
                "20",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self._wait_for(self.log.exists, 15, "the slow step to start")
            self._wait_for(
                lambda: self._step_status("crash-1", "slow") == "running",
                15,
                "the slow step to be claimed",
            )
            os.kill(phase1.pid, signal.SIGKILL)
            returncode = phase1.wait(timeout=15)
        finally:
            if phase1.poll() is None:  # pragma: no cover - defensive
                phase1.kill()

        self.assertEqual(returncode, -signal.SIGKILL, "the worker must die from SIGKILL")

        # 1. Nothing was rolled back, and the in-flight claim survived.
        with DurableTaskStore(self.db) as store:
            snapshot = store.get_task("crash-1")
            self.assertEqual(snapshot.status, TaskStatus.RUNNING)
            states = {step.step_id: step for step in snapshot.steps}
            self.assertEqual(states["first"].status, StepStatus.SUCCEEDED)
            self.assertEqual(states["slow"].status, StepStatus.RUNNING)
            self.assertEqual(states["slow"].attempts, 1)
            first_verdict = store.step_result("crash-1", "first")["verification"]
            self.assertTrue(first_verdict["ok"], "phase 1's verified work must survive")
            lines_after_crash = self.log.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines_after_crash), 1)
            self.assertTrue(json.loads(lines_after_crash[0])["fresh_effect"])

        # 2. A brand-new process requeues exactly the lost step and finishes.
        phase2 = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [
                sys.executable,
                str(WORKER),
                "phase2",
                "--db",
                self.db,
                "--workspace",
                str(self.ws),
                "--task",
                "crash-1",
                "--lease",
                "2",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        self.assertEqual(phase2.returncode, 0, phase2.stderr or phase2.stdout)
        payload = json.loads(phase2.stdout.strip().splitlines()[-1])
        self.assertEqual(len(payload["recovered"]), 1)
        self.assertEqual(payload["recovered"][0]["step_id"], "slow")
        self.assertEqual(payload["status_final"], "succeeded")
        self.assertEqual(payload["degraded"], False)

        steps = {step["step_id"]: step for step in payload["steps"]}
        # 4. Attempt 1 was lost and recorded; the resume ran attempt 2.
        self.assertEqual(steps["slow"]["attempt_numbers"], [1, 2])
        self.assertEqual(steps["slow"]["attempts"], 2)
        self.assertEqual(steps["first"]["attempt_numbers"], [1], "completed work is not redone")

        # 5. The side effect happened once: two attempts, one fresh effect.
        lines = self.log.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2, "both attempts logged")
        records = [json.loads(line) for line in lines]
        self.assertEqual(sum(1 for record in records if record["fresh_effect"]), 1)

        # 6. The audit chain still verifies end to end.
        with DurableTaskStore(self.db) as store:
            integrity = store.verify_integrity()
            self.assertTrue(integrity["ok"], integrity)
            self.assertGreaterEqual(integrity["audit_rows"], 5)
            # Exactly one effect per step: the slow step's effect plus the
            # success path's own ledger row for the quick step.
            self.assertEqual(store.stats()["effects"], 2)

    def _step_status(self, task_id: str, step_id: str) -> str | None:
        if not Path(self.db).exists():
            return None
        try:
            with DurableTaskStore(self.db) as store:
                for step in store.get_task(task_id).steps:
                    if step.step_id == step_id:
                        return step.status.value
        except Exception:  # noqa: BLE001 - the db may be mid-creation
            return None
        return None


if __name__ == "__main__":
    unittest.main(verbosity=2)
