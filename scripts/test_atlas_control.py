#!/usr/bin/env python3
"""Tests for the additive Atlas Control Center (stdlib only, read-only)."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("atlas_control", HERE / "atlas_control.py")
assert SPEC and SPEC.loader
control = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(control)


class ControlTests(unittest.TestCase):
    def test_snapshot_is_git_backed(self) -> None:
        snapshot = control.build_snapshot()
        self.assertGreater(len(snapshot["blobs"]), 1000)
        self.assertIn(".github/workflows/ci.yml", snapshot["blobs"])
        self.assertGreater(snapshot["counts"]["files"], 1000)

    def test_evidence_validator_rejects_nothing_in_audited_atlas(self) -> None:
        result = control.module_validate(control.atlas_payload())
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(result["rows"]), 0)
        self.assertGreater(result["summary"][1]["value"], 0)  # strong claims exist

    def test_ci_ignores_local_reusable_workflows(self) -> None:
        files = control.git_files()
        result = control.module_ci(files, json.loads(control.INVENTORY.read_text(encoding="utf-8")))
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(sum(r["unpinned"] for r in result["rows"]), 0)

    def test_all_seven_modules_are_present_and_report_is_self_contained(self) -> None:
        report = json.loads(control.REPORT_JSON.read_text(encoding="utf-8"))
        self.assertEqual(set(report["modules"]), {"scan", "validator", "architecture", "security", "ci", "progress", "regression"})
        page = control.OUT.read_text(encoding="utf-8")
        self.assertIn("const REPORT = ", page)
        self.assertIn("data:font/woff2;base64,", page)
        self.assertNotIn("__REPORT_DATA__", page)
        self.assertNotIn("__AR_FONT__", page)


if __name__ == "__main__":
    unittest.main()
