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
