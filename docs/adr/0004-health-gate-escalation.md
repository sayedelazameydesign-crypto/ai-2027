# ADR 0004 — One retry, then an issue, then an escalation

- **Status**: Accepted
- **Date**: 2026-11-17
- **Context**: the ember beat could report `15/15` while having measured only 3
  checks, because every field it printed was true of *that* beat. Nothing compared
  a beat to the recorded past, so a capability could disappear without anyone
  being told. The same gap made the failure loop binary: one bad beat opened an
  issue, and a single flaky HTTP request looked identical to a real regression.

## Decision

- `scripts/health_gate.py` judges every beat against `ember/history.jsonl` and
  returns one of three exit codes: `0` healthy, `2` retry, `1` issue.
- The ladder is fixed and published:
  1. **stage 1** — a transient signal (memory endpoint did not answer, no public
     path, first beat with no baseline) gets **one** retry of the beat.
  2. **stage 2** — if the retry also fails, or the failure is a real regression
     (incomplete smoke, a check vanished, the last beat is stale), one issue is
     opened/updated with label `ember-degraded`.
  3. **stage 3** — three consecutive degraded **runs** add `escalation/s3`, mention
     the code owner from `CODEOWNERS`, and POST to `EMBER_ALERT_WEBHOOK` when that
     secret exists. When it does not, the gate writes `not-configured`; it never
     claims a page was sent.
- The **history row is committed by the gate**, on the run's final attempt only
  (`--commit-beat`, idempotent on `beat_ts`). A retry therefore cannot inflate
  `beats_7d` or the 7-day success rate.
- The gate asserts the T4 invariant numerically: losing one of three paths costs
  `0.2/3 = 0.0667 ≤ 0.07` heat.

## Consequences

- Escalation is measured in *runs*, not in minutes: three degraded runs at a 6h
  cadence means a real regression is escalated within ~18 hours, while a single
  flake is retried in the same workflow run and never pages anyone.
- The gate cannot see a server that never reported at all; that shows up as
  `beat_freshness` going stale on the next beat.
- The first beat on a fresh clone can only ever return *retry*, never *healthy* —
  the gate refuses to certify what it has no history for.
- Cost: one extra smoke run at most per beat (~1 s measured).
