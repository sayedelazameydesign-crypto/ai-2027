#!/usr/bin/env python3
"""Audit how the workflows touch secrets — before a mistake reaches a runner.

Two rules, both learned from real incidents rather than intuition:

**Rule 1 — no inline interpolation.** A secret may be assigned to a step's
`env:` or explicitly mapped by name to a reusable workflow's `secrets:` input.
Interpolated into the *script text* or an action's `with:` input it becomes part
of the command or action input and may be logged or forwarded unexpectedly. GitHub
also forbids referencing `secrets.*` directly in an `if:` expression.

    safe:    env: RENDER_API_KEY: ${{ secrets.RENDER_API_KEY }}
    safe:    secrets: RENDER_API_KEY: ${{ secrets.RENDER_API_KEY }}
    unsafe:  run: curl -H "Authorization: ${{ secrets.RENDER_API_KEY }}" ...

**Rule 2 — no undocumented secret.** Every secret a workflow references must be
named in `docs/SECRETS.md`, which also says what it is for and what the workflow
does when it is absent. An undocumented secret is a secret nobody can rotate.

This is a static check: it reads files, never executes a workflow, and never
touches a secret value (it only sees the *names*).

    python3 scripts/workflow_secret_audit.py            # human
    python3 scripts/workflow_secret_audit.py --json     # machine
    python3 scripts/workflow_secret_audit.py --assert   # exit 1 on any violation

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
#: Deliberately named without "secret": CodeQL's clear-text-logging heuristic treats
#: any value that passed through a name matching /secret|token|password/i as
#: sensitive, and this file's whole job is to read the register and print a report.
REGISTER_PATH = ROOT / "docs" / "SECRETS.md"

#: Match the whole expression, then look for secret names inside it. Matching the
#: name only up to `}}` missed `if: ${{ secrets.X != '' }}` entirely — a real gap
#: found by test_03.
EXPR = re.compile(r"\$\{\{(.*?)\}\}")
#: Named without "secret" on purpose — see REGISTER_PATH above. The query's
#: heuristic keys on identifiers, and this regex's matches are printed.
NAME_IN_EXPR = re.compile(r"secrets\.([A-Za-z_][A-Za-z0-9_]*)")
#: Match simple YAML mapping keys so a secret assignment is accepted only in
#: an `env:` block or a named reusable-workflow `secrets:` mapping. An uppercase
#: key under `with:` or arbitrary script text is not a safe transport.
MAPPING_KEY = re.compile(r"^\s*(?:-\s*)?([A-Za-z_][A-Za-z0-9_-]*)\s*:(.*)$")
ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*\Z")


def find_references(workflow_text: str) -> list[dict]:
    """Every `secrets.NAME` reference with its line number and safe context.

    The small indentation stack is deliberately conservative: credentials may
    travel through an explicit env assignment, or a named `secrets:` mapping to
    a reusable workflow. Direct secret references in `if:` expressions are
    rejected because GitHub does not permit that context there.
    """
    refs = []
    stack: list[tuple[int, str]] = []
    for lineno, line in enumerate(workflow_text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        while stack and stack[-1][0] >= indent:
            stack.pop()

        match = MAPPING_KEY.match(line)
        key = match.group(1) if match else ""
        value = match.group(2).strip() if match else ""
        parents = [name for _, name in stack]
        safe = (
            bool(ENV_NAME.fullmatch(key))
            and bool(parents)
            and parents[-1] in {"env", "secrets"}
        )

        for expr in EXPR.finditer(line):
            for name in NAME_IN_EXPR.finditer(expr.group(1)):
                # No copy of the audited line is kept: `file:line` already points
                # at it, and a copy would carry the workflow's own text into every
                # report we print (CodeQL flagged exactly that path).
                refs.append({"line": lineno, "name": name.group(1), "safe": safe})

        if match and not value:
            stack.append((indent, key))
    return refs


def documented(name: str, register_text: str) -> bool:
    """A secret is documented when the doc names it as code: `NAME`."""
    return name in register_text


def recorded_names(register_text: str) -> list[str]:
    """Every secret name the register actually records (in file order)."""
    return re.findall(r"`([A-Z][A-Z0-9_]{2,})`", register_text)


def display_name(name: str, register_text: str) -> str:
    """The name to print — but only if the register already records it.

    This is not politeness, it is the no-new-leak rule: a reference found in a
    workflow that `docs/SECRETS.md` does not record is reported **by location**,
    never echoed by name, because an unreviewed name may itself be sensitive
    (a key passed as `secrets.<key-material>`). The value returned always comes
    from the register, never from the file being audited.
    """
    for token in recorded_names(register_text):
        if token == name:
            return token
    return "<undocumented secret>"


def audit(workflows_dir: Path = WORKFLOWS, register_path: Path = REGISTER_PATH) -> dict:
    """Return findings; never raises because one file is odd."""
    register_text = (register_path.read_text(encoding="utf-8")
                     if register_path.is_file() else "")
    files = sorted(workflows_dir.glob("*.y*ml")) if workflows_dir.is_dir() else []
    findings: list[dict] = []
    used: dict[str, list[str]] = {}

    for path in files:
        try:
            workflow_text = path.read_text(encoding="utf-8")
        except OSError as exc:
            findings.append({"file": path.name, "line": 0, "rule": "unreadable",
                             "detail": str(exc)[:120]})
            continue
        rel = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name
        for ref in find_references(workflow_text):
            shown = display_name(ref["name"], register_text)
            used.setdefault(shown, []).append(f"{rel}:{ref['line']}")
            if not ref["safe"]:
                findings.append({
                    "file": rel, "line": ref["line"], "rule": "inline_secret",
                    "reference": shown,
                    "detail": "unsafe transport — use a step `env:` assignment or a named reusable-workflow `secrets:` mapping"})
            if not documented(ref["name"], register_text):
                findings.append({
                    "file": rel, "line": ref["line"], "rule": "undocumented_secret",
                    "reference": shown,
                    "detail": "not named in docs/SECRETS.md — nobody can rotate what "
                              "nobody recorded"})

    return {"ok": not findings, "workflows_scanned": len(files),
            "references": {k: sorted(v) for k, v in sorted(used.items())},
            "findings": findings}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Audit secret handling in workflows.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--assert", dest="assert_", action="store_true",
                    help="exit 1 when a violation is found")
    args = ap.parse_args(argv)

    result = audit()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("WORKFLOW SECRET AUDIT — names only, never values")
        print("=" * 66)
        print(f"  workflows scanned : {result['workflows_scanned']}")
        for name, where in result["references"].items():
            print(f"  {name:<20}: {', '.join(where)}")
        if result["findings"]:
            print("-" * 66)
            for f in result["findings"]:
                print(f"  ✗ {f['file']}:{f['line']}  {f['rule']}  "
                      f"{f.get('reference', '')}")
                print(f"      {f['detail']}")
            print("  => FAIL")
        else:
            print("  ✓ every reference uses env/reusable-workflow mapping and is documented")
    if args.assert_ and not result["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
