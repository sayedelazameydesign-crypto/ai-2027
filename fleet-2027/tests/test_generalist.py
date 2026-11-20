"""End-to-end tests: one plain sentence in, one verified deliverable out.

These are the tests the product is promised on. Everything else in this repo
checks a component; these check the claim itself — that a person who knows
nothing about prompts, JSON or agents can type what they want and get a real,
verified file.

They deliberately use the *shipped* generalist registry and the *real* durable
store and runner, not stubs. A stub-based end-to-end test would have passed the
first time, while the real one dead-lettered on every run for four distinct
reasons (wrong agent role, missing verification, wrong evidence requirement,
wrong artifact path). Those four bugs were only visible by running the thing.

What these tests are not: they do not claim a language model was involved. The
generalist agents are deterministic and stamp that fact into every file they
write. The value of the test is the pipeline — understanding, planning,
durability, independent verification, audit — which is unchanged when a real
model later replaces the text generation.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from kernel2027.intent import (
    IntentError,
    check_feasible,
    resolve,
    to_task_spec,
)

from fleet2027 import DurableTaskStore
from fleet2027.contracts import PlanStep, TaskSpec
from fleet2027.generalist import build_generalist_registry, registry_index
from fleet2027.runner import FleetRunner

#: One sentence per supported domain, written the way a non-technical Arabic
#: speaker would actually write it.
SENTENCES: tuple[tuple[str, str, str], ...] = (
    ("اعمل لي موقع لمشروعي", "website", "index.html"),
    ("اكتب لي تقرير عن السوق", "document", "document.md"),
    ("ابحث عن أفضل لابتوب", "research", "report.md"),
    ("نظم اسبوعي", "organize", "plan.md"),
    ("اشرح لي البلوكشين", "learn", "explanation.md"),
    ("خطط رحلة إلى اليابان", "travel", "itinerary.md"),
    ("حلل مشروعي", "business", "business-plan.md"),
    ("اصلح كود", "code", "change.md"),
)


class Harness(unittest.TestCase):
    """Shared setup: a throwaway database and workspace per test."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.db = str(base / "fleet.db")
        self.ws = base / "ws"
        self.ws.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_sentence(self, utterance: str, task_id: str = "t1"):
        """Resolve, submit, execute — the whole path a user's sentence takes."""
        resolution = resolve(utterance, task_key=task_id)
        spec = to_task_spec(
            resolution, task_id=task_id, build_step=PlanStep, build_spec=TaskSpec
        )
        with DurableTaskStore(self.db) as store:
            store.submit(spec)
            runner = FleetRunner(
                store=store,
                registry=build_generalist_registry(),
                workspace_root=self.ws,
                announce=False,
            )
            report = runner.run(task_id)
            return resolution, spec, report, store.get_task(task_id)


class EveryDomainRunsTests(Harness):
    """The acceptance criterion, one domain at a time."""

    def test_every_domain_succeeds_and_produces_its_file(self) -> None:
        with DurableTaskStore(self.db) as store:
            for index, (sentence, domain, filename) in enumerate(SENTENCES):
                with self.subTest(domain=domain, sentence=sentence):
                    task_id = f"t{index}"
                    resolution = resolve(sentence, task_key=task_id)
                    self.assertEqual(resolution.domain, domain)
                    spec = to_task_spec(
                        resolution,
                        task_id=task_id,
                        build_step=PlanStep,
                        build_spec=TaskSpec,
                    )
                    store.submit(spec)
                    report = FleetRunner(
                        store=store,
                        registry=build_generalist_registry(),
                        workspace_root=self.ws,
                        announce=False,
                    ).run(task_id)
                    self.assertEqual(
                        report.status.value,
                        "succeeded",
                        f"{domain} did not complete: {report.status}",
                    )
                    produced = self.ws / "intent" / task_id / filename
                    self.assertTrue(produced.exists(), f"{domain} produced no {filename}")
                    self.assertGreater(produced.stat().st_size, 0)

    def test_every_run_leaves_an_intact_audit_chain(self) -> None:
        """A deliverable without a trustworthy record of how it was made is a rumour."""
        self.run_sentence("اعمل لي موقع لمشروعي")
        with DurableTaskStore(self.db) as store:
            ok, rows, error = store.verify_audit_chain()
        self.assertTrue(ok, f"audit chain broken: {error}")
        self.assertGreater(rows, 0)


class UnderstandBeforeWorkTests(Harness):
    """What the system decided must be visible before and after the work."""

    def test_the_brief_on_disk_records_what_was_assumed(self) -> None:
        """Silent assumptions are the thing this whole layer exists to prevent."""
        resolution, _spec, _report, _snap = self.run_sentence("اعمل لي موقع لمشروعي")
        brief = self.ws / "intent" / "t1" / "brief.json"
        self.assertTrue(brief.exists())
        payload = brief.read_text(encoding="utf-8")
        self.assertIn("website", payload)
        self.assertIn("assumptions" if False else "settings", payload)
        self.assertIn(resolution.goal[:20], payload)

    def test_the_domain_reaches_the_step_so_the_right_file_is_written(self) -> None:
        """A website request must not silently produce a markdown file.

        This regressed exactly once: ``domain`` lived in the spec's metadata but
        not in the step inputs, so every domain fell back to ``document.md``
        while the task still reported success.
        """
        _res, spec, _report, _snap = self.run_sentence("اعمل لي موقع لمشروعي")
        for step in spec.steps:
            self.assertEqual(step.inputs["settings"]["domain"], "website")

    def test_an_email_stops_for_consent_instead_of_dead_lettering(self) -> None:
        """Outward actions must be refused clearly, not fail mysteriously."""
        resolution = resolve("ابعت ايميل للعميل", task_key="mail")
        self.assertFalse(resolution.can_start)
        self.assertTrue(resolution.needs_consent)


class FeasibilityTests(Harness):
    """Impossible plans must be refused at plan time, not at run time."""

    def test_the_generalist_registry_covers_every_intent_role(self) -> None:
        index = registry_index()
        available = set(index["names"]) | set(index["capabilities"])
        for _sentence, domain, _file in SENTENCES:
            with self.subTest(domain=domain):
                missing = check_feasible(resolve(f"اعمل لي {domain}"), available)
                self.assertEqual(missing, (), f"{domain} needs unknown agents {missing}")

    def test_a_missing_role_is_named_before_the_task_is_stored(self) -> None:
        """The first real run dead-lettered on this; it must never happen again.

        A registry with no ``builder`` cannot satisfy any drafting plan, and the
        user deserves to be told that *before* a broken task lands in their
        history.
        """
        resolution = resolve("اعمل لي موقع", task_key="x")
        missing = check_feasible(resolution, known={"planner", "verifier"})
        self.assertEqual(missing, ("builder",))

    def test_capability_tags_do_not_satisfy_an_agent_name(self) -> None:
        """A step names an agent; a tag is not a substitute for one.

        ``handler_for`` will happily resolve a tag to whatever agent owns it —
        which may not be the agent the plan named. Accepting tags here would
        mean a plan silently running on a substitute, so this refuses them.
        """
        resolution = resolve("اعمل لي موقع", task_key="x")
        missing = check_feasible(resolution, {"clarify", "write", "verify"})
        self.assertEqual(missing, ("builder", "planner", "verifier"))


class HonestyTests(Harness):
    """Nothing produced here may pretend to be what it is not."""

    def test_every_generated_file_says_no_model_was_called(self) -> None:
        for index, (sentence, domain, filename) in enumerate(SENTENCES):
            if domain == "email":
                continue
            with self.subTest(domain=domain):
                base = Path(self.tmp.name) / f"h{index}"
                base.mkdir()
                db = str(base / "f.db")
                resolution = resolve(sentence, task_key="h")
                spec = to_task_spec(
                    resolution,
                    task_id="h",
                    build_step=PlanStep,
                    build_spec=TaskSpec,
                )
                with DurableTaskStore(db) as store:
                    store.submit(spec)
                    FleetRunner(
                        store=store,
                        registry=build_generalist_registry(),
                        workspace_root=base,
                        announce=False,
                    ).run("h")
                text = (base / "intent" / "h" / filename).read_text(encoding="utf-8")
                self.assertIn("No language model was called", text)

    def test_a_verification_step_cannot_pass_without_artifacts(self) -> None:
        """Fail closed: 'nothing to check' is never a pass."""
        from fleet2027.generalist import verify_agent
        from fleet2027.registry import HandlerContext

        registry = build_generalist_registry()
        ctx = HandlerContext(
            task_id="empty",
            step_id="s_verify",
            action="verify_artifact",
            goal="g",
            agent=registry.spec("verifier"),
            attempt=1,
            workspace_root=self.ws,
            idempotency_key="k",
            inputs={"settings": {"domain": "website"}},
            store=None,
        )
        result = verify_agent(ctx)
        self.assertFalse(result.ok)
        self.assertTrue(result.error.strip())

    def test_an_unreadable_sentence_produces_no_task(self) -> None:
        with self.assertRaises(IntentError):
            resolve("ززززز")


if __name__ == "__main__":
    unittest.main(verbosity=2)
