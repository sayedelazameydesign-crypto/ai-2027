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

import os
import sys
import unittest
import unittest.mock
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




def _load_runner():
    """Import `run_real_model` by path: it lives in scripts, not a package."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "run_real_model_probe", Path(__file__).with_name("run_real_model.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FreeModelPolicyTest(unittest.TestCase):
    """$0 must be decided by code the caller cannot talk around."""

    def test_only_explicitly_free_models_pass(self) -> None:
        runner = _load_runner()
        self.assertTrue(runner.is_free_model("openrouter/free"))
        self.assertTrue(runner.is_free_model("meta-llama/llama-3.3-70b-instruct:free"))
        self.assertFalse(runner.is_free_model("anthropic/claude-sonnet-5.5"))
        self.assertFalse(runner.is_free_model("openai/gpt-4o-mini"))
        self.assertFalse(runner.is_free_model(""))
        self.assertFalse(runner.is_free_model("free-ish-paid-model"))

    def test_paid_model_is_refused_before_any_network_call(self) -> None:
        runner = _load_runner()
        with unittest.mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-fake"}):
            code = runner.main(["--free", "openrouter", "--model", "openai/gpt-4o-mini"])
        self.assertEqual(code, 2)

    def test_missing_key_is_refused_before_any_network_call(self) -> None:
        runner = _load_runner()
        env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
        with unittest.mock.patch.dict("os.environ", env, clear=True):
            code = runner.main(["--free", "openrouter"])
        self.assertEqual(code, 2)

    def test_spend_ok_is_the_only_door_to_a_paid_model(self) -> None:
        runner = _load_runner()
        with unittest.mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-fake"}):
            # No --spend-ok: refused at the policy gate, never reaching the network.
            self.assertEqual(
                runner.main(["--free", "openrouter", "--model", "openai/gpt-4o-mini"]), 2
            )

    def test_each_declared_free_provider_passes_its_default(self) -> None:
        """A free tap is only usable if its own default clears the policy."""
        runner = _load_runner()
        for name, spec in runner.FREE_PROVIDERS.items():
            with self.subTest(provider=name):
                self.assertTrue(
                    runner.is_free_model(spec["default_model"], name),
                    f"{name} default {spec['default_model']!r} must be free",
                )
                self.assertTrue(spec["endpoint"].startswith("https://"))

    def test_a_free_list_does_not_leak_across_providers(self) -> None:
        """Being free on Gemini does not make an id free on OpenRouter."""
        runner = _load_runner()
        self.assertFalse(runner.is_free_model("gemini-2.5-flash", "openrouter"))
        self.assertFalse(runner.is_free_model("openrouter/free", "gemini"))
        self.assertFalse(runner.is_free_model("gemini-2.5-pro", "gemini"))
        self.assertFalse(runner.is_free_model("meta/llama-3.1-8b-instruct", "nvidia"))

    def test_every_provider_has_the_four_declared_keys(self) -> None:
        """A half-declared provider fails at run time, not at review time."""
        runner = _load_runner()
        self.assertGreaterEqual(len(runner.FREE_PROVIDERS), 3)
        for name, spec in runner.FREE_PROVIDERS.items():
            with self.subTest(provider=name):
                for key in ("endpoint", "key_env", "model_env", "default_model"):
                    self.assertIn(key, spec)
                self.assertTrue(spec["key_env"].endswith("_API_KEY"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

class TapOrderTest(unittest.TestCase):
    """--taps must control the order, not merely filter.

    The first version filtered but iterated the provider dict, so
    `--taps gemini,openrouter` still started with openrouter. A run that
    starts with an exhausted quota loses every sentence that depends on it,
    which is exactly what the option exists to prevent.
    """

    def _order(self, taps: str) -> list[str]:
        import importlib

        module = importlib.import_module("run_real_model")
        with unittest.mock.patch.dict(
            os.environ,
            {"OPENROUTER_API_KEY": "x", "GEMINI_API_KEY": "y", "NVIDIA_API_KEY": "z"},
        ), unittest.mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as out:
            try:
                module.main(["--rotate", "--taps", taps, "--limit", "0", "--out", "/tmp/x.json"])
            except SystemExit:
                pass
        line = next(l for l in out.getvalue().splitlines() if "rotation: order=" in l)
        raw = line.split("order=", 1)[1]
        return [part.split(":")[0].strip(" []'") for part in raw.split(",")]

    def test_the_requested_order_is_the_rotation_order(self) -> None:
        self.assertEqual(self._order("gemini,openrouter"), ["gemini", "openrouter"])

    def test_reversing_the_request_reverses_the_rotation(self) -> None:
        self.assertEqual(self._order("openrouter,gemini"), ["openrouter", "gemini"])

    def test_a_single_tap_does_not_rotate(self) -> None:
        self.assertEqual(self._order("gemini"), ["gemini"])
