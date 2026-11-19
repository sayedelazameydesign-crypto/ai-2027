#!/usr/bin/env python3
"""Unit tests for the PAP-2027 gap audit gate (stdlib only).

    python3 scripts/test_pap_gap_audit.py -v
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "pap_gap_audit.py"
sys.path.insert(0, str(ROOT / "scripts"))

import pap_gap_audit as audit_mod  # noqa: E402  (path set above)


def _write(root: Path, rel: str, body: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")


def _catalog(engine: dict) -> dict:
    return {"protocol": "PAP-2027", "engines": [engine]}


class CatalogueShapeTests(unittest.TestCase):
    def test_shipped_catalogue_has_twelve_unique_engines(self) -> None:
        catalog = audit_mod.load_catalog(audit_mod.CATALOG)
        ids = [entry["id"] for entry in catalog["engines"]]
        self.assertEqual(len(ids), 12)
        self.assertEqual(len(set(ids)), 12)
        self.assertIn("agent-fleet", ids)

    def test_statuses_are_from_the_published_legend(self) -> None:
        catalog = audit_mod.load_catalog(audit_mod.CATALOG)
        statuses = {entry["status"] for entry in catalog["engines"]}
        self.assertTrue(statuses <= set(audit_mod.VALID_STATUSES))

    def test_duplicate_ids_are_rejected(self) -> None:
        entry = {"id": "x", "status": "absent", "evidence": [], "tests": []}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "catalog.json"
            path.write_text(json.dumps({"engines": [entry, entry]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                audit_mod.load_catalog(path)

    def test_unknown_status_is_rejected(self) -> None:
        entry = {"id": "x", "status": "maybe", "evidence": [], "tests": []}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "catalog.json"
            path.write_text(json.dumps({"engines": [entry]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                audit_mod.load_catalog(path)


class DriftDetectionTests(unittest.TestCase):
    def test_real_repository_has_no_drift(self) -> None:
        catalog = audit_mod.load_catalog(audit_mod.CATALOG)
        report = audit_mod.audit(catalog, ROOT)
        self.assertEqual(report.problems, [])
        self.assertTrue(report.ok)
        self.assertGreaterEqual(len(report.engines), 12)

    def test_missing_evidence_file_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "code/thing.py", "x = 1\n")
            _write(root, "tests/test_thing.py", "def test_ok():\n    pass\n")
            catalog = _catalog(
                {
                    "id": "e1",
                    "status": "present",
                    "evidence": ["code/thing.py", "code/gone.py"],
                    "tests": [{"file": "tests/test_thing.py", "marker": "def test_"}],
                }
            )
            report = audit_mod.audit(catalog, root)
            self.assertFalse(report.ok)
            self.assertTrue(any("missing evidence file code/gone.py" in p for p in report.problems))

    def test_missing_test_marker_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "code/thing.py", "x = 1\n")
            _write(root, "tests/test_thing.py", "def check_not_a_test():\n    pass\n")
            catalog = _catalog(
                {
                    "id": "e1",
                    "status": "present",
                    "evidence": ["code/thing.py"],
                    "tests": [{"file": "tests/test_thing.py", "marker": "def test_"}],
                }
            )
            report = audit_mod.audit(catalog, root)
            self.assertFalse(report.ok)
            self.assertTrue(any("has no 'def test_' test" in p for p in report.problems))

    def test_present_engine_without_tests_is_a_problem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "code/thing.py", "x = 1\n")
            catalog = _catalog(
                {
                    "id": "e1",
                    "status": "present",
                    "evidence": ["code/thing.py"],
                    "tests": [],
                }
            )
            report = audit_mod.audit(catalog, root)
            self.assertTrue(any("status 'present' needs evidence files and tests" in p for p in report.problems))

    def test_absent_engine_must_not_carry_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "code/thing.py", "x = 1\n")
            catalog = _catalog(
                {
                    "id": "e1",
                    "status": "absent",
                    "evidence": ["code/thing.py"],
                    "tests": [],
                }
            )
            report = audit_mod.audit(catalog, root)
            self.assertTrue(any("must not list evidence" in p for p in report.problems))

    def test_test_cases_are_counted_from_the_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "code/thing.py", "x = 1\n")
            _write(
                root,
                "tests/test_thing.py",
                "def test_a():\n    pass\n\ndef test_b():\n    pass\n",
            )
            catalog = _catalog(
                {
                    "id": "e1",
                    "status": "present",
                    "evidence": ["code/thing.py"],
                    "tests": [{"file": "tests/test_thing.py", "marker": "def test_"}],
                }
            )
            report = audit_mod.audit(catalog, root)
            self.assertEqual(report.engines[0].test_cases, 2)


class CommandLineTests(unittest.TestCase):
    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    def test_assert_passes_on_the_real_repository(self) -> None:
        result = self._run("--assert")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("drift problems=0", result.stdout)

    def test_json_output_is_parseable_and_reports_engines(self) -> None:
        result = self._run("--json")
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["engines"]), 12)
        self.assertEqual(payload["problems"], [])

    def test_assert_fails_on_a_broken_catalogue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "catalog.json"
            path.write_text(
                json.dumps(
                    _catalog(
                        {
                            "id": "broken",
                            "status": "present",
                            "evidence": ["nope/missing.py"],
                            "tests": [],
                        }
                    )
                ),
                encoding="utf-8",
            )
            result = self._run("--catalog", str(path), "--assert")
            self.assertEqual(result.returncode, 1)
            self.assertIn("missing evidence file nope/missing.py", result.stdout)

    def test_unreadable_catalogue_exits_two(self) -> None:
        result = self._run("--catalog", "docs/does-not-exist.json", "--assert")
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
