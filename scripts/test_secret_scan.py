#!/usr/bin/env python3
"""Tests for the pre-commit secret gate — it must catch keys and spare fixtures.

Run: python3 scripts/test_secret_scan.py -v
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import secret_scan as ss  # noqa: E402

# Built at import time so this file never contains a pattern-valid string twice.
GOOGLE = "AIza" + "Sy" + "A" * 33
AWS = "AKIA" + "Q" * 16
GH = "ghp_" + "z" * 36
OPENAI = "sk-" + "Q" * 32
SLACK = "xoxb-" + "123456789012"
STRIPE = "sk_live_" + "Q" * 20
JWT = "eyJ" + "a" * 20 + ".eyJ" + "b" * 20 + "." + "c" * 10
PEM = "-----BEGIN RSA PRIVATE KEY-----"  # allowlist-secret (fixture under test)


class DetectionTests(unittest.TestCase):
    def test_01_each_high_confidence_shape_is_caught(self):
        cases = {"google_api_key": GOOGLE, "aws_access_key": AWS,
                 "github_token": GH, "openai_key": OPENAI, "slack_token": SLACK,
                 "stripe_live_key": STRIPE, "jwt": JWT,
                 "private_key_block": PEM}
        for rule, value in cases.items():
            found = ss.scan_text(f"x = '{value}'\n", "f.txt")
            self.assertTrue(any(f.rule == rule for f in found),
                            f"{rule} not detected in {found}")
            self.assertTrue(all(f.severity == "high" for f in found))

    def test_02_the_found_value_is_masked_never_echoed(self):
        found = ss.scan_text(f"KEY={GOOGLE}\n", "f.txt")
        self.assertTrue(found)
        self.assertNotIn(GOOGLE, found[0].as_text())
        self.assertIn("chars", found[0].masked)
        self.assertLess(len(found[0].masked), len(GOOGLE))

    def test_03_line_numbers_point_at_the_real_line(self):
        text = "safe\nsafe\n" + f"K={AWS}\n" + "safe\n"
        found = ss.scan_text(text, "f.txt")
        self.assertEqual([f.line for f in found], [3])

    def test_04_documented_fixtures_are_allowed(self):
        """SECURITY.md allows well-known fake values; the gate must honour it."""
        for value in ("AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE1",
                      "ghp_thisIsAFakeToken1234",
                      "AIzaSyExampleNotARealKey0000000000000000"):
            self.assertEqual(ss.scan_text(f'k = "{value}"', "f.txt"), [],
                             f"{value} should be recognised as a fixture")

    def test_05_placeholders_and_redactions_are_not_secrets(self):
        text = "\n".join([
            'api_key = "REDACTED-rotate-this-key-see-AUDIT.md"',
            'client_secret = "YOUR-SECRET-HERE-000000000000000000"',
            'token = "changeme-changeme-changeme-1234"',
            'password = "${DB_PASSWORD_FROM_ENV_00000000}"',
        ])
        self.assertEqual(ss.scan_text(text, "f.txt"), [])

    def test_06_a_generic_match_is_a_warning_not_a_blocker(self):
        found = ss.scan_text('api_key = "q7Zr2Lm9Xp4Kd8Tv1Nc6Hb3Wj5"', "f.txt")  # allowlist-secret
        self.assertTrue(found)
        self.assertEqual({f.severity for f in found}, {"warn"})

    def test_07_low_entropy_lookalikes_are_ignored(self):
        found = ss.scan_text('api_key = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"', "f.txt")
        self.assertEqual(found, [])

    def test_08_an_inline_allowlist_marker_wins(self):
        text = f'k = "{GOOGLE}"  # allowlist-secret (fixture)\n'
        self.assertEqual(ss.scan_text(text, "f.txt"), [])

    def test_09_forbidden_files_are_refused_by_name(self):
        self.assertIsNotNone(ss.forbidden_path("apps/site/.env"))
        self.assertIsNotNone(ss.forbidden_path("deploy/env.local"))
        self.assertIsNotNone(ss.forbidden_path("certs/server.key"))
        # a template is the correct way to ship config
        self.assertIsNone(ss.forbidden_path("apps/site/.env.example"))
        self.assertIsNone(ss.forbidden_path("docs/env.md"))

    def test_10_binary_and_oversized_content_is_skipped(self):
        self.assertEqual(ss.scan_bytes(b"\x00\x01" + GOOGLE.encode()), [])
        self.assertEqual(ss.scan_bytes(b"A" * (ss.MAX_BYTES + 1)), [])
        self.assertEqual(ss.scan_bytes(b"\xff\xfe invalid utf8 " + GOOGLE.encode()), [])

    def test_11_scope_keeps_the_archive_out_of_the_blocking_path(self):
        self.assertTrue(ss.in_scope("live-agent/server.py", "code"))
        self.assertFalse(ss.in_scope("projects/cela2027/app.py", "code"))
        self.assertTrue(ss.in_scope("projects/cela2027/app.py", "archive"))
        self.assertFalse(ss.in_scope("live-agent/server.py", "archive"))
        self.assertTrue(ss.in_scope("projects/cela2027/app.py", "all"))
        self.assertFalse(ss.in_scope("node_modules/x/index.js", "all"))


class GitIntegrationTests(unittest.TestCase):
    """The hook path itself: what `git commit` would actually see."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        if not shutil.which("git"):
            self.skipTest("git not available")
        self._git("init", "-q")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "t")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _git(self, *args):
        return subprocess.run(["git", *args], cwd=self.tmp, capture_output=True,
                              text=True, check=True)

    def _stage(self, name: str, content: str):
        path = self.tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self._git("add", name)

    def test_20_a_staged_google_key_blocks_the_commit(self):
        self._stage("config.js", f'const key = "{GOOGLE}";\n')
        self.assertEqual(ss.main(["--staged", "--repo", str(self.tmp), "--json"]), 1)

    def test_21_a_clean_staged_change_passes(self):
        self._stage("app.py", "print('hello')\n")
        self.assertEqual(ss.main(["--staged", "--repo", str(self.tmp), "--json"]), 0)

    def test_22_a_staged_env_file_is_refused_even_when_empty_of_keys(self):
        self._stage(".env", "API_URL=http://localhost\n")
        self.assertEqual(ss.main(["--staged", "--repo", str(self.tmp), "--json"]), 1)

    def test_23_unstaged_files_are_not_scanned_by_the_hook_mode(self):
        (self.tmp / "leak.py").write_text(f'k = "{GOOGLE}"\n', encoding="utf-8")
        self.assertEqual(ss.main(["--staged", "--repo", str(self.tmp)]), 0)

    def test_24_json_output_names_the_file_and_hides_the_secret(self):
        self._stage("a/b.py", f'TOKEN = "{GH}"\n')
        out = ss.main(["--staged", "--repo", str(self.tmp), "--json"])
        self.assertEqual(out, 1)
        # capture the payload main() prints
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ss.main(["--staged", "--repo", str(self.tmp), "--json"])
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["blocking"], 1)
        self.assertEqual(payload["findings"][0]["path"], "a/b.py")
        self.assertNotIn(GH, buf.getvalue())


class HookFileTests(unittest.TestCase):
    def test_30_the_committed_hook_calls_this_scanner(self):
        hook = HERE.parent / ".githooks" / "pre-commit"
        self.assertTrue(hook.is_file(), "pre-commit hook is missing")
        body = hook.read_text(encoding="utf-8")
        self.assertIn("secret_scan.py", body)
        self.assertIn("--staged", body)
        if os.name != "nt":
            self.assertTrue(hook.stat().st_mode & 0o111, "hook is not executable")


if __name__ == "__main__":
    unittest.main(verbosity=2)
