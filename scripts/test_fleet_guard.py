"""Regression tests for the Fleet import boundary."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import fleet_guard


class RelativeImportPolicyTests(unittest.TestCase):
    def test_ops_is_allowed_only_as_a_package_relative_import(self) -> None:
        with tempfile.TemporaryDirectory(dir=fleet_guard.ROOT) as tmp:
            package = Path(tmp)
            candidate = package / "candidate.py"
            with patch.object(fleet_guard, "PACKAGE", package):
                candidate.write_text("from .ops import AwrOps\n", encoding="utf-8")
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    self.assertEqual(fleet_guard.check_imports(), 0)

                candidate.write_text("import ops\n", encoding="utf-8")
                errors = io.StringIO()
                with redirect_stdout(io.StringIO()), redirect_stderr(errors):
                    self.assertEqual(fleet_guard.check_imports(), 1)
                self.assertIn("ops", errors.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
