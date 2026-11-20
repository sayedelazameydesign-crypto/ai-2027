<div dir="rtl">

# ⚙️ fleet-2027 — محرك مهام دائم لأسطول وكلاء يثبت عمله

**الوضع:** 🟢 مستقل · صفر اعتماديات خارجية (stdlib فقط) · Python 3.10+
**الاختبارات:** 174 اختبارًا · **البوابات:** 12/12 · **الأثر:** `launch/evidence/fleet-2027-gate.json`

> **البدائيات المشتركة:** التجزئة/الوقت (`contracts.py`)، وقاطع الدارة (`registry.py`)، وإخفاء الأسرار — كلها تُستورد من `kernel-2027` بدل إعادة تعريفها هنا. `scripts/kernel_guard.py` يُفشِل الـCI إذا عادت أي نسخة خاصة.

</div>

---

## Why this exists (the gap it closes)

The repository's own audit (`docs/pap-2027-engines.json`) recorded
**`agent-fleet = absent`**: a single `AutonomousAgent` planned and executed,
sessions were persisted for *resume*, and nothing coordinated cooperating
specialists — and nothing proved that a fleet could survive a crash, a lying
agent, or a dead provider.

`fleet-2027` is that missing layer. It is deliberately **not** a rewrite of the
kernel and it adds **no dependency**: it is `sqlite3` + `threading` + `hashlib`.

```
           ┌──────────────┐   submit(spec)   ┌────────────────────────┐
 user ───▶ │  FleetRunner │ ───────────────▶ │  DurableTaskStore      │
           │  (waves)     │ ◀─── claim ───── │  SQLite WAL+FULL       │
           └──────┬───────┘                  │  tasks/steps/attempts  │
                  │ execute                  │  effects/checkpoints   │
           ┌──────▼───────┐                  │  dead_letters/audit    │
           │ AgentRegistry│                  └────────────────────────┘
           │ breakers     │                            ▲
           │ bulkheads    │      verify(step, result)   │
           └──────┬───────┘   ┌──────────────────┐      │
                  └──────────▶│ Verifier         │──────┘
                              │ re-hashes disk   │  ok ⇒ record_success
                              │ refuses lies     │  ✗  ⇒ retry ⇒ DLQ
                              └──────────────────┘
```

## The five guarantees (each one falsifiable)

| Guarantee | What it means in practice | How it can be refuted |
|---|---|---|
| **Durable** | State lives in SQLite with `journal_mode=WAL`, `synchronous=FULL` | `kill -9` the worker → state is still there |
| **Resumable** | A *different* process requeues the stale lease and continues | attempt numbers must read `[1, 2]`, never `[1]` |
| **Exactly-once effects** | A deterministic key per step; replays are *counted*, not repeated | two attempts, one `fresh_effect: true` |
| **Verified, not trusted** | A step is done when the digest recomputed from disk matches | fabricate a digest → the step dead-letters |
| **Auditable** | Every transition is appended to a hash-chained log | edit a row → `verify_audit_chain()` fails |

## Quick start

```bash
# 1) Run the deterministic 6-step demo (real files, real compile+probe)
python3 fleet-2027/cli.py demo --db /tmp/fleet.db --workspace /tmp/ws

# 2) Inspect it — the exit code is part of the contract
python3 fleet-2027/cli.py status  --db /tmp/fleet.db --task <task-id>
python3 fleet-2027/cli.py verify  --db /tmp/fleet.db
python3 fleet-2027/cli.py dlq     --db /tmp/fleet.db

# 3) Run the twelve evidence gates (real SIGKILL + two OS processes + router failover, ~36 s)
python3 fleet-2027/gate.py --assert
```

Exit codes: `0` succeeded (degraded runs say so explicitly) · `2` terminal
failure / dead letter / cancelled · `3` not finished yet · `1` usage error.
A dead-lettered task never exits `0`.

## Using it as a library

```python
import sys; sys.path.insert(0, "fleet-2027")
from fleet2027 import (
    AgentRegistry, AgentSpec, ArtifactRef, DurableTaskStore,
    EvidenceKind, FleetRunner, PlanStep, StepResult, TaskSpec,
)

def writer(ctx):
    path = ctx.workspace_root / "out" / "report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {ctx.goal}\n", encoding="utf-8")
    return StepResult(
        ok=True, summary="report written", output=str(path),
        artifacts=(ArtifactRef.from_file(str(path), record_path="out/report.md"),),
    )

registry = AgentRegistry()
registry.register(
    AgentSpec(name="writer", role="writes reports",
              requires_evidence=(EvidenceKind.ARTIFACT_SHA256.value,)),
    writer,
)

spec = TaskSpec(
    task_id="demo-1",
    goal="write the report",
    steps=(PlanStep(step_id="write", agent="writer", action="write",
                    verification=(EvidenceKind.ARTIFACT_SHA256.value,)),),
)

with DurableTaskStore("/tmp/fleet.db") as store:
    report = FleetRunner(store, registry, workspace_root="/tmp/ws").submit_and_run(spec)
    print(report.status.value, report.verified)
```

### Writing an agent handler

A handler receives a `HandlerContext` and returns a `StepResult`. Three rules:

1. **Report artifacts with a workspace-relative path** (`record_path=`), then
   the digest — the verifier re-hashes the file from disk and refuses a mismatch.
2. **Declare the evidence you can actually produce** on the `PlanStep`
   (`verification=`), or the step cannot pass (`no check could be computed`).
3. **Raise `StepFailure`** for a retryable infrastructure problem. Anything else
   is treated as an agent bug: recorded, retried within the budget, then
   dead-lettered — it never takes the fleet down.

## Evidence requirements (`verification=`)

| Kind | Passes only when |
|---|---|
| `artifact_exists` | every claimed artifact resolves inside the workspace |
| `artifact_sha256` | the digest recomputed **from disk** equals the claim |
| `output_non_empty` | the handler returned output or evidence |
| `exit_code_zero` | `evidence["exit_code"] == 0` (an `int`, not `True`) |
| `test_passed` | `tests_run > 0` and `tests_failed == 0` |
| `json_schema` | the payload satisfies the declared subset of JSON Schema |
| `independent_review` | a **different** agent approved (self-review is refused) |
| `no_pending_compensation` | no rollback is still pending for the task |

## The twelve gates

```
parallel-wave · verification-refuses · retry-backoff · dead-letter
degraded-success · failover · idempotency · audit-chain · sigkill-resume
multi-process · router-failover · llm-verified-artifact
```

`multi-process` is the only gate that spawns **two interpreters** against one
database file instead of reasoning about coordination inside one process (see
"Several workers, one database" below).

`launch/evidence/fleet-2027-gate.json` holds the measured values, the platform,
and the SHA-256 of the artifact itself — CI re-computes that digest and fails if
it does not match.

## Honest limits (read this before quoting the module)

* **The demo agents are deterministic local programs.** They write real files
  and run a real `compile()` + behaviour probe, but **no LLM is called**. The
  CLI prints this on every run.
* **Threads inside one process by default.** A hard crash takes the whole wave
  with it; that is exactly why the lease + `recover` path exists — and the crash
  gate kills a real process to prove it. Work *can* now be spread over several
  processes sharing one database file (below), but nothing is replicated: a
  shared file on a network mount is still one failure domain.
* **Liveness is a heartbeat, not an oracle.** A worker killed with `SIGKILL`
  keeps looking alive until its heartbeat goes stale, so recovery latency is
  bounded by `--stale-after-s`. The gate measures that latency instead of
  pretending it is zero.
* **SQLite protects integrity, not authorship.** A writer with SQL access can
  forge rows; `verify_integrity()` says so in its `limitations` list.
* **Cancellation cannot interrupt a handler already inside a C extension.**
  The lease expiry is the backstop; heartbeats are separate from timeouts.

## LLM agents (A1)

`fleet2027.llm` wraps any completion backend as a fleet agent whose output must
still survive verification:

```python
from fleet2027.llm import LLMAgent, OllamaBackend

agent = LLMAgent(backend=OllamaBackend(model="qwen3.5:0.8b"))
registry.register(agent.spec(), agent.handler)

step = PlanStep(
    step_id="write", agent=agent.spec().name, action="generate",
    inputs={"prompt": "write add(a, b)", "out": "app.py", "language": "python"},
    verification=(EvidenceKind.ARTIFACT_SHA256.value, EvidenceKind.OUTPUT_NON_EMPTY.value),
)
```

```bash
# one model-backed step, verified (uses a local Ollama; or --backend scripted)
python3 fleet-2027/cli.py llm --db /tmp/f.db --workspace /tmp/ws \
    --prompt "write add(a, b)" --out app.py
```

| Backend | Use | Safety limit |
|---|---|---|
| `OllamaBackend` | default · Free-First · $0 | plain HTTP **loopback only** |
| `OpenAICompatibleBackend` | any compatible vendor | HTTPS only; key from env, never logged |
| `AsyncProviderBridge` | drive `python-2027` providers | provider is *injected*; no SDK import |
| `ScriptedBackend` | tests and the gate | never touches a network |

The model writes the file; the **verifier** re-hashes it from disk and decides.
A refusal, an empty answer, a truncated file, an oversized output, a path
outside the workspace, or a file changed after the digest was taken all fail the
step — and fail it the same way any other agent fails.

## Several workers, one database (A3)

The engine is no longer single-process. A worker is a row in the database, not a
private assumption:

```bash
# terminal 1 and terminal 2 — distinct ids, same file
python3 fleet-2027/cli.py worker --db /tmp/fleet.db --workspace /tmp/ws \
    --id w1 --idle-exit-s 5
python3 fleet-2027/cli.py worker --db /tmp/fleet.db --workspace /tmp/ws \
    --id w2 --idle-exit-s 5
```

| Mechanism | What it actually guarantees |
|---|---|
| `workers` table (schema v3) | who is alive, on which host/pid, how much work they kept |
| `claim_ready_steps(capacities=…)` | a wave never exceeds the agent's `max_parallel`, so surplus steps stay *queued* instead of burning attempts |
| `requeue_steps_of_dead_workers` | only a **provably dead owner's** steps move; a live worker's fresh lease is never stolen |
| write-ahead effects | `declare_effect_intent` → act → `complete_effect`; a crash leaves `state='intent'`, which a recovering worker must verify or compensate instead of repeating |
| `serve(idle_exit_after_s=…)` | exits by itself when the database owes nothing — a test, a CLI call and a cron tick all terminate |

Proven by `--mode concurrent | contention | kill` in
`scripts/multi_process_probe.py`, and by the `multi-process` gate: two real
interpreters split one task (no duplicated ledger line), a serial agent is never
oversubscribed, and a `SIGKILL` between the external write and its completion
leaves **4 effects for 4 steps** with zero unresolved intents.

Honest limit: SQLite on a local disk coordinates *processes*; it is not a
distributed consensus system. Two *machines* need a network filesystem with
working file locking, which is why `multi-node` stays unbuilt rather than
claimed.

## Routing steps through FreeFirstRouter (A2)

A model call is no longer "one backend, or nothing". A step can be driven by
`python-2027`'s `FreeFirstRouter`, which owns the *choice* of provider —
zero-spend budget guard, circuit breaker, retry with backoff — while the runner
still owns *what* must be done and whether it verified.

```python
from fleet2027.llm import LLMAgent
from fleet2027.routing import RouterBackend

backend = RouterBackend(router=free_first_router, request_factory=build_request)
registry.register(LLMAgent(backend=backend, agent_name="routed-writer").spec(), ...)
```

| Behaviour | What is recorded |
|---|---|
| first provider times out, second answers | `step.failover` event (persisted + audit-chained) and `evidence["routing"]["failovers"] = 1` |
| the router answers with its deterministic provider | `step.simulated` event — never passed off as a model |
| every provider fails | `RoutingExhausted` → retried, then **dead-lettered with the per-provider trail in the reason** |
| backend that has no router | `{"routed": false}` — an explicit "not routed", not a silent zero |

**The deterministic trap.** `build_default_providers()` ends with an always
"configured" deterministic provider. A naive wiring therefore reports success
with **zero model calls**. That is why live mode exits `3` (not `0`) unless a
*real* provider is configured — measured, not assumed:

```
$ python3 scripts/fleet_router_bridge_check.py --mode live
NOT ATTEMPTED: the router would answer from its deterministic simulation
provider. Set a real provider key (e.g. GEMINI_API_KEY) or run a local Ollama.
exit=3
```

Honest scope: the router is exercised for real (`FreeFirstRouter`, its budget
guard, its circuit breaker), but the *providers* in the `simulated` mode are
stubs; only the `live` mode talks to a real provider, and it needs a local
Ollama or a key.

**When the dependency is missing, the gate skips — it never passes.**
`router-failover` needs `pip install -e python-2027`; without it the gate returns
`status=skipped_missing_dependency` with the reason "this is not a pass", so an
unrun A2 can never look like a working A2. CI installs python-2027 explicitly
before running the gate (the fleet *package* still imports nothing external —
`Guards` proves that on every run). A measured live run is in `launch/evidence/fleet-router-live.json`
(gitignored by design — regenerate it, never quote it as live proof).

## What was *not* built here

An MCP server, a real browser under test, xlsx/pptx artifacts, adaptive
quality-based retry (A4), and multi-node (multi-*machine*) distribution. Each has a row and an
acceptance criterion in
[`docs/AI-2027-COMPETITIVE-ARCHITECTURE.md`](../docs/AI-2027-COMPETITIVE-ARCHITECTURE.md) §4.
