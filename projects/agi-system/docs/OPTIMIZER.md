# Exact Integer Optimizer — v1

This module adds a small, local, dependency-free optimization layer to `agi-system`:

```text
natural-language request (optional; outside this module)
  → strict JSON MILP contract
  → safe linear-expression parser (AST inspection only)
  → exhaustive bounded integer search with exact rational arithmetic
  → independent post-solve verification
  → optional append-only SQLite audit record
```

The model contract is data-only. The module never calls `eval()` or `exec()`, never executes user/model-generated code, and never performs production writes. Only an `OPTIMAL` result with `verified: true` contains a candidate plan.

## Supported v1 scope

- Non-negative **integer** decision variables only.
- Linear objective and `<=`, `>=`, `==` constraints.
- Finite upper bounds may be explicit or safely inferred from monotone resource constraints. If a finite bound cannot be proved, the result is `BOUND_REQUIRED` and contains no assignment.
- Exact rational comparisons are used internally. Decimal literals such as `0.1` are parsed from their source text, not through binary floating-point arithmetic.
- The solver enumerates the entire bounded integer domain. Default cap: 250,000 assignments; hard cap: 5,000,000 assignments plus a 25,000,000 estimated constraint-check budget. If either work limit would be exceeded, the result is `SEARCH_LIMIT` with **no partial incumbent**.
- Maximum model size: 12 variables, 128 constraints, and bounded JSON/expression lengths.
- Continuous LP, unbounded MILP, large-scale branch-and-bound, and commercial/production scheduling are **not** claimed by this v1 engine. Use a scalable solver backend for those models, and keep the independent verification gate.

This design favors a transparent exact result for small discrete decisions over an unverified floating-point result from a hidden or implicit backend. **SciPy is intentionally not a v1 requirement**: the supplied factory model has only 546 candidate assignments, while this package and its CI are dependency-free and must remain usable offline. The `scipy.optimize.milp` example is a reasonable future optional backend for larger models, not the current solver contract; any backend must preserve the same strict parser, fail-closed result gate, and independent verifier.

## Strict JSON contract

Expressions use a deliberately small linear grammar: numeric constants, declared variable names, parentheses, unary `+`/`-`, addition, subtraction, multiplication by a constant, division by a nonzero constant, and one comparison for constraints. Function calls, attributes, indexing, powers, variable-by-variable multiplication, chained comparisons, unknown variables, duplicate JSON keys, and unknown object fields are rejected.

```json
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
```

For this model the capacity constraints prove `x1 <= 25` and `x2 <= 20`; the enumerated domain has only 546 assignments. The exact result is:

```text
x1 = 18, x2 = 14
profit = 12,400 EGP
machine hours = 60 / 60 (100%, slack 0)
raw material = 50 / 50 kg (100%, slack 0)
minimum order = 18 >= 5
```

The supplied intuitive baseline `(x1=5, x2=18)` is also feasible: it earns 10,500 EGP, uses 59 machine hours and 28 kg of material. When passed as a baseline, the solver reports an exact improvement of 1,900 EGP and a relative improvement of `19/105` (about 18.1%). A baseline is independently verified and never influences the selected optimum.

## Python API

```python
from core import (
    OptimizationAuditStore,
    OptimizationProblem,
    OptimizationSolver,
    SolveStatus,
    solve_and_audit,
)

problem = OptimizationProblem.from_json(model_json)
result = solve_and_audit(
    problem,
    OptimizationAuditStore("./local-audit/optimizer.sqlite3"),
    raw_request="Maximize weekly profit without exceeding resource limits.",
    baseline={"x1": 5, "x2": 18},
)

if result.status is not SolveStatus.OPTIMAL or not result.verified:
    # Do not display or execute a numeric decision.
    raise RuntimeError("No verified optimum is available")

print(result.to_dict())
```

`verify_solution(problem, assignment)` is also exposed for an independent check of any supplied candidate. Results include the normalized model SHA-256, effective bounds, search counts, each constraint's exact usage/slack/utilization, and optional baseline comparison. `OptimizationSolver.solve()` is the compute-only primitive and does not persist records; use `solve_and_audit()` as the decision path when local input/result logging is required. It returns no result if verification or audit persistence fails.

## SQLite audit trail

`OptimizationAuditStore` records the optional raw request, source JSON expressions, normalized coefficients, solver result, timestamp, and a SHA-256 previous-record chain in a local SQLite database. Before recording an `OPTIMAL` result, `append()` independently checks the assignment and replays the bounded exhaustive search; this intentionally spends a second solve to reject stale or forged result objects. The database file is set to mode `0600` on POSIX; SQL triggers reject ordinary `UPDATE`/`DELETE`; `verify_chain()` detects sequence, link, or record changes. `solve_and_audit()` returns a result only after the audit append succeeds.

Raw request text is stored in plaintext if supplied. Callers should omit it or apply their retention/redaction policy when requests may include private information. The local hash chain is tamper-evident, not protection against an administrator who can rewrite the database and recompute every hash.

## Tests

`tests/test_optimizer_engine.py` covers the 12,400 EGP factory case, exact baseline comparison, bound inference, minimization, infeasibility, explicit search limits, AST injection/nonlinearity rejection, post-solve tampering, SQLite append-only behavior, and chain verification.

Run the dependency-free project suite from `projects/agi-system`:

```bash
python3 -m unittest discover -s tests -t . -v
```
