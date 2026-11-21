"""Exact, dependency-free optimization for bounded integer decision models.

The LLM-facing boundary is data-only: a small linear-expression grammar is
parsed with :mod:`ast`, but no Python expression is ever evaluated. The v1
solver exhaustively checks a provably finite integer domain using ``Fraction``
arithmetic, independently verifies the winning assignment, and returns no
usable assignment when model bounds, search limits, or verification are unsafe.

This is intentionally a small-model solver, not a replacement for a scalable
MILP backend. Large domains fail closed with ``SEARCH_LIMIT``.
"""

from __future__ import annotations

import ast
import hashlib
import json
import keyword
import math
import os
import re
import sqlite3
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from fractions import Fraction
from itertools import product
from pathlib import Path
from types import MappingProxyType
from typing import Any

SCHEMA_VERSION = "1.0"
MAX_MODEL_BYTES = 256_000
MAX_EXPRESSION_LENGTH = 2_048
MAX_AST_NODES = 256
MAX_VARIABLES = 12
MAX_CONSTRAINTS = 128
MAX_BOUND = 10**12
MAX_NUMERATOR = 10**15
MAX_DENOMINATOR = 10**12
DEFAULT_MAX_CANDIDATES = 250_000
MAX_ALLOWED_CANDIDATES = 5_000_000
MAX_CONSTRAINT_EVALUATIONS = 25_000_000
MAX_AUDIT_REQUEST_BYTES = 64_000
GENESIS_HASH = "0" * 64

_VARIABLE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,31}\Z")
_CONSTRAINT_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}\Z")
_NUMBER_TOKEN = re.compile(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")


class OptimizationError(ValueError):
    """Base class for invalid optimization inputs and unsafe execution."""


class InvalidOptimizationProblem(OptimizationError):
    """The JSON contract is malformed, unsupported, or unsafe to interpret."""


class AuditLogError(RuntimeError):
    """The durable audit record could not be written or verified."""


class ObjectiveDirection(str, Enum):
    """Supported linear objective directions."""

    MAXIMIZE = "maximize"
    MINIMIZE = "minimize"


class ConstraintRelation(str, Enum):
    """Supported linear comparison operators."""

    LE = "<="
    GE = ">="
    EQ = "=="


class SolveStatus(str, Enum):
    """Truthful solver outcomes; only OPTIMAL carries a verified decision."""

    OPTIMAL = "OPTIMAL"
    INFEASIBLE = "INFEASIBLE"
    SEARCH_LIMIT = "SEARCH_LIMIT"
    BOUND_REQUIRED = "BOUND_REQUIRED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


@dataclass(frozen=True)
class DecisionVariable:
    """A non-negative integer variable with an optional explicit upper bound."""

    name: str
    lower_bound: int
    upper_bound: int | None
    kind: str = "integer"


@dataclass(frozen=True)
class LinearConstraint:
    """A normalized linear constraint plus the source expression for audit."""

    constraint_id: str
    coefficients: Mapping[str, Fraction]
    relation: ConstraintRelation
    rhs: Fraction
    source_expression: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "coefficients", MappingProxyType(dict(self.coefficients)))


@dataclass(frozen=True)
class OptimizationProblem:
    """Validated MILP contract for a bounded, non-negative integer model."""

    name: str
    objective_direction: ObjectiveDirection
    objective_expression: str
    objective_coefficients: Mapping[str, Fraction]
    objective_constant: Fraction
    variables: tuple[DecisionVariable, ...]
    constraints: tuple[LinearConstraint, ...]
    problem_type: str = "MILP"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "objective_coefficients", MappingProxyType(dict(self.objective_coefficients))
        )
        object.__setattr__(self, "variables", tuple(self.variables))
        object.__setattr__(self, "constraints", tuple(self.constraints))

    @classmethod
    def from_json(cls, raw_json: str) -> OptimizationProblem:
        """Parse strict JSON, rejecting duplicate keys and non-finite numbers."""
        if not isinstance(raw_json, str):
            raise InvalidOptimizationProblem("model JSON must be text")
        try:
            raw_size = len(raw_json.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise InvalidOptimizationProblem("model JSON is not valid UTF-8 text") from exc
        if raw_size > MAX_MODEL_BYTES:
            raise InvalidOptimizationProblem(
                f"model JSON exceeds the {MAX_MODEL_BYTES}-byte limit"
            )
        try:
            payload = json.loads(
                raw_json,
                parse_float=Decimal,
                parse_constant=_reject_json_constant,
                object_pairs_hook=_unique_object,
            )
        except InvalidOptimizationProblem:
            raise
        except (json.JSONDecodeError, UnicodeError, ValueError, RecursionError, MemoryError) as exc:
            raise InvalidOptimizationProblem(f"invalid model JSON: {exc}") from exc
        return cls.from_mapping(payload)

    @classmethod
    def from_mapping(cls, payload: Any) -> OptimizationProblem:
        """Validate and parse the strict, expression-based v1 JSON contract."""
        root = _require_object(payload, "model")
        _check_keys(
            root,
            required={"problem_type", "objective", "variables", "constraints"},
            optional={"name", "schema_version"},
            context="model",
        )
        problem_type = _require_string(root["problem_type"], "problem_type")
        if problem_type != "MILP":
            raise InvalidOptimizationProblem("problem_type must be 'MILP' in v1")
        schema_version = root.get("schema_version", SCHEMA_VERSION)
        if schema_version != SCHEMA_VERSION:
            raise InvalidOptimizationProblem(
                f"unsupported schema_version {schema_version!r}; expected {SCHEMA_VERSION!r}"
            )
        name = root.get("name", "unnamed-optimization")
        name = _require_string(name, "name", max_length=120)
        if any(ord(char) < 32 for char in name):
            raise InvalidOptimizationProblem("name must not contain control characters")

        objective_payload = _require_object(root["objective"], "objective")
        _check_keys(
            objective_payload,
            required={"sense", "expression"},
            optional=set(),
            context="objective",
        )
        try:
            direction = ObjectiveDirection(
                _require_string(objective_payload["sense"], "objective.sense")
            )
        except ValueError as exc:
            raise InvalidOptimizationProblem(
                "objective.sense must be 'maximize' or 'minimize'"
            ) from exc
        objective_expression = _require_expression(
            objective_payload["expression"], "objective.expression"
        )

        variable_payload = _require_object(root["variables"], "variables")
        if not 1 <= len(variable_payload) <= MAX_VARIABLES:
            raise InvalidOptimizationProblem(
                f"variables must contain between 1 and {MAX_VARIABLES} entries"
            )
        variables: list[DecisionVariable] = []
        for variable_name, raw_variable in variable_payload.items():
            _validate_variable_name(variable_name)
            variable = _require_object(raw_variable, f"variables.{variable_name}")
            _check_keys(
                variable,
                required={"type", "lower_bound", "upper_bound"},
                optional=set(),
                context=f"variables.{variable_name}",
            )
            kind = _require_string(variable["type"], f"variables.{variable_name}.type")
            if kind != "integer":
                raise InvalidOptimizationProblem(
                    f"variables.{variable_name}.type must be 'integer'; v1 does not accept continuous variables"
                )
            lower = _require_integer(
                variable["lower_bound"], f"variables.{variable_name}.lower_bound"
            )
            if lower < 0:
                raise InvalidOptimizationProblem(
                    f"variables.{variable_name}.lower_bound must be non-negative"
                )
            if lower > MAX_BOUND:
                raise InvalidOptimizationProblem(
                    f"variables.{variable_name}.lower_bound exceeds the supported bound"
                )
            raw_upper = variable["upper_bound"]
            upper = (
                None
                if raw_upper is None
                else _require_integer(raw_upper, f"variables.{variable_name}.upper_bound")
            )
            if upper is not None and (upper < lower or upper > MAX_BOUND):
                raise InvalidOptimizationProblem(
                    f"variables.{variable_name}.upper_bound must be >= lower_bound and <= {MAX_BOUND}"
                )
            variables.append(DecisionVariable(variable_name, lower, upper, kind))

        variable_names = {item.name for item in variables}
        objective_form = _parse_linear_expression(
            objective_expression, variable_names, "objective.expression"
        )
        constraints_payload = _require_array(root["constraints"], "constraints")
        if len(constraints_payload) > MAX_CONSTRAINTS:
            raise InvalidOptimizationProblem(
                f"constraints exceeds the {MAX_CONSTRAINTS}-entry limit"
            )
        constraints: list[LinearConstraint] = []
        constraint_ids: set[str] = set()
        for index, raw_constraint in enumerate(constraints_payload):
            context = f"constraints[{index}]"
            constraint = _require_object(raw_constraint, context)
            _check_keys(
                constraint,
                required={"id", "expression"},
                optional=set(),
                context=context,
            )
            constraint_id = _require_string(
                constraint["id"], f"{context}.id", max_length=64
            )
            if not _CONSTRAINT_ID.fullmatch(constraint_id):
                raise InvalidOptimizationProblem(
                    f"{context}.id must start with a letter and contain only letters, digits, '_', '.', or '-'"
                )
            if constraint_id in constraint_ids:
                raise InvalidOptimizationProblem(
                    f"duplicate constraint id {constraint_id!r}"
                )
            constraint_ids.add(constraint_id)
            expression = _require_expression(
                constraint["expression"], f"{context}.expression"
            )
            relation, coefficients, rhs = _parse_constraint_expression(
                expression, variable_names, f"{context}.expression"
            )
            constraints.append(
                LinearConstraint(
                    constraint_id=constraint_id,
                    coefficients=coefficients,
                    relation=relation,
                    rhs=rhs,
                    source_expression=expression,
                )
            )

        problem = cls(
            name=name,
            objective_direction=direction,
            objective_expression=objective_expression,
            objective_coefficients=objective_form.coefficients,
            objective_constant=objective_form.constant,
            variables=tuple(variables),
            constraints=tuple(constraints),
            problem_type=problem_type,
            schema_version=schema_version,
        )
        problem.validate()
        return problem

    def validate(self) -> OptimizationProblem:
        """Re-check invariants, including for instances built without JSON."""
        if self.problem_type != "MILP" or self.schema_version != SCHEMA_VERSION:
            raise InvalidOptimizationProblem("unsupported problem type or schema version")
        if not isinstance(self.objective_direction, ObjectiveDirection):
            raise InvalidOptimizationProblem("objective direction is invalid")
        _require_string(self.name, "name", max_length=120)
        if any(ord(char) < 32 for char in self.name):
            raise InvalidOptimizationProblem("name must not contain control characters")
        _require_expression(self.objective_expression, "objective.expression")
        if not 1 <= len(self.variables) <= MAX_VARIABLES:
            raise InvalidOptimizationProblem("variable count is outside supported limits")
        names: set[str] = set()
        for variable in self.variables:
            if not isinstance(variable, DecisionVariable):
                raise InvalidOptimizationProblem("variables must be DecisionVariable values")
            _validate_variable_name(variable.name)
            if variable.name in names:
                raise InvalidOptimizationProblem(f"duplicate variable {variable.name!r}")
            names.add(variable.name)
            if variable.kind != "integer":
                raise InvalidOptimizationProblem("only integer variables are supported")
            if type(variable.lower_bound) is not int or variable.lower_bound < 0:
                raise InvalidOptimizationProblem(
                    f"variable {variable.name!r} must have an integer non-negative lower bound"
                )
            if variable.lower_bound > MAX_BOUND:
                raise InvalidOptimizationProblem(f"variable {variable.name!r} bound is too large")
            if variable.upper_bound is not None and (
                type(variable.upper_bound) is not int
                or not variable.lower_bound <= variable.upper_bound <= MAX_BOUND
            ):
                raise InvalidOptimizationProblem(
                    f"variable {variable.name!r} upper bound is invalid"
                )

        if not isinstance(self.objective_coefficients, Mapping):
            raise InvalidOptimizationProblem("objective coefficients must be a mapping")
        normalized_objective: dict[str, Fraction] = {}
        for variable_name, coefficient in self.objective_coefficients.items():
            if variable_name not in names:
                raise InvalidOptimizationProblem(
                    f"objective references undeclared variable {variable_name!r}"
                )
            normalized_objective[variable_name] = _as_fraction(
                coefficient, f"objective coefficient for {variable_name}"
            )
        objective_constant = _as_fraction(self.objective_constant, "objective constant")
        parsed_objective = _parse_linear_expression(
            self.objective_expression, names, "objective.expression"
        )
        if (
            dict(parsed_objective.coefficients) != normalized_objective
            or parsed_objective.constant != objective_constant
        ):
            raise InvalidOptimizationProblem(
                "objective source expression does not match its normalized coefficients"
            )
        if len(self.constraints) > MAX_CONSTRAINTS:
            raise InvalidOptimizationProblem("constraint count exceeds supported limits")
        constraint_ids: set[str] = set()
        for constraint in self.constraints:
            if not isinstance(constraint, LinearConstraint):
                raise InvalidOptimizationProblem("constraints must be LinearConstraint values")
            _require_string(constraint.constraint_id, "constraint id", max_length=64)
            if not _CONSTRAINT_ID.fullmatch(constraint.constraint_id):
                raise InvalidOptimizationProblem("constraint id is invalid")
            if constraint.constraint_id in constraint_ids:
                raise InvalidOptimizationProblem(
                    f"duplicate constraint id {constraint.constraint_id!r}"
                )
            constraint_ids.add(constraint.constraint_id)
            if not isinstance(constraint.relation, ConstraintRelation):
                raise InvalidOptimizationProblem(
                    f"constraint {constraint.constraint_id!r} relation is invalid"
                )
            rhs = _as_fraction(constraint.rhs, f"constraint {constraint.constraint_id} RHS")
            source_expression = _require_expression(
                constraint.source_expression,
                f"constraint {constraint.constraint_id}.expression",
            )
            if not isinstance(constraint.coefficients, Mapping):
                raise InvalidOptimizationProblem(
                    f"constraint {constraint.constraint_id!r} coefficients must be a mapping"
                )
            normalized_coefficients: dict[str, Fraction] = {}
            for variable_name, coefficient in constraint.coefficients.items():
                if variable_name not in names:
                    raise InvalidOptimizationProblem(
                        f"constraint {constraint.constraint_id!r} references undeclared variable {variable_name!r}"
                    )
                normalized_coefficients[variable_name] = _as_fraction(
                    coefficient,
                    f"constraint {constraint.constraint_id} coefficient for {variable_name}",
                )
            parsed_relation, parsed_coefficients, parsed_rhs = _parse_constraint_expression(
                source_expression, names, f"constraint {constraint.constraint_id}.expression"
            )
            if (
                parsed_relation is not constraint.relation
                or dict(parsed_coefficients) != normalized_coefficients
                or parsed_rhs != rhs
            ):
                raise InvalidOptimizationProblem(
                    f"constraint {constraint.constraint_id!r} source expression does not match its normalized form"
                )
        return self

    def to_contract_dict(self) -> dict[str, Any]:
        """Return the expression-based JSON contract, with stable key ordering."""
        return {
            "problem_type": self.problem_type,
            "schema_version": self.schema_version,
            "name": self.name,
            "objective": {
                "sense": self.objective_direction.value,
                "expression": self.objective_expression,
            },
            "variables": {
                variable.name: {
                    "type": variable.kind,
                    "lower_bound": variable.lower_bound,
                    "upper_bound": variable.upper_bound,
                }
                for variable in sorted(self.variables, key=lambda item: item.name)
            },
            "constraints": [
                {"id": item.constraint_id, "expression": item.source_expression}
                for item in sorted(self.constraints, key=lambda item: item.constraint_id)
            ],
        }

    def to_canonical_dict(self) -> dict[str, Any]:
        """Return normalized exact coefficients for hashes and audit records."""
        return {
            "problem_type": self.problem_type,
            "schema_version": self.schema_version,
            "name": self.name,
            "objective": {
                "sense": self.objective_direction.value,
                "source_expression": self.objective_expression,
                "coefficients": {
                    name: _fraction_token(value)
                    for name, value in sorted(self.objective_coefficients.items())
                },
                "constant": _fraction_token(_as_fraction(self.objective_constant, "objective constant")),
            },
            "variables": [
                {
                    "name": variable.name,
                    "type": variable.kind,
                    "lower_bound": variable.lower_bound,
                    "upper_bound": variable.upper_bound,
                }
                for variable in sorted(self.variables, key=lambda item: item.name)
            ],
            "constraints": [
                {
                    "id": item.constraint_id,
                    "source_expression": item.source_expression,
                    "coefficients": {
                        name: _fraction_token(value)
                        for name, value in sorted(item.coefficients.items())
                    },
                    "relation": item.relation.value,
                    "rhs": _fraction_token(_as_fraction(item.rhs, f"{item.constraint_id} RHS")),
                }
                for item in sorted(self.constraints, key=lambda item: item.constraint_id)
            ],
        }

    @property
    def model_sha256(self) -> str:
        """SHA-256 of the normalized, deterministic problem representation."""
        canonical = _canonical_json(self.to_canonical_dict()).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @property
    def variable_names(self) -> tuple[str, ...]:
        """Variable names in stable lexicographic order."""
        return tuple(sorted(variable.name for variable in self.variables))


@dataclass(frozen=True)
class ConstraintAudit:
    """Independent exact resource usage / residual check for one constraint."""

    constraint_id: str
    relation: ConstraintRelation
    lhs: Fraction
    rhs: Fraction
    satisfied: bool
    slack: Fraction | None = None
    surplus: Fraction | None = None
    residual: Fraction | None = None
    utilization: Fraction | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.constraint_id,
            "relation": self.relation.value,
            "used": _fraction_data(self.lhs),
            "limit": _fraction_data(self.rhs),
            "satisfied": self.satisfied,
            "slack": _fraction_data(self.slack) if self.slack is not None else None,
            "surplus": _fraction_data(self.surplus) if self.surplus is not None else None,
            "residual": _fraction_data(self.residual) if self.residual is not None else None,
            "utilization": (
                _fraction_data(self.utilization) if self.utilization is not None else None
            ),
        }


@dataclass(frozen=True)
class VerificationReport:
    """Result of re-evaluating an assignment with standard exact arithmetic."""

    passed: bool
    objective_value: Fraction | None
    constraint_checks: tuple[ConstraintAudit, ...] = ()
    issues: tuple[str, ...] = ()
    checked_assignment: tuple[tuple[str, int], ...] = field(default=(), repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "objective_value": (
                _fraction_data(self.objective_value)
                if self.objective_value is not None
                else None
            ),
            "constraint_checks": [item.to_dict() for item in self.constraint_checks],
            "issues": list(self.issues),
        }


@dataclass(frozen=True)
class BaselineComparison:
    """Verified comparison against a caller-supplied feasible baseline plan."""

    valid: bool
    assignment: Mapping[str, int] | None
    objective_value: Fraction | None
    absolute_improvement: Fraction | None
    relative_improvement: Fraction | None
    issues: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.assignment is not None:
            object.__setattr__(self, "assignment", MappingProxyType(dict(self.assignment)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "assignment": dict(self.assignment) if self.assignment is not None else None,
            "objective_value": (
                _fraction_data(self.objective_value)
                if self.objective_value is not None
                else None
            ),
            "absolute_improvement": (
                _fraction_data(self.absolute_improvement)
                if self.absolute_improvement is not None
                else None
            ),
            "relative_improvement": (
                _fraction_data(self.relative_improvement)
                if self.relative_improvement is not None
                else None
            ),
            "issues": list(self.issues),
        }


@dataclass(frozen=True)
class OptimizationResult:
    """Fail-closed result; only an exhaustively optimal, verified plan is usable."""

    status: SolveStatus
    model_sha256: str
    assignment: Mapping[str, int] | None = None
    objective_value: Fraction | None = None
    verification: VerificationReport | None = None
    baseline_comparison: BaselineComparison | None = None
    effective_bounds: Mapping[str, tuple[int, int]] | None = None
    search_space_size: int | None = None
    candidates_examined: int = 0
    feasible_candidates: int = 0
    optimality_gap: Fraction | None = None
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.assignment is not None:
            object.__setattr__(self, "assignment", MappingProxyType(dict(self.assignment)))
        if self.effective_bounds is not None:
            object.__setattr__(
                self,
                "effective_bounds",
                MappingProxyType(dict(self.effective_bounds)),
            )

    @property
    def verified(self) -> bool:
        """True only when status, assignment, and post-solve checks all agree."""
        return (
            self.status is SolveStatus.OPTIMAL
            and self.assignment is not None
            and self.objective_value is not None
            and self.optimality_gap == 0
            and isinstance(self.verification, VerificationReport)
            and self.verification.passed
            and self.verification.objective_value == self.objective_value
            and self.verification.checked_assignment == tuple(sorted(self.assignment.items()))
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "verified": self.verified,
            "model_sha256": self.model_sha256,
            "assignment": dict(self.assignment) if self.assignment is not None else None,
            "objective_value": (
                _fraction_data(self.objective_value)
                if self.objective_value is not None
                else None
            ),
            "optimality_gap": (
                _fraction_data(self.optimality_gap)
                if self.optimality_gap is not None
                else None
            ),
            "effective_bounds": (
                {
                    name: {"lower": bounds[0], "upper": bounds[1]}
                    for name, bounds in sorted(self.effective_bounds.items())
                }
                if self.effective_bounds is not None
                else None
            ),
            "search": {
                "space_size": self.search_space_size,
                "candidates_examined": self.candidates_examined,
                "feasible_candidates": self.feasible_candidates,
            },
            "verification": self.verification.to_dict() if self.verification else None,
            "baseline_comparison": (
                self.baseline_comparison.to_dict() if self.baseline_comparison else None
            ),
            "diagnostics": list(self.diagnostics),
        }


@dataclass(frozen=True)
class AuditRecord:
    """One immutable row from the local optimization audit chain."""

    sequence: int
    recorded_at: str
    raw_request: str
    problem: Mapping[str, Any]
    result: Mapping[str, Any]
    previous_hash: str
    record_hash: str


@dataclass(frozen=True)
class _LinearForm:
    coefficients: Mapping[str, Fraction] = field(default_factory=dict)
    constant: Fraction = Fraction(0)


class OptimizationSolver:
    """Exact bounded integer solver with explicit worst-case enumeration caps."""

    def __init__(self, max_candidates: int = DEFAULT_MAX_CANDIDATES) -> None:
        if type(max_candidates) is not int or not 1 <= max_candidates <= MAX_ALLOWED_CANDIDATES:
            raise ValueError(
                f"max_candidates must be an integer from 1 to {MAX_ALLOWED_CANDIDATES}"
            )
        self.max_candidates = max_candidates

    def solve(
        self,
        problem: OptimizationProblem,
        *,
        baseline: Mapping[str, int] | None = None,
    ) -> OptimizationResult:
        """Solve exhaustively, then independently verify before returning a plan."""
        problem.validate()
        model_hash = problem.model_sha256
        effective_bounds, bound_issues = _derive_integer_bounds(problem)
        if bound_issues:
            return OptimizationResult(
                status=SolveStatus.INFEASIBLE,
                model_sha256=model_hash,
                diagnostics=tuple(bound_issues),
            )
        if effective_bounds is None:
            missing = [
                variable.name
                for variable in problem.variables
                if variable.upper_bound is None
            ]
            return OptimizationResult(
                status=SolveStatus.BOUND_REQUIRED,
                model_sha256=model_hash,
                diagnostics=(
                    "Could not prove finite upper bounds for: " + ", ".join(sorted(missing)),
                    "Set explicit upper_bound values or add safe resource constraints; no solution was returned.",
                ),
            )

        search_space_size = math.prod(
            upper - lower + 1 for lower, upper in effective_bounds.values()
        )
        impossible = _pre_solve_infeasibility(problem, effective_bounds)
        if impossible:
            return OptimizationResult(
                status=SolveStatus.INFEASIBLE,
                model_sha256=model_hash,
                effective_bounds=effective_bounds,
                search_space_size=search_space_size,
                diagnostics=tuple(impossible),
            )
        estimated_constraint_checks = search_space_size * max(1, len(problem.constraints))
        if (
            search_space_size > self.max_candidates
            or estimated_constraint_checks > MAX_CONSTRAINT_EVALUATIONS
        ):
            budget_diagnostic = (
                f"Finite search space {search_space_size} or its estimated "
                f"{estimated_constraint_checks} constraint checks exceeds the configured work budget."
            )
            return OptimizationResult(
                status=SolveStatus.SEARCH_LIMIT,
                model_sha256=model_hash,
                effective_bounds=effective_bounds,
                search_space_size=search_space_size,
                diagnostics=(
                    budget_diagnostic,
                    "No incumbent or partial assignment is returned; tighten bounds or use a scalable MILP backend.",
                ),
            )

        names = problem.variable_names
        ranges = [
            range(effective_bounds[name][0], effective_bounds[name][1] + 1)
            for name in names
        ]
        best_assignment: dict[str, int] | None = None
        best_value: Fraction | None = None
        best_key: tuple[int, ...] | None = None
        examined = 0
        feasible = 0
        for values in product(*ranges):
            examined += 1
            assignment = dict(zip(names, values))
            if not _satisfies_all(problem, assignment):
                continue
            feasible += 1
            objective = _evaluate_objective(problem, assignment)
            key = tuple(values)
            if best_value is None:
                better = True
            elif problem.objective_direction is ObjectiveDirection.MAXIMIZE:
                better = objective > best_value
            else:
                better = objective < best_value
            if better or (objective == best_value and (best_key is None or key < best_key)):
                best_value = objective
                best_assignment = assignment
                best_key = key

        if best_assignment is None or best_value is None:
            return OptimizationResult(
                status=SolveStatus.INFEASIBLE,
                model_sha256=model_hash,
                effective_bounds=effective_bounds,
                search_space_size=search_space_size,
                candidates_examined=examined,
                feasible_candidates=feasible,
                diagnostics=("Exhaustive search found no feasible integer assignment.",),
            )

        verification = verify_solution(problem, best_assignment)
        if not verification.passed or verification.objective_value != best_value:
            return OptimizationResult(
                status=SolveStatus.VERIFICATION_FAILED,
                model_sha256=model_hash,
                verification=verification,
                effective_bounds=effective_bounds,
                search_space_size=search_space_size,
                candidates_examined=examined,
                feasible_candidates=feasible,
                diagnostics=(
                    "Post-solve verification rejected the candidate; no assignment was returned.",
                    *verification.issues,
                ),
            )

        baseline_comparison = (
            _compare_baseline(problem, best_value, baseline) if baseline is not None else None
        )
        if (
            baseline_comparison is not None
            and baseline_comparison.valid
            and baseline_comparison.absolute_improvement is not None
            and baseline_comparison.absolute_improvement < 0
        ):
            return OptimizationResult(
                status=SolveStatus.VERIFICATION_FAILED,
                model_sha256=model_hash,
                verification=verification,
                baseline_comparison=baseline_comparison,
                effective_bounds=effective_bounds,
                search_space_size=search_space_size,
                candidates_examined=examined,
                feasible_candidates=feasible,
                diagnostics=(
                    "A verified feasible baseline outperforms the exhaustive result; the claimed optimum was withheld.",
                ),
            )

        return OptimizationResult(
            status=SolveStatus.OPTIMAL,
            model_sha256=model_hash,
            assignment=best_assignment,
            objective_value=best_value,
            verification=verification,
            baseline_comparison=baseline_comparison,
            effective_bounds=effective_bounds,
            search_space_size=search_space_size,
            candidates_examined=examined,
            feasible_candidates=feasible,
            optimality_gap=Fraction(0),
        )


def verify_solution(
    problem: OptimizationProblem, assignment: Mapping[str, int]
) -> VerificationReport:
    """Independently re-evaluate an untrusted assignment; never trust the solver."""
    problem.validate()
    if not isinstance(assignment, Mapping):
        return VerificationReport(False, None, issues=("assignment must be a mapping",))

    expected_names = set(problem.variable_names)
    supplied_names = set(assignment.keys())
    issues: list[str] = []
    if supplied_names != expected_names:
        missing = sorted(expected_names - supplied_names)
        extra = sorted(supplied_names - expected_names, key=str)
        if missing:
            issues.append("missing variables: " + ", ".join(missing))
        if extra:
            issues.append("unknown variables: " + ", ".join(map(str, extra)))

    variable_by_name = {item.name: item for item in problem.variables}
    clean_assignment: dict[str, int] = {}
    for name in sorted(expected_names & supplied_names):
        value = assignment[name]
        if type(value) is not int:
            issues.append(f"variable {name!r} must be an integer, not {type(value).__name__}")
            continue
        variable = variable_by_name[name]
        if value < variable.lower_bound:
            issues.append(
                f"variable {name!r}={value} is below lower_bound {variable.lower_bound}"
            )
        if variable.upper_bound is not None and value > variable.upper_bound:
            issues.append(
                f"variable {name!r}={value} exceeds upper_bound {variable.upper_bound}"
            )
        clean_assignment[name] = value

    if issues or len(clean_assignment) != len(expected_names):
        return VerificationReport(False, None, issues=tuple(issues))

    audits: list[ConstraintAudit] = []
    for constraint in problem.constraints:
        # Deliberately evaluate the verified candidate through a separate loop
        # rather than the solver's _evaluate_linear/_constraint_holds helpers.
        lhs = Fraction(0)
        for variable_name, value in clean_assignment.items():
            coefficient = _as_fraction(
                constraint.coefficients.get(variable_name, Fraction(0)),
                f"verification coefficient for {constraint.constraint_id}.{variable_name}",
            )
            lhs += coefficient * value
        rhs = _as_fraction(constraint.rhs, f"verification RHS for {constraint.constraint_id}")
        if constraint.relation is ConstraintRelation.LE:
            satisfied = lhs <= rhs
        elif constraint.relation is ConstraintRelation.GE:
            satisfied = lhs >= rhs
        else:
            satisfied = lhs == rhs
        slack = rhs - lhs if constraint.relation is ConstraintRelation.LE else None
        surplus = lhs - rhs if constraint.relation is ConstraintRelation.GE else None
        residual = lhs - rhs if constraint.relation is ConstraintRelation.EQ else None
        utilization = (
            lhs / rhs
            if constraint.relation is ConstraintRelation.LE and rhs > 0 and lhs >= 0
            else None
        )
        audits.append(
            ConstraintAudit(
                constraint_id=constraint.constraint_id,
                relation=constraint.relation,
                lhs=lhs,
                rhs=rhs,
                satisfied=satisfied,
                slack=slack,
                surplus=surplus,
                residual=residual,
                utilization=utilization,
            )
        )
        if not satisfied:
            issues.append(
                f"constraint {constraint.constraint_id!r} violated: "
                f"{_fraction_token(lhs)} {constraint.relation.value} {_fraction_token(rhs)} is false"
            )

    objective_value = _as_fraction(problem.objective_constant, "verification objective constant")
    for variable_name, value in clean_assignment.items():
        coefficient = _as_fraction(
            problem.objective_coefficients.get(variable_name, Fraction(0)),
            f"verification objective coefficient for {variable_name}",
        )
        objective_value += coefficient * value
    return VerificationReport(
        passed=not issues and all(check.satisfied for check in audits),
        objective_value=objective_value,
        constraint_checks=tuple(audits),
        issues=tuple(issues),
        checked_assignment=tuple(sorted(clean_assignment.items())),
    )


def solve_and_audit(
    problem: OptimizationProblem,
    audit_store: OptimizationAuditStore,
    *,
    raw_request: str = "",
    solver: OptimizationSolver | None = None,
    baseline: Mapping[str, int] | None = None,
) -> OptimizationResult:
    """Solve and durably log the request/result; propagate audit failures closed."""
    result = (solver or OptimizationSolver()).solve(problem, baseline=baseline)
    audit_store.append(problem, result, raw_request=raw_request)
    return result


def _compare_baseline(
    problem: OptimizationProblem,
    optimal_value: Fraction,
    baseline: Mapping[str, int],
) -> BaselineComparison:
    report = verify_solution(problem, baseline)
    if not report.passed or report.objective_value is None:
        return BaselineComparison(
            valid=False,
            assignment=None,
            objective_value=None,
            absolute_improvement=None,
            relative_improvement=None,
            issues=report.issues or ("baseline assignment was not verified",),
        )
    baseline_value = report.objective_value
    improvement = (
        optimal_value - baseline_value
        if problem.objective_direction is ObjectiveDirection.MAXIMIZE
        else baseline_value - optimal_value
    )
    relative = improvement / abs(baseline_value) if baseline_value != 0 else None
    return BaselineComparison(
        valid=True,
        assignment=dict(baseline),
        objective_value=baseline_value,
        absolute_improvement=improvement,
        relative_improvement=relative,
    )


def _satisfies_all(problem: OptimizationProblem, assignment: Mapping[str, int]) -> bool:
    return all(
        _constraint_holds(
            _evaluate_linear(constraint.coefficients, assignment),
            constraint.relation,
            constraint.rhs,
        )
        for constraint in problem.constraints
    )


def _evaluate_objective(
    problem: OptimizationProblem, assignment: Mapping[str, int]
) -> Fraction:
    return problem.objective_constant + _evaluate_linear(
        problem.objective_coefficients, assignment
    )


def _evaluate_linear(
    coefficients: Mapping[str, Fraction], assignment: Mapping[str, int]
) -> Fraction:
    return sum(
        (
            _as_fraction(coefficient, f"coefficient for {name}") * assignment[name]
            for name, coefficient in coefficients.items()
        ),
        Fraction(0),
    )


def _constraint_holds(
    lhs: Fraction, relation: ConstraintRelation, rhs: Fraction
) -> bool:
    if relation is ConstraintRelation.LE:
        return lhs <= rhs
    if relation is ConstraintRelation.GE:
        return lhs >= rhs
    return lhs == rhs


def _parse_constraint_expression(
    expression: str, variable_names: set[str], context: str
) -> tuple[ConstraintRelation, dict[str, Fraction], Fraction]:
    try:
        tree = ast.parse(expression, mode="eval").body
        if not isinstance(tree, ast.Compare) or len(tree.ops) != 1 or len(tree.comparators) != 1:
            raise InvalidOptimizationProblem(
                f"{context} must contain exactly one comparison (<=, >=, or ==)"
            )
        operator = tree.ops[0]
        if isinstance(operator, ast.LtE):
            relation = ConstraintRelation.LE
        elif isinstance(operator, ast.GtE):
            relation = ConstraintRelation.GE
        elif isinstance(operator, ast.Eq):
            relation = ConstraintRelation.EQ
        else:
            raise InvalidOptimizationProblem(f"{context} uses an unsupported comparison operator")

        left = _parse_linear_node(tree.left, expression, variable_names, context)
        right = _parse_linear_node(tree.comparators[0], expression, variable_names, context)
        coefficients = _combine_coefficients(left.coefficients, right.coefficients, Fraction(-1))
        rhs = right.constant - left.constant
        return relation, coefficients, rhs
    except InvalidOptimizationProblem:
        raise
    except (SyntaxError, ValueError, MemoryError, RecursionError) as exc:
        raise InvalidOptimizationProblem(f"{context} is not a valid linear comparison") from exc


def _parse_linear_expression(
    expression: str, variable_names: set[str], context: str
) -> _LinearForm:
    try:
        tree = ast.parse(expression, mode="eval").body
        return _parse_linear_node(tree, expression, variable_names, context)
    except InvalidOptimizationProblem:
        raise
    except (SyntaxError, ValueError, MemoryError, RecursionError) as exc:
        raise InvalidOptimizationProblem(f"{context} is not a valid linear expression") from exc


def _parse_linear_node(
    node: ast.AST,
    source: str,
    variable_names: set[str],
    context: str,
) -> _LinearForm:
    if sum(1 for _ in ast.walk(node)) > MAX_AST_NODES:
        raise InvalidOptimizationProblem(f"{context} exceeds the expression complexity limit")
    if isinstance(node, ast.Constant):
        if type(node.value) not in (int, float):
            raise InvalidOptimizationProblem(f"{context} contains a non-numeric constant")
        token = ast.get_source_segment(source, node)
        if token is None or not _NUMBER_TOKEN.fullmatch(token):
            raise InvalidOptimizationProblem(f"{context} contains an unsupported numeric literal")
        return _LinearForm({}, _parse_number_token(token, context))
    if isinstance(node, ast.Name):
        if node.id not in variable_names:
            raise InvalidOptimizationProblem(
                f"{context} references undeclared variable {node.id!r}"
            )
        return _LinearForm({node.id: Fraction(1)}, Fraction(0))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        operand = _parse_linear_node(node.operand, source, variable_names, context)
        return operand if isinstance(node.op, ast.UAdd) else _scale_form(operand, Fraction(-1))
    if isinstance(node, ast.BinOp):
        left = _parse_linear_node(node.left, source, variable_names, context)
        right = _parse_linear_node(node.right, source, variable_names, context)
        if isinstance(node.op, ast.Add):
            return _add_forms(left, right)
        if isinstance(node.op, ast.Sub):
            return _add_forms(left, right, Fraction(-1))
        if isinstance(node.op, ast.Mult):
            if left.coefficients and right.coefficients:
                raise InvalidOptimizationProblem(f"{context} contains a nonlinear variable product")
            if not left.coefficients:
                return _scale_form(right, left.constant)
            return _scale_form(left, right.constant)
        if isinstance(node.op, ast.Div):
            if right.coefficients:
                raise InvalidOptimizationProblem(f"{context} divides by a variable expression")
            if right.constant == 0:
                raise InvalidOptimizationProblem(f"{context} divides by zero")
            return _scale_form(left, Fraction(1, 1) / right.constant)
        raise InvalidOptimizationProblem(
            f"{context} contains an unsupported operator; only +, -, *, and constant / are allowed"
        )
    raise InvalidOptimizationProblem(
        f"{context} contains unsupported syntax ({type(node).__name__}); "
        "calls, attributes, powers, indexing, and code are forbidden"
    )


def _add_forms(
    left: _LinearForm, right: _LinearForm, right_scale: Fraction = Fraction(1)
) -> _LinearForm:
    coefficients = _combine_coefficients(left.coefficients, right.coefficients, right_scale)
    return _LinearForm(coefficients, left.constant + right.constant * right_scale)


def _scale_form(form: _LinearForm, scale: Fraction) -> _LinearForm:
    return _LinearForm(
        {
            name: value * scale
            for name, value in form.coefficients.items()
            if value * scale != 0
        },
        form.constant * scale,
    )


def _combine_coefficients(
    left: Mapping[str, Fraction],
    right: Mapping[str, Fraction],
    right_scale: Fraction,
) -> dict[str, Fraction]:
    result = dict(left)
    for name, coefficient in right.items():
        result[name] = result.get(name, Fraction(0)) + coefficient * right_scale
        if result[name] == 0:
            del result[name]
    return result


def _parse_number_token(token: str, context: str) -> Fraction:
    try:
        decimal_value = Decimal(token)
    except InvalidOperation as exc:
        raise InvalidOptimizationProblem(f"{context} contains an invalid number") from exc
    return _as_fraction(decimal_value, f"number in {context}")


def _as_fraction(value: Any, context: str) -> Fraction:
    """Convert finite JSON/Python numeric values to bounded exact rationals."""
    if isinstance(value, bool):
        raise InvalidOptimizationProblem(f"{context} must be a finite number, not bool")
    try:
        if isinstance(value, Fraction):
            parsed = value
        elif isinstance(value, Decimal):
            if not value.is_finite():
                raise InvalidOptimizationProblem(f"{context} must be finite")
            if not value.is_zero() and (
                value.adjusted() > 15
                or value.adjusted() < -12
                or value.as_tuple().exponent < -12
            ):
                raise InvalidOptimizationProblem(f"{context} is outside supported numeric limits")
            parsed = Fraction(value)
        elif type(value) is int:
            parsed = Fraction(value)
        elif type(value) is float:
            if not math.isfinite(value):
                raise InvalidOptimizationProblem(f"{context} must be finite")
            parsed = Fraction(Decimal(str(value)))
        else:
            raise InvalidOptimizationProblem(f"{context} must be a finite number")
    except (OverflowError, ValueError, ZeroDivisionError) as exc:
        if isinstance(exc, InvalidOptimizationProblem):
            raise
        raise InvalidOptimizationProblem(f"{context} is outside supported numeric limits") from exc
    if abs(parsed.numerator) > MAX_NUMERATOR or parsed.denominator > MAX_DENOMINATOR:
        raise InvalidOptimizationProblem(f"{context} is outside supported numeric limits")
    return parsed


def _fraction_token(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def _fraction_data(value: Fraction) -> int | str:
    return value.numerator if value.denominator == 1 else _fraction_token(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _derive_integer_bounds(
    problem: OptimizationProblem,
) -> tuple[dict[str, tuple[int, int]] | None, list[str]]:
    """Return explicit or safely inferred bounds, or report a contradiction."""
    lower = {item.name: item.lower_bound for item in problem.variables}
    upper = {item.name: item.upper_bound for item in problem.variables}
    for constraint in problem.constraints:
        if constraint.relation is ConstraintRelation.LE:
            rows = [(constraint.coefficients, constraint.rhs)]
        elif constraint.relation is ConstraintRelation.GE:
            rows = [
                ({name: -value for name, value in constraint.coefficients.items()}, -constraint.rhs)
            ]
        else:
            rows = [
                (constraint.coefficients, constraint.rhs),
                ({name: -value for name, value in constraint.coefficients.items()}, -constraint.rhs),
            ]
        for coefficients, rhs in rows:
            # Inferring from a row with negative coefficients requires additional
            # proof about every variable's upper bound. v1 deliberately skips it.
            if any(value < 0 for value in coefficients.values()):
                continue
            for variable_name, coefficient in coefficients.items():
                if coefficient <= 0:
                    continue
                other_minimum = sum(
                    (
                        value * lower[other_name]
                        for other_name, value in coefficients.items()
                        if other_name != variable_name
                    ),
                    Fraction(0),
                )
                candidate = (rhs - other_minimum) // coefficient
                if candidate < lower[variable_name]:
                    message = (
                        f"constraint {constraint.constraint_id!r} implies upper bound "
                        f"{candidate} for {variable_name!r}, below lower bound {lower[variable_name]}"
                    )
                    return None, [message]
                if upper[variable_name] is None or candidate < upper[variable_name]:
                    upper[variable_name] = int(candidate)

    missing = sorted(name for name, value in upper.items() if value is None)
    if missing:
        return None, []
    return {name: (lower[name], int(upper[name])) for name in sorted(lower)}, []


def _pre_solve_infeasibility(
    problem: OptimizationProblem,
    bounds: Mapping[str, tuple[int, int]],
) -> list[str]:
    """Detect constraints impossible over the continuous interval hull."""
    issues: list[str] = []
    for constraint in problem.constraints:
        minimum = Fraction(0)
        maximum = Fraction(0)
        for name, (lower, upper) in bounds.items():
            coefficient = _as_fraction(
                constraint.coefficients.get(name, Fraction(0)),
                f"{constraint.constraint_id} coefficient for {name}",
            )
            low_term = coefficient * lower
            high_term = coefficient * upper
            minimum += min(low_term, high_term)
            maximum += max(low_term, high_term)
        if constraint.relation is ConstraintRelation.LE:
            impossible = minimum > constraint.rhs
        elif constraint.relation is ConstraintRelation.GE:
            impossible = maximum < constraint.rhs
        else:
            impossible = not minimum <= constraint.rhs <= maximum
        if impossible:
            issues.append(
                f"constraint {constraint.constraint_id!r} is infeasible within variable bounds "
                f"(possible LHS {_fraction_token(minimum)}..{_fraction_token(maximum)}, "
                f"required {constraint.relation.value} {_fraction_token(constraint.rhs)})"
            )
    return issues


def _require_object(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise InvalidOptimizationProblem(f"{context} must be a JSON object with string keys")
    return value


def _require_array(value: Any, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise InvalidOptimizationProblem(f"{context} must be a JSON array")
    return value


def _require_string(value: Any, context: str, max_length: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise InvalidOptimizationProblem(
            f"{context} must be a non-empty string of at most {max_length} characters"
        )
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InvalidOptimizationProblem(f"{context} must contain valid Unicode text") from exc
    return value


def _require_expression(value: Any, context: str) -> str:
    expression = _require_string(value, context, MAX_EXPRESSION_LENGTH)
    if any(ord(char) < 32 and char not in "\t\r\n" for char in expression):
        raise InvalidOptimizationProblem(f"{context} contains a control character")
    return expression


def _require_integer(value: Any, context: str) -> int:
    if type(value) is not int:
        raise InvalidOptimizationProblem(f"{context} must be an integer")
    return value


def _check_keys(
    value: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str],
    context: str,
) -> None:
    present = set(value)
    missing = required - present
    unknown = present - required - optional
    if missing:
        raise InvalidOptimizationProblem(
            f"{context} is missing required field(s): {', '.join(sorted(missing))}"
        )
    if unknown:
        raise InvalidOptimizationProblem(
            f"{context} contains unsupported field(s): {', '.join(sorted(unknown))}"
        )


def _validate_variable_name(name: Any) -> None:
    if (
        not isinstance(name, str)
        or not _VARIABLE_NAME.fullmatch(name)
        or keyword.iskeyword(name)
    ):
        raise InvalidOptimizationProblem(
            f"invalid variable name {name!r}; use a simple ASCII identifier"
        )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidOptimizationProblem(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise InvalidOptimizationProblem(f"non-finite JSON number {value!r} is forbidden")


class OptimizationAuditStore:
    """SQLite append-only, SHA-256-chained local log for solver decisions."""

    def __init__(self, path: os.PathLike[str] | str) -> None:
        if os.fspath(path) == ":memory:":
            raise ValueError("the optimizer audit store requires a durable SQLite path")
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _initialize(self) -> None:
        try:
            connection = self._connect()
            try:
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS optimizer_runs (
                        sequence INTEGER PRIMARY KEY,
                        recorded_at TEXT NOT NULL,
                        raw_request TEXT NOT NULL,
                        problem_json TEXT NOT NULL,
                        result_json TEXT NOT NULL,
                        previous_hash TEXT NOT NULL,
                        record_hash TEXT NOT NULL UNIQUE
                    )"""
                )
                connection.executescript(
                    """CREATE TRIGGER IF NOT EXISTS optimizer_runs_no_update
                       BEFORE UPDATE ON optimizer_runs BEGIN
                           SELECT RAISE(ABORT, 'optimizer audit log is append-only');
                       END;
                       CREATE TRIGGER IF NOT EXISTS optimizer_runs_no_delete
                       BEFORE DELETE ON optimizer_runs BEGIN
                           SELECT RAISE(ABORT, 'optimizer audit log is append-only');
                       END;"""
                )
            finally:
                connection.close()
            if os.name == "posix":
                os.chmod(self.path, 0o600)
        except (OSError, sqlite3.Error) as exc:
            raise AuditLogError(f"could not initialize optimizer audit database: {exc}") from exc

    def append(
        self,
        problem: OptimizationProblem,
        result: OptimizationResult,
        *,
        raw_request: str = "",
    ) -> AuditRecord:
        """Recheck an optimum and persist the raw input, model, and result."""
        problem.validate()
        if not isinstance(result.status, SolveStatus):
            raise AuditLogError("result status is invalid")
        if result.model_sha256 != problem.model_sha256:
            raise AuditLogError("result model hash does not match the supplied problem")
        if result.status is SolveStatus.OPTIMAL:
            if not result.verified or result.assignment is None:
                raise AuditLogError("an unverified result cannot be recorded as optimal")
            independent = verify_solution(problem, result.assignment)
            if (
                not independent.passed
                or independent.objective_value != result.objective_value
                or not isinstance(result.verification, VerificationReport)
                or result.verification != independent
            ):
                raise AuditLogError("result failed independent audit verification")
            if result.optimality_gap != 0:
                raise AuditLogError("optimal results must have a verified zero optimality gap")
            if (
                type(result.search_space_size) is not int
                or not 1 <= result.search_space_size <= MAX_ALLOWED_CANDIDATES
            ):
                raise AuditLogError("optimal result has invalid exhaustive-search evidence")
            replay = OptimizationSolver(max_candidates=result.search_space_size).solve(problem)
            if (
                replay.status is not SolveStatus.OPTIMAL
                or replay.assignment != result.assignment
                or replay.objective_value != result.objective_value
                or replay.search_space_size != result.search_space_size
                or replay.candidates_examined != result.candidates_examined
                or replay.feasible_candidates != result.feasible_candidates
                or replay.effective_bounds != result.effective_bounds
            ):
                raise AuditLogError("claimed optimum did not match an exhaustive local replay")
        elif result.assignment is not None or result.objective_value is not None:
            raise AuditLogError("non-optimal results must not carry a decision assignment or objective")
        if not isinstance(raw_request, str):
            raise AuditLogError("raw_request must be text")
        try:
            request_size = len(raw_request.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise AuditLogError("raw_request must contain valid Unicode text") from exc
        if request_size > MAX_AUDIT_REQUEST_BYTES:
            raise AuditLogError(
                f"raw_request exceeds the {MAX_AUDIT_REQUEST_BYTES}-byte audit limit"
            )
        recorded_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")  # noqa: UP017 -- Python 3.10
        problem_json = _canonical_json(
            {"contract": problem.to_contract_dict(), "normalized": problem.to_canonical_dict()}
        )
        result_json = _canonical_json(result.to_dict())

        with self._lock:
            try:
                connection = self._connect()
            except sqlite3.Error as exc:
                raise AuditLogError(f"could not open optimizer audit database: {exc}") from exc
            try:
                connection.execute("BEGIN IMMEDIATE")
                previous = connection.execute(
                    "SELECT sequence, record_hash FROM optimizer_runs "
                    "ORDER BY sequence DESC LIMIT 1"
                ).fetchone()
                sequence = 1 if previous is None else int(previous["sequence"]) + 1
                previous_hash = GENESIS_HASH if previous is None else str(previous["record_hash"])
                payload = {
                    "sequence": sequence,
                    "recorded_at": recorded_at,
                    "raw_request": raw_request,
                    "problem": json.loads(problem_json),
                    "result": json.loads(result_json),
                    "previous_hash": previous_hash,
                }
                record_hash = hashlib.sha256(
                    _canonical_json(payload).encode("utf-8")
                ).hexdigest()
                connection.execute(
                    "INSERT INTO optimizer_runs "
                    "(sequence, recorded_at, raw_request, problem_json, result_json, previous_hash, record_hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        sequence,
                        recorded_at,
                        raw_request,
                        problem_json,
                        result_json,
                        previous_hash,
                        record_hash,
                    ),
                )
                connection.execute("COMMIT")
            except (OSError, sqlite3.Error) as exc:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise AuditLogError(f"could not append optimizer audit record: {exc}") from exc
            finally:
                connection.close()

        return AuditRecord(
            sequence=sequence,
            recorded_at=recorded_at,
            raw_request=raw_request,
            problem=MappingProxyType(json.loads(problem_json)),
            result=MappingProxyType(json.loads(result_json)),
            previous_hash=previous_hash,
            record_hash=record_hash,
        )

    def read_all(self) -> tuple[AuditRecord, ...]:
        """Read all audit records in their append order."""
        if not self.path.is_file():
            raise AuditLogError("optimizer audit database is missing")
        try:
            connection = self._connect()
            try:
                rows = connection.execute(
                    "SELECT sequence, recorded_at, raw_request, problem_json, result_json, "
                    "previous_hash, record_hash FROM optimizer_runs ORDER BY sequence"
                ).fetchall()
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise AuditLogError(f"could not read optimizer audit records: {exc}") from exc
        try:
            return tuple(
                AuditRecord(
                    sequence=int(row["sequence"]),
                    recorded_at=str(row["recorded_at"]),
                    raw_request=str(row["raw_request"]),
                    problem=MappingProxyType(json.loads(row["problem_json"])),
                    result=MappingProxyType(json.loads(row["result_json"])),
                    previous_hash=str(row["previous_hash"]),
                    record_hash=str(row["record_hash"]),
                )
                for row in rows
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AuditLogError(f"optimizer audit record is malformed: {exc}") from exc

    def verify_chain(self) -> bool:
        """Verify sequence continuity, previous hashes, and stored payload hashes."""
        try:
            previous_hash = GENESIS_HASH
            for expected_sequence, record in enumerate(self.read_all(), start=1):
                if record.sequence != expected_sequence or record.previous_hash != previous_hash:
                    return False
                payload = {
                    "sequence": record.sequence,
                    "recorded_at": record.recorded_at,
                    "raw_request": record.raw_request,
                    "problem": dict(record.problem),
                    "result": dict(record.result),
                    "previous_hash": record.previous_hash,
                }
                digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
                if digest != record.record_hash:
                    return False
                previous_hash = record.record_hash
            return True
        except (AuditLogError, TypeError, ValueError, OSError):
            return False


__all__ = [
    "AuditLogError",
    "AuditRecord",
    "BaselineComparison",
    "ConstraintAudit",
    "ConstraintRelation",
    "DecisionVariable",
    "InvalidOptimizationProblem",
    "LinearConstraint",
    "ObjectiveDirection",
    "OptimizationAuditStore",
    "OptimizationError",
    "OptimizationProblem",
    "OptimizationResult",
    "OptimizationSolver",
    "SolveStatus",
    "VerificationReport",
    "solve_and_audit",
    "verify_solution",
]
