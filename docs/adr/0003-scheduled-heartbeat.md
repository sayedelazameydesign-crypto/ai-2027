# ADR 0003 — The repository must run without its owner

- **Status**: Accepted
- **Date**: 2026-11-17
- **Context**: `grep -rn "schedule|cron" .github/workflows/` returned nothing.
  Every guarantee depended on a human pushing. A project that stops when its
  author sleeps is a demonstration, not infrastructure.

## Decision

- `.github/workflows/ember.yml` beats every 6 hours: boots the real services, runs
  the 15 HTTP checks, computes a Heat Index, publishes status to the `heartbeat`
  branch, opens one issue when degraded and closes it on recovery.
- The beat also **pushes a commit**, because GitHub disables `schedule:` on public
  repositories after ~60 days without repository activity. The commit is fuel.
- The failure path is not decorative: `workflow_dispatch` accepts
  `force_degraded=true` so the immune loop can be exercised on demand.

## Consequences

- Continuous noise risk (4 beats/day). Mitigated: one issue maximum, label
  `ember-degraded`, auto-closed on recovery.
- A scheduled job is not an uptime SLA (GitHub delays cron under load); status is
  therefore published with timestamps, never as "always up".
- Verified on 2026-11-17: degraded dispatch failed the job, opened issue #34, and
  the next healthy beat closed it.
