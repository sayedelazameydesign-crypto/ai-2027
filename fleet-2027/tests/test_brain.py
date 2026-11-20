"""Phase 3: the brain is connected — and it cannot lie about it.

The whole point of this file is the gap between two failures that look alike
from the outside:

* **no model configured** → a labelled scaffold. Honest: nothing was attempted.
* **model configured and failed** → the step fails. Honest: nothing was hidden.

The failure mode this file exists to rule out is the third one: *model
configured and failed, system quietly produced a scaffold, task reported
success.* A user would read that result, believe a model wrote it, and be wrong.

Every test here runs offline. ``ScriptedBackend`` is a real backend from
``fleet2027.llm`` that returns scripted text and never opens a socket, so the
loop is exercised end to end at zero cost.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from kernel2027.intent import resolve, to_task_spec

from fleet2027 import DurableTaskStore
from fleet2027.contracts import PlanStep, TaskSpec
from fleet2027.generalist import build_generalist_registry
from fleet2027.llm import ScriptedBackend
from fleet2027.runner import FleetRunner

MODEL_HTML = (
    "```html\n"
    '<!doctype html>\n<html lang="ar" dir="rtl"><body>\n'
    "<h1>متجر النور</h1>\n<p>نص كتبه النموذج.</p>\n"
    "</body></html>\n"
    "```"
)


class _FailingBackend:
    """A configured backend that always fails, like a model that is down."""

    name = "broken"
    model = "broken-v1"

    def is_configured(self) -> bool:
        """It *is* configured — that is what makes the failure meaningful."""
        return True

    def complete(self, request: object) -> object:
        """Fail the way a dead provider fails."""
        raise RuntimeError("model exploded")


class Harness(unittest.TestCase):
    """Run one sentence with a chosen backend and report what really happened."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.db = str(base / "fleet.db")
        self.ws = base / "ws"
        self.ws.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_with(self, backend: object, utterance: str = "اعمل لي موقع لمشروعي"):
        spec = to_task_spec(
            resolve(utterance, task_key="s"),
            task_id="s",
            build_step=PlanStep,
            build_spec=TaskSpec,
        )
        with DurableTaskStore(self.db) as store:
            store.submit(spec)
            report = FleetRunner(
                store=store,
                registry=build_generalist_registry(backend=backend),
                workspace_root=self.ws,
                announce=False,
            ).run("s")
            result = store.step_result("s", "s_build") or {}
        return report, result

    def produced(self, name: str = "index.html") -> Path:
        """Where a deliverable must be — never the workspace root."""
        return self.ws / "intent" / "s" / name


class BrainIsConnectedTests(Harness):
    """With a backend, the model's words are what land on disk."""

    def test_the_models_output_is_the_file(self) -> None:
        backend = ScriptedBackend([MODEL_HTML])
        report, _result = self.run_with(backend)
        self.assertEqual(report.status.value, "succeeded")
        self.assertEqual(len(backend.calls), 1, "the model was not called exactly once")
        text = self.produced().read_text(encoding="utf-8")
        self.assertIn("متجر النور", text)
        self.assertNotIn("No language model was called", text)

    def test_the_receipt_says_the_model_was_called(self) -> None:
        """Evidence must distinguish 'a model wrote this' from 'a template did'."""
        _report, result = self.run_with(ScriptedBackend([MODEL_HTML]))
        evidence = result.get("evidence") or {}
        self.assertTrue(evidence.get("called"))
        self.assertEqual(evidence.get("backend"), "scripted")
        self.assertGreater(evidence.get("output_bytes", 0), 0)
        self.assertEqual(len(evidence.get("output_sha256", "")), 64)

    def test_the_prompt_reaches_the_model(self) -> None:
        """The intent layer composes the instruction; the backend must receive it."""
        backend = ScriptedBackend([MODEL_HTML])
        self.run_with(backend)
        prompt = backend.calls[0]
        self.assertIn("موقع", prompt)
        self.assertIn("Do not invent", prompt)

    def test_a_novice_request_reaches_the_model(self) -> None:
        """'I do not know where to start' must survive all the way to the prompt."""
        backend = ScriptedBackend([MODEL_HTML])
        self.run_with(backend, "عايز أعمل موقع ومش عارف أبدأ منين")
        self.assertIn("not technical", backend.calls[0])


class ScaffoldIsHonestTests(Harness):
    """With no backend, nothing is spent and the file says so."""

    def test_no_backend_means_no_model_and_a_labelled_file(self) -> None:
        report, result = self.run_with(None)
        self.assertEqual(report.status.value, "succeeded")
        text = self.produced().read_text(encoding="utf-8")
        self.assertIn("No language model was called", text)
        self.assertFalse((result.get("evidence") or {}).get("called"))

    def test_the_default_registry_never_calls_a_model(self) -> None:
        """The zero-spend guard: absence of a flag must mean absence of calls."""
        registry = build_generalist_registry()
        for name in registry.names:
            self.assertNotIn("llm", name, f"{name} looks model-backed by default")


class FailureIsHonestTests(Harness):
    """A configured model that fails must fail the task — never fall back."""

    def test_a_failing_model_does_not_produce_a_scaffold(self) -> None:
        """The single most dangerous bug this phase could have introduced."""
        report, _result = self.run_with(_FailingBackend())
        self.assertNotEqual(
            report.status.value,
            "succeeded",
            "a failed model was reported as success",
        )
        self.assertFalse(
            self.produced().exists(),
            "a scaffold was written in place of the model's failure",
        )

    def test_a_model_that_returns_nothing_fails(self) -> None:
        """Empty output is not a deliverable, however confident the backend is."""
        report, _result = self.run_with(ScriptedBackend(["   \n  \n"]))
        self.assertNotEqual(report.status.value, "succeeded")

    def test_a_model_that_refuses_fails(self) -> None:
        report, _result = self.run_with(
            ScriptedBackend(["I'm sorry, but I cannot help with that request."])
        )
        self.assertNotEqual(report.status.value, "succeeded")


class ParityTests(Harness):
    """Both paths must satisfy the same contract, or one of them is untested."""

    def test_both_paths_verify_independently(self) -> None:
        for label, backend in (("scaffold", None), ("model", ScriptedBackend([MODEL_HTML]))):
            with self.subTest(path=label):
                base = Path(self.tmp.name) / label
                base.mkdir()
                db = str(base / "f.db")
                ws = base / "ws"
                ws.mkdir()
                spec = to_task_spec(
                    resolve("اعمل لي موقع", task_key="s"),
                    task_id="s",
                    build_step=PlanStep,
                    build_spec=TaskSpec,
                )
                with DurableTaskStore(db) as store:
                    store.submit(spec)
                    report = FleetRunner(
                        store=store,
                        registry=build_generalist_registry(backend=backend),
                        workspace_root=ws,
                        announce=False,
                    ).run("s")
                    self.assertEqual(report.status.value, "succeeded")
                    file = ws / "intent" / "s" / "index.html"
                    self.assertTrue(file.exists(), f"{label} produced no file")
                    self.assertGreater(file.stat().st_size, 0)

    def test_both_paths_write_to_the_same_place(self) -> None:
        """A handler that recomputes its own path can disagree with the verifier.

        It did: the model wrote ``index.html`` to the workspace root while the
        verifier looked in ``intent/<task>/``, and the task reported success.
        """
        for label, backend in (("scaffold", None), ("model", ScriptedBackend([MODEL_HTML]))):
            with self.subTest(path=label):
                base = Path(self.tmp.name) / f"place-{label}"
                base.mkdir()
                ws = base / "ws"
                ws.mkdir()
                spec = to_task_spec(
                    resolve("اعمل لي موقع", task_key="s"),
                    task_id="s",
                    build_step=PlanStep,
                    build_spec=TaskSpec,
                )
                with DurableTaskStore(str(base / "f.db")) as store:
                    store.submit(spec)
                    FleetRunner(
                        store=store,
                        registry=build_generalist_registry(backend=backend),
                        workspace_root=ws,
                        announce=False,
                    ).run("s")
                self.assertTrue((ws / "intent" / "s" / "index.html").exists())
                self.assertFalse((ws / "index.html").exists(), "written to the root")


class RegistryShapeTests(unittest.TestCase):
    """Choosing the brain must not change the plan, the roles or the verifier."""

    def test_roles_are_identical_in_both_modes(self) -> None:
        plain = set(build_generalist_registry().names)
        with_brain = set(build_generalist_registry(backend=ScriptedBackend([MODEL_HTML])).names)
        self.assertEqual(plain, with_brain)

    def test_the_verifier_is_never_model_backed(self) -> None:
        """A model grading its own work destroys the independence of the check."""
        registry = build_generalist_registry(backend=ScriptedBackend([MODEL_HTML]))
        self.assertEqual(registry.spec("verifier").role.count("model"), 0)
        self.assertIn("fails closed", registry.spec("verifier").role)

    def test_a_model_backed_writer_still_requires_evidence(self) -> None:
        registry = build_generalist_registry(backend=ScriptedBackend([MODEL_HTML]))
        self.assertIn("artifact_sha256", registry.spec("builder").requires_evidence)

    def test_no_provider_sdk_is_imported(self) -> None:
        """The core stays dependency-free; backends are injected, never imported."""
        import ast

        for path in (HERE.parent / "fleet2027").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(
                            alias.name.split(".")[0],
                            {"openai", "anthropic", "httpx", "requests", "google"},
                            f"{path.name} imports {alias.name}",
                        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
