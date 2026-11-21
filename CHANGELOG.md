# Changelog

All notable changes to this repository are recorded here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) ·
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Rule for this project: **a version number is only allowed to exist if a tag, a
GitHub Release, an SBOM and `SHA256SUMS` exist for it.** The release workflow
(`.github/workflows/release.yml`) enforces that — it refuses to publish when tests
fail or when any GitHub Action is not pinned to a full-length SHA.

---

## [Unreleased]

### CI maintenance — GitHub Actions runtime support
- Updated the full-SHA-pinned checkout, Python, Node, artifact upload, CodeQL, and
  Pages deployment actions to maintained Node 24-compatible major versions.
- Workflow logic and permissions are unchanged; the SBOM pinning gate remains in
  force for every external action reference.

### Security — fail-closed writes for the Live Agent deployment
- `deployment/live_server.py` disables `POST /api/run` unless an operator supplies
  `AI2027_WRITE_TOKEN` (32-512 visible ASCII characters, no whitespace); enabled
  writes require a constant-time-checked Bearer token. Symbols including `+`, `/`,
  and `=` are accepted consistently by the server, clients, Ember, and Workspace.
- `scripts/ember_up.sh` reuses an already healthy Live Agent only after a loopback,
  non-mutating check proves both the reader and operator credentials; `--no-smoke`
  does not bypass it. Unknown or mismatched services are left running with manual
  reconfiguration/shutdown guidance.
- Added a bounded, process-local sliding-window rate limit (default 10 attempts per
  60 seconds per observed peer IP). Untrusted forwarded-IP headers are ignored;
  this is not a substitute for proxy-level controls or per-user authentication.
- Render and Docker defaults are read-only, and the direct Live Agent CLI now binds
  to `127.0.0.1` by default. Public GET endpoints still expose run records and
  sandbox artifacts; the bilingual UI warns users not to submit secrets or
  confidential data. See `deployment/README.md` for deployment limitations.
- Verified with 40 deployment tests (one optional YAML-parser test skipped because
  PyYAML is not installed), 8 Live Agent server tests, and 12 mock-only planner tests.

### Fixed — P0.1: one writer per session, ordered by a fencing token (2026-11-19)
- **Gap R1 closed.** The audit measured the defect: four writers saving one
  session lost **59–67 of 240 writes** per run (previously 23–61 by a different
  counter), because `session.tmp` was one fixed filename and nothing owned the
  session. Re-running the same harness with the lease gives **0 lost writes,
  0 torn reads, 240/240 accepted** (`tests/session_race_demo.py --mode lease --assert`).
- **`lease.py`** (new) — `LeaseStore` protocol and `SqliteLeaseStore`: one row per
  session, acquired inside `BEGIN IMMEDIATE`, carrying `owner`, `fence` and
  `expires_at`; WAL and a bounded busy timeout. Leases expire, so a crashed
  process cannot lock a session forever — the next writer takes over and the
  fence moves.
- **Every write is atomic.** The temp file is now unique per write
  (`.session.{pid}.{hex8}.tmp`), so one writer can no longer publish another's
  half-finished JSON — the torn reads in the audit came from exactly that.
- **`session_store.py` / `session_state.py`** (new) — `SessionStore` and the
  session models moved out of `agent.py` (778 → 478 lines, back under the
  auditor's 650-line ceiling) so persistence can be tested on its own; the public
  names `ai2027_protocol.agent.SessionStore` and `ai2027_protocol.SessionStore`
  are unchanged.
- **Refusals are explicit.** Three modes via `AI2027_SESSION_LEASE`: `off`
  (default, historical behaviour), `warn` (logs the refusal, still writes),
  `enforce` (refuses). Under `enforce` a writer that lost its lease gets
  `LeaseLostError` instead of silently overwriting newer state; the first write
  of a brand-new session is exempt because a fresh uuid cannot be contended.
- **Studio answers 409, not a dead stream.** `/v1/studio/sessions/{id}/run` and
  `/stream` acquire the lease *before* the 200, return `409` naming the holder
  when the session is busy, and release it in `finally`. The CLI maps
  `SessionBusyError` to exit code 2 with a readable message.
- **Fence stays monotonic across release.** Releasing a lease invalidates the row
  (`expires_at = 0`) instead of deleting it: a deleted row would restart the fence
  at 1 and hand a stale writer a token that looks current. Found by
  `tests/test_session_lease.py`, which now pins it.
- **`tests/test_session_lease.py`** (new, 10 tests) — cross-process contention,
  live-lease refusal, takeover after expiry with a higher fence, stale-writer
  write refused, first-write exemption, `warn` logging, `409` on both Studio
  paths, and no lease left behind after a finished run.

### Added — Phase A resilience audit: what breaks, and what already holds (2026-11-19)
- **`docs/PAP-2027-RESILIENCE-AUDIT.md`** — read-only audit of the execution and
  failure paths (architecture, dependency graph, failure propagation, protection
  inventory, ranked gaps, phased plan, exact files). Every claim carries one
  classification (PROVEN / TESTED LOCALLY / READ FROM CODE / SIMULATED /
  NOT EXECUTED); nothing is called live that was not executed.
- **`python-2027/tests/session_race_demo.py`** — manual harness (not a pytest
  test, not in CI) that runs four writer processes against one session. Measured
  today: **23–61 of 240 concurrent saves fail**, and up to **13 torn JSON reads**,
  because `session.tmp` is one fixed filename for every writer. After the session
  lease lands this harness must report zero; `--assert` already encodes that.
- **Findings that need action** (details in the audit): no per-session writer
  lease (R1), checkpointing is not exactly-once execution and there is no action
  journal or idempotency key anywhere (R2), circuit breaking covers LLM providers
  only (R3), a corrupt memory store aborts the run (R4), no bulkhead (R5), no
  per-component health states (R6), no deploy rollback (R7), and the
  `failure-recovery` gate is still unimplemented (R8).
- **Verified existing protections** worth reusing instead of rebuilding:
  `EventLedger`'s cross-process lock, router timeouts/backoff-with-jitter/breaker,
  `BudgetGuard`, deterministic planner fallback, tool timeouts and output caps,
  evidence-gated memory admission, human approval for connector writes, and the
  SIGKILL resume gate.
- No product code was modified; `agent-fleet` stays blocked until the lease,
  action journal, and failure-recovery gate pass.

### Added — the `live-connector` gate: one real call, judged as evidence (2026-11-19)
- **Fixed after the first `main` run (same day).** The gate passed
  `--python .venv/bin/python` but launched the worker from the repo root, so the
  relative interpreter did not exist, the process died before writing evidence,
  and the step failed in under a second with no artifact. Both gates now resolve
  a relative `--python` against the caller's cwd, refuse a missing interpreter
  with a written FAIL report (exit 2), always write a report even on an
  unexpected exception, and emit a GitHub `::error::` annotation carrying the
  failing check names — because a failure you cannot read is a failure you cannot
  fix. Regression tests cover the relative path, the missing interpreter, and
  the exact marker spellings requested from the model.
- **`python-2027/src/ai2027_protocol/live_probe.py`** — probes a provider with the
  production transport and records the exchange: endpoint host, request/response
  timestamps, latency, HTTP status, response headers, `request_id` (payload or
  header, else `server_model_version`), token usage, response SHA-256 and a
  redacted excerpt. A provider whose billing tier is not `FREE_TIER`/`LOCAL` is
  **never called**, so `MAX_SPEND = $0` is enforced before the request instead of
  asserted afterwards.
- **`scripts/live_connector_gate.py`** (stdlib) — judges the worker report. Per
  the audit's stricter rule, `skipped_no_key` is a **failure**, not a skip; a
  simulated (`mock`) answer fails; a missing marker fails; a credential-shaped
  string anywhere in the evidence fails *and* is redacted from the artifact.
  Evidence JSON on pass and fail; `--assert` exits 1 on FAIL, 2 when the worker
  could not run (so a broken interpreter can never look like a pass).
- **`tests/live_connector_worker.py`** — thin JSON worker; `--provider` selects
  candidates, exits 3 only when it cannot run at all.
- **Tests** — `test_live_probe.py` (9: free-tier refusal without a call, missing
  key, end-to-end evidence against a mocked HTTP transport, header-id fallback,
  410/503 error paths, key redaction) and `test_live_connector_gate.py` (11:
  clean pass, no-key failure, mock failure, marker failure, unknown-cost failure,
  missing id/time, leaked credential, non-object JSON, missing worker, crashing
  worker, broken interpreter).
- **CI** — the gate runs in the main-only `live-actions-keys` job with the
  existing secrets and uploads its evidence artifact; PRs stay offline.

### Added — checkpoint/resume + the first live capability gate (2026-11-19)
- **`python-2027/src/ai2027_protocol/agent.py`** — the autonomous loop now
  checkpoints after **every completed step** (`checkpoint_step`,
  `checkpoint_at`) and accepts `AgentTaskRequest(resume=True)`. Resume is
  refused with `AgentResumeError` when the session is not `running`, when the
  task text differs, or when no step was checkpointed: restarting from step 1 is
  never silently reported as a resume. A `session_resumed` event reports
  `resume_from_step`/`next_step`/`checkpoint_at`.
- **`scripts/persistent_task_gate.py`** (stdlib) + **`python-2027/tests/resume_worker.py`**
  — the first PAP-2027 "Live Capability Gate". It runs the real agent in a child
  process, SIGKILLs it **inside step 3** after steps 1–2 were checkpointed
  (phase-1 returncode `-9`), then resumes in a second process that must start at
  step 3. Ten checks, all falsifiable: no step executed twice, every step
  completed exactly once, artifacts match their journaled SHA-256, final session
  `completed` with steps `[1,2,3,4]`. Evidence JSON is written on pass **and**
  fail, and the report is uploaded as a CI artifact.
- **Tests** — `tests/test_agent_resume.py` (7) and
  `tests/test_persistent_task_gate.py` (4, including a tampered worker that must
  not be able to produce a PASS). Wired into the `python-2027.yml` matrix job.
- **`docs/PAP-2027-GAP-AUDIT.md` / `docs/pap-2027-engines.json`** — the
  `persistent-task` gate is marked implemented, `memory-engine` gained the
  resume evidence, and the engine table no longer copies counts that the audit
  script owns.

### Added — PAP-2027 gap audit on real code (2026-11-19)
- **`docs/pap-2027-engines.json`** + **`scripts/pap_gap_audit.py`** — the twelve
  proposed PAP-2027 engines are mapped to files that actually exist, each with
  the test files and the marker string that proves it is exercised in CI.
  `--assert` fails when an evidence path or a test disappears, when a
  `present` engine has no tests, or when an `absent` engine starts listing
  evidence. 14 stdlib tests (`scripts/test_pap_gap_audit.py`) keep the gate
  itself honest; wired into the `tests` job of `ci.yml`.
- **`docs/PAP-2027-GAP-AUDIT.md`** — the audit result: **4 present · 7 partial ·
  1 absent** (`agent-fleet`), the exact names and `if:` conditions of the two
  skipped checks on #66, which CI job proves which engine, what CI explicitly
  does *not* prove (mock connectors, simulated browser, wrangler dry-run,
  offline E2B), and a falsifiable eight-gate "Live Capability Gate" proposal
  where every gate must produce a stored artifact or it did not run.

### Fixed — retired model IDs and the "free" label (2026-11-19)
- **`python-2027/src/ai2027_protocol/model_catalog.py`** (new) — one registry for
  every shipped remote default (`GEMINI_MODEL`, `NVIDIA_MODEL`, `GROQ_MODEL`,
  `OPENROUTER_MODEL`) and every retirement we have evidence for:
  `meta/llama-3.1-8b-instruct` (NVIDIA NIM, HTTP 410, 2026-08-26),
  `llama-3.1-8b-instant` / `llama-3.3-70b-versatile` (Groq free + developer
  tiers, HTTP 404, 2026-08-16), and the two Groq preview models retired
  2026-07-17. `find_retired_model()` and `retirement_reason()` name the dated
  replacement instead of leaving an operator with a bare 404/410.
- **`budget.py`** — retirement is checked *before* any tier (an explicit
  `FREE_TIER` claim can no longer resurrect a dead ID); `openrouter/free` is
  accepted next to the `:free` suffix in both the explicit and the inferred
  path; the Groq allowlist is now empty because Groq's free Llama models are
  gone and `openai/gpt-oss-20b` is priced per token, so a paid model is never
  used to "fix" a free check. `AI2027_FREE_MODELS` still opts in explicitly.
- **`ai_providers.py` / `demo.py` / workers** — defaults follow the catalogue
  (NVIDIA → `meta/llama-3.3-70b-instruct`, OpenRouter →
  `openrouter/free`); `complete()` refuses a retired ID before the network;
  `HTTP 410` is reported as `ProviderError("model_retired")`; `ai2027-py27 live`
  and both Workers report `model_retired` with `0.0 ms` latency (no request
  spent). New `ai2027-py27 models [--json]` command.
- **`docs/MODEL-CATALOG.md`** (new) + `docs/SECRETS.md` — defaults, retirement
  table, the exact Groq opt-in, and an explicit note that the four
  Cloudflare/R2 secrets are defined but consumed by no workflow yet.
- **Tests** — `tests/test_model_catalog.py` (12 tests) plus worker/gateway
  regressions; `pytest` 77 passed at 91% branch coverage, `ruff`, `mypy src
  tests`, `node --test` (9), and `scripts/` (208) all green locally.

### Added — BrowserSkill Stage 1 of 3 (`local` mode: browser + machine, 2026-11-18)
- **`docs/adr/0005-browserskill-adapter-boundary.md`** + **`docs/BROWSERSKILL-PLAN.md`** —
  three-stage / three-mode rollout plan (`local` → `skill_mcp` → `remote_wss`)
  for `Tencent/BrowserSkill` (MIT, pinned `cli-v0.5.0` / `ext-v0.5.0`, Protocol
  `2.0`) as an external policy-gated adapter rather than a vendored clone,
  preserving `projects/` archive identity and the zero-dependency kernel.
- **`browser_skill_adapter/`** (stdlib-only Python 3.10+, AST-gated in CI, 34
  offline tests): `DomainPolicy`, cross-platform sandbox confinement, single-use
  HMAC+nonce `ApprovalEngine` (+ exact confirmation for `borrow_tab`, `upload`,
  `download`, `evaluate`; `evaluate` off by default), `IntentGuard` refusing
  blind replay after `effect_state == "unknown"` / `timeout` / `dialog_pending`,
  subprocess credential stripping (`GEMINI_API_KEY`, `RENDER_API_KEY`,
  `GITHUB_TOKEN`, …) with `BSK_AUTO_START=0`, `Redactor`, `untrusted_page_data`
  envelope, mandatory `session stop` even under freeze, SHA-256 hash-chained
  `AuditLedger`, independent 7-check `verify_and_record()` compatible with L1
  `memory/store.py`, read-only `probe.py` (exit `0`/`1`/`2`/`3`, `0` mutations),
  and falsifiable-budget `run_local_task.py`. Modes 2 (`skill_mcp`) and 3
  (`remote_wss`) raise `ModeNotEnabledError` until their stages are enabled.
- **`scripts/install_browserskill.sh`** + **`.ps1`** (10 offline tests) —
  fail-closed installer for pinned `bsk v0.5.0` requiring a verified 64-hex
  SHA-256 (closing upstream's warn-and-continue fallback), no `sudo`, no silent
  shell-profile edits.
- **`scripts/secret_scan.py`** + `.gitignore` — blocks `bsk-local.v1.*`,
  `bsk-auth.*`, `ws(s)://…#<fragment>` pairing URLs, and `local-pairings.json`
  before commit and in CI (18 tests).
- **`command-hud/server.py`** (`GET /api/browser/status`, 16 HUD tests) +
  **`scripts/ember_up.sh --with-browser`** — opt-in read-only local browser
  status without touching the default 15/15 smoke contract; `ci.yml` also wires
  `vars.GEMINI_MODEL` into the opt-in `live-gemini` job per `docs/SECRETS.md`.

### Added — repository secrets, wired safely (2026-11-18)
- **`docs/SECRETS.md`** — the register of every secret (`GEMINI_API_KEY`,
  `RENDER_API_KEY`, `EMBER_ALERT_WEBHOOK`), who uses it, what the workflow does
  when it is absent, and how to rotate it. An undocumented secret is one nobody
  can rotate.
- **`scripts/workflow_secret_audit.py`** (16 tests, static, names only) — fails
  CI when a workflow interpolates `${{ secrets.X }}` into script text (it belongs
  under `env:`, or it lands in the printed command line) or references a secret
  that `docs/SECRETS.md` does not name.
- **`scripts/gemini_live_check.py`** (15 tests) — one tiny real call
  (`maxOutputTokens=24`, `temperature=0`) so "the credential works" becomes
  measured. Key comes from `env` only, travels in the `x-goog-api-key` header
  (**never** `?key=` in a URL), is reported only as `present (39 chars)`, and is scrubbed
  out of provider error bodies that quote it back. No key ⇒ exit **2 (skipped)**;
  no egress ⇒ exit **3 (blocked)** — neither is reported as a failure.
  Wired as the opt-in `live-gemini` job (never on pull requests).
- **`scripts/render_deploy.py`** (14 tests) + **`.github/workflows/deploy.yml`** —
  manual-only deploy through `render_control_adapter`'s existing policy gate:
  approval token required for the mutation, the service's exact name required as
  confirmation (empty ⇒ dry run, nothing sent), `api.render.com` reachability
  checked first (unreachable ⇒ exit **3 BLOCKED**, never "deploy failed"), and
  `verified: true` only after a terminal deploy state **and** a liveness check.

### Added — two guards against false claims (2026-11-18)
- **`scripts/network_preflight.py`** — measures whether the runner can reach the
  public internet *before* any URL is judged, and classifies egress as `OPEN` /
  `ALLOWLISTED_ONLY` / `CLOSED`. `urllib` raises the same `URLError` for "the site
  is dead" and "this machine is blind"; the preflight makes the difference
  explicit. An HTTP status (even 404) counts as reachable — the network answered.
- **`ember_beat.public_paths()`** now emits `live` / `dead` / `blocked` instead of
  flattening everything to `live: false`. Blocked paths are excluded from the
  heat index and the weight is renormalised, so a network-isolated runner cannot
  cool the repository down. `STATUS.md` prints `Public egress` and
  `N BLOCKED — not counted`.
- **`docs/deviations.json`** — machine-readable register read by
  `scripts/verify_archive_identity.py`, which now separates `identical` /
  `deviation` / `generated` / `drifted` / `blocked`. `easy-ai/data/manifest.json`
  is declared `generated` (upstream re-ran it 9 hours after our sync).
  New flags: `--strict` (drift only), `--strict-all` (+ blocked/failed), `--json`.
- `command-hud/audit.py` reports `Public egress (measured)` and warns that a
  public-site verdict from a blind runner is `BLOCKED`, not `dead`.
- 27 new tests, all offline (network calls are injected):
  `test_network_preflight.py` (10), `test_verify_identity.py` (17), plus 5 in
  `test_ember_beat.py` (12 total). Wired into `.github/workflows/ci.yml`.

### Added — proactive governance (2026-11-18, after #42 merged)
- **`scripts/secret_scan.py`** + `.githooks/pre-commit` — refuses a commit whose
  staged content contains a credential (Gemini / AWS / GitHub / OpenAI / Slack /
  Stripe / JWT / PEM) or a file that must never be committed (`.env`,
  `env.local`, `*.key`). `SECURITY.md` fixtures are allowed; a line can be marked
  `# allowlist-secret`. The finding is always masked — a gate that echoes the key
  into a CI log has published it twice. Install: `./scripts/install_hooks.sh`.
  CI runs the same scan over every tracked code path.
- **`generated_schema` in `docs/deviations.json`** — a generated file may change
  freely, but it may not become *broken*: required keys + JSON types are checked
  against the committed bytes (`git show HEAD:…`). A malformed generated file is
  now `drifted`, not "volatile".
- **`scripts/live_egress_audit.py`** + **`.github/workflows/live-egress-audit.yml`**
  — the first environment that can actually judge public URLs (GitHub runners have
  general egress). Every 6 hours it re-measures the 8 claims in
  `docs/public-urls.json`, publishes `EGRESS.md` + `egress/status.json` to the
  `live-egress` branch, and fails the job when a "live" claim stops answering 2xx.
  It refuses to run a guessing pass: `network_preflight --require-open` first.
  The claim register is **not** auto-edited — a bot that rewrites `expected` would
  turn an outage into a green run.
- 86 offline tests for these guards (secret 17 · live egress 18 · identity 29 ·
  preflight 10 · ember 12), all with injected network calls.

### Planned (see `docs/ROADMAP-2027.md`)
- T4 — a hosted second mirror (owner action: Vercel returned *access denied*, the
  Supabase projects are paused — both measured, see `docs/T4-T6-EVIDENCE.md`).
- T4 — Supabase `beats` table once a project is designated by the owner.

---

## [v1.0.1] — 2026-11-17

The release where the repository stops trusting its own good news: a beat is now
judged against its history, the whole stack boots with one command in **2.46 s**,
and one real task finishes in **22.9 ms** with evidence that can be re-checked.

### Added — T5 · immunity (`scripts/health_gate.py`)
- Eleven checks per beat (`beat_present`, `smoke_complete`, `smoke_no_regression`,
  `evidence_complete`, `memory_reachable`, `public_path`, `public_paths_all`,
  `beat_freshness`, `history_monotonic`, `path_independence`, `heat_not_dropped`).
- Three-stage escalation: one retry → one issue → `escalation/s3` + code-owner
  mention + optional `EMBER_ALERT_WEBHOOK`. An unconfigured webhook is reported as
  `not-configured`, never as delivered.
- `--commit-beat`: the gate writes the history row, once, on the run's final
  attempt — a retry can no longer inflate `beats_7d` or the success rate.
- 24 tests, including the case the gate exists for: a beat whose own fields say
  `15/15` but which only measured 3 checks is **degraded**.

### Added — T4 · continuity (`scripts/ember_up.sh`)
- One command boots both services, waits for real health, runs the 15 HTTP checks,
  writes `.ember-up/state.json`, reuses an already-healthy service instead of
  duplicating it, and stops with `--stop`.
- Measured cold: **2.46 s** wall (KPI ≤ 10 min) · `15/15` · exits by itself.

### Added — T6 · the product (`scripts/run_task.py`)
- Runs a real goal over real HTTP, re-reads the evidence record, downloads the
  artifact and **recomputes** its SHA-256 locally before accepting it.
- Published budget of 60 s; `DENIED` is a task failure (exit 1), over-budget is
  exit 3, an unreachable agent is exit 2. Measured: **22.9 ms** end-to-end.
- 6 tests, one of which proves the budget claim can fail.

### Added — CI
- `recovery` job: boots the ember with one command and asserts a real task under
  budget on every push.
- New gates: `scripts/test_health_gate.py` (24) and `scripts/test_run_task.py` (6).

### Fixed
- `scripts/ember_up.sh` background jobs held the caller's stdout pipe open, so
  `--json | cat` never terminated. The job is now `exec`-ed with all descriptors
  redirected, and the recorded pid is the service itself (so `--stop` is honest).
- `run_task.py` crashed writing a bundle when `--evidence-dir` pointed outside the
  repository (`Path.relative_to` on a foreign path).
- **The beat forgot its own history**: `ember/` is intentionally ignored in `main`,
  so every workflow run started with an empty `history.jsonl`; the streak could
  never grow and stage-3 escalation was unreachable. The workflow now restores
  `history.jsonl` + `gate.jsonl` from the `heartbeat` branch before beating.
  Found by reading a real issue body (`stage 2/3 · streak 1 · first beat on record`),
  not by review.

### Documentation
- `docs/T4-T6-EVIDENCE.md` — the measured evidence for these three tracks,
  including what is **not** done and the exact response that blocked it.
- `docs/adr/0004-health-gate-escalation.md` — why one retry, then an issue.

---

## [v1.0.0] — 2026-11-17

The first release that can be audited end-to-end: every number in the README is
produced by a command, and the repository now runs when nobody is watching.

### Added — evidence and runtime
- **Live Agent** (`live-agent/server.py`): real kernel, per-run sandbox, persisted
  DecisionRecord, artifact download, 15-check HTTP smoke (`scripts/runtime_smoke.py`).
- **Evidence-gated memory** (`memory/store.py`): admission requires `VERIFIED` +
  independent hash check + artifact on disk; refusals are logged with reasons.
- **Retrieval v2** (`memory/retrieval.py`): declared thesaurus (34 groups / 212
  terms), light EN/AR stemming, IDF concept coverage, two-concept guard.
  Paraphrase recall@3 **2/16 → 11/16 (0.688)** with near-miss false positives
  held at **0/7**.
- **Machine-readable surface**: `agent-card.json` + `GET /api/agent-card`,
  `GET /api/metrics` (Prometheus text), both asserted by the smoke test.
- **Ember beat** (`.github/workflows/ember.yml`, `scripts/ember_beat.py`): a
  measured 6-hourly heartbeat with a Heat Index, a `heartbeat` branch, automatic
  issue on degradation and automatic closure on recovery.

### Added — engineering discipline
- Release pipeline with SBOM (`scripts/sbom.py`, CycloneDX 1.5) and `SHA256SUMS`.
- CodeQL (security-extended, Python + JS/TS), Dependabot (actions + pip),
  `CODEOWNERS`, PR template, issue forms, Code of Conduct, `LICENSE` (MIT).
- `docs/ROADMAP-2027.md`, `docs/EMBER-PLAN.md`, ADRs (`docs/adr/`).

### Fixed
- **Path traversal (Windows style)**: `..\..\pwned.txt` was not refused; now DENIED
  with an explicit cross-platform containment reason.
- **Pages deployment**: replaced composite actions (`configure-pages`,
  `upload-pages-artifact`) that call other actions by tag — incompatible with the
  repository's SHA-pinning policy — with a hand-built `artifact.tar`.
- **HUD deleted the live agent's memory**: `kernel_bridge` redirected `RUNS_DIR`
  and `SANDBOXES_DIR` but not `MEMORY_DB`, so `_fresh_state()` wiped it.
- **Test suite wiped a running server's memory**: same missing redirect in
  `live-agent/test_server.py`; proven with `memories=1` before and after.
- **Heat Index read a fresh beat as 24 h of silence**: `since_last_h or 24.0`
  treated `0.0` as missing (falsy). Regression test added.
- **Zero-dependency gate**: unknown-but-repo-local modules (`memory`,
  `retrieval`) were reported as third-party; the allowlist now verifies that every
  entry resolves to a path inside the repository.

### Security
- All GitHub Actions pinned to full-length commit SHAs (29 components, 0 unpinned,
  verified by `scripts/sbom.py --assert-pinned`).
- Sandbox confinement enforced by policy for POSIX and Windows separators.
- `ledger.jsonl` is never served by the artifact endpoint.

### Known limits (published, not hidden)
- Retrieval is lexical: 5/16 paraphrases are still missed.
- No LLM in the loop; the planner is deterministic and rule-based.
- Branch protection cannot be enabled by the automation token (HTTP 403) — see
  `docs/GOVERNANCE.md` for the one command the owner must run.
- Gemini key revocation (#21) is an owner action and is still open.

---

[v1.0.1]: https://github.com/sayedelazameydesign-crypto/ai-2027/releases/tag/v1.0.1
[v1.0.0]: https://github.com/sayedelazameydesign-crypto/ai-2027/releases/tag/v1.0.0
[Unreleased]: https://github.com/sayedelazameydesign-crypto/ai-2027/compare/v1.0.1...HEAD
