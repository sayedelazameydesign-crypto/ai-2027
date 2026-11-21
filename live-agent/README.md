# 🧪 AI-2027 Live Agent Experience — Phase 1

> **Real execution. Independent verification. Zero dependencies.**
> تجربة وكيل حية: تنفيذ حقيقي، تحقق مستقل، بلا أي اعتمادات خارجية.

This is the first **runnable** product surface of AI-2027 — and it is deliberately honest
about what it is:

| Claim | Reality |
|---|---|
| Execution | ✅ **Real** — the `agi-system` kernel runs file goals in a per-run sandbox |
| Policy enforcement | ✅ **Real** — path traversal is DENIED with a recorded reason (try it) |
| Verification | ✅ **Real** — an independent Verifier checks the world; sha256 evidence is filed |
| Failure handling | ✅ **Real** — failures are reported as FAILED, never dressed as success |
| Planner | ⚠️ **Deterministic, rule-based — NOT an LLM** (the LLM brain is Phase 2) |
| LLM "thinking" | ❌ **Not in this phase** — no model is called; nothing is simulated to look like one |

The kernel is **imported in place** from [`projects/agi-system`](../projects/agi-system) —
zero copied code, its architecture untouched. Its **104-test suite passed on 2026-11-19**;
from the repository root, rerun it with:

```bash
(cd projects/agi-system && python3 -m unittest discover -s tests)
```

## Phase 2 planning seam — mock-only, not enabled in the UI

The kernel already accepts `Planner.propose(goal) -> list[Plan]`; `Kernel` then checks
all actions through its own policy before execution. `llm_planner.py` adds a small,
stdlib-only adapter contract for a future structured-plan provider. Its request contains
the `Goal`, explicitly supplied context, and limits; it receives no executor, filesystem
handle, or environment map. Callers must not place secrets in the goal or context. The
provider can only return bounded JSON plan proposals.

This is groundwork only: **no real provider is installed or called**, and the HTTP UI
still uses the existing deterministic planner. A separate external budget gate must
allow a provider call first. Budget rejection, provider failure, or an invalid
response ends the run as `FAILED`; there is **no implicit deterministic fallback**.
State-changing plans fail closed unless an external approval gate explicitly approves
them. There is not yet a user-facing approval flow, so do not wire a real provider into
the UI. The kernel policy, sandbox, verifier, and append-only ledger remain authoritative;
the ledger is stored under `runs/ledgers/`, outside each model-accessible sandbox.

Run the no-network, mock-provider integration tests with:

```bash
python3 live-agent/test_llm_planner.py
```

## Run

```bash
python3 server.py            # http://localhost:8090; preserves existing runtime data
python3 test_server.py       # 16 wrapper tests (authorization, privacy, safety, restart restore, local bind)
python3 server.py --fresh    # explicitly DELETE this service's runs, sandboxes, and memory
```

Normal boots restore the recent `/api/runs` index from persisted DecisionRecords
and retain the sandbox artifacts and SQLite evidence memory. On POSIX, the service
sets a restrictive `umask` and keeps runtime directories owner-only (`0700`) and
runtime files owner-only (`0600`), tightening modes on existing runtime data at
startup. With no credentials,
private reads fail closed and writes are disabled; set reader hashes and an operator
write token only in a trusted local secret manager/environment when exercising the
HTTP UI. A clean reset is opt-in via `--fresh`; tests and benchmarks use disposable
temporary runtimes. The one-command local helper `scripts/ember_up.sh` creates
random loopback-only API credentials when none are supplied; it keeps them in the
ignored `.ember-up/api-credentials.json` file with owner-only permissions so the
local smoke checks can authenticate. Treat that file as a secret and never upload it.

## Security and privacy

All API data routes fail closed in both the direct server and deployment launcher.
Only `GET /api/health` is public and returns a short status object; every other
`/api/` GET (including run records, artifacts, memory/search, metrics, and the agent
card) requires a configured reader credential. `POST /api/run` requires an operator
write token, and `AI2027_READ_ONLY=1` always disables writes. No reader hashes or
write token configured means private routes are unavailable, not public.

Reader clients send `Authorization: Bearer <token-id>.<secret>`; the service stores
only SHA-256 hashes of high-entropy reader secrets in `AI2027_READ_TOKEN_HASHES`.
Writes use `Authorization: Bearer <AI2027_WRITE_TOKEN>`. Repository HTTP clients
attach bearer credentials only to HTTPS URLs, except loopback HTTP for a local
service (which bypasses configured HTTP proxies); authenticated redirects are
refused instead of forwarding tokens. Read and write throttles
are process-local, keyed by the peer IP seen by Python, and are not distributed
edge protection. These interim bearer credentials are **not** a user-login,
per-user authorization, or production multi-user system. Use a trusted HTTPS
identity gateway for real users and edge rate limiting. Never embed secrets in
public HTML, query strings, committed files, or logs.

The static UI's credential fields are transient page inputs; this page does not
write them to localStorage, cookies, or URLs and sends them only in Authorization
headers. Enter credentials only when the UI is served over HTTPS behind an approved
gateway routing `/api` to this service. Browser-based authorization does not make a
shared token safe for public multi-user access. Run records, artifacts, memory, and
backup archives may contain user content; do not submit confidential material until
the gateway, persistent storage, encryption, and retention policies are approved.

See [`deployment/PERSISTENCE.md`](../deployment/PERSISTENCE.md) for the offline
backup/restore design and its limits. No hosting configuration is changed by that
design.

## API

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | Bilingual (EN/AR) static UI; it contains no API credentials |
| `/api/health` | GET | Minimal public service status |
| `/api/run` | POST | `{goal_type, params}` → DecisionRecord JSON; operator Bearer token required |
| `/api/run/<id>` | GET | Download persisted evidence; reader token required |
| `/api/file?run=..&path=..` | GET | Download sandbox artifact (traversal-safe); reader token required |
| `/api/runs` | GET | Recent runs; reader token required |
| `/api/memory`, `/api/memory/search` | GET | Memory summary/search; reader token required |
| `/api/metrics`, `/api/agent-card` | GET | Internal metrics/agent metadata; reader token required |

Every `/api/` route except `GET /api/health` is private by default, including
unknown routes and future additions. Private reads require a configured token ID and
secret; an empty `AI2027_READ_TOKEN_HASHES` map denies them.

Capabilities (kernel-defined): `create_file` · `read_file` · `append_file` · `list_dir` ·
`ensure_dir` · `delete_file` — all confined to a fresh sandbox per run.

## Why this matters for the launch

This is the answer to "can I actually try it?": a visitor submits a goal, watches the
7-stage loop light up from the **real** DecisionRecord, sees a denial when they attempt
a sandbox escape, and downloads the evidence JSON. No LLM theatre — the discipline is
the product at this stage.

Phase 2 plugs a real brain into the same loop (free-tier LLM or local streaming engine —
see [`docs/KIMI-K3-UTILIZATION.md`](../docs/KIMI-K3-UTILIZATION.md)).
