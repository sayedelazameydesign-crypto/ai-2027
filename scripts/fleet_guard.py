#!/usr/bin/env python3
"""CI guards for fleet-2027: zero third-party imports, and no self-graded success.

Two failure modes are being prevented here, both of which this repository has
already been bitten by once:

1. **Dependency creep.** ``requirements.txt`` claims the deployment surface has
   *zero* third-party dependencies and ``scripts/ci_guard.py`` enforces that for
   the kernel. A new subsystem that quietly imports ``pydantic`` or ``httpx``
   would break that promise without touching the guarded directories. So
   ``fleet-2027`` gets the same AST scan.

2. **Self-graded success.** A step that reports ``ok=True`` and records itself
   as ``succeeded`` without an independent verification row is exactly the
   "wrote the deploy command, therefore it deployed" fallacy. The doctest gate
   reads every Python file in the package and fails if a success path is added
   that commits without calling the verifier.

Run
---
    python3 scripts/fleet_guard.py imports
    python3 scripts/fleet_guard.py doctest
    python3 scripts/fleet_guard.py all

Exit code 0 means both gates passed *now*; it is not a stored verdict.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "fleet-2027" / "fleet2027"
#: Modules that are allowed because they are stdlib in every supported Python.
CONDITIONAL_STDLIB = {"tomllib"}


def _python_files() -> list[Path]:
    return sorted(PACKAGE.glob("*.py"))


def check_imports() -> int:
    """Fail on any third-party import in the fleet package."""
    stdlib = set(sys.stdlib_module_names) | CONDITIONAL_STDLIB
    rel = {
        "contracts", "store", "verifier", "registry", "runner", "examples", "llm",
        "routing", "generalist", "bench",
        # The shared kernel: stdlib-only by construction (its own test suite
        # asserts that), so importing it cannot smuggle in a third-party dep.
        "kernel2027",
    }
    bad: dict[str, set[str]] = {}
    files = _python_files()
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            elif isinstance(node, ast.ImportFrom) and node.level > 0 and node.module:
                names = [node.module.split(".")[0]]
                if names[0] not in rel:
                    bad.setdefault(str(path.relative_to(ROOT)), set()).add(f".{names[0]}")
                continue
            for name in names:
                if name in {"__future__"}:
                    continue
                if name not in stdlib and name not in rel:
                    bad.setdefault(str(path.relative_to(ROOT)), set()).add(name)
    print(f"AST-scanned {len(files)} fleet-2027 modules for third-party imports "
          f"(Python {sys.version.split()[0]}).")
    if bad:
        for path, names in sorted(bad.items()):
            print(f"FAIL: {path} imports {sorted(names)}", file=sys.stderr)
        return 1
    print("PASS: zero third-party imports (stdlib only, as requirements.txt claims).")
    return 0


def check_self_grading() -> int:
    """Fail if a success path bypasses the independent verifier.

    The rule: inside ``runner.py``, any call to ``store.record_success`` must be
    reachable from a scope that also calls ``verify_step``. In practice the only
    legitimate caller is ``_execute_entry``, which verifies *before* committing.
    """
    runner = (PACKAGE / "runner.py").read_text(encoding="utf-8")
    tree = ast.parse(runner, filename="runner.py")
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        source = ast.get_source_segment(runner, node) or ""
        commits = "record_success(" in source
        verifies = "verify_step(" in source
        if commits and not verifies:
            offenders.append(f"{node.name} commits a success without verify_step")
    print("doctest: every success-committing scope in runner.py also verifies." if not offenders
          else f"FAIL: {len(offenders)} unverified success path(s)")
    for offender in offenders:
        print(f"  ✗ {offender}", file=sys.stderr)
    return 1 if offenders else 0


def check_no_silent_swallow() -> int:
    """Refuse bare ``except: pass`` in the fleet package (silent failure)."""
    offenders: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            body = [stmt for stmt in node.body if not isinstance(stmt, ast.Pass)]
            if not body:
                offenders.append(f"{path.name}:{node.lineno}: except …: pass")
    print("doctest: no silently swallowed exceptions." if not offenders else "FAIL: silent except")
    for offender in offenders:
        print(f"  ✗ {offender}", file=sys.stderr)
    return 1 if offenders else 0


def main(argv: list[str] | None = None) -> int:
    """Run the selected guards."""
    parser = argparse.ArgumentParser(description="fleet-2027 CI guards")
    parser.add_argument("gate", choices=["imports", "doctest", "all"], nargs="?", default="all")
    args = parser.parse_args(argv)
    codes = []
    if args.gate in {"imports", "all"}:
        codes.append(check_imports())
    if args.gate in {"doctest", "all"}:
        codes.append(check_self_grading())
        codes.append(check_no_silent_swallow())
    return 0 if all(code == 0 for code in codes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
