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
