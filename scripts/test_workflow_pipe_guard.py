#!/usr/bin/env python3
"""Tests for workflow_pipe_guard: it must catch a real case and clear a safe one."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import workflow_pipe_guard as guard

UNGUARDED = """\
name: t
on: workflow_dispatch
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - name: boot
        run: ./scripts/boot.sh --json | tee /tmp/up.json
"""

GUARDED_PROLOGUE = """\
name: t
on: workflow_dispatch
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - name: boot
        run: |
          set -euo pipefail
          ./scripts/boot.sh --json | tee /tmp/up.json
"""

GUARDED_PIPESTATUS = """\
name: t
on: workflow_dispatch
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - name: measure
        run: |
          cmd | tee /tmp/x.log
          rc=${PIPESTATUS[0]}
"""

GUARDED_OPTIONAL = """\
name: t
on: workflow_dispatch
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - name: sure
        run: echo hi | tee /tmp/x.log || true
"""


class PipeGuardTests(unittest.TestCase):
    def _scan(self, text: str) -> list[tuple[int, str]]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "w.yml"
            path.write_text(text, encoding="utf-8")
            return guard.scan(path)

    def test_an_unguarded_tee_is_a_finding(self) -> None:
        findings = self._scan(UNGUARDED)
        self.assertEqual(len(findings), 1, findings)
        self.assertIn("tee", findings[0][1])

    def test_a_step_that_sets_pipefail_is_clean(self) -> None:
        self.assertEqual(self._scan(GUARDED_PROLOGUE), [])

    def test_reading_PIPESTATUS_is_clean(self) -> None:
        self.assertEqual(self._scan(GUARDED_PIPESTATUS), [])

    def test_an_explicit_optional_command_is_clean(self) -> None:
        self.assertEqual(self._scan(GUARDED_OPTIONAL), [])

    def test_the_real_repository_passes(self) -> None:
        """The gate must pass on the checkout it runs in, or CI cannot use it."""
        root = Path(__file__).resolve().parent.parent
        workflows = root / ".github" / "workflows"
        for path in sorted(workflows.glob("*.yml")):
            self.assertEqual(guard.scan(path), [], f"{path.name} has an unguarded tee")


if __name__ == "__main__":
    unittest.main(verbosity=2)
