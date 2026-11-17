#!/usr/bin/env python3
"""Tests for the CI guard itself — because a gate nobody tests is a claim.

Run: python3 scripts/test_ci_guard.py   (stdlib only)
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("ci_guard", HERE / "ci_guard.py")
guard = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(guard)


def quiet(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*args, **kwargs)


def payload(p50=8.0, deps=None):
    def stats(v):
        return {"n": 50, "min_ms": v / 2, "p50_ms": v, "p95_ms": v * 1.5,
                "max_ms": v * 2, "mean_ms": v, "spread_pct": 100.0}
    return {
        "environment": {"python": "3.11.2", "platform": "Linux-test"},
        "kernel": {
            "verified_create_file": stats(p50),
            "denied_traversal": stats(3.1),
            "failed_read": stats(6.2),
            "peak_rss_growth_kb_over_70_runs": 636,
        },
        "footprint": {"kernel_cold_import_ms": 42.5, "core_source_loc": 3526,
                      "core_source_kb": 124.2,
                      "third_party_imports": deps or "NONE (stdlib only)"},
        "http": {"run_goal_end_to_end": stats(10.0)},
    }


class TestImportsGate(unittest.TestCase):
    def test_real_kernel_passes(self):
        self.assertEqual(quiet(guard.scan_imports), 0)

    def test_python_310_stdlib_without_tomllib_still_passes(self):
        """Regression for PR #10: the gate failed on 3.10 only."""
        reduced = frozenset(set(sys.stdlib_module_names) - {"tomllib"})
        with patch.object(sys, "stdlib_module_names", reduced):
            self.assertEqual(quiet(guard.scan_imports), 0)

    def test_a_real_third_party_import_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            core = root / "core"
            live = root / "live"
            core.mkdir(); live.mkdir()
            (core / "evil.py").write_text("import requests\n", encoding="utf-8")
            (live / "server.py").write_text("import json\n", encoding="utf-8")
            (live / "bench.py").write_text("import ast\n", encoding="utf-8")
            with patch.object(guard, "ROOT", root), \
                 patch.object(guard, "KERNEL_DIR", core), \
                 patch.object(guard, "LIVE_DIR", live):
                self.assertEqual(quiet(guard.scan_imports), 1)


class TestBenchmarkGate(unittest.TestCase):
    def run_guard(self, data, ceiling=40.0):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bench.json"
            p.write_text(json.dumps(data), encoding="utf-8")
            return quiet(guard.check_benchmarks, p, ceiling)

    def test_valid_payload_passes(self):
        self.assertEqual(self.run_guard(payload()), 0)

    def test_catastrophic_regression_fails(self):
        self.assertEqual(self.run_guard(payload(p50=99.0)), 1)

    def test_third_party_import_fails(self):
        self.assertEqual(self.run_guard(payload(deps=["requests"])), 1)

    def test_missing_key_fails(self):
        data = payload(); del data["kernel"]["verified_create_file"]
        self.assertEqual(self.run_guard(data), 1)

    def test_implausible_stats_fail(self):
        data = payload(); data["kernel"]["verified_create_file"]["p50_ms"] = 0
        self.assertEqual(self.run_guard(data), 1)

    def test_jitter_is_not_confused_with_an_sla(self):
        # 12.1 ms (a real re-run) must pass the ceiling but 7.5 ms is not asserted.
        self.assertEqual(self.run_guard(payload(p50=12.1)), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
