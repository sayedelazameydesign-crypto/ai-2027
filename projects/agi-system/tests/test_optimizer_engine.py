"""Tests for the dependency-free exact integer optimization layer."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

from core.optimizer_engine import (
    AuditLogError,
    InvalidOptimizationProblem,
    OptimizationAuditStore,
    OptimizationProblem,
    OptimizationSolver,
    SolveStatus,
    solve_and_audit,
    verify_solution,
)

FACTORY_MODEL = r"""
{
  "problem_type": "MILP",
  "objective": {
    "sense": "maximize",
    "expression": "300 * x1 + 500 * x2"
  },
  "variables": {
    "x1": {"type": "integer", "lower_bound": 0, "upper_bound": null},
    "x2": {"type": "integer", "lower_bound": 0, "upper_bound": null}
  },
  "constraints": [
    {"id": "machine_hours", "expression": "1 * x1 + 3 * x2 <= 60"},
    {"id": "raw_materials", "expression": "2 * x1 + 1 * x2 <= 50"},
    {"id": "min_order_x1", "expression": "x1 >= 5"}
  ]
}
"""


def factory_problem() -> OptimizationProblem:
    """Return the exact two-product case supplied with this implementation request."""
    return OptimizationProblem.from_json(FACTORY_MODEL)


def one_variable_model(*, upper: int | None = 10, constraints: list[dict] | None = None) -> str:
    payload = {
        "problem_type": "MILP",
        "objective": {"sense": "maximize", "expression": "7 * x"},
        "variables": {"x": {"type": "integer", "lower_bound": 0, "upper_bound": upper}},
        "constraints": constraints or [],
    }
    return json.dumps(payload)


class TestFactoryOptimization(unittest.TestCase):
    def test_returns_the_verified_optimum_and_exact_gap_analysis(self) -> None:
        problem = factory_problem()
        result = OptimizationSolver().solve(problem, baseline={"x1": 5, "x2": 18})

        self.assertIs(result.status, SolveStatus.OPTIMAL)
        self.assertTrue(result.verified)
        self.assertEqual(dict(result.assignment or {}), {"x1": 18, "x2": 14})
        self.assertEqual(result.objective_value, Fraction(12_400))
        self.assertEqual(result.optimality_gap, 0)
        self.assertEqual(result.effective_bounds, {"x1": (0, 25), "x2": (0, 20)})
        self.assertEqual(result.search_space_size, 546)
        self.assertEqual(result.candidates_examined, 546)

        checks = {check.constraint_id: check for check in result.verification.constraint_checks}
        self.assertEqual(checks["machine_hours"].lhs, 60)
        self.assertEqual(checks["machine_hours"].slack, 0)
        self.assertEqual(checks["machine_hours"].utilization, 1)
        self.assertEqual(checks["raw_materials"].lhs, 50)
        self.assertEqual(checks["raw_materials"].slack, 0)
        self.assertEqual(checks["raw_materials"].utilization, 1)
        self.assertEqual(checks["min_order_x1"].surplus, 13)

        comparison = result.baseline_comparison
        self.assertIsNotNone(comparison)
        self.assertTrue(comparison.valid)
        self.assertEqual(comparison.objective_value, 10_500)
        self.assertEqual(comparison.absolute_improvement, 1_900)
        self.assertEqual(comparison.relative_improvement, Fraction(19, 105))

    def test_solution_and_model_hash_are_deterministic(self) -> None:
        problem = factory_problem()
        first = OptimizationSolver().solve(problem)
        second = OptimizationSolver().solve(OptimizationProblem.from_json(FACTORY_MODEL))
        self.assertEqual(first.assignment, second.assignment)
        self.assertEqual(first.objective_value, second.objective_value)
        self.assertEqual(first.model_sha256, second.model_sha256)

    def test_user_baseline_is_validated_but_never_controls_the_optimum(self) -> None:
        result = OptimizationSolver().solve(factory_problem(), baseline={"x1": 5, "x2": 20})
        self.assertTrue(result.verified)
        self.assertEqual(dict(result.assignment or {}), {"x1": 18, "x2": 14})
        self.assertIsNotNone(result.baseline_comparison)
        self.assertFalse(result.baseline_comparison.valid)
        self.assertIsNone(result.baseline_comparison.absolute_improvement)
        self.assertTrue(result.baseline_comparison.issues)

    def test_json_report_is_serializable_and_has_no_float_rounding(self) -> None:
        result = OptimizationSolver().solve(factory_problem(), baseline={"x1": 5, "x2": 18})
        encoded = json.dumps(result.to_dict(), sort_keys=True)
        self.assertIn('"objective_value": 12400', encoded)
        self.assertIn('"relative_improvement": "19/105"', encoded)
        self.assertNotIn("12400.0", encoded)


class TestContractAndFailClosedBehavior(unittest.TestCase):
    def test_rejects_duplicate_json_keys(self) -> None:
        with self.assertRaisesRegex(InvalidOptimizationProblem, "duplicate JSON key"):
            OptimizationProblem.from_json(
                '{"problem_type":"MILP","problem_type":"MILP",'
                '"objective":{},"variables":{},"constraints":[]}'
            )

    def test_rejects_unknown_fields_and_continuous_variables(self) -> None:
        payload = json.loads(FACTORY_MODEL)
        payload["objective"]["python"] = "__import__('os').system('false')"
        with self.assertRaisesRegex(InvalidOptimizationProblem, "unsupported field"):
            OptimizationProblem.from_mapping(payload)

        payload = json.loads(FACTORY_MODEL)
        payload["variables"]["x1"]["type"] = "continuous"
        with self.assertRaisesRegex(InvalidOptimizationProblem, "does not accept continuous"):
            OptimizationProblem.from_mapping(payload)

    def test_rejects_nonlinear_expressions_and_code_without_executing_them(self) -> None:
        with tempfile.TemporaryDirectory(prefix="optimizer-ast-") as temp_dir:
            marker = Path(temp_dir) / "should-not-exist"
            payload = json.loads(FACTORY_MODEL)
            payload["objective"]["expression"] = (
                f"__import__('pathlib').Path({str(marker)!r}).touch()"
            )
            with self.assertRaises(InvalidOptimizationProblem):
                OptimizationProblem.from_mapping(payload)
            self.assertFalse(marker.exists())

        payload = json.loads(FACTORY_MODEL)
        payload["objective"]["expression"] = "x1 * x2"
        with self.assertRaisesRegex(InvalidOptimizationProblem, "nonlinear"):
            OptimizationProblem.from_mapping(payload)

    def test_rejects_negative_bounds_boolean_bounds_and_non_finite_values(self) -> None:
        payload = json.loads(FACTORY_MODEL)
        payload["variables"]["x1"]["lower_bound"] = -1
        with self.assertRaisesRegex(InvalidOptimizationProblem, "non-negative"):
            OptimizationProblem.from_mapping(payload)

        payload = json.loads(FACTORY_MODEL)
        payload["variables"]["x1"]["lower_bound"] = True
        with self.assertRaisesRegex(InvalidOptimizationProblem, "must be an integer"):
            OptimizationProblem.from_mapping(payload)

        with self.assertRaisesRegex(InvalidOptimizationProblem, "non-finite JSON number"):
            OptimizationProblem.from_json(FACTORY_MODEL.replace("\"lower_bound\": 0", '"lower_bound": NaN', 1))

    def test_unbounded_integer_variable_fails_closed_without_an_assignment(self) -> None:
        result = OptimizationSolver().solve(
            OptimizationProblem.from_json(one_variable_model(upper=None))
        )
        self.assertIs(result.status, SolveStatus.BOUND_REQUIRED)
        self.assertFalse(result.verified)
        self.assertIsNone(result.assignment)

    def test_search_limit_never_returns_a_partial_incumbent(self) -> None:
        problem = OptimizationProblem.from_json(one_variable_model(upper=100))
        result = OptimizationSolver(max_candidates=50).solve(problem)
        self.assertIs(result.status, SolveStatus.SEARCH_LIMIT)
        self.assertEqual(result.search_space_size, 101)
        self.assertEqual(result.candidates_examined, 0)
        self.assertIsNone(result.assignment)
        self.assertFalse(result.verified)

    def test_infeasible_minimum_order_is_rejected_before_search(self) -> None:
        problem = OptimizationProblem.from_json(
            one_variable_model(
                upper=None,
                constraints=[
                    {"id": "capacity", "expression": "x <= 4"},
                    {"id": "minimum", "expression": "x >= 5"},
                ],
            )
        )
        result = OptimizationSolver().solve(problem)
        self.assertIs(result.status, SolveStatus.INFEASIBLE)
        self.assertIsNone(result.assignment)
        self.assertEqual(result.candidates_examined, 0)
        self.assertIn("infeasible within variable bounds", result.diagnostics[0])

    def test_direct_problem_cannot_desync_source_and_normalized_model(self) -> None:
        problem = factory_problem()
        bad_objective = replace(
            problem,
            objective_coefficients={"x1": Fraction(1), "x2": Fraction(500)},
        )
        with self.assertRaisesRegex(InvalidOptimizationProblem, "does not match its normalized"):
            OptimizationSolver().solve(bad_objective)

        first_constraint = problem.constraints[0]
        bad_constraint = replace(
            problem,
            constraints=(
                replace(first_constraint, coefficients={"x1": Fraction(1), "x2": Fraction(2)}),
                *problem.constraints[1:],
            ),
        )
        with self.assertRaisesRegex(InvalidOptimizationProblem, "does not match its normalized"):
            verify_solution(bad_constraint, {"x1": 18, "x2": 14})

    def test_post_solve_verifier_rejects_bad_or_tampered_assignments(self) -> None:
        problem = factory_problem()
        for assignment in (
            {"x1": 18, "x2": 15},
            {"x1": 18.0, "x2": 14},
            {"x1": True, "x2": 14},
            {"x1": 18},
            {"x1": 18, "x2": 14, "unexpected": 0},
        ):
            with self.subTest(assignment=assignment):
                self.assertFalse(verify_solution(problem, assignment).passed)

    def test_fractional_coefficients_remain_exact(self) -> None:
        payload = json.loads(one_variable_model(upper=3))
        payload["objective"]["expression"] = "x / 3 + 0.1"
        result = OptimizationSolver().solve(OptimizationProblem.from_mapping(payload))
        self.assertTrue(result.verified)
        self.assertEqual(result.assignment, {"x": 3})
        self.assertEqual(result.objective_value, Fraction(11, 10))

    def test_minimization_and_relation_normalization(self) -> None:
        payload = {
            "problem_type": "MILP",
            "objective": {"sense": "minimize", "expression": "3*x + 2*y + 7"},
            "variables": {
                "x": {"type": "integer", "lower_bound": 0, "upper_bound": 10},
                "y": {"type": "integer", "lower_bound": 0, "upper_bound": 10},
            },
            "constraints": [
                {"id": "demand", "expression": "x + y >= 5"},
                {"id": "balance", "expression": "x - y == 1"},
            ],
        }
        result = OptimizationSolver().solve(OptimizationProblem.from_mapping(payload))
        self.assertIs(result.status, SolveStatus.OPTIMAL)
        self.assertEqual(dict(result.assignment or {}), {"x": 3, "y": 2})
        self.assertEqual(result.objective_value, 20)


class TestOptimizationAuditStore(unittest.TestCase):
    def test_audit_store_is_append_only_and_verifiable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="optimizer-audit-") as temp_dir:
            database = Path(temp_dir) / "audit" / "decisions.sqlite3"
            store = OptimizationAuditStore(database)
            problem = factory_problem()
            recorded_result = solve_and_audit(
                problem,
                store,
                raw_request="Maximize weekly production profit under resource limits.",
                baseline={"x1": 5, "x2": 18},
            )

            self.assertTrue(recorded_result.verified)
            records = store.read_all()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].sequence, 1)
            self.assertEqual(records[0].raw_request, "Maximize weekly production profit under resource limits.")
            self.assertEqual(records[0].problem["normalized"]["objective"]["coefficients"]["x1"], "300")
            self.assertEqual(records[0].result["objective_value"], 12_400)
            self.assertEqual(records[0].result["model_sha256"], problem.model_sha256)
            self.assertTrue(store.verify_chain())

            if os.name == "posix":
                self.assertEqual(database.stat().st_mode & 0o777, 0o600)
            with sqlite3.connect(database) as connection:
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("UPDATE optimizer_runs SET raw_request = 'changed' WHERE sequence = 1")
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("DELETE FROM optimizer_runs WHERE sequence = 1")

    def test_audit_chain_detects_direct_tampering(self) -> None:
        with tempfile.TemporaryDirectory(prefix="optimizer-audit-tamper-") as temp_dir:
            database = Path(temp_dir) / "audit.sqlite3"
            store = OptimizationAuditStore(database)
            problem = factory_problem()
            solve_and_audit(problem, store, raw_request="original")
            with sqlite3.connect(database) as connection:
                connection.execute("DROP TRIGGER optimizer_runs_no_update")
                connection.execute(
                    "UPDATE optimizer_runs SET raw_request = 'tampered' WHERE sequence = 1"
                )
            self.assertFalse(store.verify_chain())

    def test_audit_refuses_unverified_decisions_and_oversized_raw_text(self) -> None:
        with tempfile.TemporaryDirectory(prefix="optimizer-audit-reject-") as temp_dir:
            store = OptimizationAuditStore(Path(temp_dir) / "audit.sqlite3")
            problem = factory_problem()
            result = OptimizationSolver().solve(problem)
            bad_result = result.__class__(
                status=SolveStatus.SEARCH_LIMIT,
                model_sha256=result.model_sha256,
                assignment={"x1": 18, "x2": 14},
            )
            with self.assertRaisesRegex(AuditLogError, "must not carry"):
                store.append(problem, bad_result)
            feasible_baseline = {"x1": 5, "x2": 18}
            baseline_report = verify_solution(problem, feasible_baseline)
            forged_optimum = replace(
                result,
                assignment=feasible_baseline,
                objective_value=baseline_report.objective_value,
                verification=baseline_report,
            )
            self.assertTrue(forged_optimum.verified)
            with self.assertRaisesRegex(AuditLogError, "exhaustive local replay"):
                store.append(problem, forged_optimum)
            with self.assertRaisesRegex(AuditLogError, "byte audit limit"):
                store.append(problem, result, raw_request="x" * 64_001)
            self.assertEqual(store.read_all(), ())


if __name__ == "__main__":
    unittest.main()
