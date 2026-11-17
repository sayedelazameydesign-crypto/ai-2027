# 🎛️ Unified Command HUD — LIVE control over the REAL kernel

One screen: live telemetry, real `DecisionRecord`s, an escape test, a 100-input
adversarial red-team, a HUD-level dispatch freeze, and a read-only health audit
of all 48 projects. **Stdlib only. Zero third-party imports. No LLM.**

## Run it

```bash
# From the repository root:
python3 command-hud/server.py --port 8090 --host 0.0.0.0
```

Then open the **live preview URL** provided by Arena. If you are running it on
your own machine, `http://localhost:8090` works.

> ⚠️ **Two corrections to the command that was circulating:**
> 1. `python -m live_agent.server --port 8090` **does not exist** — there is no
>    `live_agent` package. The original Phase-1 wrapper is
>    `python3 live-agent/server.py --port 8090` (hyphen, script, not a module).
> 2. `localhost:8090` points at **your** machine, not at this sandbox. In Arena
>    the server runs remotely; use the preview URL. All browser-side API calls
>    here are **relative** (`api/state`), so they work behind the proxy.

## What is real (and what is not)

| Thing | Reality |
|---|---|
| Kernel execution | ✅ **REAL** — `live-agent/server.py::run_goal` imports `projects/agi-system/core` in place. Nothing copied, nothing simulated. |
| Policy wall / DENIED | ✅ Real — `../../escape.txt` is rejected before the executor runs, with a recorded reason. |
| Verification | ✅ Real independent verifier; the HUD **additionally** re-reads the produced file and recomputes `sha256` with `hashlib`. |
| SHA-256 on a DENIED run | ❌ **None, by design.** The kernel writes `metrics.json` internally even on denial; hashing that would be a false proof. Fixed by `test_hud.py`. |
| Planner | ⚠️ Deterministic, rule-based — **NO LLM**. Phase 2 is still an undecided owner decision. |
| Kill switch | ⚠️ Gates **this HUD's `/api/run` dispatch only**. It does not stop OS processes, and red-team/audit stay live so the wall remains provable. |
| Timings | Measured here with `perf_counter`, jitter shown. **NOT** the published `bench.py` reference numbers and not an SLA. |
| 48-project audit | Read-only. `PROVEN` requires a command actually executed here; `DECLARED` means documented but **not** executed. Only 1 project is PROVEN. |

## Endpoints (all relative-friendly)

| Method | Path | Purpose |
|---|---|---|
| GET | `/` · `/app.js` | Static HUD (no CDN, no external scripts) |
| GET | `/api/health` | Liveness + honest scope flags |
| GET | `/api/state` | Counters, p50/p95, last 30 events, audit summary, red-team result |
| POST | `/api/run` | `{goal_type, params}` → real kernel `DecisionRecord` |
| POST | `/api/escape` | Convenience `../../escape.txt` attack |
| POST | `/api/pulse` | One benign real run (the live-pulse toggle uses this) |
| POST | `/api/kill` | `{enabled}` → freeze/release HUD dispatch |
| POST | `/api/redteam` | 100 real attacks + 10 benign controls (not gated by freeze) |
| GET | `/api/audit` | Full 48-project audit JSON |
| POST | `/api/audit/refresh` | Re-run the 5 real verification probes in the background |
| GET | `/api/record/<run_id>` | Download the persisted DecisionRecord |

## Verification (reproducible)

```bash
python3 command-hud/test_hud.py -v          # 13 end-to-end tests, real HTTP + real kernel
python3 command-hud/redteam.py --markdown --assert
python3 command-hud/audit.py --report docs/REPOSITORY-AUDIT.md --assert-probes
python3 scripts/ci_guard.py imports         # HUD itself is stdlib-only too
```

### Red-team: the honest result

```
100 attacks | 100/100 expectations met | 75 DENIED | 25 CONTAINED-as-literal
0 breaches | 10/10 benign controls VERIFIED | p50 4.4 ms (this container)
```

This deliberately **refuses** a fake "100/100 DENIED" badge. The 25
Windows-separator / percent-encoded paths become literal filenames inside the
sandbox on Linux. Containment holds, but `core/policy.py` uses POSIX
`Path.resolve()` — cross-platform containment is **NOT VERIFIED**. See
[`docs/REPOSITORY-AUDIT.md`](../docs/REPOSITORY-AUDIT.md) §8.

## Files

| File | Role |
|---|---|
| `server.py` | Stdlib HTTP server; the only component that dispatches to the kernel |
| `kernel_bridge.py` | Imports `live-agent/server.py` in place, redirects evidence to an isolated runtime dir |
| `audit.py` | **Read-only** 48-project inspection + 5 executed probes → classified JSON/Markdown |
| `redteam.py` | 100 adversarial inputs + 10 controls against the real policy wall |
| `static/` | Dark bilingual HUD, zero external requests |
| `test_hud.py` | 13 end-to-end tests (real HTTP, real kernel) |
| `runtime/` | Generated evidence trail — **gitignored**, never committed |

## Scope discipline

This PR is **additive**: it creates `command-hud/` and `docs/REPOSITORY-AUDIT.md`
only. It does **not** rewrite any of the 48 projects, does not touch
`projects/agi-system/core`, does not change repo settings, and does not add a
model integration. Per the owner's governance prompt, it is opened as a
**reviewable PR and left unmerged** until explicit approval.
