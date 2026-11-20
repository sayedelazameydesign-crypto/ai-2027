#!/usr/bin/env python3
"""Run every gate the Python-2027 workflow runs, in one command, locally.

Why this exists
---------------
Twice in one session a change passed locally and failed in CI, for the same
reason both times: the workflow runs a **whole-tree** command and I ran a
per-file one.

    local:  ruff check src/ai2027_protocol/ingest.py tests/test_ingest.py
    CI:     ruff check --target-version py313 src tests workers/python-worker/src
            ruff format --check src tests workers/python-worker/src
            mypy src tests

`ruff format --check` failed first. Then `mypy --strict src tests` found two
errors in a file I had "checked" because I checked a different file. A gate
you can pass by scoping it down is not a gate.

The repository's own defect table lists "a guard that was not run" as a
recurring pattern. This script removes the scoping choice: it runs exactly
the workflow's commands, on exactly the workflow's paths, and reports each
one separately so a failure says which gate it was.

Exit code is non-zero if any stage fails. `--fast` skips pytest coverage,
which is the slow part, and says so in its output rather than pretending.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKDIR = ROOT / "python-2027"

#: The paths the workflow lints. Kept identical on purpose: a difference here
#: would recreate the blindness this script exists to remove.
LINT_PATHS = ("src", "tests", "workers/python-worker/src")


def _run(label: str, command: list[str], cwd: Path) -> bool:
    """Run one gate and report it as a single line."""
    printable = " ".join(command)
    print(f"\n=== {label} ===\n$ {printable}")
    try:
        result = subprocess.run(command, cwd=cwd, check=False)
    except FileNotFoundError:
        print(
            f"SKIPPED: {command[0]} is not installed. Install it before trusting a green run."
        )
        return False
    if result.returncode == 0:
        print(f"PASS {label}")
        return True
    print(f"FAIL {label} (exit {result.returncode})")
    return False


def main(argv: list[str] | None = None) -> int:
    """Run the gates and return 0 only if every one passed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fast",
        action="store_true",
        help="skip the pytest stage (the slow one); the output says it was skipped",
    )
    parser.add_argument(
        "tool",
        nargs="?",
        default="",
        help="prefer this executable (e.g. .venv/bin/ruff) if it exists",
    )
    args = parser.parse_args(argv)

    if not WORKDIR.is_dir():
        print(f"FAIL: {WORKDIR} does not exist", file=sys.stderr)
        return 1

    def tool(name: str) -> str:
        """Prefer an explicit interpreter, then the venv, then PATH."""
        if args.tool:
            candidate = WORKDIR / args.tool / name
            if candidate.exists():
                return str(candidate)
        venv = WORKDIR / ".venv" / "bin" / name
        if venv.exists():
            return str(venv)
        found = shutil.which(name)
        return found or name

    stages: list[tuple[str, list[str]]] = [
        (
            "ruff check (whole tree, as CI scopes it)",
            [tool("ruff"), "check", "--target-version", "py313", *LINT_PATHS],
        ),
        (
            "ruff format --check (whole tree)",
            [tool("ruff"), "format", "--check", *LINT_PATHS],
        ),
        (
            "mypy --strict (whole tree)",
            [tool("mypy"), "src", "tests"],
        ),
    ]
    if not args.fast:
        stages.append(
            (
                "pytest with the 90% coverage floor",
                [sys.executable, "-m", "pytest", "--cov-fail-under=90", "-q"],
            )
        )
    else:
        print(
            "\nNOTE: pytest was skipped by --fast. This run does not prove the tests pass."
        )

    failures = [label for label, command in stages if not _run(label, command, WORKDIR)]

    print("\n" + "=" * 60)
    if failures:
        print(f"{len(failures)} gate(s) failed:")
        for label in failures:
            print(f"  - {label}")
        return 1
    print(f"All {len(stages)} gate(s) passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
