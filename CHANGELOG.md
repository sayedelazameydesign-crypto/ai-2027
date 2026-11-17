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
- T4 — one-command local boot (`scripts/ember_up.sh`) and a second hosted mirror.
- T5 — `health_gate.py` with three-stage escalation.
- T6 — real execution UI: run a task from the page and get an evidence link.

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

[v1.0.0]: https://github.com/sayedelazameydesign-crypto/ai-2027/releases/tag/v1.0.0
[Unreleased]: https://github.com/sayedelazameydesign-crypto/ai-2027/compare/v1.0.0...HEAD
