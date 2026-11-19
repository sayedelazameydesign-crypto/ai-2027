<div dir="rtl">

# ⚡ بروتوكول الإبهار البرمجي بايثون 2027 — `python-2027`

> **الافتراض:** الإبهار = جودة تتجاوز المتوقع ضمن قيد **$0**، والمنصات المستهدفة **Cloudflare Workers + Google Colab + CI مجاني**، مع بقاء النواة `projects/agi-system` صفر-اعتماديات وفق `ADR-0002`.

</div>

**One-command Agent Studio (Manus + Claude Workspace UI, L0/L1/L4/L5 unified, $0):**

```bash
./scripts/studio_up.sh          # boots http://0.0.0.0:8080/studio (use --check for HTTP smoke)
```

**One-command verification (works from first run, $0, zero API keys required):**

```bash
bash python-2027/scripts/one_command_demo.sh --verify-all
```

Or inside `python-2027/`:

```bash
uv run ai2027-py27 demo
uv run ai2027-py27 agent "Audit python-2027 and generate a verified HTML + SVG report"
```

---

## 📋 The 10 Protocol Rules — Mapped to Code & Verification

| # | Rule | Implementation | Mechanical Gate |
|---:|---|---|---|
| 1 | **Python 3.13+ · `uv` · `ruff` · Strict Types + Pydantic V2** | `pyproject.toml`, `contracts.py` (`ConfigDict(extra="forbid", frozen=True)`), `from __future__ import annotations` | `ruff check`, `mypy --strict` + `pydantic.mypy`, CI matrix on Python `3.13` (locally runnable on `3.11+`) |
| 2 | **Small Modular Units · Swappable Contracts** | `AIProvider`, `TelemetrySink`, `AsyncHttpTransport` `typing.Protocol` interfaces | `create_app(...)` & `FreeFirstRouter(...)` inject every dependency |
| 3 | **Concurrency: `asyncio` + `TaskGroup`, Non-Blocking** | `FreeFirstRouter.route_batch()`, `ProtocolAuditor.audit_projects()`, `httpx.AsyncClient` | Ruff `ASYNC` rules + AST test asserting zero blocking calls inside `async def` |
| 4 | **Performance: Measure Before Optimizing** | `benchmark.py` (`perf_counter_ns` stats: sequential vs `TaskGroup`, uncached vs precompiled regex, Pydantic V2 ops/s) | `ai2027-py27 bench` + `tests/test_benchmark.py` |
| 5 | **Testing: `pytest` + `hypothesis`, Coverage >90%** | `tests/` with Hypothesis property fuzzing + async & HTTP tests | `pytest --cov-fail-under=90` (branch coverage) |
| 6 | **Security: Zero Secrets · Validate Every Input** | `security.py` (traversal containment, SSRF host guard, prompt sanitizer, regex + key scrubbing) | `scripts/secret_scan.py --all --scope code` + Hypothesis fuzz tests |
| 7 | **Observability: `structlog` + OpenTelemetry** | `telemetry.py` (`ObservabilityRuntime` with OTel `TracerProvider` + `InMemorySpanExporter` + scrubbed `structlog`) | Every route/audit/bench captures real OTel `trace_id` & `span_id` |
| 8 | **AI Resilience: Free-First · Fallback · Timeout · Retry** | `ai_providers.py` + `router.py` (`Ollama` → `Gemini` → `NVIDIA NIM` → `Groq` → `OpenRouter` → `DeterministicFallback`) | Per-provider `asyncio.timeout`, jittered retry on `429`/`5xx`, circuit breaker |
| 9 | **Delivery: Automated CI · $0 Deployment** | `.github/workflows/python-2027.yml`, `workers/python-worker/`, `workers/edge-gateway/`, `colab/` | Automated CI on GitHub Actions ($0) + gated `live-actions-keys` job for `GEMINI_API_KEY` & `NVIDIA_API_KEY`; Worker & Edge Gateway tests |
| 10 | **Excellence: First-Run Ready · 1-Command Demo · 1-Line Docstrings** | `scripts/one_command_demo.sh`, `ai2027-py27 demo`, `colab/ai2027_python_protocol_lab.ipynb` | Ruff `D100-D104` & `D400` enforce docstrings on every public module, class, method, and function |

---

## 🌐 $0 Platform Targets & GitHub Actions Secrets

1. **Local / Agent Studio / CLI / FastAPI (`src/ai2027_protocol/`)**:
   - `ai2027-py27 studio --host 0.0.0.0 --port 8080` — unified bilingual **Manus + Claude Studio** web UI (`/studio` & `/`) featuring SSE streaming timeline, Free-First LLM planning (`Ollama -> Gemini -> NVIDIA NIM -> Groq -> OpenRouter -> Honest Offline Planner`), isolated `python3 -I` & workspace tools, live sandboxed HTML/SVG/Code Artifact preview, L0 SHA-256 verification, L1 Evidence Memory search & admission, and L5 Human-in-the-Loop approval gate.
   - `ai2027-py27 agent "<task>"` — multi-step autonomous CLI coding agent with JSON & terminal trace output.
   - `ai2027-py27 approvals list|approve|reject` — manage queued external L5 connector mutations safely from CLI.
   - `ai2027-py27 demo [--live]` — full Free-First failover + `TaskGroup` batch + benchmark + repo audit + OTel trace summary (`--live` activates existing env/Actions keys when present).
   - `ai2027-py27 providers` — sanitized key readiness (`present (N chars)` / `absent`, never values).
   - `ai2027-py27 live [--provider all|auto|gemini|nvidia|groq|openrouter|ollama]` — verifies configured Actions/env keys safely and exits cleanly.
   - `ai2027-py27 audit python-2027 connectors-hub projects/agi-system` — AST compliance audit across `ai-2027`.
   - `ai2027-py27 serve --host 0.0.0.0 --port 8790` — FastAPI async service (`AI2027_READ_ONLY=1` supported).
2. **GitHub Actions Existing Secrets (`.github/workflows/python-2027.yml` · `live-actions-keys`)**:
   - Runs automatically on `main` pushes and manually via `workflow_dispatch` (never on `pull_request`), binding existing repository secrets `GEMINI_API_KEY` and `NVIDIA_API_KEY` strictly through `env:` (`set +x`, header/Bearer auth, zero secret logging, documented in `docs/SECRETS.md`).
3. **Cloudflare Python Worker (`workers/python-worker/`)**:
   - `src/entry.py` + `wrangler.toml` (`compatibility_flags = ["python_workers"]`), supporting `GEMINI_API_KEY`, `NVIDIA_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY` and deterministic fallback, contract-tested offline via `pytest`.
4. **Cloudflare JS Edge Gateway (`workers/edge-gateway/`)**:
   - `src/index.mjs` + `wrangler.jsonc` — zero-dependency `$0` auth + sliding-window rate limit + HTTPS upstream proxy + direct `GEMINI_API_KEY`/`NVIDIA_API_KEY` fallback + deterministic edge fallback, verified via `node --test`.
5. **Google Colab (`colab/ai2027_python_protocol_lab.ipynb`)**:
   - Self-contained notebook using `uv`, `google.colab.userdata` (`GEMINI_API_KEY`, `NVIDIA_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`), Free-First routing, `TaskGroup` benchmarks, and repo auditing.

---

## ⚠️ Honest Scope & `[CAVEATS]`

- **Python version compatibility:** Primary CI targets **Python 3.13** (`Programming Language :: Python :: 3.13`), while `requires-python = ">=3.11"` ensures first-run execution on Google Colab, Pyodide Workers, and Debian/Ubuntu Python 3.11+ hosts without failing locally.
- **Cloudflare live deployment:** Both `workers/python-worker` and `workers/edge-gateway` are tested locally against simulated Cloudflare `Request`/`Response`/`fetch` contracts; live deployment (`npx wrangler deploy`) requires a Cloudflare account and was not executed from this sandbox.
- **LLM keys:** When no free-tier key (`GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, or local `OLLAMA_BASE_URL`) is configured, the router cleanly falls back to `DeterministicFallbackProvider` and sets `simulated=True` — never pretending deterministic output came from an LLM.
