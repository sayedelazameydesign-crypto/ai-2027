<div dir="rtl">

# ⚡ بروتوكول الإبهار البرمجي بايثون 2027 — `python-2027`

> **الافتراض:** الإبهار = جودة تتجاوز المتوقع ضمن قيد **$0** حقيقي وقابل للإثبات التنفيذي (`BudgetGuard: MAX_SPEND=0`)، مع بقاء النواة `projects/agi-system` صفر-اعتماديات وفق `ADR-0002` وتوثيق صريح للقيود دون ادعاء نجاح نشر حي لم يُختبر.

</div>

**One-command Agent Studio (Manus + Claude Workspace UI, L0/L1/L4/L5 + BudgetGuard + 48-Project Registry):**

```bash
./scripts/studio_up.sh          # boots http://0.0.0.0:8080/studio (use --check for HTTP smoke)
```

**One-command verification (locked dependencies via `uv==0.13.9`, $0, zero API keys required):**

```bash
bash python-2027/scripts/one_command_demo.sh --verify-all
```

Or inside `python-2027/`:

```bash
uv sync --locked --group dev
.venv/bin/ai2027-py27 demo
.venv/bin/ai2027-py27 agent "audit python-2027" --resume sess_xxxxxxxx  # continue after a crash
.venv/bin/ai2027-py27 models          # live defaults + retired IDs (no network)
.venv/bin/ai2027-py27 providers       # tier + $0 guard per provider
.venv/bin/ai2027-py27 registry
.venv/bin/ai2027-py27 agent "Audit python-2027 and generate a verified HTML + SVG architecture report"
```

---

## 🛡️ Production Hardening Controls (P0–P3)

1. **Real `$0` Budget Enforcement (`budget.py`, `router.py`, `workers/`)**:
   - Distinguishes `BillingTier.LOCAL` (`proven_zero_local`, `cost_known=True`), `BillingTier.FREE_TIER` (`declared_free_tier_unbilled`, `cost_known=False`), `BillingTier.UNKNOWN_COST`, and `BillingTier.PAID`.
   - **Model catalogue (`model_catalog.py`, `ai2027-py27 models`)**: every shipped default and every provider retirement lives in one place. Retired IDs (`meta/llama-3.1-8b-instruct` → HTTP 410; Groq `llama-3.1-8b-instant` / `llama-3.3-70b-versatile` → HTTP 404 on free keys) are blocked *before* a request with the dated replacement in the reason, and an `HTTP 410` answer is reported as `ProviderError("model_retired")`. OpenRouter defaults to the dynamic `openrouter/free` router so no pinned `:free` ID goes stale. Groq's replacement `openai/gpt-oss-20b` is priced per token, so it stays `UNKNOWN_COST` until declared in `AI2027_FREE_MODELS` — a paid model is never used to "fix" a free check. See [`docs/MODEL-CATALOG.md`](../docs/MODEL-CATALOG.md).
   - Having an API key (`GEMINI_API_KEY`, `OPENROUTER_API_KEY`, etc.) **never** proves `$0` usage by itself: unapproved models or OpenRouter models missing `:free` are classified `UNKNOWN_COST` and **blocked pre-request** by `BudgetGuard` when `AI2027_MAX_SPEND_USD=0`.
2. **Fail-Closed Cloudflare Workers (`workers/python-worker/` & `workers/edge-gateway/`)**:
   - Protected routes (`/v1/route`, `/v1/agent/plan`) return `401` by default when `WORKER_AUTH_TOKEN` / `GATEWAY_TOKEN` is missing unless the route is explicitly declared in `AI2027_PUBLIC_ROUTES`.
   - `Default.fetch()` enforces auth, `Content-Length`, and streaming chunk byte limits (`<= 32 KiB`) **before** buffering payloads in memory.
   - `python-worker/wrangler.toml` enables `python_workers_314` (`compatibility_date = "2026-09-08"`, `requires-python = ">=3.13"`).
3. **Replaceable & Persistent Observability (`telemetry.py`, `server.py`)**:
   - Supports `LOCAL` (in-memory), `PERSISTENT` (`spans.jsonl` + `logs.jsonl` evidence store), `DISTRIBUTED` (`traceparent` / `x-trace-id` header propagation), and optional zero-cost `EXPORT` (`HttpOtlpSpanExporter`).
4. **3-Tier 48-Project Compliance Registry (`compliance.py`, `ai2027-py27 registry`)**:
   - Scores all 48 `catalog.json` entries + core platform components across **Tier 1 (Production Python Standard)**, **Tier 2 (Lightweight / Edge Standard)**, and **Tier 3 (Legacy / Kernel Preservation — `ADR-0002`)** with AST verification that `projects/agi-system` remains pure `stdlib`.
5. **Studio Visibility (`studio.py`, `static/studio.html`)**:
   - Surfaces real `MAX_SPEND=0` budget counters, per-attempt errors/blocks, SHA-256 artifact proofs, persistent OTel spans, and `/v1/studio/compliance` across EN/AR UI.
6. **Ollama Local Intelligence Layer & Celia Memory RAG (`ollama_layer.py`, `celia_memory.py`, `ollama_readiness.py`)**:
   - **4GB RAM safe defaults (`4gb-laptop`)**: `qwen3.5:0.8b` chat, `qwen2.5-coder:0.5b` coding, `embeddinggemma` RAG, `OLLAMA_NUM_CTX=2048`, and `keep_alive=0` unload discipline after embeddings.
   - **Structured Outputs & Policy-Gated Tool Calling**: `/api/chat` JSON Schema verified by Pydantic V2 and tool proposals checked against `SandboxToolRuntime` allowlists (zero direct shell/GitHub access).
   - **Celia Memory Intelligence**: Persistent SQLite chunked RAG (`ai2027-py27 celia-memory index/search/stats`) supporting Ollama `/api/embed` or honest bilingual lexical fallback.
   - **Cloud Guard**: `OllamaCloudProvider` (`OLLAMA_API_KEY`) is separated from loopback Ollama and classified `UNKNOWN_COST` (blocked by `BudgetGuard` at `MAX_SPEND=0`).
   - **Phase-0 Checks**: `ai2027-py27 ollama-check` and `python-2027/scripts/ollama_windows_check.ps1` measure RAM/disk/daemon state and refuse to claim `VERIFIED` offline.

---

## ⚠️ Honest Scope & `[CAVEATS]`

- **Python version matrix:** CI tests both **Python 3.13** and **Python 3.14** strictly against `uv.lock` (`uv==0.13.9`). `python-2027/pyproject.toml` keeps `requires-python = ">=3.11"` for compatibility with Debian/Ubuntu hosts (`3.11.2`), while `workers/python-worker/pyproject.toml` enforces `requires-python = ">=3.13"` matching `python_workers_314`.
- **Cloudflare Workers local vs live:** `node --test`, `pytest`, `wrangler deploy --dry-run`, and local `wrangler dev --local` (on `edge-gateway`) verify local request/auth/budget/stream contracts. Local contract tests and CI runs **do not** prove a live deployment on Cloudflare Workers; live deployment requires `wrangler deploy` against a Cloudflare account.
- **Checkpoint/resume honesty:** the autonomous loop persists `checkpoint_step` after every completed step. `resume=True` (API body or `agent --resume SESSION_ID`) continues from the next step, and is **refused** — not silently restarted — when the session is not `running`, when the task text differs, or when nothing was checkpointed. `scripts/persistent_task_gate.py` proves it by SIGKILLing a real run inside step 3 and showing the second process starts at step 3.
- **Cost classification honesty:** Local deterministic/Ollama execution records `cost_known=True, cost_usd=0.0`. Remote free-tier calls record `cost_usd=0.0, cost_known=False, cost_status="declared_free_tier_unbilled"` unless an invoice/usage proof exists — never faking invoice proof.
- **Model IDs age:** the catalogue blocks retirements we have evidence for; it does not prove that a provider is serving a model right now, that a free plan still covers it, or that quota remains. Only `ai2027-py27 live --require-any` on `main` with Actions secrets shows the state at that instant.
