"""Verifier tests: fabricated evidence must fail, real evidence must pass.

The point of these tests is falsifiability — every "passes" case has a paired
"refuses" case where the *only* difference is whether the claim matches disk.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from fleet2027 import (  # noqa: E402
    ArtifactRef,
    EvidenceKind,
    PlanStep,
    StepResult,
    VerificationPolicy,
    aggregate_report,
    resolve_artifact,
    sha256_file,
    sha256_text,
    verify_step,
)
from fleet2027.verifier import PathEscape  # noqa: E402


def _step(*evidence: EvidenceKind, **inputs) -> PlanStep:
    return PlanStep(
        step_id="s1",
        agent="writer",
        action="write",
        verification=tuple(item.value for item in evidence),
        inputs=inputs,
    )


class VerifierTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.file = self.root / "out" / "report.txt"
        self.file.parent.mkdir(parents=True)
        self.file.write_text("verified content\n", encoding="utf-8")
        self.digest = sha256_file(str(self.file))

    def tearDown(self) -> None:
        self.tmp.cleanup()


class ArtifactTests(VerifierTestCase):
    def test_matching_digest_passes(self) -> None:
        result = StepResult(
            ok=True,
            summary="wrote",
            artifacts=(ArtifactRef(path="out/report.txt", sha256=self.digest),),
        )
        report = verify_step(_step(EvidenceKind.ARTIFACT_SHA256), result, workspace_root=self.root)
        self.assertTrue(report.ok, report.failures)

    def test_fabricated_digest_is_refused(self) -> None:
        result = StepResult(
            ok=True,
            summary="wrote",
            artifacts=(ArtifactRef(path="out/report.txt", sha256="0" * 64),),
        )
        report = verify_step(_step(EvidenceKind.ARTIFACT_SHA256), result, workspace_root=self.root)
        self.assertFalse(report.ok)
        self.assertTrue(any("digest on disk differs" in f for f in report.failures))

    def test_a_file_that_does_not_exist_is_refused(self) -> None:
        result = StepResult(
            ok=True,
            summary="wrote",
            artifacts=(ArtifactRef(path="out/ghost.txt", sha256=self.digest),),
        )
        report = verify_step(_step(EvidenceKind.ARTIFACT_EXISTS), result, workspace_root=self.root)
        self.assertFalse(report.ok)
        self.assertTrue(any("does not exist" in f for f in report.failures))

    def test_the_file_changing_after_the_claim_is_detected(self) -> None:
        result = StepResult(
            ok=True,
            summary="wrote",
            artifacts=(ArtifactRef(path="out/report.txt", sha256=self.digest),),
        )
        self.file.write_text("tampered after the fact\n", encoding="utf-8")
        report = verify_step(_step(EvidenceKind.ARTIFACT_SHA256), result, workspace_root=self.root)
        self.assertFalse(report.ok)

    def test_traversal_outside_the_workspace_is_refused(self) -> None:
        outside = Path(self.tmp.name).parent / "outside.txt"
        outside.write_text("x", encoding="utf-8")
        with self.assertRaises(PathEscape):
            resolve_artifact(self.root, "../outside.txt", VerificationPolicy())

    def test_absolute_paths_are_refused_for_portability(self) -> None:
        with self.assertRaises(PathEscape):
            resolve_artifact(self.root, str(self.file), VerificationPolicy())

    def test_absolute_paths_can_be_allowed_explicitly(self) -> None:
        policy = VerificationPolicy(allow_absolute_artifact_paths=True)
        self.assertEqual(resolve_artifact(self.root, str(self.file), policy), self.file.resolve())

    def test_artifacts_are_checked_even_when_nothing_is_declared(self) -> None:
        result = StepResult(
            ok=True,
            summary="wrote",
            artifacts=(ArtifactRef(path="out/report.txt", sha256="f" * 64),),
        )
        report = verify_step(_step(), result, workspace_root=self.root)
        self.assertFalse(report.ok, "an undeclared claim must not be a free pass")


class OutputAndExitTests(VerifierTestCase):
    def test_empty_output_fails(self) -> None:
        report = verify_step(
            _step(EvidenceKind.OUTPUT_NON_EMPTY),
            StepResult(ok=True, summary="", output="   "),
            workspace_root=self.root,
        )
        self.assertFalse(report.ok)

    def test_non_empty_output_passes(self) -> None:
        report = verify_step(
            _step(EvidenceKind.OUTPUT_NON_EMPTY),
            StepResult(ok=True, summary="", output="something real"),
            workspace_root=self.root,
        )
        self.assertTrue(report.ok, report.failures)

    def test_exit_code_must_be_the_integer_zero(self) -> None:
        good = verify_step(
            _step(EvidenceKind.EXIT_CODE_ZERO),
            StepResult(ok=True, output="ok", evidence={"exit_code": 0}),
            workspace_root=self.root,
        )
        self.assertTrue(good.ok, good.failures)
        for bad_code in (1, None, False):
            report = verify_step(
                _step(EvidenceKind.EXIT_CODE_ZERO),
                StepResult(ok=True, output="ok", evidence={"exit_code": bad_code}),
                workspace_root=self.root,
            )
            self.assertFalse(report.ok, f"exit_code={bad_code!r} must fail")

    def test_tests_must_have_run_and_none_may_fail(self) -> None:
        cases = {
            (0, 0): False,
            (2, 1): False,
            (2, 0): True,
            (None, None): False,
        }
        for (run, failed), expected in cases.items():
            report = verify_step(
                _step(EvidenceKind.TEST_PASSED),
                StepResult(
                    ok=True, output="x", evidence={"tests_run": run, "tests_failed": failed}
                ),
                workspace_root=self.root,
            )
            self.assertEqual(report.ok, expected, f"run={run} failed={failed}")


class SchemaTests(VerifierTestCase):
    def test_json_schema_subset(self) -> None:
        schema = {
            "type": "object",
            "required": ["sum"],
            "properties": {"sum": {"type": "integer"}},
        }
        good = verify_step(
            _step(EvidenceKind.JSON_SCHEMA, json_schema=schema),
            StepResult(ok=True, output="x", evidence={"json": {"sum": 5}}),
            workspace_root=self.root,
        )
        self.assertTrue(good.ok, good.failures)
        missing = verify_step(
            _step(EvidenceKind.JSON_SCHEMA, json_schema=schema),
            StepResult(ok=True, output="x", evidence={"json": {"total": 5}}),
            workspace_root=self.root,
        )
        self.assertFalse(missing.ok)
        wrong_type = verify_step(
            _step(EvidenceKind.JSON_SCHEMA, json_schema=schema),
            StepResult(ok=True, output="x", evidence={"json": {"sum": "five"}}),
            workspace_root=self.root,
        )
        self.assertFalse(wrong_type.ok)


class IndependentReviewTests(VerifierTestCase):
    def test_missing_reviewer_cannot_pass(self) -> None:
        report = verify_step(
            _step(EvidenceKind.INDEPENDENT_REVIEW),
            StepResult(ok=True, output="done"),
            workspace_root=self.root,
        )
        self.assertFalse(report.ok)
        self.assertIn("no reviewer configured", " ".join(report.failures))

    def test_reviewer_verdict_is_honoured(self) -> None:
        report = verify_step(
            _step(EvidenceKind.INDEPENDENT_REVIEW),
            StepResult(ok=True, output="done"),
            workspace_root=self.root,
            reviewer=lambda step, result: (True, "ok"),
        )
        self.assertTrue(report.ok, report.failures)
        rejected = verify_step(
            _step(EvidenceKind.INDEPENDENT_REVIEW),
            StepResult(ok=True, output="done"),
            workspace_root=self.root,
            reviewer=lambda step, result: (False, "not good enough"),
        )
        self.assertFalse(rejected.ok)


class AggregateTests(VerifierTestCase):
    def test_progress_ratio_is_computed_from_verdicts(self) -> None:
        verified, unverified, ratio = aggregate_report(
            {
                "a": verify_step(
                    _step(EvidenceKind.OUTPUT_NON_EMPTY),
                    StepResult(ok=True, output="x"),
                    workspace_root=self.root,
                ),
                "b": verify_step(
                    _step(EvidenceKind.OUTPUT_NON_EMPTY),
                    StepResult(ok=True, output=""),
                    workspace_root=self.root,
                ),
            }
        )
        self.assertEqual((verified, unverified), (1, 1))
        self.assertAlmostEqual(ratio, 0.5)

    def test_deterministic_hash_helper(self) -> None:
        self.assertEqual(sha256_text("abc"), sha256_text("abc"))
        self.assertNotEqual(sha256_text("abc"), sha256_text("abd"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
