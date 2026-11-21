# AI-2027 Evolution Baseline

This is an additive starting point for the evolutionary work. It records measured
state; it is not a claim that every archived project is a complete or production
system.

## Repository checkpoint

- Active working branch: `arena/577f283e-ai-2027`
- Baseline commit: `3ba54f612555e45c6adf5533f34753adf309bc7c` (merge commit for PR #148)
- The baseline was taken before this document was added.
- Method: `python3 command-hud/audit.py --assert-probes`
- The audit reports that it only inspects `projects/` and runs probes; it does not
  modify archived project files.

## Measured inventory

| Measure | Result |
|---|---:|
| Archived projects | 48 |
| Files (filesystem / catalog) | 3,981 / 3,981 |
| `PROVEN` | 1 |
| `DECLARED` | 40 |
| `SIMULATED` | 6 |
| `FAILED` | 1 |
| Projects with tests / CI | 22 / 19 |
| Projects without a license file | 23 |
| Audit probes passed / failed | 5 / 0 |

These classifications describe evidence available to the audit, not product
completion. Do not promote a status without a reproducible test or other explicit
evidence.

## Probe evidence at the baseline

- Kernel unit suite: 104 tests passed.
- Live Agent server suite: 8 tests passed.
- Zero-third-party-import AST gate: passed.
- CI guard self-tests: 9 passed.
- Public demo contract: 12 tests passed.
- `probe_failed`: `false`.

Post-merge GitHub Actions for the baseline commit also reported Evidence-First CI
and CodeQL successful. The opt-in live Gemini check is not part of this baseline.

## Known issues and boundaries

- The audit identifies one invalid certification JSON artifact:
  `projects/12pro/certification/v1.0.0-raw/health-raw.json`.
  It is inside the archived source tree. Preserve that tree; do not edit the mirror
  locally. The prepared correction belongs in the original repository, subject to
  owner authorization and write access.
- Issue #21 tracks owner action for the exposed Gemini key. A code change or
  redaction is not proof that a key was revoked; only its owner can rotate it.
- Issue #23 tracks repairs blocked from the original repositories by the current
  read-only GitHub permission. Do not retry those writes without authorized access.
- General outbound egress is unavailable in this environment
  (`ALLOWLISTED_ONLY`); failed probes to non-allowlisted hosts are not proof that
  those services are down.
- Live Agent GET endpoints expose run records and sandbox artifacts without
  per-user access control. Read-only mode is not privacy.
- License, product, and model-provider decisions remain with the owner.

## Non-destructive operating rules

1. Work on the active branch and add small, reviewable changes; do not reset,
   force-push, delete, move, or overwrite existing project data.
2. Keep verification runs isolated in temporary directories. Do not use the
   destructive `--fresh` option on persistent user data.
3. Preserve the archive under `projects/`; handle corrections through the
   authorized original-repository workflow, not by silently changing the mirror.
4. Keep the AWR implementation and its exclusion decision unchanged.
5. Do not enable public writes or deploy without explicit authorization and
   security controls appropriate to the intended audience.

## Next gate

Before feature work, select one bounded user-facing capability and define its
acceptance tests. Implement it additively, verify that existing behavior and data
remain intact, then review the diff and the test evidence before proceeding to
another capability.
