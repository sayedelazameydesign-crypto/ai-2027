# Phase 2 decision: where the monitoring center runs

**Status:** decided. This is the answer I owed you, with the reasoning that
produced it and the conditions that would reverse it.

---

## The decision

**Build Phase 2 locally first. Do not put the monitoring center on Render yet.**

Concretely:

| Component | Where it runs | Why |
|---|---|---|
| The adapter (`render_control_adapter/`) | anywhere; stdlib only | no state, no server, no cost |
| The **audit / inventory** passes | **your machine, on demand** | read-only, minutes, zero cost |
| The **monitoring loop** (Phase 2) | **a long-running process on your machine** | it needs to outlive a request, and Render cannot be the thing that watches Render |
| The **dashboard/HUD surface** | local, later a static page | no server-side secret handling |
| Render-deployed services | unchanged | they are the *subject*, not the *host* |

Before Phase 2 code starts, one gate: **the Phase 0 report must exist.** It is
the only thing that converts the adapter's fixtures from "shaped like the docs"
to "shaped like your account".

---

## Why, in the order the reasons actually bind

### 1. A watcher on the watched platform is a single point of failure

If the monitoring center runs *on* Render and the Render service it watches dies
— or the whole region degrades — the monitor dies with it and reports nothing.
Silence looks identical to "everything is fine". This is the classic reason
production monitoring never runs on the monitored host.

This one reason is sufficient on its own. The rest are second-order.

### 2. It inverts your own secrets rule

You said: the agent must never read secret values, log them, or send them to
another agent. Hosting the monitoring center on Render means the
`RENDER_API_KEY` — an account-wide key, not a scoped one — sits in a Render
service's environment as a value that Render itself stores and injects.

That is not a leak. But it is a **wider blast radius than necessary**: the key
can restart, deploy, change env vars and delete services across the whole
account. Putting it on a machine that is already internet-facing is trading real
risk for a small amount of convenience.

### 3. The free plan is not a monitoring substrate

Free-tier services sleep. A process that sleeps cannot poll on a schedule, and a
monitoring loop that misses windows is worse than no loop, because it produces
gaps that look like incidents. You also asked for **near-zero budget**. Local
execution costs exactly zero and has no sleep behaviour.

### 4. There is nothing to deploy yet

Phase 2 is a loop plus a state store plus a report. The adapter is already
written such that none of that needs to be a web service. Deploying before there
is a service is the exact "deploy and hope" pattern this project keeps rejecting.

---

## What this decision costs

Being honest about the trade, not just the win:

- **Your machine must be awake** for the loop to be continuous. That is a real
  limitation, not a footnote. Mitigation: the loop is designed to be
  **resumable and stateless-on-restart** — it writes an append-only event log and
  a last-seen cursor, so a gap is *visible* in the data rather than silently
  smoothed over. A monitoring system that hides its own downtime has failed.
- **No shared dashboard** until we choose to build one. Phase 2's output is a
  report file plus a local page, not a URL you can open from a phone.
- **If you later want 24/7 coverage**, the correct move is a *separate cheap
  host* (not the watched account's own Render workspace), and the decision
  reverses — see below.

---

## Conditions that reverse this decision

Any one of these flips Phase 2 to cloud hosting, and I would say so:

1. You need the loop running when your machine is off, **and** you accept a
   second provider (so the watcher is not on the watched platform).
2. The Phase 0 report shows enough services that polling from a residential
   connection is unreliable.
3. Render's API rate limits (logs: 30/min) turn out to require a cadence your
   machine cannot hold.

Until one of those is true, cloud hosting buys nothing and costs risk.

---

## What I am building next, in this order

1. **Phase 2a — the loop, locally.** Poll deploys and service status, append to a
   JSONL event log, compute state transitions, write a report. No dashboard yet.
   Tested against recorded fixtures; the loop's arithmetic (downtime,
   flapping, time-to-detect) is unit-tested without a network.
2. **Phase 2b — error analysis on real log lines.** `diagnose()` exists and
   classifies; Phase 2b feeds it real shapes from the Phase 0 report and measures
   how often it says "unclassified" instead of guessing. That number gets
   published either way.
3. **Phase 3 gate — CI before deploy.** Refuse to deploy when the GitHub check
   run for the commit is red. This is the one automation I will not ship without
   the check-run read path being tested first.

**Not** in this order: dashboards, alerts to your phone, self-healing. Self-healing
is Phase 4 and it will not exist before the loop has proven it can detect a
failure it did not cause.

---

## One thing still owed to you, unrelated

The Render work is Phase 1 of the Render plan and does not close the three
standing gates: the case study is still **NOT PUBLISHABLE** (it needs your real
Pro-Print measurements, which I cannot invent), and the adversarial simulator —
five categories, 100% block rate, p50/p95 — was built once and lost in a reset.
It is not done and I will not claim it is.
