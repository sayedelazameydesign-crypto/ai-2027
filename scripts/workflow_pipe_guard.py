#!/usr/bin/env python3
"""Refuse a `| tee` pipeline whose exit status cannot be trusted.

`cmd | tee log` reports the exit status of `tee`, not of `cmd`. In a workflow
that means a step which failed for a real reason still reports success. This
happened in ``ci.yml``: the ember boot step piped into ``tee`` and would have
passed with a dead ember.

Proven, not assumed::

    $ bash -c 'false | tee f; echo $?'
    0

A step is safe when it does any of these:

* sets ``pipefail`` before the pipeline (``set -euo pipefail`` or
  ``set -o pipefail``);
* captures ``${PIPESTATUS[0]}``;
* contains ``|| true``/``|| exit`` on the same line, which states the failure
  is expected;
* is a ``run:`` single line under ``continue-on-error: true``.

Usage::

    python3 scripts/workflow_pipe_guard.py            # report
    python3 scripts/workflow_pipe_guard.py --assert   # exit 1 on a finding
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

WORKFLOWS = Path(".github/workflows")
TEE = re.compile(r"\|[^|]*\btee\b")
MASK = re.compile(r"(\|\|\s*(true|exit\b))|(PIPESTATUS)")
SAFE_PROLOGUE = re.compile(r"set\s+(-[a-z]*o\s+pipefail|-euo\s+pipefail|-[a-z]*o\s+pipefail)")


def _step_blocks(text: str) -> list[tuple[int, list[str]]]:
    """Split a workflow into (first line number, lines) per ``run:`` block."""
    lines = text.splitlines()
    blocks: list[tuple[int, list[str]]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        match = re.match(r"^(\s*)(?:-\s+)?run:\s*(\||>|-)?\s*(.*)$", line)
        if not match:
            i += 1
            continue
        indent = len(match.group(1))
        body = [match.group(3)] if match.group(3) else []
        start = i + 1
        if match.group(2) in ("|", ">"):
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent:
                    break
                body.append(nxt)
                j += 1
            i = j
        else:
            i += 1
        blocks.append((start, body))
    return blocks


def scan(path: Path) -> list[tuple[int, str]]:
    """Return (line number, line) for every unguarded tee pipeline."""
    text = path.read_text(encoding="utf-8")
    findings: list[tuple[int, str]] = []
    for start, body in _step_blocks(text):
        joined = "\n".join(body)
        guarded = bool(SAFE_PROLOGUE.search(joined))
        # `${PIPESTATUS[0]}` is read on the line *after* the pipeline, so it
        # guards the whole step rather than one line.
        reads_status = bool(MASK.search(joined))
        for offset, line in enumerate(body):
            stripped = line.strip()
            if not TEE.search(line):
                continue
            if guarded or reads_status or MASK.search(line) or stripped.startswith("#"):
                continue
            findings.append((start + offset, stripped))
    return findings


def main(argv: list[str]) -> int:
    files = sorted(WORKFLOWS.glob("*.yml"))

    def iter_findings():
        for path in files:
            for number, line in scan(path):
                yield path.name, number, line

    total = 0
    for name, number, line in iter_findings():
        print(f"{name}:{number}: {line[:110]}")
        total += 1

    if total:
        print(f"\nFAIL {total} tee pipeline(s) can report success while the command failed.")
        print("Guard with 'set -euo pipefail', 'set -o pipefail', or read ${PIPESTATUS[0]}.")
        return 1 if "--assert" in argv else 0

    print(f"OK every tee pipeline in {len(files)} workflow file(s) is guarded by pipefail/PIPESTATUS.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
