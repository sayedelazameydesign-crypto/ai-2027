# 🎛️ Unified Command HUD — LIVE control over the REAL kernel

One screen: live telemetry, real `DecisionRecord`s, an escape test, a 100-input
adversarial red-team, a HUD-level dispatch freeze, and a read-only health audit
of all 48 projects. **Stdlib only. Zero third-party imports. No LLM.**

## Run it

```bash
# From the repository root; binds only to the local machine:
python3 command-hud/server.py --port 8090
```

Open `http://localhost:8090` in a browser on the same machine. This service has
unauthenticated dispatch and control endpoints, so it is intentionally restricted
to an IPv4 loopback bind. `0.0.0.0`, LAN addresses, and hostnames are refused;
foreign `Host`/`Origin` requests are rejected to reduce DNS-rebinding and browser
CSRF exposure. **It is not available through Arena's remote preview or as a
network-shared service.** Do not tunnel or reverse-proxy it for remote use. A
remote control UI needs a separately designed authenticated HTTPS gateway first.

> `python -m live_agent.server --port 8090` **does not exist** — there is no
> `live_agent` package. The original Phase-1 wrapper is
> `python3 live-agent/server.py --port 8090` (hyphen, script, not a module).
> Browser-side API calls use relative paths; that does not make the HUD safe to
> publish through an unauthenticated proxy.

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

## Endpoints (operator-local only)

The routes below have no bearer authentication; every state/read endpoint and every
dispatch/control route is reachable only via the local loopback listener. Browser
POSTs must also have a matching loopback `Host` and `Origin`; command-line requests
without an `Origin` are supported for local operator use.

| Method | Path | Purpose |
|---|---|---|
| GET | `/` · `/app.js` | Static HUD (no CDN, no external scripts) |
| GET | `/api/health` | Liveness + honest scope flags |
| GET | `/api/state` | Counters, p50/p95, last 30 restored/live events, audit summary, red-team result |
| POST | `/api/run` | `{goal_type, params}` → real kernel `DecisionRecord` |
| POST | `/api/escape` | Convenience `../../escape.txt` attack |
| POST | `/api/pulse` | One benign real run (the live-pulse toggle uses this) |
| POST | `/api/kill` | `{enabled}` → freeze/release HUD dispatch |
| POST | `/api/redteam` | 100 real attacks + 10 benign controls (not gated by freeze) |
| GET | `/api/audit` | Full 48-project audit JSON |
| POST | `/api/audit/refresh` | Re-run the 5 real verification probes in the background |
| GET | `/api/record/<run_id>` | Download the persisted DecisionRecord |

## Runtime and topology

Normal starts preserve the gitignored `command-hud/runtime/` evidence, isolated
memory, and append-only `state/hud-history.jsonl`; the HUD restores its latest
400 events and backfills a run from its persisted DecisionRecord if shutdown
interrupts the HUD-history append. On POSIX, the service keeps its runtime data
owner-only, including the HUD-specific root-level `memory.sqlite` and its
sidecars, and refuses symlinked runtime or SQLite paths. `--fresh` is an
intentional, destructive reset and is also refused through a symlinked runtime
alias.

The HUD and Live Agent are separate HTTP surfaces, but **the HUD does not call
the Live Agent service over HTTP**. `kernel_bridge.py` imports the Live Agent
wrapper in-process and gives it a separate runtime directory. The runtime smoke
tests exercise both services over HTTP independently; they do not claim a
HUD-to-Live-Agent network hop. A network hop should only be added if a product
boundary actually requires it.

## Verification (reproducible)

```bash
python3 command-hud/test_hud.py -v          # 19 end-to-end tests, real HTTP + real kernel
python3 command-hud/redteam.py --markdown --assert
python3 command-hud/audit.py --report docs/REPOSITORY-AUDIT.md --assert-probes
python3 scripts/ci_guard.py imports         # HUD itself is stdlib-only too
```

### Red-team: the honest result

```
100 attacks | 100/100 expectations met | 91 DENIED | 9 CONTAINED-as-literal
0 breaches | 10/10 benign controls VERIFIED | p50 ~4 ms (this container)
```

**Cross-platform gap: CLOSED on 2026-11-17.** Previously 25 Windows-separator /
percent-encoded paths were only *contained on Linux*. `core/policy.py` now denies
backslashes, Windows drive prefixes and the encoded separators `%2f %5c %2e %00`
on every platform, so 16 of those inputs are rejected outright. The 9 that are
still accepted are literal POSIX names (`NUL`, `...`, `%252e%252e/x`) that resolve
inside the sandbox on any OS — nothing to traverse. The suite still refuses a
fake "100/100 DENIED" badge and publishes the 9.

## Files

| File | Role |
|---|---|
| `server.py` | Stdlib HTTP server; the only component that dispatches to the kernel |
| `kernel_bridge.py` | Imports `live-agent/server.py` in place, redirects evidence to an isolated runtime dir |
| `audit.py` | **Read-only** 48-project inspection + 5 executed probes → classified JSON/Markdown |
| `redteam.py` | 100 adversarial inputs + 10 controls against the real policy wall |
| `static/` | Dark bilingual HUD, zero external requests |
| `test_hud.py` | 19 end-to-end tests (real HTTP, real kernel, local exposure boundary, restore + artifact hash) |
| `runtime/` | Generated evidence, isolated memory, and HUD history — **gitignored**, preserved across normal restarts; `--fresh` is destructive opt-in |

## Scope discipline

This PR is **additive**: it creates `command-hud/` and `docs/REPOSITORY-AUDIT.md`
only. It does **not** rewrite any of the 48 projects, does not touch
`projects/agi-system/core`, does not change repo settings, and does not add a
model integration. Per the owner's governance prompt, it is opened as a
**reviewable PR and left unmerged** until explicit approval.
