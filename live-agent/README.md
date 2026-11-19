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
zero copied code, its architecture untouched. Its **86 unit tests pass** (re-verified
2026-11-17 on Python 3.11).

## Run

```bash
python3 server.py            # http://localhost:8090; preserves existing runtime data
python3 test_server.py       # 7 wrapper tests (verification, denial, safety, restart restore)
python3 server.py --fresh    # explicitly DELETE this service's runs, sandboxes, and memory
```

Normal boots restore the recent `/api/runs` index from persisted DecisionRecords
and retain the sandbox artifacts and SQLite evidence memory. A clean reset is
opt-in via `--fresh`; tests and benchmarks use disposable temporary runtimes.

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
