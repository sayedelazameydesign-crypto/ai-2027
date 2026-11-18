#!/usr/bin/env python3
"""Tests for the workflow secret audit — static, names only, no values.

Run: python3 scripts/test_workflow_secret_audit.py -v
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import workflow_secret_audit as wsa  # noqa: E402


class ReferenceTests(unittest.TestCase):
    def test_01_an_env_assignment_is_the_safe_shape(self):
        refs = wsa.secret_refs("        API_KEY: ${{ secrets.MY_KEY }}\n")
        self.assertEqual(len(refs), 1)
        self.assertTrue(refs[0]["safe"])
        self.assertEqual(refs[0]["name"], "MY_KEY")

    def test_02_interpolation_into_script_text_is_unsafe(self):
        line = '          run: curl -H "K: ${{ secrets.MY_KEY }}" https://x\n'
        refs = wsa.secret_refs(line)
        self.assertFalse(refs[0]["safe"])

    def test_03_an_if_condition_is_allowed(self):
        refs = wsa.secret_refs("    if: ${{ secrets.MY_KEY != '' }}\n")
        self.assertTrue(refs[0]["safe"])

    def test_04_commented_references_are_ignored(self):
        self.assertEqual(wsa.secret_refs("#   KEY: ${{ secrets.OLD }}\n"), [])

    def test_05_line_numbers_and_multiple_references(self):
        text = ("a\n"
                "  A: ${{ secrets.ONE }}\n"
                "  B: ${{ secrets.TWO }} ${{ secrets.THREE }}\n")
        refs = wsa.secret_refs(text)
        self.assertEqual([r["line"] for r in refs], [2, 3, 3])
        self.assertEqual([r["name"] for r in refs], ["ONE", "TWO", "THREE"])

    def test_06_an_unrelated_expression_is_not_a_secret(self):
        self.assertEqual(wsa.secret_refs("  X: ${{ github.sha }}\n"), [])


class AuditTests(unittest.TestCase):
    def _audit(self, workflow_text: str, doc_text: str):
        with tempfile.TemporaryDirectory() as tmp:
            wf = Path(tmp) / "workflows"
            wf.mkdir()
            (wf / "x.yml").write_text(workflow_text, encoding="utf-8")
            doc = Path(tmp) / "SECRETS.md"
            doc.write_text(doc_text, encoding="utf-8")
            return wsa.audit(workflows_dir=wf, secrets_doc=doc)

    def test_10_a_documented_env_secret_passes(self):
        result = self._audit("        K: ${{ secrets.MY_KEY }}\n", "uses `MY_KEY`")
        self.assertTrue(result["ok"], result["findings"])
        self.assertEqual(result["secrets_used"]["MY_KEY"], ["x.yml:1"])

    def test_11_an_undocumented_secret_fails(self):
        result = self._audit("        K: ${{ secrets.MY_KEY }}\n", "nothing here")
        self.assertFalse(result["ok"])
        self.assertEqual(result["findings"][0]["rule"], "undocumented_secret")

    def test_12_an_inline_secret_fails_even_when_documented(self):
        text = '          run: echo "${{ secrets.MY_KEY }}"\n'
        result = self._audit(text, "`MY_KEY`")
        self.assertFalse(result["ok"])
        rules = {f["rule"] for f in result["findings"]}
        self.assertEqual(rules, {"inline_secret"})

    def test_13_a_missing_doc_file_documents_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            wf = Path(tmp) / "workflows"
            wf.mkdir()
            (wf / "x.yml").write_text("  K: ${{ secrets.MY_KEY }}\n", encoding="utf-8")
            result = wsa.audit(workflows_dir=wf,
                               secrets_doc=Path(tmp) / "absent.md")
        self.assertFalse(result["ok"])

    def test_14_an_empty_workflow_directory_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = wsa.audit(workflows_dir=Path(tmp) / "nope",
                               secrets_doc=Path(tmp) / "nope.md")
        self.assertTrue(result["ok"])
        self.assertEqual(result["workflows_scanned"], 0)

    def test_15_the_result_is_json_serialisable(self):
        result = self._audit("  K: ${{ secrets.MY_KEY }}\n", "`MY_KEY`")
        json.loads(json.dumps(result))


class RealRepositoryTests(unittest.TestCase):
    """The check that keeps the actual workflows honest as they change."""

    def test_20_every_secret_in_the_real_workflows_is_documented_and_env_assigned(self):
        result = wsa.audit()
        self.assertTrue(result["ok"], json.dumps(result["findings"], indent=2))
        self.assertIn("GEMINI_API_KEY", result["secrets_used"])
        self.assertIn("RENDER_API_KEY", result["secrets_used"])

    def test_21_the_audit_never_reads_a_secret_value(self):
        """It can only see names — proof: the source has no env-value reader."""
        src = (HERE / "workflow_secret_audit.py").read_text(encoding="utf-8")
        self.assertNotIn("os.environ", src)
        # a secret reference is matched by name only, never resolved
        result = wsa.audit()
        self.assertTrue(all("value" not in f for f in result["findings"]))

    def test_22_assert_mode_exits_one_on_a_violation(self):
        original = wsa.audit
        wsa.audit = lambda *a, **k: {"ok": False, "workflows_scanned": 1,
                                     "secrets_used": {},
                                     "findings": [{"file": "x.yml", "line": 1,
                                                   "rule": "inline_secret",
                                                   "detail": "…"}]}
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(wsa.main(["--assert"]), 1)
            self.assertIn("FAIL", buf.getvalue())
        finally:
            wsa.audit = original

    def test_23_assert_mode_exits_zero_when_clean(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(wsa.main(["--assert"]), 0)
        self.assertIn("env-assigned and documented", buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
