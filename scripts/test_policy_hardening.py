#!/usr/bin/env python3
"""Regression tests for the 2026-11-17 cross-platform containment fix.

The kernel lives in projects/agi-system and is imported IN PLACE (the archive is
never copied). These tests live in our own tree so the fix is pinned without
adding another deviation to the byte-mirror.

What is being pinned:

  * inputs that mean a SEPARATOR on Windows (or after a URL-decode) are refused
    on the running platform — Linux — instead of being accepted as literal names;
  * inputs that are literal filenames on every platform still work, so the fix
    cannot silently turn into "deny everything";
  * the sandbox invariant itself (nothing resolves outside) still holds.

    python3 scripts/test_policy_hardening.py -v
"""
from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KERNEL = ROOT / "projects" / "agi-system"
sys.path.insert(0, str(KERNEL))

policy_mod = importlib.import_module("core.policy")
PolicyEngine = policy_mod.PolicyEngine

#: Must be refused: backslash, Windows drive prefix, percent-encoded separators.
HOSTILE = [
    "..\\escape.txt",
    "..\\..\\escape.txt",
    "a\\..\\..\\x.txt",
    "C:\\Windows\\win.ini",
    "D:\\secret.txt",
    "C:/Windows/../x.txt",
    "\\\\srv\\share\\x.txt",
    "%2e%2e/x.txt",
    "%2e%2e%2fx.txt",
    "..%2fx.txt",
    "..%00/x.txt",
    "a/..\\b.txt",
    "\\..\\x",
    "C:\\..\\..\\escape",
    "\\\\localhost\\c$\\x",
]

#: Literal POSIX names: they resolve INSIDE the sandbox on any OS, so they are
#: allowed (denying them would break legitimate work and prove nothing).
SAFE_LITERALS = [
    "NUL",
    "CON",
    "...",
    "....",
    "..;",
    "%252e%252e",
    "report\\u005fnot-a-separator".replace("\\u005f", "_"),
]

#: Classic traversal that must always fail.
TRAVERSAL = ["../x", "../../etc/passwd", "/etc/passwd", "/tmp/xx", "a/../../.."]


class ContainmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sandbox = Path(self._tmp.name) / "sandbox"
        self.sandbox.mkdir()
        self.policy = PolicyEngine(self.sandbox)

    def test_hostile_inputs_are_refused(self) -> None:
        for p in HOSTILE:
            with self.subTest(path=p):
                resolved, err = self.policy.contain(p)
                self.assertIsNone(resolved, f"{p!r} must not resolve")
                self.assertIsNotNone(err, f"{p!r} must carry a reason")
                self.assertIn("cross-platform", err or "")

    def test_safe_literals_still_resolve_inside(self) -> None:
        for p in SAFE_LITERALS:
            with self.subTest(path=p):
                resolved, err = self.policy.contain(p)
                self.assertIsNone(err, f"{p!r} should be allowed: {err}")
                assert resolved is not None
                self.assertTrue(
                    str(resolved).startswith(str(self.sandbox.resolve())),
                    f"{p!r} resolved outside the sandbox")

    def test_traversal_always_refused(self) -> None:
        for p in TRAVERSAL:
            with self.subTest(path=p):
                resolved, err = self.policy.contain(p)
                self.assertIsNone(resolved)
                self.assertIsNotNone(err)

    def test_ordinary_paths_are_unaffected(self) -> None:
        for p in ["notes.txt", "sub/dir/file.md", "./a.txt", "deep/nested/x.json"]:
            with self.subTest(path=p):
                resolved, err = self.policy.contain(p)
                self.assertIsNone(err, f"{p!r} must keep working: {err}")
                self.assertIsNotNone(resolved)

    def test_reason_names_the_offending_character(self) -> None:
        _, err = self.policy.contain("a\\b")
        self.assertIn("backslash", err or "")
        _, err = self.policy.contain("C:foo")
        self.assertIn("drive", err or "")
        _, err = self.policy.contain("%2f")
        self.assertIn("%2f", err or "")

    def test_no_escape_ever_reaches_disk(self) -> None:
        """End-to-end: a refused path must not create a file anywhere."""
        outside = Path(self._tmp.name) / "pwned.txt"
        for p in [f"../{outside.name}", f"..\\{outside.name}", "%2e%2e/" + outside.name]:
            resolved, _ = self.policy.contain(p)
            self.assertIsNone(resolved)
        self.assertFalse(outside.exists(), "containment must never write outside")


if __name__ == "__main__":
    unittest.main(verbosity=2)
