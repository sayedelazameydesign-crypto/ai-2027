# Phase 2A — the local watchdog

**Status:** built, 44 tests green, plus an end-to-end run against a real HTTP
source that is killed mid-flight. **Not yet run against `api.render.com`** — see
"What is still not proven" at the bottom. That distinction is not a formality.

```
monitoring/
├── local_watchdog.py   the loop, the hash-chained log, verify/report, CLI
├── render_source.py    the Render source (fails loudly without a key)
├── test_watchdog.py    44 tests
└── e2e_demo.py         a live HTTP source that dies and recovers
```

Zero third-party dependencies, enforced by an `ast` gate in CI that fails the
build on any import outside the standard library.

---

## What it does

Polls a source on a fixed cadence and appends one record per poll to a JSONL log.
Every record commits to the hash of the record before it. When the loop was not
running, that is recorded too. The point is not to collect data — it is to
produce a log that **cannot quietly disagree with reality**.

```bash
# read-only, no key needed: proves the loop mechanics
python3 monitoring/local_watchdog.py run --log watch.jsonl \
    --interval 60 --source monitoring/e2e_fixture.json

# against Render (needs RENDER_API_KEY; fails loudly without one)
RENDER_API_KEY=rnd_... python3 monitoring/local_watchdog.py run \
    --log watch.jsonl --interval 60 \
    --source monitoring.render_source:render_snapshot

python3 monitoring/local_watchdog.py verify --log watch.jsonl --anchor watch.anchor
python3 monitoring/local_watchdog.py report --log watch.jsonl
```

`verify` exit codes: **0** clean · **1** tampering · **3** clean but a write was
interrupted. Exit 3 exists because a power cut and an attacker are different
facts and a monitoring tool that merges them teaches you to ignore both.

---

## The three guarantees, and exactly where each one ends

### 1. Silence is recorded, and it is not the same as failure

Two different gaps are kept distinct on purpose:

| Record | Means | What you conclude |
|---|---|---|
| `silence` | the loop was not running or not recording | the **host** was asleep, killed, or blocked |
| `poll_failed` | the loop was alive, the source was not answerable | the **network or the API** was down |

Collapsing these into one "no data" bucket throws away the only information that
distinguishes "my laptop died" from "Render was unreachable". The e2e demo
exercises both: it kills the HTTP source (→ `poll_failed`, loop survives) and
`kill -9`s the watchdog itself (→ `silence` on restart, no `stop` record).

A hard kill is detected on the *next* start by comparing the wall clock with the
last record's timestamp. Nothing needs to run for that to work.

### 2. The log is tamper-evident — up to a measured boundary

This is where the honest version differs from the brochure version. Every claim
below is a test, not a paragraph:

| Attack | Caught by | Caught? |
|---|---|---|
| change a value in place | `hash_mismatch` | ✅ |
| reorder two records | `prev_mismatch` | ✅ |
| delete or replace a record, hashes untouched | `seq_break`, `prev_mismatch` | ✅ |
| **rewrite the whole file and recompute every hash** | **nothing** | ❌ |
| the same rewrite, with an `--anchor` | `anchor_ahead` / `anchor_mismatch` | ✅ |

The fourth row is not a defect waiting on a cleverer chain. **No purely local
hash chain can beat an attacker who owns the whole file.** I measured this rather
than assuming it: a consistently recomputed rewrite verifies as *intact* with no
anchor, and fails the moment there is one (`test_17`, `test_30`). If you take one
thing from this document, take that one — "tamper-evident" is routinely sold as
"tamper-proof" and it is not the same claim.

**So use an anchor.** `--anchor` appends the current head hash to a second file
on every stop. For real protection that file must live somewhere the attacker
does not control: a commit, a ticket, a second machine. If both files are on the
same disk and the attacker has the disk, nothing here helps and I will not
pretend otherwise.

### 3. A crash does not corrupt the rest of the log

A power loss can leave a half-written final line. That is detected, reported as
`truncated_tail`, and repaired on the next append — and the repair is written
into the log as a `tail_repaired` record carrying the number of bytes dropped.

Without the repair, appending after a torn line fuses the fragment with the new
record and *every* later verification fails at a random index: one crash would
destroy the whole log's credibility. With it, one crash costs one record, and the
loss is visible.

The first version of this code counted the torn fragment a second time as an
unparseable line, so **a power cut was reported as forgery**. That is the exact
failure this module exists to prevent, and the test suite caught it, not review.

---

## Two bugs the tests found

Both were shipped as plausible code, and neither was visible by reading it.

**Sub-millisecond jitter recorded as an outage.** The drift check fired whenever
`now > expected`, which includes being *early*. A four-writer run produced **92
"silence" records whose `gap_seconds` was 0.002**. A log full of fake incidents
is worse than no log, because it trains the reader to skip the entry that matters.

The fix is a `--min-gap` floor (default 1s) plus counting what it suppresses:
every `stop` record carries `jitter_suppressed` and `jitter_largest_seconds`.
Measured after the fix, eight concurrent writers: **0 silence records, 99
suppressed at ≤11.6 ms**, and a genuine 180 s hole still recorded as `silence`
with `missed_intervals: 3`. The threshold is auditable instead of silent.

**A crash reported as forgery.** Described above. `verify` now exits 3.

---

## Concurrency

Multiple watchdogs may share one log. Writes take an exclusive `flock`, read the
head, append, and release; `seq` stays contiguous across writers. Tested with 4
and 8 concurrent processes: zero corrupted lines, contiguous sequence numbers,
unique hashes, chain intact — 160 and 320 polls recorded exactly.

This is a lock, not a distributed consensus protocol. It is correct for several
processes on one machine. It is **not** correct for writers on different machines
sharing a network filesystem, and it does not claim to be.

---

## What the log contains, and what it deliberately does not

`render_source.py` records service names, `suspended` state, deploy IDs and
deploy statuses. It does **not** read env vars, log bodies, or any secret value —
so the log cannot become the leak. Without `RENDER_API_KEY` it raises, on
purpose: a source that returns a plausible "all good" when it has no credentials
produces a monitoring system that is green and blind at the same time.

The log file is created `0600`. It contains service names and error text; it is
not world-readable, and `test_71` enforces it.

---

## What is still not proven

- **No live Render data has ever passed through this loop.** The sandbox that
  built it cannot reach `api.render.com` (TCP opens, TLS is reset). Everything
  above was proven against a local HTTP server and fixtures. The first real run
  is yours, and the honest expectation is that something in `render_source.py`
  needs adjusting the moment a real response arrives.
- **Coverage is a claim about the loop, not about the service.** `observed_fraction`
  says how much of the wall clock the loop can vouch for. It says nothing about
  whether the service was healthy during that time — that is what the snapshots
  are for.
- **No alerting.** This records; it does not wake you up. Phase 2B.

## Numbers from the runs in this repo

| Scenario | Result |
|---|---|
| 4 concurrent writers × 40 polls | 160 polls, 0 silence, 14 jitter suppressed, chain intact |
| 8 concurrent writers × 40 polls | 320 polls, 0 silence, 99 jitter suppressed (max 11.6 ms), chain intact |
| e2e: source killed, watchdog `kill -9`d, restarted | 3 `poll_failed`, 1 `silence` (1.618 s), `observed_fraction` 0.532, intact |
| Hand-edit one value | `verify` exits 1 |
| Consistent full rewrite, no anchor | verifies **intact** — the documented limit |
| Same rewrite, with anchor | detected |
