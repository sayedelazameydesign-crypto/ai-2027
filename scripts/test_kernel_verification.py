#!/usr/bin/env python3
"""Regression tests for issue #54: ``verify=False`` must never mean VERIFIED.

The kernel in ``projects/agi-system`` is imported IN PLACE (the archive is never
copied). These tests live in our own tree, following the same rule as
``scripts/test_policy_hardening.py``: the mirror keeps the one-file fix (deviation
D-3) and its regression suite stays in the hub.

What is being pinned:

  * a successful action with ``KernelConfig(verify=False)`` ends as
    ``INCONCLUSIVE`` with an explicit reason — never ``VERIFIED``;
  * that run produces no evidence block, because nothing independent ran: a
    verdict without proof must not carry proof-shaped output;
  * the default configuration is untouched: the verifier still runs, still
    passes, and ``VERIFIED`` still requires its checks;
  * a failing verifier cannot be softened into ``VERIFIED``;
  * the memory admission gate refuses an ``INCONCLUSIVE`` record (the downstream
    guard, tested so it cannot regress into "close enough to verified").

    python3 scripts/test_kernel_verification.py -v
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KERNEL = ROOT / "projects" / "agi-system"
sys.path.insert(0, str(KERNEL))

core_mod = importlib.import_module("core")
executor_mod = importlib.import_module("core.executor")
models_mod = importlib.import_module("core.models")

Goal = core_mod.Goal
Kernel = core_mod.Kernel
KernelConfig = core_mod.KernelConfig
Executor = executor_mod.Executor
Evidence = models_mod.Evidence
ExecStatus = models_mod.ExecStatus


def _load_memory_store():
    """Import memory/store.py by path — it is a script, not an installed module."""
    spec = importlib.util.spec_from_file_location(
        "hub_memory_store", ROOT / "memory" / "store.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclasses resolve ``__future__`` annotations through sys.modules
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FailingVerifier:
    """A verifier that proves the world does not match the intent."""

    def verify(self, action, observation) -> Evidence:
        return Evidence(
            action_id=action.action_id,
            capability=action.capability,
            checks={"claimed_ok": bool(observation.ok), "hash_matches": False},
            details={"capability": action.capability, "reason": "stub: mismatch"},
        )


class CrashingExecutor(Executor):
    """Drops the process mid-run, the way a SIGKILL would."""

    def execute(self, action, timeout=None):
        raise KeyboardInterrupt("simulated crash")


class KernelVerificationCase(unittest.TestCase):
    """Each test gets its own sandbox, ledger and kernel."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.sandbox = self.root / "sandbox"
        self.ledger = self.root / "ledger.jsonl"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def kernel(self, **kwargs) -> Kernel:
        config = kwargs.pop("config", None) or KernelConfig()
        return Kernel(self.sandbox, self.ledger, config=config, **kwargs)

    def run_write(self, kernel: Kernel, name: str = "fixture.txt"):
        return kernel.run(
            Goal("create_file", {"path": name, "content": "local fixture"})
        )


class TestDisabledVerificationIsNotVerification(KernelVerificationCase):
    def test_disabled_verification_never_reports_verified(self) -> None:
        """The exact reproduction from #54, now inverted."""
        kernel = self.kernel(config=KernelConfig(verify=False))
        record = self.run_write(kernel)
        self.assertIsNot(
            record.status,
            ExecStatus.VERIFIED,
            "verify=False produced VERIFIED — that is the bug in issue #54",
        )

    def test_disabled_verification_reports_inconclusive_with_reason(self) -> None:
        kernel = self.kernel(config=KernelConfig(verify=False))
        record = self.run_write(kernel)
        self.assertIs(record.status, ExecStatus.INCONCLUSIVE)
        self.assertEqual(record.error_code, "VERIFICATION_DISABLED")
        self.assertIn("verification", (record.error or "").lower())
        self.assertFalse(record.verified)

    def test_disabled_verification_produces_no_evidence(self) -> None:
        """No independent check ran, so no evidence block may imply one did."""
        kernel = self.kernel(config=KernelConfig(verify=False))
        record = self.run_write(kernel)
        self.assertEqual(record.evidence, [])
        self.assertFalse(record.evidence_passed)

    def test_disabled_verification_still_executes_the_action(self) -> None:
        """The mode is still useful for smoke runs: execution itself is unchanged."""
        kernel = self.kernel(config=KernelConfig(verify=False))
        self.run_write(kernel, "smoke.txt")
        self.assertEqual((self.sandbox / "smoke.txt").read_text(), "local fixture")

    def test_ledger_records_the_unverified_verdict(self) -> None:
        kernel = self.kernel(config=KernelConfig(verify=False))
        record = self.run_write(kernel)
        replayed = kernel.replay().runs[record.run_id]
        self.assertEqual(replayed["record"]["status"], ExecStatus.INCONCLUSIVE.value)
        self.assertEqual(replayed["record"]["error_code"], "VERIFICATION_DISABLED")

    def test_resumed_run_cannot_be_verified_without_verification(self) -> None:
        """The resume path drives the same code; it must not launder INCONCLUSIVE."""
        crashing = Kernel(
            self.sandbox,
            self.ledger,
            config=KernelConfig(verify=False),
            executor=CrashingExecutor(self.sandbox),
        )
        goal = Goal("create_file", {"path": "resume.txt", "content": "resumed"})
        with self.assertRaises(KeyboardInterrupt):
            crashing.run(goal, run_id="run-verification-disabled")
        self.assertNotIn("record", crashing.replay().runs["run-verification-disabled"])

        # a restarted process: same sandbox and ledger, a working executor
        restarted = self.kernel(config=KernelConfig(verify=False))
        record = restarted.resume("run-verification-disabled")
        self.assertNotIn(record.status, (ExecStatus.VERIFIED,))
        self.assertIs(record.status, ExecStatus.INCONCLUSIVE)
        self.assertEqual((self.sandbox / "resume.txt").read_text(), "resumed")


class TestTheVerifiedPathStillWorks(KernelVerificationCase):
    def test_default_config_verifies_with_independent_evidence(self) -> None:
        """Positive control: the fix must not weaken ordinary verification."""
        kernel = self.kernel()
        record = self.run_write(kernel)
        self.assertIs(record.status, ExecStatus.VERIFIED)
        self.assertTrue(record.verified)
        self.assertEqual(len(record.evidence), 1)
        checks = record.evidence[0].checks
        for name in ("claimed_ok", "file_exists", "correct_path", "correct_content",
                     "hash_matches"):
            self.assertTrue(checks.get(name), f"verifier check {name!r} did not pass")
        self.assertTrue(record.evidence[0].passed)

    def test_failing_verifier_is_never_promoted_to_verified(self) -> None:
        """A run that executed fine but failed the checks stays unverified."""
        kernel = self.kernel(verifier=FailingVerifier())
        record = self.run_write(kernel, "mismatch.txt")
        self.assertIsNot(record.status, ExecStatus.VERIFIED)
        self.assertIs(record.status, ExecStatus.INCONCLUSIVE)
        self.assertEqual(record.error_code, "VERIFICATION_FAILED")

    def test_denied_action_is_never_promoted_to_verified(self) -> None:
        """A policy refusal ends DENIED, exactly as before the fix."""
        kernel = self.kernel()
        record = kernel.run(Goal("create_file", {"path": "../escape.txt", "content": "x"}))
        self.assertIs(record.status, ExecStatus.DENIED)
        self.assertEqual(record.error_code, "POLICY_VIOLATION")


class TestMemoryDoesNotAcceptInconclusiveAsCompletion(KernelVerificationCase):
    def test_memory_refuses_a_record_from_disabled_verification(self) -> None:
        store = _load_memory_store()
        kernel = self.kernel(config=KernelConfig(verify=False))
        record = self.run_write(kernel)
        verdict = store.admit(record.to_dict(), sandbox_root=self.root)
        self.assertFalse(verdict.admitted)
        self.assertIn("INCONCLUSIVE", verdict.reason)

    def test_memory_still_admits_a_genuinely_verified_record(self) -> None:
        """Control: the gate is not simply refusing everything after the fix."""
        store = _load_memory_store()
        kernel = self.kernel()
        record = self.run_write(kernel, "admitted.txt")
        verdict = store.admit(record.to_dict(), sandbox_root=self.root)
        self.assertTrue(verdict.admitted, verdict.reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
