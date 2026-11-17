# ADR 0001 — Evidence before claims

- **Status**: Accepted
- **Date**: 2026-11-17
- **Deciders**: maintainer
- **Context**: the README had numbers nobody could reproduce, and 40 of 48
  projects were declared rather than proven.

## Decision

A statement may appear in any document only if a command reproduces it.

1. Every published number names the command that produced it.
2. `python3 scripts/runtime_smoke.py` (15 checks over real HTTP) is the runtime gate.
3. `python3 memory/evaluate.py` is the retrieval gate; it prints the misses too.
4. Documents separate **PROVEN / DECLARED / SIMULATED** (see `docs/GROUND-TRUTH-2026-11-17.md`).
5. CI fails when a published number drifts from the measurement — the claim is read
   *out of the docs*, not duplicated in the workflow.

## Consequences

- Slower to write docs; cheap to trust them.
- Numbers that flatter us are removed from the README even when the code is fine.
- Some claims became embarrassing (2/16 paraphrase recall). They stayed published
  until a measured fix replaced them, which is exactly the point.
