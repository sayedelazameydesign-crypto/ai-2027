# 🧪 T4 / T5 / T6 — measured evidence (2026-11-17)

> **Rule of this file:** every number below was produced by the command printed
> next to it, in this repository, on 2026-11-17. Anything that could not be
> executed is reported as *not executed*, with the measured reason — not as done.

---

## T4 — continuity: one command, measured boot

| Claim | Command | Measured result |
|---|---|---|
| The whole stack boots with one command | `./scripts/ember_up.sh --check --no-browser --json` | `elapsed_ms: 2000` (cold), `smoke: 15/15 checks`, `smoke_exit: 0`, process exits by itself |
| Wall-clock restore time (KPI ≤ 10 min) | `time ./scripts/ember_up.sh --check` | **2.46 s** wall, cold, including the 15 HTTP checks |
| A second run reuses healthy services instead of duplicating them | `./scripts/ember_up.sh --json` | `reused: "live-agent hud"` |
| Stopping is one command | `./scripts/ember_up.sh --stop` | `stopped 2 process(es)` |

Boot logs, pids and the measured boot time land in `.ember-up/` (git-ignored), so
the script never commits a stale claim.

### Paths live today

| Path | Status | Evidence |
|---|---|---|
| GitHub Pages | ✅ live | `gh api repos/:owner/:repo/pages --jq .status` → `built` |
| One-command local boot (this repo) | ✅ live | table above |
| Hosted second mirror (Vercel) | ⛔ blocked by measured permission | `create_project` → *"Access denied. Your token may not have the required permissions."* |
| Supabase `beats` table | ⛔ blocked by measured cost decision | 4 projects on the account, **all `INACTIVE`** (paused); restoring or creating one is an owner decision |
| jsDelivr CDN mirror of `website/` | ✅ **live and measured** | beat run `44153401233`: `200` for `cdn.jsdelivr.net/gh/…/website/demo.html` → **3/3 paths live** |

### The 2026-11-17 18:57 UTC beat (first one with the new pipeline)

```
beat 18:57:59Z · heat 0.802 BURNING · embers 3/3 · smoke 15/15 · memories 1
path: 200  https://sayedelazameydesign-crypto.github.io/ai-2027/demo.html
path: 200  https://cdn.jsdelivr.net/gh/sayedelazameydesign-crypto/ai-2027@main/website/demo.html
gate: healthy · stage 0 · streak 0 · failures []
```

Its **first attempt exited 2** (a transient path failure while the CDN was cold),
the workflow retried once, and the retry was healthy. No issue was opened, no human
was paged — which is exactly what stage 1 is for. `ember/history.jsonl` gained
**one** row for that run, not two: the retry-inflation guard works in production.

The heat formula gives each of 3 paths `0.2/3 = 0.0667` — **≤ 0.07**, which is the
T4 acceptance criterion ("losing any one path must not cost more than 0.07 heat").
`scripts/health_gate.py` asserts that invariant numerically on every beat
(`path_independence`), so the number cannot drift away from the formula.

---

## T5 — immunity: the gate refuses silent degradation

`scripts/health_gate.py` judges one beat against the recorded past. Eleven checks,
one escalation ladder, three exit codes:

| Stage | Meaning | Exit | Action taken by `ember.yml` |
|---|---|---|---|
| 0 | healthy | `0` | nothing |
| 1 | transient (memory unreachable, no live path, first beat) | `2` | re-runs the beat once |
| 2 | the retry also failed | `1` | opens/updates **one** issue |
| 3 | the same degradation in **3 consecutive runs** | `1` | adds `escalation/s3`, mentions the code owner, POSTs `EMBER_ALERT_WEBHOOK` if configured |

Measured:

```
$ python3 scripts/health_gate.py --self-test
health_gate self-test: PASS (11 checks, verdict healthy)

$ python3 scripts/test_health_gate.py -v
Ran 24 tests in 0.11s — OK
```

The tests are the claims: a beat that reports `15/15` while having measured only 3
checks **fails** (`test_02`); a retry is not infinite (`test_07`); three degraded
runs escalate (`test_12`); an unconfigured webhook is reported as
`not-configured`, never as delivered (`test_15`).

### Live proof on GitHub — the whole ladder, executed

Three `workflow_dispatch` runs with `force_degraded: true`, then one healthy run:

| # | Run | Gate on the heartbeat branch | Effect |
|---|---|---|---|
| 1 | `44153564221` | `stage 2 · streak 1 · failures [smoke_complete]` | opened **issue #39** with the full 11-check table |
| 2 | `44154466911` | `stage 2 · streak 2` | updated the same issue (never a second one) |
| 3 | `44154576765` | `stage 3 · streak 3 · alert not-configured` | label **`escalation/s3`** + comment mentioning `@sayedelazameydesign-crypto` |
| 4 | `44154837814` | healthy | **closed #39 automatically at 19:05:51Z** |

Issue #39's body is the gate's own table — not a hand-written summary — including
`attempts: 2` and the exact failing check. The stage-3 comment says
`alert delivery: not-configured`, because no webhook secret exists: the gate
refuses to claim a page it did not send.

**Cost of the drill, stated:** three deliberately failed beats are in the 7-day
history, so the measured Heat Index fell from **0.802 (burning)** to **0.5013**
(success rate 2/5). The number is not massaged; it recovers as those beats age out
of the window.

---

## T6 — the product: one real task, under 60 seconds, with an evidence link

```
$ python3 scripts/run_task.py --boot --json
{
  "run_id": "run_1794941411_1b4d451a",
  "status": "VERIFIED",
  "verdict": "VERIFIED-AND-WITHIN-BUDGET",
  "claims": {"elapsed_ms": 22.9, "submit_ms": 16.1, "evidence_fetch_ms": 1.4,
             "boot_ms": 1301.8, "budget_ms": 60000.0, "within_budget": true},
  "artifact": {"path": "demo/t6-boot.txt", "bytes": 20,
               "hash_verified": true},
  "evidence_link": "http://127.0.0.1:8090/api/run/run_1794941411_1b4d451a",
  "bundle": "launch/evidence/task_run_1794941411_1b4d451a.json"
}
```

- **22.9 ms** from submit to a re-read evidence record and a locally recomputed
  SHA-256 that matched the kernel's — the published ceiling is 60 000 ms.
- Cold start included: **1.30 s** of boot is inside the same measurement.
- `DENIED` is reported as a **task failure** (`exit 1`), never as success;
  over-budget is `exit 3`; an unreachable agent is `exit 2`.
- The budget is falsifiable: `--budget 0.000001` exits 3 (`test_03`).

```
$ python3 scripts/test_run_task.py -v
Ran 6 tests in 2.62s — OK
```

---

## Release v1.0.1 (same pipeline, second time)

```
$ gh release view v1.0.1
tag=v1.0.1 · published 2026-11-17T19:07:45Z
  agi_kernel-0.1.0-py3-none-any.whl · 40604 B
  sbom.cdx.json                     ·  3686 B
  SHA256SUMS                        ·   184 B
notes: the real CHANGELOG entry (extracted by .github/workflows/release.yml)
```

Release run `44155173127` **success**: tests + `sbom --assert-pinned` + wheel +
SBOM + checksums + notes, with no manual step. `gh api …/releases/latest` → `v1.0.1`.

---

## What is still **not** done (said out loud)

1. A hosted second mirror needs the owner: Vercel returned *access denied* and the
   Supabase projects are paused. Both are recorded above with the exact response.
2. jsDelivr is wired into the beat but is **not** counted until a beat measures
   `200` for it.
3. Branch protection, Dependabot alerts and secret scanning are still owner-only
   (HTTP 403 measured — see `docs/GOVERNANCE.md`).
4. Gemini key revocation (#21) is still open and still the largest security item.
