#!/usr/bin/env python3
"""Tests for the Phase 2.1 Operational Evidence Validator (read-only)."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("operational_evidence", ROOT / "scripts" / "operational_evidence.py")
assert SPEC and SPEC.loader
oe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(oe)


class OperationalEvidenceTests(unittest.TestCase):
    def test_unittest_output_parser(self) -> None:
        parsed = oe.parse_unittest("....\nRan 152 tests in 2.9s\n\nOK\n")
        self.assertEqual(parsed["tests"], 152)
        self.assertTrue(parsed["ok"])
        failed = oe.parse_unittest("Ran 3 tests in 0.1s\n\nFAILED (errors=1)\nERROR: x\nModuleNotFoundError: No module named 'pydantic'\n")
        self.assertEqual(failed["failed_summary"], "errors=1")
        self.assertEqual(failed["missing_module"], "pydantic")

    def test_baseline_commit_matches_head_and_blobs(self) -> None:
        baseline = oe.collect_baseline(False)
        self.assertEqual(baseline["head"], baseline["baseline_commit"])
        self.assertEqual(baseline["modified_vs_head"], [])
        self.assertEqual(baseline["removed_vs_head"], [])
        self.assertEqual(baseline["status"], "PASS")

    def test_no_core_or_kernel_changes(self) -> None:
        tree = oe.collect_working_tree()
        self.assertEqual(tree["core_kernel_changes"], [])
        self.assertEqual(tree["deletions"], [])

    def test_real_kernel_suite_executes_and_passes(self) -> None:
        spec = next(s for s in oe.RUN_SPECS if s["id"] == "kernel-2027 unit tests")
        result = oe.run(spec)
        self.assertEqual(result["exit_code"], 0)
        self.assertGreaterEqual(result["unittest"]["tests"] or 0, 100)
        self.assertTrue(result["unittest"]["ok"])
        self.assertEqual(len(result["stdout_sha256"]), 64)

    def test_python2027_suite_is_honestly_blocked(self) -> None:
        spec = next(s for s in oe.RUN_SPECS if s["id"] == "python-2027 protocol unit tests")
        result = oe.run(spec)
        self.assertEqual(result["unittest"]["missing_module"], "pydantic")
        self.assertNotEqual(result["exit_code"], 0)

    def test_functional_before_after_probes_are_equal(self) -> None:
        result = oe.collect_regression()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(result["rows"]), len(oe.PROBES))
        for row in result["rows"]:
            self.assertEqual(row["before_sha256"], row["after_sha256"])

    def test_report_deliverables_are_present_and_additive(self) -> None:
        for path in (ROOT / "docs" / "operational-evidence.json", ROOT / "docs" / "OPERATIONAL-EVIDENCE.md"):
            self.assertTrue(path.exists(), f"missing {path}")
            self.assertGreater(path.stat().st_size, 500)


if __name__ == "__main__":
    unittest.main()
