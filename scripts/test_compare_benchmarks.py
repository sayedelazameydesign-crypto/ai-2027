"""Tests for the comparison tool.

The dangerous failure here is not a crash — it is a confident verdict built on
two numbers that were never comparable. Every test below pins one way that
could happen:

* different corpora (different questions),
* different sentence counts (a dropped failure looks like progress),
* a difference so small it is noise (0.01 is not an improvement).

The tool must refuse, or say "indistinguishable". It must never manufacture a
winner.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from compare_benchmarks import NOISE, compare


def report(
    *,
    mean: float,
    backend: str = "none",
    called: bool = False,
    corpus: str = "a" * 64,
    sentences: int = 24,
    usable: float = 0.0,
    domains: dict[str, float] | None = None,
) -> dict:
    """Build a minimal report shaped like the real one."""
    return {
        "backend": backend,
        "model_called": called,
        "corpus_sha256": corpus,
        "summary": {
            "sentences": sentences,
            "mean_score": mean,
            "usable_rate": usable,
            "completion_rate": 1.0,
            "by_domain": domains or {"website": mean},
        },
        "results": [],
    }


class ComparabilityTests(unittest.TestCase):
    """Refuse to compare what cannot be compared."""

    def test_different_corpora_are_refused(self) -> None:
        code, lines = compare(
            report(mean=0.5, corpus="a" * 64), report(mean=0.9, corpus="b" * 64)
        )
        self.assertEqual(code, 1)
        self.assertIn("different corpora", " ".join(lines))

    def test_different_sentence_counts_are_refused(self) -> None:
        """24 vs 20 could be four dropped failures, which inflates the mean."""
        code, lines = compare(
            report(mean=0.5, sentences=24), report(mean=0.9, sentences=20)
        )
        self.assertEqual(code, 1)
        self.assertIn("sentence counts", " ".join(lines))

    def test_the_same_report_compares_cleanly(self) -> None:
        code, lines = compare(report(mean=0.5783), report(mean=0.5783))
        self.assertEqual(code, 0)
        self.assertIn("indistinguishable", " ".join(lines))


class VerdictTests(unittest.TestCase):
    """A verdict must follow the numbers, not the other way round."""

    def test_a_large_improvement_is_called_better(self) -> None:
        code, lines = compare(report(mean=0.5783), report(mean=0.8082, backend="ollama",
                                                        called=True))
        self.assertEqual(code, 0)
        self.assertIn("BETTER", " ".join(lines))

    def test_a_small_difference_is_noise_not_an_improvement(self) -> None:
        """0.01 over 24 sentences is which sentences landed well, not a better model."""
        code, lines = compare(report(mean=0.50), report(mean=0.50 + NOISE / 2))
        self.assertEqual(code, 0)
        self.assertIn("indistinguishable", " ".join(lines))

    def test_a_regression_fails(self) -> None:
        code, lines = compare(report(mean=0.80), report(mean=0.60))
        self.assertEqual(code, 1)
        self.assertIn("WORSE", " ".join(lines))

    def test_a_domain_regression_is_visible_even_when_the_mean_rises(self) -> None:
        """Overall gains can hide a domain going backwards; that is the point of the table."""
        before = report(mean=0.60, domains={"website": 0.80, "travel": 0.40})
        after = report(mean=0.70, domains={"website": 0.85, "travel": 0.20})
        code, lines = compare(before, after)
        self.assertEqual(code, 1, "a collapsing domain was hidden by the average")
        joined = " ".join(lines)
        self.assertIn("WORSE", joined)
        self.assertIn("travel", joined)


class ReportingTests(unittest.TestCase):
    """The comparison must carry the provenance of both sides."""

    def test_both_backends_are_stated(self) -> None:
        _code, lines = compare(
            report(mean=0.5), report(mean=0.9, backend="ollama", called=True)
        )
        joined = " ".join(lines)
        self.assertIn("backend=none", joined)
        self.assertIn("backend=ollama", joined)

    def test_the_noise_band_is_declared(self) -> None:
        self.assertGreater(NOISE, 0.0)
        self.assertLess(NOISE, 0.10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
