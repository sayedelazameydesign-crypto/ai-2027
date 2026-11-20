"""Tests for the enforcement mechanism itself.

A guard that cannot fail is a guard nobody should trust. These tests build
synthetic engines in a temporary tree and assert that the guard:
  * fails a real re-implementation,
  * passes a delegating shim,
  * honours the exemption marker (and reports it),
  * detects an adoption regression.

They run against the real `scripts/kernel_guard.py`, not a copy of its logic.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
GUARD = REPO / "scripts" / "kernel_guard.py"


def load_guard():
    """Import the guard module from its file path."""
    spec = importlib.util.spec_from_file_location("kernel_guard_under_test", GUARD)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclasses inspects sys.modules[cls.__module__]; register before executing
    # or every @dataclass in the guard fails to build.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class GuardDetectionTests(unittest.TestCase):
    """The guard must distinguish a copy from a shim — both ways."""

    def setUp(self) -> None:
        """Point the guard at a throwaway repository tree."""
        self.guard = load_guard()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "kernel-2027").mkdir()
        (self.root / "kernel-2027" / "adoption.json").write_text("{}", encoding="utf-8")
        self._orig_root = self.guard.ROOT
        self._orig_baseline = self.guard.BASELINE
        self._orig_roots = self.guard.ENGINE_ROOTS
        self.guard.ROOT = self.root
        self.guard.BASELINE = self.root / "kernel-2027" / "adoption.json"
        self.guard.ENGINE_ROOTS = ("engine-a",)

    def tearDown(self) -> None:
        """Restore the guard's module state."""
        self.guard.ROOT = self._orig_root
        self.guard.BASELINE = self._orig_baseline
        self.guard.ENGINE_ROOTS = self._orig_roots
        self._tmp.cleanup()

    def _write(self, relative: str, source: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        return path

    def test_real_reimplementation_fails(self) -> None:
        """A locally redefined primitive is a failure."""
        path = self._write(
            "engine-a/dup.py",
            "def sha256_text(text):\n    import hashlib\n    return hashlib.sha256(text.encode()).hexdigest()\n",
        )
        applied, _ = self.guard._scan_file(path, set())
        self.assertEqual([finding.primitive for finding in applied], ["hashing/integrity"])

    def test_delegating_shim_passes(self) -> None:
        """A wrapper that calls the kernel is compliance, not duplication."""
        path = self._write(
            "engine-a/shim.py",
            "from kernel2027.contracts import sha256_text as _k\n\n"
            "def sha256_text(text):\n    return _k(text)\n",
        )
        applied, _ = self.guard._scan_file(path, set())
        self.assertEqual(applied, [])

    def test_alias_only_delegation_passes(self) -> None:
        """Delegation through a renamed import must also count."""
        path = self._write(
            "engine-a/shim2.py",
            "from kernel2027.redaction import redact as kernel_redact\n\n"
            "def redact(text):\n    return kernel_redact(text)\n",
        )
        applied, _ = self.guard._scan_file(path, set())
        self.assertEqual(applied, [])

    def test_exemption_is_honoured_and_reported(self) -> None:
        """An exempt copy passes but is surfaced, never hidden."""
        path = self._write(
            "engine-a/exempt.py",
            "# kernel-exempt: persisted row format, changing it would strand data\n"
            "def sha256_text(text):\n    return text\n",
        )
        applied, exemptions = self.guard._scan_file(path, set())
        self.assertEqual(applied, [])
        self.assertEqual(len(exemptions), 1)
        self.assertIn("persisted row format", exemptions[0].reason)

    def test_exemption_requires_the_marker_to_be_adjacent(self) -> None:
        """A marker far away must not excuse an unrelated copy."""
        path = self._write(
            "engine-a/far.py",
            "# kernel-exempt: not about the next function\n"
            "import os\n\n"
            "def sha256_text(text):\n    return text\n",
        )
        applied, _ = self.guard._scan_file(path, set())
        self.assertEqual(len(applied), 1)

    def test_interface_stub_is_not_a_finding(self) -> None:
        """`def require_approval(...): ...` declares intent, it does not duplicate."""
        path = self._write(
            "engine-a/proto.py",
            "class Bridge:\n    def require_approval(self, action):\n        ...\n",
        )
        applied, _ = self.guard._scan_file(path, set())
        self.assertEqual(applied, [])

    def test_baseline_marks_debt_without_failing(self) -> None:
        """Pre-existing debt is visible, not fatal."""
        path = self._write(
            "engine-a/legacy.py",
            "def canonical_json(payload):\n    import json\n    return json.dumps(payload)\n",
        )
        applied, exemptions = self.guard._scan_file(path, {"engine-a/legacy.py"})
        self.assertEqual(applied, [])
        self.assertIn("baseline", exemptions[0].reason)

    def test_adoption_regression_is_a_failure(self) -> None:
        """Once an engine imports the kernel, dropping it must fail the guard."""
        self._write("engine-a/uses_kernel.py", "import kernel2027\n")
        self.guard.BASELINE.write_text(
            '{"engines_importing": ["engine-a"], "accepted_duplicates": []}',
            encoding="utf-8",
        )
        self._write("engine-a/uses_kernel.py", "import os\n")
        report = self.guard.check(verbose=False)
        self.assertTrue(any("regressed" in failure for failure in report.failures))

    def test_adoption_is_detected_from_import(self) -> None:
        """The count the ratchet protects must come from a real import."""
        self._write("engine-a/uses_kernel.py", "from kernel2027 import retry\n")
        report = self.guard.check(verbose=False)
        self.assertIn("engine-a", report.adopting)

    def test_missing_engine_directory_is_ignored(self) -> None:
        """A declared engine that does not exist yet must not crash the guard."""
        self.guard.ENGINE_ROOTS = ("engine-a", "does-not-exist")
        self._write("engine-a/ok.py", "import kernel2027\n")
        report = self.guard.check(verbose=False)
        self.assertEqual(report.failures, [])


class RealRepositoryTests(unittest.TestCase):
    """The repository itself must satisfy the guard it ships."""

    def test_guard_passes_on_this_repository(self) -> None:
        """If this fails, a promoted primitive was re-implemented somewhere."""
        guard = load_guard()
        report = guard.check(verbose=False)
        self.assertEqual(report.failures, [], msg="\n".join(report.failures))

    def test_kernel_is_importable(self) -> None:
        """The guard's advice must be actionable: the kernel must import."""
        if str(REPO / "kernel-2027") not in sys.path:
            sys.path.insert(0, str(REPO / "kernel-2027"))
        import kernel2027  # the import itself is the assertion

        self.assertTrue(kernel2027.KERNEL_VERSION)


if __name__ == "__main__":
    unittest.main(verbosity=2)
