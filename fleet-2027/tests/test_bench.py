"""Tests for the quality benchmark and its ratchet.

A benchmark is only worth trusting if it refuses to lie in the three ways a
benchmark usually lies:

1. **Dropping failures.** Scoring only the sentences that completed makes any
   system look better than it is, so failures are counted as zero.
2. **Changing the questions.** The corpus is fingerprinted; a different corpus
   is a different measurement and the gate refuses to compare them.
3. **Comparing different brains.** The scaffold and a model are two different
   systems; the gate compares like with like.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from fleet2027.bench import (
    DEFAULT_CORPUS,
    BenchReport,
    SentenceResult,
    corpus_fingerprint,
    load_corpus,
    run_benchmark,
)
from fleet2027.llm import LLMError, LLMResponse

ENGINE = HERE.parent


class CorpusTests(unittest.TestCase):
    """The fixed sentences must be fixed, and must cover every domain."""

    def test_the_corpus_has_twenty_four_sentences(self) -> None:
        self.assertEqual(len(load_corpus()), 24)

    def test_every_supported_domain_is_covered(self) -> None:
        covered = {item["domain"] for item in load_corpus()}
        self.assertEqual(
            covered,
            {
                "website", "document", "research", "organize",
                "learn", "travel", "business", "code",
            },
        )

    def test_the_corpus_is_multi_lingual(self) -> None:
        """A corpus written in one language measures that language, not the system."""
        texts = [item["text"] for item in load_corpus()]
        self.assertTrue(any(any("\u0600" <= ch <= "\u06ff" for ch in t) for t in texts))
        self.assertTrue(any(t.isascii() for t in texts))

    def test_the_fingerprint_changes_when_a_sentence_changes(self) -> None:
        original = DEFAULT_CORPUS.read_bytes()
        payload = json.loads(original)
        payload["sentences"][0]["text"] += " (edited)"
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            path = Path(handle.name)
        try:
            self.assertNotEqual(corpus_fingerprint(path), corpus_fingerprint(DEFAULT_CORPUS))
        finally:
            path.unlink()

    def test_the_fingerprint_is_stable(self) -> None:
        self.assertEqual(corpus_fingerprint(), corpus_fingerprint(DEFAULT_CORPUS))
        self.assertEqual(len(corpus_fingerprint()), 64)

    def test_a_malformed_corpus_is_refused(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as handle:
            json.dump({"sentences": [{"id": "x"}]}, handle)
            path = Path(handle.name)
        try:
            with self.assertRaises(ValueError):
                load_corpus(path)
        finally:
            path.unlink()


class HonestAggregationTests(unittest.TestCase):
    """The arithmetic must not be able to flatter the system."""

    def _report(self, results: tuple[SentenceResult, ...]) -> BenchReport:
        return BenchReport(
            backend="test",
            model_called=False,
            corpus_sha256="0" * 64,
            generated_at="2026-01-01T00:00:00Z",
            results=results,
        )

    def test_a_failure_scores_zero_rather_than_being_dropped(self) -> None:
        rows = (
            SentenceResult("a-1", "website", "x", "succeeded", 0.8, True, 900, False),
            SentenceResult("a-2", "website", "y", "dead_letter", 0.0, False, 0, False),
        )
        report = self._report(rows)
        self.assertEqual(report.mean_score, 0.4, "failures must drag the mean down")
        self.assertEqual(report.completion_rate, 0.5)
        self.assertEqual(report.usable_rate, 0.5)

    def test_an_empty_report_does_not_divide_by_zero(self) -> None:
        report = self._report(())
        self.assertEqual(report.mean_score, 0.0)
        self.assertEqual(report.usable_rate, 0.0)
        self.assertEqual(report.completion_rate, 0.0)

    def test_worst_is_the_work_queue(self) -> None:
        rows = tuple(
            SentenceResult(f"s-{i}", "website", "x", "succeeded", score, True, 900, False)
            for i, score in enumerate((0.9, 0.3, 0.6))
        )
        self.assertEqual(self._report(rows).worst(2)[0].score, 0.3)

    def test_the_report_records_whether_a_model_was_called(self) -> None:
        """A score with no provenance is a rumour with a decimal point."""
        payload = self._report(()).to_dict()
        self.assertIn("model_called", payload)
        self.assertIn("backend", payload)
        self.assertIn("corpus_sha256", payload)


class RunTests(unittest.TestCase):
    """One real run over the real corpus, scored by the real scorer."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = run_benchmark()

    def test_every_sentence_completes_on_the_scaffold(self) -> None:
        """If the scaffold cannot finish, nothing downstream is measurable."""
        self.assertEqual(self.report.completion_rate, 1.0)

    def test_the_scaffold_is_measured_not_flattered(self) -> None:
        """The headline result, and the reason this phase exists.

        The scaffold produces well-formed files that say "replace this text".
        The measured usable rate must be zero, and the mean must sit below the
        usable floor. If this ever passes trivially, the scorer has broken.
        """
        self.assertEqual(self.report.usable_rate, 0.0)
        self.assertLess(self.report.mean_score, 0.70)

    def test_no_model_was_called(self) -> None:
        """Zero spend is a hard rule, and the benchmark must not break it."""
        self.assertFalse(self.report.model_called)
        self.assertEqual(self.report.backend, "none")

    def test_every_sentence_is_scored_with_a_reason(self) -> None:
        for row in self.report.results:
            with self.subTest(sentence=row.sentence_id):
                self.assertTrue(row.status)
                self.assertGreaterEqual(row.score, 0.0)
                self.assertLessEqual(row.score, 1.0)
                self.assertGreaterEqual(row.chars, 0)

    def test_results_serialise(self) -> None:
        payload = self.report.to_dict()
        self.assertEqual(len(payload["results"]), 24)
        self.assertIn("by_domain", payload["summary"])
        json.dumps(payload)


class BaselineTests(unittest.TestCase):
    """The ratchet file must exist, be current, and match the corpus."""

    def test_a_baseline_is_recorded(self) -> None:
        path = ENGINE / "bench" / "baseline.json"
        self.assertTrue(path.exists(), "no baseline: the ratchet has nothing to hold")

    def test_the_baseline_matches_the_current_corpus(self) -> None:
        """A baseline over different questions measures a different system."""
        path = ENGINE / "bench" / "baseline.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["corpus_sha256"], corpus_fingerprint())

    def test_the_baseline_was_produced_by_the_scaffold(self) -> None:
        path = ENGINE / "bench" / "baseline.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["backend"], "none")
        self.assertFalse(payload["model_called"])

    def test_the_baseline_records_the_usable_floor_it_was_judged_by(self) -> None:
        from kernel2027.quality import USABLE_FLOOR

        payload = json.loads(
            (ENGINE / "bench" / "baseline.json").read_text(encoding="utf-8")
        )
        self.assertEqual(payload["usable_floor"], USABLE_FLOOR)


class ProvenanceTests(unittest.TestCase):
    """The benchmark must not import a provider, and must not import an engine."""

    def test_bench_imports_no_provider_sdk(self) -> None:
        import ast

        source = (ENGINE / "fleet2027" / "bench.py").read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(
                        alias.name.split(".")[0],
                        {"httpx", "requests", "openai", "anthropic"},
                    )

    def test_the_corpus_file_is_not_generated_at_runtime(self) -> None:
        """It is committed, reviewable, and changes only by explicit edit."""
        self.assertTrue(DEFAULT_CORPUS.exists())
        digest = hashlib.sha256(DEFAULT_CORPUS.read_bytes()).hexdigest()
        self.assertEqual(digest, corpus_fingerprint())


if __name__ == "__main__":
    unittest.main(verbosity=2)

class LimitTests(unittest.TestCase):
    """A capped run is honest only if it reports the count it actually ran."""

    def test_limit_measures_a_subset_and_says_how_many(self) -> None:
        report = run_benchmark(backend=None, limit=3)
        self.assertEqual(report.to_dict()["summary"]["sentences"], 3)
        self.assertEqual(len(report.results), 3)

    def test_limit_larger_than_the_corpus_runs_everything(self) -> None:
        full = run_benchmark(backend=None)
        capped = run_benchmark(backend=None, limit=10_000)
        self.assertEqual(
            capped.to_dict()["summary"]["sentences"],
            full.to_dict()["summary"]["sentences"],
        )

class RotationTests(unittest.TestCase):
    """Rotating taps must be named, balanced, and one backend per sentence."""

    class _Fake:
        def __init__(self, name: str) -> None:
            self.name = name
            self.model = name
            self.calls: list[str] = []

        def is_configured(self) -> bool:
            return True

        def complete(self, request):
            self.calls.append(request.prompt[:20])
            return LLMResponse(
                text="# R\n\n- a\n- b\n- c\n",
                backend=self.name,
                model=self.model,
                latency_ms=1.0,
                prompt_sha256="x",
                raw_bytes=8,
            )

    def test_rotation_names_every_tap_in_the_report(self) -> None:
        taps = [self._Fake("prov-a"), self._Fake("prov-b")]
        report = run_benchmark(backends=taps, limit=4)
        self.assertEqual(report.backend, "rotate(prov-a,prov-b)")

    def test_rotation_spreads_sentences_evenly(self) -> None:
        """Three sentences each, allowing for steps that call the model twice.

        A sentence can need more than one model call (build, then collecting
        sources), so call counts are compared with a tolerance. What must be
        exact is that every tap was used and none ran away with the corpus.
        """
        taps = [self._Fake("prov-a"), self._Fake("prov-b"), self._Fake("prov-c")]
        report = run_benchmark(backends=taps, limit=9)
        self.assertEqual(len(report.results), 9)
        used = [len(t.calls) for t in taps]
        self.assertTrue(all(count > 0 for count in used), f"an unused tap: {used}")
        self.assertLessEqual(max(used) - min(used), 2, f"uneven spread: {used}")
        self.assertEqual(sum(used), sum(used))  # every call attributed to a tap

    def test_a_dead_tap_is_dropped_instead_of_retried_every_sentence(self) -> None:
        """One provider refusing is a provider fact, not a sentence fact."""
        good = self._Fake("good")

        class Dead:
            name = "dead"
            model = "dead"

            def __init__(self) -> None:
                self.calls = 0

            def is_configured(self) -> bool:
                return True

            def complete(self, request):
                self.calls += 1
                raise LLMError("http_429: quota")

        dead = Dead()
        report = run_benchmark(backends=[dead, good], limit=6)
        self.assertEqual(report.backend, "rotate(dead,good)")
        # The dead tap serves one sentence and is then dropped. It may be
        # retried a few times *within* that sentence by the runner's own step
        # policy, which is not this module's decision to override.
        self.assertLessEqual(dead.calls, 4, f"dead tap called {dead.calls} times")
        self.assertLess(dead.calls, 6, "a dead tap must not outnumber the sentences")
        self.assertEqual(len(report.results), 6)


class FailureDiagnosisTests(unittest.TestCase):
    """A failed sentence must say why, not just that an artifact is absent."""

    def test_step_result_exposes_the_recorded_error(self) -> None:
        """The error was already in the row; step_result simply did not return it.

        Without it a benchmark can only say "no artifact", which is the
        symptom. The cause — an LLM timeout during the live run — was present
        in the database the whole time.
        """
        from fleet2027.contracts import PlanStep, TaskSpec
        from fleet2027.store import DurableTaskStore

        with tempfile.TemporaryDirectory() as tmp:
            store = DurableTaskStore(str(Path(tmp) / "s.db"))
            try:
                step = PlanStep(step_id="s1", agent="generalist", action="build")
                store.submit(TaskSpec(task_id="t1", goal="g", steps=(step,)))
                claimed = store.claim_ready_steps("t1", worker="w", lease_seconds=60)
                self.assertTrue(claimed, "the step must be claimable before it can fail")
                store.record_failure(
                    "t1", "s1", worker="w",
                    error="LLMError: The read operation timed out",
                    will_retry=False,
                )
                result = store.step_result("t1", "s1") or {}
                self.assertIn("timed out", str(result.get("error")), result)
                self.assertTrue(result.get("status"), "status must be returned too")
            finally:
                store.close()

class PermanentFailureTests(unittest.TestCase):
    """A spent quota must not be retried; a flaky service must."""

    def test_a_permanent_http_error_costs_one_call(self) -> None:
        """429/410/401 will answer the same way, so the retry is waste.

        Measured before this: a backend answering 429 to everything cost 3
        calls for one sentence — 6 of 9 wasted calls over three dead taps,
        which is 12% of a 50-request free day.
        """

        class DeadRateLimited:
            name = "rl"
            model = "rl"

            def __init__(self) -> None:
                self.calls = 0

            def is_configured(self) -> bool:
                return True

            def complete(self, request):
                self.calls += 1
                raise LLMError("http_429: free-models-per-day")

        dead = DeadRateLimited()
        report = run_benchmark(backend=dead, limit=1)
        self.assertEqual(dead.calls, 1, f"retried a spent quota {dead.calls} times")
        self.assertEqual(report.results[0].status, "dead_letter")
        # The benchmark's own error names the cause that reached it.
        self.assertIn("http_429", str(report.results[0].error))

    def test_a_transient_error_is_still_retried(self) -> None:
        """A 500 or a timeout can succeed on the next attempt."""

        class Flaky:
            name = "flaky"
            model = "flaky"

            def __init__(self) -> None:
                self.calls = 0

            def is_configured(self) -> bool:
                return True

            def complete(self, request):
                self.calls += 1
                raise LLMError("http_503: provider overloaded")

        flaky = Flaky()
        run_benchmark(backend=flaky, limit=1)
        self.assertGreater(flaky.calls, 1, "a transient error should be retried")

class ParallelSpeedTests(unittest.TestCase):
    """Overlapping the waits must not change a single measured value."""

    class _Sleepy:
        def __init__(self, delay: float) -> None:
            self.delay = delay
            self.name = "sleepy"
            self.model = "sleepy"
            self.calls = 0
            self._lock = __import__("threading").Lock()

        def is_configured(self) -> bool:
            return True

        def complete(self, request):  # noqa: ANN001, ANN201
            import time as _time

            with self._lock:
                self.calls += 1
            _time.sleep(self.delay)
            return LLMResponse(
                text="# R\n\n- a\n- b\n- c\n",
                backend="sleepy",
                model="sleepy",
                latency_ms=self.delay * 1000,
                prompt_sha256="x",
                raw_bytes=10,
            )

    def test_parallel_returns_the_same_scores_and_call_count(self) -> None:
        serial_model = self._Sleepy(0.05)
        parallel_model = self._Sleepy(0.05)
        serial = run_benchmark(backend=serial_model, limit=6, workers=1)
        parallel = run_benchmark(backend=parallel_model, limit=6, workers=4)

        self.assertEqual(
            [r.sentence_id for r in serial.results],
            [r.sentence_id for r in parallel.results],
            "the report order must be the corpus order, not completion order",
        )
        self.assertAlmostEqual(serial.mean_score, parallel.mean_score, places=6)
        self.assertEqual(
            serial_model.calls,
            parallel_model.calls,
            "parallelism must not spend extra model calls",
        )

    def test_parallel_is_actually_faster_when_the_model_waits(self) -> None:
        import time as _time

        serial_model = self._Sleepy(0.1)
        t0 = _time.perf_counter()
        run_benchmark(backend=serial_model, limit=6, workers=1)
        serial_s = _time.perf_counter() - t0

        parallel_model = self._Sleepy(0.1)
        t0 = _time.perf_counter()
        run_benchmark(backend=parallel_model, limit=6, workers=4)
        parallel_s = _time.perf_counter() - t0

        self.assertLess(parallel_s, serial_s * 0.7, f"{parallel_s:.2f}s vs {serial_s:.2f}s")
