"""Tests for the intent layer — the product's central promise, pinned.

Every test here corresponds to a sentence a real person might type, because the
only question this module answers is: *did the system understand, and can work
begin?* Assertions therefore check behaviour a user would notice, not internals:
whether work starts, whether a question is asked when it should not be, and
whether assumptions are visible.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kernel2027.intent import (
    CONFIDENCE_FLOOR,
    IntentError,
    Question,
    Setting,
    normalise,
    resolve,
)


class UnderstandingTests(unittest.TestCase):
    """Does the system read what was actually asked?"""

    def test_the_sentence_from_the_product_brief(self) -> None:
        """'I want to start a project and don't know where to begin.'

        This is the exact utterance the vision is built around: no technical
        vocabulary, no stated deliverable, only a situation. The system must
        read it as business planning and start work.
        """
        result = resolve("عايز أبدأ مشروع، ومش عارف أبدأ منين")
        self.assertEqual(result.domain, "business")
        self.assertTrue(result.can_start)
        self.assertTrue(result.steps)

    def test_domains_are_recognised_in_arabic(self) -> None:
        cases = {
            "اعمل لي موقع لمشروعي": "website",
            "اكتب لي تقرير عن السوق": "document",
            "ابحث عن أفضل لابتوب": "research",
            "نظم اسبوعي": "organize",
            "اشرح لي البلوكشين": "learn",
            "خطط رحلة إلى تركيا": "travel",
            "ابعت ايميل للعميل": "email",
        }
        for utterance, expected in cases.items():
            with self.subTest(utterance=utterance):
                self.assertEqual(resolve(utterance).domain, expected)

    def test_domains_are_recognised_in_english(self) -> None:
        cases = {
            "build me a website": "website",
            "write a report on the market": "document",
            "compare these two laptops": "research",
            "organize my week": "organize",
            "explain how transformers work": "learn",
            "plan a trip to Turkey": "travel",
            "fix a bug in my script": "code",
        }
        for utterance, expected in cases.items():
            with self.subTest(utterance=utterance):
                self.assertEqual(resolve(utterance).domain, expected)

    def test_a_named_outcome_is_not_undermined_by_an_incidental_word(self) -> None:
        """'a website for my project' is a website request, not an ambiguity."""
        result = resolve("اعمل لي موقع لمشروعي")
        self.assertEqual(result.domain, "website")
        self.assertTrue(result.is_confident, "a decisive signal must not read as a tie")

    def test_genuine_ambiguity_is_reported_not_hidden(self) -> None:
        """'I want to learn programming' is both learning and code — say so.

        Either reading is defensible, so the honest behaviour is to surface the
        uncertainty while still allowing work to begin with a default.
        """
        result = resolve("عايز اتعلم البرمجة")
        self.assertIn(result.domain, {"learn", "code"})
        self.assertFalse(result.is_confident)
        self.assertGreaterEqual(len(result.alternatives), 2)
        self.assertTrue(result.can_start, "uncertainty must not block work")

    def test_arabic_diacritics_and_hamza_are_folded(self) -> None:
        """'مَوْقِع' and 'موقع' are the same request."""
        self.assertEqual(resolve("مَوْقِع").domain, "website")
        self.assertEqual(normalise("مَوْقِع"), normalise("موقع"))
        self.assertEqual(normalise("أريد"), normalise("اريد"))

    def test_empty_and_meaningless_input_is_refused(self) -> None:
        for bad in ("", "   ", "\n\t ", "x"):
            with self.subTest(bad=bad), self.assertRaises(IntentError):
                resolve(bad)

    def test_unrecognisable_text_explains_itself(self) -> None:
        """The error must tell a non-technical user what to do instead."""
        with self.assertRaises(IntentError) as caught:
            resolve("ققققق ززززز")
        self.assertIn("موقع", str(caught.exception))


class ZeroQuestionTests(unittest.TestCase):
    """The promise: the user is not interrogated."""

    def test_no_request_ever_blocks_on_a_question(self) -> None:
        """The contract is not 'never ask' — it is 'never stop to ask'.

        A question is allowed when its answer genuinely reshapes the output
        (a website's purpose does). What is never allowed is work waiting on an
        answer, because that turns a helper into a form.
        """
        for utterance in (
            "اعمل لي موقع", "organize my week", "ابحث عن لابتوب",
            "اكتب لي تقرير", "خطط رحلة", "اصلح كود",
        ):
            with self.subTest(utterance=utterance):
                result = resolve(utterance)
                self.assertTrue(result.steps, "a question must not cost the plan")
                self.assertTrue(result.can_start, "a question must not block work")
                for question in result.questions:
                    self.assertTrue(question.default.strip())

    def test_a_routine_request_asks_nothing_at_all(self) -> None:
        """Most requests need no dialogue; prove the common path stays silent."""
        for utterance in ("organize my week", "نظم اسبوعي", "نظم لي مهامي"):
            with self.subTest(utterance=utterance):
                self.assertEqual(resolve(utterance).questions, ())

    def test_questions_are_capped_at_two(self) -> None:
        """Five questions is the system outsourcing its job to the user."""
        for utterance in ("اعمل لي موقع لمشروعي", "اكتب لي تقرير", "خطط رحلة"):
            with self.subTest(utterance=utterance):
                self.assertLessEqual(len(resolve(utterance).questions), 2)

    def test_every_question_carries_a_usable_default(self) -> None:
        """'I don't know' must always be a valid answer."""
        for utterance in ("اعمل لي موقع", "خطط رحلة", "اصلح الخطأ"):
            for question in resolve(utterance).questions:
                with self.subTest(utterance=utterance, key=question.key):
                    self.assertTrue(question.default.strip())
                    self.assertEqual(question.answered(None), question.default)
                    self.assertEqual(question.answered("   "), question.default)

    def test_an_answered_question_takes_the_answer(self) -> None:
        self.assertEqual(Question("k", "a", "w", "d").answered("mine"), "mine")

    def test_a_question_whose_answer_is_known_is_not_asked(self) -> None:
        """Asking what the sentence already said is the rudest possible failure."""
        result = resolve("خطط رحلة إلى تركيا")
        keys = [question.key for question in result.questions]
        self.assertIn("travellers", keys, "an unstated party size really does matter")
        self.assertNotIn("purpose", keys, "a purpose question belongs to websites only")

    def test_a_known_setting_removes_its_question(self) -> None:
        """Answering in the sentence must delete the question about it."""
        travelled = resolve("خطط رحلة، عدد المسافرين 2")
        self.assertIn("travellers", travelled.settings_map())

    def test_urgency_alone_is_not_a_task(self) -> None:
        """'I need it fast' names no outcome; refusing is the honest answer.

        Guessing a domain from urgency would produce confident, wrong work —
        the exact failure this layer exists to prevent.
        """
        with self.assertRaises(IntentError):
            resolve("محتاجه بسرعة ضروري")


class NoBlockingTests(unittest.TestCase):
    """Nothing stops for configuration; only outward actions stop for consent."""

    def test_drafting_work_always_starts(self) -> None:
        """A plan, a site, a report — producing them needs no permission."""
        for utterance in (
            "اعمل لي موقع", "اكتب لي تقرير", "خطط رحلة",
            "نظم اسبوعي", "اشرح لي شيئًا", "حلل السوق",
        ):
            with self.subTest(utterance=utterance):
                result = resolve(utterance)
                self.assertTrue(result.can_start)
                self.assertFalse(result.needs_consent)

    def test_sending_on_the_users_behalf_stops_for_consent(self) -> None:
        """An email leaves the machine under their name and cannot be recalled."""
        result = resolve("ابعت ايميل للعميل")
        self.assertTrue(result.needs_consent)
        self.assertFalse(result.can_start)

    def test_stated_facts_are_acknowledged_back(self) -> None:
        """An unacknowledged fact is indistinguishable from an ignored one."""
        result = resolve("خطط رحلة لـ 3 أشخاص، ميزانيتي 2000 دولار")
        self.assertEqual(result.settings_map().get("travellers"), "3")
        self.assertEqual(result.settings_map().get("budget_usd"), "2000")
        text = result.explain()
        self.assertIn("أخذت من كلامك", text)
        self.assertIn("3", text)
        self.assertIn("2000", text)

    def test_assumptions_are_always_declared(self) -> None:
        result = resolve("اعمل لي موقع")
        self.assertTrue(result.assumed)
        for item in result.assumed:
            self.assertNotEqual(item.source, "explicit")

    def test_a_stated_value_is_marked_explicit_and_not_overridden(self) -> None:
        result = resolve("اعمل لي موقع، الموعد النهائي خلال 3 أيام")
        deadline = [item for item in result.settings if item.key == "deadline"]
        self.assertTrue(deadline)
        self.assertEqual(deadline[0].source, "explicit")
        self.assertIn("3", deadline[0].value)
        self.assertNotIn("deadline", [item.key for item in result.assumed])

    def test_a_novice_request_changes_the_plan_not_just_a_log_line(self) -> None:
        """'I don't know where to start' must shape the output."""
        result = resolve("عايز أعمل موقع ومش عارف أبدأ منين")
        self.assertIn("explain_level", result.settings_map())
        self.assertIn("بلا مصطلحات", result.explain())

    def test_urgency_becomes_a_deadline_when_a_task_is_present(self) -> None:
        """Speed words must reach the plan, not just be noticed."""
        result = resolve("اعمل لي موقع بسرعة")
        self.assertEqual(result.settings_map().get("deadline"), "as soon as possible")
        self.assertIn("deadline", [item.key for item in result.assumed])


class ExplanationTests(unittest.TestCase):
    """The user must be able to see and correct what was assumed."""

    def test_explanation_is_human_readable_and_jargon_free(self) -> None:
        text = resolve("اعمل لي موقع لمشروعي").explain()
        self.assertIn("فهمت", text)
        for jargon in ("TaskSpec", "PlanStep", "step_id", "verifier", "artifact"):
            self.assertNotIn(jargon, text)

    def test_explanation_states_the_assumptions(self) -> None:
        text = resolve("اعمل لي موقع").explain()
        self.assertIn("سأفترض", text)
        self.assertIn("اللغة", text)

    def test_explanation_surfaces_uncertainty_in_plain_words(self) -> None:
        text = resolve("عايز اتعلم البرمجة").explain()
        self.assertIn("لست متأكد", text)


class PlanShapeTests(unittest.TestCase):
    """The output must be executable, ordered and verifiable."""

    def test_every_domain_produces_a_verification_step(self) -> None:
        """Nothing is 'done' without a step whose job is to check it."""
        for utterance in (
            "اعمل لي موقع", "اكتب لي تقرير", "ابحث عن شيء",
            "نظم اسبوعي", "اشرح لي", "خطط رحلة", "حلل مشروعي", "اصلح كود",
        ):
            with self.subTest(utterance=utterance):
                steps = resolve(utterance).steps
                actions = {step.action for step in steps}
                self.assertTrue(
                    actions & {"verify_artifact", "run_tests"},
                    f"no verification step in {actions}",
                )

    def test_dependencies_only_reference_existing_steps(self) -> None:
        for utterance in ("اعمل لي موقع", "ابحث عن سوق", "اصلح كود"):
            steps = resolve(utterance).steps
            known = {step.step_id for step in steps}
            for step in steps:
                for dep in step.depends_on:
                    self.assertIn(dep, known)
                    self.assertNotEqual(dep, step.step_id, "a step cannot depend on itself")

    def test_the_plan_has_no_cycles(self) -> None:
        """A cycle would deadlock the fleet runner, so prove acyclicity here."""
        steps = resolve("ابحث عن سوق").steps
        graph = {step.step_id: step.depends_on for step in steps}
        state: dict[str, int] = {}

        def visit(node: str) -> None:
            if state.get(node) == 1:
                raise AssertionError(f"cycle through {node}")
            if state.get(node) == 2:
                return
            state[node] = 1
            for dep in graph[node]:
                visit(dep)
            state[node] = 2

        for node in graph:
            visit(node)

    def test_step_ids_are_unique_and_prefixed(self) -> None:
        steps = resolve("اعمل لي موقع", task_key="a").steps
        ids = [step.step_id for step in steps]
        self.assertEqual(len(set(ids)), len(ids))
        self.assertTrue(all(step_id.startswith("a_") for step_id in ids))

    def test_two_utterances_in_one_task_do_not_collide(self) -> None:
        first = {step.step_id for step in resolve("اعمل لي موقع", task_key="one").steps}
        second = {step.step_id for step in resolve("اعمل لي موقع", task_key="two").steps}
        self.assertFalse(first & second)

    def test_resolution_serialises_for_the_audit_log(self) -> None:
        import json

        payload = resolve("اعمل لي موقع").to_dict()
        round_tripped = json.loads(json.dumps(payload))
        self.assertEqual(round_tripped["domain"], "website")
        self.assertIn("assumed", round_tripped)


class FleetIntegrationTests(unittest.TestCase):
    """The reading must be directly executable by the fleet runner.

    Note the shape of these tests: the *engine's* constructors are passed in.
    The kernel decides what the plan is; the fleet decides how a plan is spelled.
    """

    def setUp(self) -> None:
        fleet = Path(__file__).resolve().parent.parent.parent / "fleet-2027"
        if str(fleet) not in sys.path:
            sys.path.insert(0, str(fleet))

    def _build(self, utterance: str, task_id: str):
        from fleet2027.contracts import PlanStep, TaskSpec

        from kernel2027.intent import to_task_spec

        return to_task_spec(
            resolve(utterance), task_id=task_id, build_step=PlanStep, build_spec=TaskSpec
        )

    def test_resolution_becomes_a_valid_task_spec(self) -> None:
        spec = self._build("اعمل لي موقع لمشروعي", "site-1")
        self.assertEqual(spec.task_id, "site-1")
        self.assertTrue(spec.goal)
        self.assertGreaterEqual(len(spec.steps), 2)
        self.assertEqual(spec.metadata["domain"], "website")

    def test_the_spec_carries_the_assumptions_so_they_are_auditable(self) -> None:
        spec = self._build("اعمل لي موقع", "s2")
        self.assertIn("assumed", spec.metadata)
        self.assertTrue(spec.metadata["assumed"])

    def test_the_spec_dag_is_accepted_by_the_fleet_contract(self) -> None:
        """TaskSpec validates identifiers and dependencies on construction."""
        spec = self._build("ابحث عن سوق اللابتوب", "research-1")
        known = {step.step_id for step in spec.steps}
        for step in spec.steps:
            for dep in step.depends_on:
                self.assertIn(dep, known)

    def test_the_kernel_stays_independent_of_every_engine(self) -> None:
        """The boundary rule, pinned.

        An earlier version imported ``fleet2027`` inside ``to_task_spec``. It was
        a lazy import, and it still violated the rule: the shared kernel would
        have depended on one engine, so that engine's contract could change under
        every other consumer.
        """
        import ast

        root = Path(__file__).resolve().parent.parent / "kernel2027"
        forbidden = {"fleet2027", "ai2027_protocol", "connectors_hub", "memory"}
        for path in root.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    top = node.module.split(".")[0]
                    self.assertNotIn(
                        top, forbidden, f"{path.name} imports engine {top}"
                    )
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(
                            alias.name.split(".")[0], forbidden,
                            f"{path.name} imports engine {alias.name}",
                        )


class DeterminismTests(unittest.TestCase):
    """Same sentence, same reading — or nothing here can be trusted."""

    def test_resolution_is_deterministic(self) -> None:
        first = resolve("اعمل لي موقع لمشروعي")
        second = resolve("اعمل لي موقع لمشروعي")
        self.assertEqual(first.to_dict(), second.to_dict())

    def test_no_network_or_provider_is_needed(self) -> None:
        """The zero-spend guard applies to understanding, not just execution."""
        source = (Path(__file__).resolve().parent.parent /
                  "kernel2027" / "intent.py").read_text(encoding="utf-8")
        for forbidden in ("httpx", "requests", "urllib.request", "socket", "openai"):
            self.assertNotIn(f"import {forbidden}", source)

    def test_confidence_floor_is_a_stated_number(self) -> None:
        self.assertGreater(CONFIDENCE_FLOOR, 0.0)
        self.assertLess(CONFIDENCE_FLOOR, 1.0)


class CommandLineTests(unittest.TestCase):
    """The interface a non-programmer actually touches."""

    def _run(self, *argv: str) -> tuple[int, str, str]:
        import contextlib
        import io

        ask = Path(__file__).resolve().parent.parent / "ask.py"
        namespace: dict = {"__name__": "ask_test", "__file__": str(ask)}
        spec = compile(ask.read_text(encoding="utf-8"), str(ask), "exec")
        exec(spec, namespace)  # noqa: S102 - executing the CLI under test
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = namespace["main"](list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_one_plain_sentence_is_enough(self) -> None:
        code, out, _ = self._run("عايز أبدأ مشروع، ومش عارف أبدأ منين")
        self.assertEqual(code, 0)
        self.assertIn("فهمت", out)
        self.assertIn("خطة عمل", out)

    def test_the_summary_is_arabic_not_half_english(self) -> None:
        """The last line is the one people read; it must not leak schema words."""
        _code, out, _ = self._run("اعمل لي موقع")
        self.assertIn("موقع جاهز للنشر", out)
        self.assertNotIn("static site", out)

    def test_an_unreadable_request_exits_non_zero_and_explains(self) -> None:
        code, _out, err = self._run("ززززز")
        self.assertEqual(code, 2)
        self.assertIn("لم أفهم", err)

    def test_json_output_is_machine_readable(self) -> None:
        import json

        code, out, _ = self._run("اعمل لي موقع", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["domain"], "website")
        self.assertTrue(payload["can_start"])


class SettingTests(unittest.TestCase):
    """Small value object, but its `source` field is the honesty mechanism."""

    def test_default_source_is_explicit(self) -> None:
        self.assertEqual(Setting("k", "v").source, "explicit")

    def test_assumed_is_the_negation_of_explicit(self) -> None:
        result = resolve("اعمل لي موقع")
        explicit = {item.key for item in result.settings if item.source == "explicit"}
        assumed = {item.key for item in result.assumed}
        self.assertFalse(explicit & assumed)
        self.assertEqual(len(explicit) + len(assumed), len(result.settings))


if __name__ == "__main__":
    unittest.main(verbosity=2)
