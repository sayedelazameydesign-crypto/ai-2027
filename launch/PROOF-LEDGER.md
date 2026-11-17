# 🔗 Proof Ledger — every public claim has a command

> **Rule:** no claim goes into [`case-study-deterministic-core.md`](case-study-deterministic-core.md),
> [`show-hn.md`](show-hn.md), a thread, or a comment unless it has a row here.
> Regenerate all measured rows with **one command**:
>
> ```bash
> python3 scripts/generate_launch_evidence.py --assert
> ```
>
> It writes `launch/evidence/latest.json` + `launch/evidence/REPORT.md`
> (both gitignored) and fails if any gate breaks. CI runs it on every push.

**Last measured run:** 2026-11-17 · commit `9ad27fc` · Python 3.11.2 · Linux 6.1 x86_64, shared container.

---

## A. Measured claims (timings — always quote with spread)

| # | Public claim | Exact value from this run | Command that produced it | Raw evidence |
|---|---|---|---|---|
| A1 | Full `VERIFIED` cycle p50 | **7.416 ms** (n=50, p95 10.143, max 12.695, spread 94.9%) | `python3 live-agent/bench.py` | `launch/evidence/latest.json → kernel.verified` |
| A2 | Policy-wall `DENIED` p50 | **2.964 ms** (n=10, p95 3.241, spread 14.3%) | same | `kernel.denied` |
| A3 | Honest `FAILED` read p50 | **5.598 ms** (n=10, p95 7.132) | same | `kernel.failed` |
| A4 | Live HTTP end-to-end p50 | **8.443 ms** (n=20, p95 10.649) | same | `kernel.http` |
| A5 | Cold import | **40.56 ms** (fresh subprocess, n=5) | same | `kernel.cold_import_ms` |
| A6 | p50 per red-team attack | **4.135 ms**, 100 attacks in 577 ms | `python3 command-hud/redteam.py` | `launch/evidence/latest.json → redteam.timing` |
| A7 | Jitter is real | re-runs gave **12.1 / 8.1 / 7.5 / 7.416 ms** p50; spread up to 280% | re-run A1 at different load | [`docs/BENCHMARKS.md`](../docs/BENCHMARKS.md) §"Re-run variance" |
| A8 | p50 < 10 ms is a **target**, not an SLA | CI ceiling is 40 ms, 10 ms is not enforced on shared runners | `python3 scripts/ci_guard.py bench --input …` | [`scripts/ci_guard.py`](../scripts/ci_guard.py) docstring |

## B. Exact claims (counts / bytes — do not vary)

| # | Public claim | Value | Command | Evidence |
|---|---|---|---|---|
| B1 | Kernel source | **3,526 LOC / 124.2 KB**, 15 modules | `python3 live-agent/bench.py` | `kernel.loc`, `kernel.source_kb` |
| B2 | Zero third-party imports | **NONE (stdlib only)**, AST-verified | `python3 scripts/ci_guard.py imports` | CI job `tests` (3 Python versions) |
| B3 | Wheel size | **40,036 bytes / 39.1 KB** | `./packaging/agi-kernel/build_wheel.sh` | `launch/evidence/latest.json → wheel` |
| B4 | 86 kernel tests | **86/86 OK** | `python3 -m unittest discover -s projects/agi-system/tests -t projects/agi-system` | CI + evidence JSON |
| B5 | 126 tests total, all passing | 86 + 6 + 9 + 12 + 13 | `python3 scripts/generate_launch_evidence.py --assert` | `tests` block |
| B6 | 100 adversarial inputs | **75 DENIED · 25 CONTAINED · 0 breaches · 10/10 controls VERIFIED** | `python3 command-hud/redteam.py --markdown --assert` | `redteam` block |
| B7 | Peak RSS growth over 70 runs | **416 KB** (other runs: 568–636 KB) | `python3 live-agent/bench.py` | `kernel.rss_growth_kb_over_70_runs` |
| B8 | 48 projects, 3,967 files | exact match between `catalog.json` and disk | `python3 command-hud/audit.py` | [`docs/REPOSITORY-AUDIT.md`](../docs/REPOSITORY-AUDIT.md) |
| B9 | Only 1 project `PROVEN` | 1 PROVEN / 40 DECLARED / 6 SIMULATED / 1 FAILED | same | same |
| B11 | L1 memory: admission gate | `VERIFIED` + `hash_matches` + SHA-256 + on-disk hash match | `python3 memory/test_memory.py -v` | 16/16 tests; refusals logged in `admission_log` |
| B12 | L1 memory: retrieval quality | recall@3 **2/16 = 0.125** on paraphrase set · FP 0/5 unrelated · 0/7 near-miss | `python3 memory/evaluate.py --markdown` | `memory/README.md` §Measured quality |
| B13 | L1 memory: backend honesty | `sqlite3` + hashed-lexical, **no `sqlite-vec`** (`vec_version()` → OperationalError) | `python3 memory/store.py stats` | `stats.backend` |
| B10 | 5/5 verification probes pass | 86 tests, 6 wrapper, AST gate, 9 guard, 12 demo | same | `probes` block |
| B19 | Live demo can run 24/7 on a free host | `render.yaml` → **1** complete service; Dockerfile builds from a **verified** file list; **16** deployment tests pass incl. real HTTP | `python3 deployment/test_deployment.py -v` | [`deployment/README.md`](../deployment/README.md) |
| B20 | Deployment keeps zero dependencies | `requirements.txt` declares **0** packages; AST gate on `deployment/*.py` passes; image runs **no** `pip install` | same | CI job `deployment` |
| B21 | Disk cap holds under load | 60 real requests against a **1 MB** cap → directory measured at **≤ 1 MB**, oldest runs pruned | `deployment/test_deployment.py::test_31` | measured, not config-read |

## C. Structural claims (code-reading, not measurement)

| # | Claim | Where to read it |
|---|---|---|
| C1 | `VERIFIED` requires an independent verifier re-reading the world | [`core/verifier.py`](https://github.com/sayedelazameydesign-crypto/ai-2027/blob/main/projects/agi-system/core/verifier.py) — `_verify_filesystem_write`, `sha256_file` |
| C2 | Policy is the only authoriser; denial happens before execution | [`core/policy.py`](https://github.com/sayedelazameydesign-crypto/ai-2027/blob/main/projects/agi-system/core/policy.py) — `evaluate`, `contain` |
| C3 | Failures are terminal states, never reported as success | [`core/models.py`](https://github.com/sayedelazameydesign-crypto/ai-2027/blob/main/projects/agi-system/core/models.py) — `ExecStatus` |
| C4 | Append-only, verifiable ledger | [`core/event_ledger.py`](https://github.com/sayedelazameydesign-crypto/ai-2027/blob/main/projects/agi-system/core/event_ledger.py) |
| C5 | Planner is deterministic, no model call | [`core/planner.py`](https://github.com/sayedelazameydesign-crypto/ai-2027/blob/main/projects/agi-system/core/planner.py) |
| C6 | Live demo is a labelled JS **port**, not the Python kernel | [`website/kernel-demo.js`](https://github.com/sayedelazameydesign-crypto/ai-2027/blob/main/website/kernel-demo.js) — "HONESTY CONTRACT" header |
| C7 | CI gates, benchmark artifact, Pages deploy | [`.github/workflows/ci.yml`](https://github.com/sayedelazameydesign-crypto/ai-2027/blob/main/.github/workflows/ci.yml), [`pages.yml`](https://github.com/sayedelazameydesign-crypto/ai-2027/blob/main/.github/workflows/pages.yml) |

## D. Known defects — publish these too, not only the green rows

| # | Honest disclosure | Evidence |
|---|---|---|
| D1 | ~~Cross-platform containment **NOT VERIFIED**~~ → **CLOSED 2026-11-17**: backslashes / drive prefixes / `%2f %5c %2e %00` now denied on every platform (91 DENIED · 9 literal-contained) | `redteam.portability_warning`, [`docs/GROUND-TRUTH-2026-11-17.md`](../docs/GROUND-TRUTH-2026-11-17.md) |
| D2 | `12pro/.../health-raw.json` is empty → JSON parse fails | audit `repo_checks.certification_json_invalid` |
| D3 | `new-agent/LICENSE` is a 4-byte corrupt placeholder | audit project row |
| D4 | `agi-system` license undecided → wheel **not on PyPI** | [`packaging/agi-kernel/pyproject.toml`](https://github.com/sayedelazameydesign-crypto/ai-2027/blob/main/packaging/agi-kernel/pyproject.toml) |
| D5 | 23 projects without LICENSE, 2 proprietary | [`docs/PHASE-0-READINESS.md`](https://github.com/sayedelazameydesign-crypto/ai-2027/blob/main/docs/PHASE-0-READINESS.md) |
| D6 | Redundant legacy Pages build fails cosmetically on each push | [`manual-github-settings.md`](manual-github-settings.md) §6 |
| D7 | Own bugs found & fixed: false SHA-256 on `DENIED`, p50 polluted by 0 ms control events, Python 3.10 `tomllib` gate, 4 failed Jekyll deploys | `command-hud/test_hud.py`, `scripts/test_ci_guard.py`, git history |
| D8 | L1 memory is **lexical, not neural** — misses synonyms (2/16 on paraphrase set) | [`memory/evaluate.py`](../memory/evaluate.py), `test_memory.py::test_11` |
| D9 | L1 memory is tamper-**evident**, not tamper-proof; no signing, single-writer assumption | `store.verify()`, [`memory/README.md`](../memory/README.md) §limits |
| D13 | `live-agent/server.py` has **no auth and no rate limit**; exposed publicly anyone can run goals at ~90 req/s | [`deployment/README.md`](../deployment/README.md), `deployment/live_server.py` banner | documented; read-only mode provided, not enforced |
| D14 | Without a disk cap, **69.5 KB per run** fills a 512 MB free tier in **~1.4 min** (~75,000 runs for 5 GB). The launcher caps it; a bare `live-agent/server.py` does not | `deployment/live_server.py`, measured | cap verified by test |
| D15 | A `facebook_connector.py` referenced in planning **does not exist in this repo** (checked with `find`). No server-side process can read a logged-in browser session | `find . -name "*facebook*"` → none | integration would need its own OAuth app |
| D16 | The first proposed deployment configs were **wrong in 4 ways**: `render.yaml` parsed to 2 half-services, `-m live_agent.server` names a package that does not exist, the Dockerfile COPY'd a file absent from the repo, and nothing capped disk | `deployment/test_deployment.py` (4 failing assertions reproduced against the original) | each pinned by a test |

## E. Claims we REFUSE to make

| ❌ Forbidden | Why |
|---|---|
| "100/100 DENIED" | Real result is **91 / 9 / 0**: 9 inputs are legitimate literal POSIX names (e.g. `NUL`, `...`) that stay inside the sandbox on any OS. |
| "10 ms guaranteed / blazing fast" | Spread is published; 10 ms is a reference target, CI ceiling is 40 ms. |
| "All 48 projects work / all open source" | Only 1 PROVEN; licenses mixed; 2 proprietary. |
| "Production success story / saved X% / revenue" | No measured production data exists. See [`REAL-WORLD-CASE-STUDY.md`](REAL-WORLD-CASE-STUDY.md) — deliberately unfilled. |
| "LLM-powered agent" | No model is called. Phase 2 is an undecided owner decision. |
| "The browser demo is the kernel" | It is a labelled JS port on an in-memory sandbox. |
| "Independently secure / unbreakable" | Verifier is a separate component in the same process, re-reading disk — not a security boundary against a malicious in-process executor. |

---

## Mechanical enforcement

CI runs `python3 launch/test_launch_claims.py -v`, which verifies that:

- every local link in `launch/*.md` resolves to a real file;
- every evidence asset named in this ledger exists;
- the case study declares a measurement date + commit and a regeneration command;
- no forbidden marketing phrase appears in publishable copy;
- the real-world case study is still explicitly marked **NOT PUBLISHABLE**.

A claim that loses its evidence breaks the build. That is the whole point.
