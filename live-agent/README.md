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
python3 test_server.py       # 8 wrapper tests (verification, denial, safety, restart restore, local bind)
python3 server.py --fresh    # explicitly DELETE this service's runs, sandboxes, and memory
```

Normal boots restore the recent `/api/runs` index from persisted DecisionRecords
and retain the sandbox artifacts and SQLite evidence memory. A clean reset is
opt-in via `--fresh`; tests and benchmarks use disposable temporary runtimes.

## Security and privacy

`live-agent/server.py` is a **local-only** demo server: it has no authentication or
rate limiting and must not be exposed directly to the internet. For deployment,
use [`deployment/live_server.py`](../deployment/README.md), which disables writes
without an operator-managed bearer token, applies a process-local write limit, and
ships read-only defaults for Render and Docker. That is still not a full public
production auth system: the shared token is not user identity, and a public auth
proxy is required for interactive writes.

**GET endpoints remain public.** Run lists, DecisionRecords, memory summaries, and
sandbox artifacts have no per-user access control. Do not submit passwords, API
keys, personal data, or confidential content to a publicly reachable instance;
read-only does not mean private. Terminate HTTPS at a trusted hosting proxy.

## API

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | Bilingual (EN/AR) live UI |
| `/api/health` | GET | Kernel status + supported goals |
| `/api/run` | POST | `{goal_type, params}` → full DecisionRecord JSON |
| `/api/run/<id>` | GET | Download the persisted evidence record |
| `/api/file?run=..&path=..` | GET | Download a sandbox artifact (traversal-safe) |
| `/api/runs` | GET | Recent runs |

Capabilities (kernel-defined): `create_file` · `read_file` · `append_file` · `list_dir` ·
`ensure_dir` · `delete_file` — all confined to a fresh sandbox per run.

## Why this matters for the launch

This is the answer to "can I actually try it?": a visitor submits a goal, watches the
7-stage loop light up from the **real** DecisionRecord, sees a denial when they attempt
a sandbox escape, and downloads the evidence JSON. No LLM theatre — the discipline is
the product at this stage.

Phase 2 plugs a real brain into the same loop (free-tier LLM or local streaming engine —
see [`docs/KIMI-K3-UTILIZATION.md`](../docs/KIMI-K3-UTILIZATION.md)).
