"""CLI contract tests: exit codes must mean exactly what the docs say they mean.

``0`` = succeeded, ``2`` = terminal failure, ``3`` = not finished yet. A CLI that
returns 0 on a dead-lettered task is how "green CI" becomes a lie.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import cli  # noqa: E402
from fleet2027 import DurableTaskStore, PlanStep, TaskSpec  # noqa: E402


class CliTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.db = str(base / "fleet.db")
        self.ws = str(base / "ws")
        Path(self.ws).mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

class AskCommandTests(CliTestCase):
    """The command that removes the need to write a spec by hand.

    These tests exist because this is the layer the product is promised on: a
    person who has never written JSON types a sentence and gets a real task.
    """

    def test_a_plain_sentence_becomes_a_stored_task(self) -> None:
        code = cli.main(
            [
                "ask", "اعمل لي موقع لمشروعي",
                "--db", self.db, "--workspace", self.ws, "--task", "ask-1",
            ]
        )
        self.assertEqual(code, 0)
        with DurableTaskStore(self.db) as store:
            spec = store.spec("ask-1")
        self.assertIn("موقع", spec.goal)
        self.assertGreaterEqual(len(spec.steps), 2)
        self.assertEqual(spec.created_by, "ask")
        self.assertEqual(spec.metadata["domain"], "website")

    def test_dry_run_stores_nothing(self) -> None:
        """Looking at the plan must be free, or nobody will ever check it."""
        code = cli.main(
            ["ask", "اعمل لي موقع", "--db", self.db, "--workspace", self.ws, "--dry-run"]
        )
        self.assertEqual(code, 0)
        with DurableTaskStore(self.db) as store:
            self.assertEqual(store.list_tasks(), ())

    def test_an_unreadable_request_fails_without_storing(self) -> None:
        code = cli.main(
            ["ask", "ززززز", "--db", self.db, "--workspace", self.ws, "--task", "bad-1"]
        )
        self.assertNotEqual(code, 0, "an unreadable request must not exit 0")
        with DurableTaskStore(self.db) as store:
            self.assertEqual(store.list_tasks(), ())

    def test_ask_run_produces_a_verified_file_in_one_command(self) -> None:
        """The whole product in one invocation: sentence in, file out, exit 0."""
        code = cli.main(
            [
                "ask", "اعمل لي موقع لمشروعي",
                "--db", self.db, "--workspace", self.ws,
                "--task", "one-shot", "--run",
            ]
        )
        self.assertEqual(code, 0)
        produced = Path(self.ws) / "intent" / "one-shot" / "index.html"
        self.assertTrue(produced.exists(), "no website was produced")
        self.assertIn("No language model was called", produced.read_text(encoding="utf-8"))

    def test_a_failed_run_does_not_exit_zero(self) -> None:
        """A CLI that reports success on a dead-lettered task makes green a lie."""
        code = cli.main(
            ["ask", "ابحث عن شيء", "--db", self.db, "--workspace", self.ws,
             "--task", "will-fail", "--run"]
        )
        with DurableTaskStore(self.db) as store:
            status = store.get_task("will-fail").status.value
        if status != "succeeded":
            self.assertNotEqual(code, 0)

    def test_the_generated_plan_is_executable_by_the_runner(self) -> None:
        """An intent plan is only worth anything if the fleet can actually run it.

        The demo registry provides planner/builder/verifier, so a task derived
        from a sentence must clear the same bar as a hand-written one.
        """
        cli.main(
            ["ask", "نظم اسبوعي", "--db", self.db, "--workspace", self.ws, "--task", "run-1"]
        )
        with DurableTaskStore(self.db) as store:
            spec = store.spec("run-1")
        self.assertEqual(spec.metadata["domain"], "organize")
        for step in spec.steps:
            self.assertIn(step.agent, {"planner", "builder", "verifier"})


class DemoCommandTests(CliTestCase):
    def test_demo_runs_and_returns_zero_when_the_task_succeeds(self) -> None:
        code = cli.main(
            ["demo", "--db", self.db, "--workspace", self.ws, "--task", "cli-1"]
        )
        self.assertEqual(code, 0)
        with DurableTaskStore(self.db) as store:
            snapshot = store.get_task("cli-1")
            self.assertEqual(snapshot.status.value, "succeeded")
            self.assertTrue(snapshot.degraded)
            self.assertEqual(store.stats()["dead_letters"], 1)

    def test_verify_command_passes_on_a_healthy_store(self) -> None:
        cli.main(["demo", "--db", self.db, "--workspace", self.ws, "--task", "cli-2"])
        self.assertEqual(cli.main(["verify", "--db", self.db]), 0)

    def test_verify_command_fails_after_tampering(self) -> None:
        cli.main(["demo", "--db", self.db, "--workspace", self.ws, "--task", "cli-3"])
        with DurableTaskStore(self.db) as store:
            store._conn.execute("UPDATE audit_log SET payload_json = '{}' WHERE seq = 2")
        self.assertEqual(cli.main(["verify", "--db", self.db]), 2)


class PlanAndRunTests(CliTestCase):
    def _write_spec(self, base: Path, *, task_id: str, failing: bool) -> str:
        path = base / "spec.json"
        step = PlanStep(step_id="s", agent="ok" if not failing else "missing", action="go")
        path.write_text(
            json.dumps(TaskSpec(task_id=task_id, goal="cli", steps=(step,)).to_dict()),
            encoding="utf-8",
        )
        return str(path)

    def test_plan_then_recover_reports_nothing_to_do(self) -> None:
        spec_path = self._write_spec(Path(self.tmp.name), task_id="cli-plan", failing=False)
        self.assertEqual(
            cli.main(["plan", "--db", self.db, "--spec", spec_path]), 0
        )
        self.assertEqual(
            cli.main(["recover", "--db", self.db, "--workspace", self.ws]), 0
        )
        with DurableTaskStore(self.db) as store:
            self.assertEqual(store.get_task("cli-plan").status.value, "queued")

    def test_running_a_task_with_an_unknown_agent_exits_non_zero(self) -> None:
        spec_path = self._write_spec(Path(self.tmp.name), task_id="cli-missing", failing=True)
        cli.main(["plan", "--db", self.db, "--spec", spec_path])
        code = cli.main(
            ["run", "--db", self.db, "--workspace", self.ws, "--task", "cli-missing"]
        )
        self.assertEqual(code, 2, "an unroutable task must not look like success")

    def test_status_json_is_machine_readable(self) -> None:
        cli.main(["demo", "--db", self.db, "--workspace", self.ws, "--task", "cli-4"])
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            cli.main(["status", "--db", self.db, "--task", "cli-4", "--json"])
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["task_id"], "cli-4")
        self.assertEqual(payload["status"], "succeeded")
        self.assertEqual(len(payload["steps"]), 6)

    def test_export_then_import_moves_work_between_stores(self) -> None:
        with DurableTaskStore(self.db) as store:
            store.submit(
                TaskSpec(
                    task_id="cli-seed",
                    goal="seed",
                    steps=(PlanStep(step_id="a", agent="x", action="1"),
                           PlanStep(step_id="b", agent="x", action="2", depends_on=("a",))),
                )
            )
            seed = store.export_resume_seed("cli-seed")
        seed_path = Path(self.tmp.name) / "seed.json"
        seed_path.write_text(json.dumps(seed), encoding="utf-8")
        other = str(Path(self.tmp.name) / "other.db")
        self.assertEqual(cli.main(["import", "--db", other, "--seed", str(seed_path)]), 0)
        with DurableTaskStore(other) as store:
            self.assertEqual(store.get_task("cli-seed").steps[0].status.value, "ready")

    def test_cancel_command_exits_failed(self) -> None:
        with DurableTaskStore(self.db) as store:
            store.submit(
                TaskSpec(task_id="cli-cancel", goal="g",
                         steps=(PlanStep(step_id="a", agent="x", action="1"),))
            )
        self.assertEqual(
            cli.main(["cancel", "--db", self.db, "--task", "cli-cancel"]), 2
        )

    def test_unknown_command_exits_non_zero_without_a_traceback(self) -> None:
        with self.assertRaises(SystemExit):
            cli.main(["nonsense"])


class LlmCommandTests(CliTestCase):
    def test_scripted_backend_produces_a_verified_file_and_exits_zero(self) -> None:
        code = cli.main([
            "llm", "--db", self.db, "--workspace", self.ws, "--backend", "scripted",
            "--prompt", "write add(a,b)", "--out", "app.py", "--task", "cli-llm",
        ])
        self.assertEqual(code, 0)
        written = Path(self.ws) / "app.py"
        self.assertTrue(written.is_file())
        self.assertIn("def add", written.read_text(encoding="utf-8"))
        with DurableTaskStore(self.db) as store:
            record = store.step_result("cli-llm", "write")
            self.assertTrue(record["verification"]["ok"])
            self.assertEqual(record["evidence"]["called"], True)

    def test_ollama_without_an_endpoint_is_a_usage_error_not_a_fake_success(self) -> None:
        import os

        previous = os.environ.pop("OLLAMA_BASE_URL", None)
        try:
            code = cli.main([
                "llm", "--db", self.db, "--workspace", self.ws, "--backend", "ollama",
                "--prompt", "x", "--out", "x.py", "--task", "cli-llm-off",
            ])
        finally:
            if previous is not None:
                os.environ["OLLAMA_BASE_URL"] = previous
        self.assertEqual(code, 1, "a missing endpoint must never look like success")
        self.assertFalse((Path(self.ws) / "x.py").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
