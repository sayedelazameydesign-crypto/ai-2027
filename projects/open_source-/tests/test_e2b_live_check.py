"""Hermetic tests for the opt-in E2B live smoke wrapper."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import e2b_live_check as live  # noqa: E402
from os_tools.code_exec import E2BBackend  # noqa: E402


class FakeSandbox:
    killed = False

    def run_code(self, code: str):
        assert code == live.SMOKE_CODE
        return SimpleNamespace(
            logs=SimpleNamespace(stdout=["42\n"]),
            error=None,
            text=None,
        )

    def kill(self) -> None:
        type(self).killed = True


class E2BLiveCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeSandbox.killed = False

    def test_fixed_smoke_code_runs_and_sandbox_is_killed(self) -> None:
        result = live.check(
            env={"E2B_API_KEY": "unit-test-e2b-key"},
            backend_factory=lambda: E2BBackend(sandbox_factory=FakeSandbox),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["expected_output_matched"])
        self.assertTrue(FakeSandbox.killed)
        self.assertNotIn("unit-test-e2b-key", json.dumps(result))

    def test_missing_key_skips_without_creating_a_backend(self) -> None:
        calls = []
        result = live.check(
            env={},
            backend_factory=lambda: calls.append("created"),
        )
        self.assertEqual(result["error_kind"], "no_key")
        self.assertEqual(result["credential"], "absent")
        self.assertEqual(calls, [])

    def test_backend_errors_are_not_copied_into_the_report(self) -> None:
        key = "unit-test-e2b-key"

        class ErrorSandbox(FakeSandbox):
            def run_code(self, code: str):
                return SimpleNamespace(
                    logs=SimpleNamespace(stdout=[]),
                    error=SimpleNamespace(name="Error", value=key),
                    text=None,
                )

        result = live.check(
            env={"E2B_API_KEY": key},
            backend_factory=lambda: E2BBackend(sandbox_factory=ErrorSandbox),
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], "sandbox_failed")
        self.assertNotIn(key, json.dumps(result))
        self.assertTrue(ErrorSandbox.killed)

    def test_json_cli_never_prints_secret_or_execution_error(self) -> None:
        key = "unit-test-e2b-key"
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = live.main(
                ["--json"],
                env={"E2B_API_KEY": key},
                backend_factory=lambda: E2BBackend(sandbox_factory=FakeSandbox),
            )
        self.assertEqual(exit_code, 0)
        self.assertNotIn(key, output.getvalue())
        self.assertTrue(json.loads(output.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
