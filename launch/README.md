# 🚀 Launch Kit — AI-2027

> **Status: global-distribution track, 2026-11-17 — 🚀 READY TO PUBLISH.**
> Focus is the public launch
> path (repo as technical front door + deterministic niche + Show HN) **before**
> Phase 2. Phase 2 (cloud LLM / local model / defer) is deliberately parked
> awaiting your decision — nothing here commits to a model integration.
>
> Publication rule is unchanged: **no claims without evidence.** Every number
> links to code, a raw artifact, or the audit; every demo states its honest scope.

## Files

| File | Channel | Status |
|---|---|---|
| [`show-hn.md`](show-hn.md) | Show HN post (EN) — sharpened kernel-focused draft | **READY TO COPY** (after URL check) |
| [`case-study-deterministic-core.md`](case-study-deterministic-core.md) | DEV.to / Medium technical case study | DRAFT — evidence-linked |
| [`producthunt.md`](producthunt.md) | Product Hunt launch copy (EN) | DRAFT |
| [`founder-posts.md`](founder-posts.md) | 6 article/video scripts for the founder brand (EN+AR) | DRAFT |
| [`manual-github-settings.md`](manual-github-settings.md) | **Re-verified** manual settings + what is now automatic | READY |

## Pre-publish checklist — actual state

- [x] Real deterministic kernel + independent verifier (86/86 tests, CLI contract in CI)
- [x] Measured benchmarks **with jitter published beside every timing** → [`docs/BENCHMARKS.md`](../docs/BENCHMARKS.md)
- [x] Positioning "determinism vs hallucination" + technical case study
- [x] Installable 40 KB wheel, CLI smoke-tested in CI
- [x] CI/CD on every push: 86 + 6 + 12 tests, AST zero-deps gate, bench.py, wheel → [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)
- [x] **Public live demo built**: [`website/demo.html`](../website/demo.html) — 7 stages, policy wall, SHA-256 evidence, hash-chained ledger, 12 contract tests
- [x] **Pages deploy fixed** → [`.github/workflows/pages.yml`](../.github/workflows/pages.yml) (the old Jekyll workflow failed 4/4 times)
- [x] **Live URL verified 2026-11-17 11:25 UTC** — `pages/builds` → `built`, Pages workflow `44073888821` success, page + JS fetched over HTTPS → <https://sayedelazameydesign-crypto.github.io/ai-2027/demo.html>
- [ ] GitHub description + topics + homepage + Discussions (403 re-verified — 4 manual clicks)
- [ ] License decision for `agi-system` (blocks PyPI, NOT the HN post)
- [ ] Video recorded (60–90s, real screen capture) — needed for Product Hunt, not HN
- [ ] `WEB` & `celia-pro-deployment` excluded from all "open source" claims ✅ already enforced

## The one honest caveat about the demo

`website/demo.html` is a **dependency-free JavaScript port of the kernel's
rules**, not the Python kernel and not an LLM. It exists because GitHub Pages is
static. The verifier, policy wall and SHA-256 evidence in it are real (proven by
`website/kernel-demo.test.js`), but the published p50 numbers belong to the
Python kernel and are measured by `live-agent/bench.py`. This is stated on the
demo page itself and in the Show HN body. **Do not blur that line.**
