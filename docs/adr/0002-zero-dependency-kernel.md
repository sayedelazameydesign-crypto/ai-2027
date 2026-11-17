# ADR 0002 — Zero third-party dependencies in the execution path

- **Status**: Accepted (partially revisited for build tooling only)
- **Date**: 2026-11-17
- **Context**: an "AGI kernel" that needs a package index is not runnable in ten
  years, on a locked-down machine, or during an outage.

## Decision

- The kernel (`projects/agi-system/core`) and every runtime service import
  **standard library only**.
- `scripts/ci_guard.py imports` fails CI on any third-party import; the allowlist
  may only contain modules that resolve to a path inside this repository.
- Build tooling (`setuptools`, `wheel`) is allowed **outside** the kernel, in the
  packaging job only.
- Memory retrieval uses hand-written resources (thesaurus, stopwords) rather than a
  model, and publishes its weakness instead of upgrading the dependency surface.

## Consequences

- Runs on Python 3.10+ with no network, no venv, no lockfile.
- We cannot use `sqlite-vec`, `numpy` or a sentence encoder; retrieval stays
  lexical and its recall is capped. That cap is measured and published.
- Adding a dependency is a decision that needs a new ADR, not a convenience.
