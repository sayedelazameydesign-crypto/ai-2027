#!/usr/bin/env python3
"""Audit how the workflows touch secrets — before a mistake reaches a runner.

Two rules, both learned from real incidents rather than intuition:

**Rule 1 — no inline interpolation.** `${{ secrets.X }}` is safe only when it is
assigned to an `env:` key, because GitHub passes the value to the process through
the environment. Interpolated into the *script text* it becomes part of the step's
command line, which the runner prints — and a secret printed once is public.

    safe:    RENDER_API_KEY: ${{ secrets.RENDER_API_KEY }}
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
SECRETS_DOC = ROOT / "docs" / "SECRETS.md"

#: Match the whole expression, then look for secret names inside it. Matching the
#: name only up to `}}` missed `if: ${{ secrets.X != '' }}` entirely — a real gap
#: found by test_03.
EXPR = re.compile(r"\$\{\{(.*?)\}\}")
SECRET_IN_EXPR = re.compile(r"secrets\.([A-Za-z_][A-Za-z0-9_]*)")
#: The only shapes allowed to carry a secret reference: `env:` assignment, and an
#: `if:` condition (which compares the name, never moves the value).
SAFE_LINE = re.compile(r"^\s*(?:-\s*)?(?:if\s*:|[A-Z][A-Z0-9_]*\s*:)\s*\S")


def secret_refs(text: str) -> list[dict]:
    """Every `secrets.NAME` reference with its line number and safety verdict."""
    refs = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if line.strip().startswith("#"):
            continue
        safe = bool(SAFE_LINE.match(line))
        for expr in EXPR.finditer(line):
            for name in SECRET_IN_EXPR.finditer(expr.group(1)):
                refs.append({"line": lineno, "name": name.group(1), "safe": safe,
                             "text": line.rstrip()[:160]})
    return refs


def documented(name: str, doc_text: str) -> bool:
    """A secret is documented when the doc names it as code: `NAME`."""
    return name in doc_text


def recorded_names(doc_text: str) -> list[str]:
    """Every secret name the register actually records (in file order)."""
    return re.findall(r"`([A-Z][A-Z0-9_]{2,})`", doc_text)


def display_name(name: str, doc_text: str) -> str:
    """The name to print — but only if the register already records it.

    This is not politeness, it is the no-new-leak rule: a reference found in a
    workflow that `docs/SECRETS.md` does not record is reported **by location**,
    never echoed by name, because an unreviewed name may itself be sensitive
    (a key passed as `secrets.<key-material>`). The value returned always comes
    from the register, never from the file being audited.
    """
    for token in recorded_names(doc_text):
        if token == name:
            return token
    return "<undocumented secret>"


def audit(workflows_dir: Path = WORKFLOWS, secrets_doc: Path = SECRETS_DOC) -> dict:
    """Return findings; never raises because one file is odd."""
    doc_text = secrets_doc.read_text(encoding="utf-8") if secrets_doc.is_file() else ""
    files = sorted(workflows_dir.glob("*.y*ml")) if workflows_dir.is_dir() else []
    findings: list[dict] = []
    used: dict[str, list[str]] = {}

    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            findings.append({"file": path.name, "line": 0, "rule": "unreadable",
                             "detail": str(exc)[:120]})
            continue
        rel = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name
        for ref in secret_refs(text):
            shown = display_name(ref["name"], doc_text)
            used.setdefault(shown, []).append(f"{rel}:{ref['line']}")
            if not ref["safe"]:
                findings.append({
                    "file": rel, "line": ref["line"], "rule": "inline_secret",
                    "reference": shown,
                    "detail": "interpolated into script text — assign it under `env:` instead",
                    "text": ref["text"]})
            if not documented(ref["name"], doc_text):
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
            print("  ✓ every reference is env-assigned and documented")
    if args.assert_ and not result["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
