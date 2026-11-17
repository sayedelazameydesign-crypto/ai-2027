# 🛰️ AI-2027 — Repository Intelligence & Health Report

**Generated:** 2026-11-17T12:02:36Z · **Mode:** full · **Method:** read-only filesystem inspection + executed subprocess probes; nothing under projects/ was modified

> Classification is deliberate: a README describing a feature is **DECLARED**, not **PROVEN**. Only commands actually executed in this sandbox earn PROVEN. Nothing here modifies `projects/`.

## 1. Verdict

| Metric | Value |
|---|---:|
| Projects | 48 |
| Actual files on disk | 3967 |
| Declared files (catalog.json) | 3967 |
| With test files | 21 |
| With CI workflows | 19 |
| Without LICENSE | 23 |
| HIGH-severity findings | 5 |
| Probes passed / failed | 5 / 0 |

| Classification | Count | Meaning |
|---|---:|---|
| PROVEN | 1 | a real command ran here and passed |
| DECLARED | 40 | documented, not executed in this sandbox |
| SIMULATED | 6 | README explicitly labels a simulation/mock mode |
| NOT VERIFIED | 0 | no runnable evidence available |
| FAILED | 1 | concrete defect found by inspection |

## 2. Executed probes (real output, not claims)

| Probe | Exit | Evidence | Verdict | Time |
|---|---:|---|---|---:|
| `kernel-86-tests` | 0 | Ran 86 tests | **PROVEN** | 1630.8 ms |
| `live-agent-6-tests` | 0 | Ran 6 tests | **PROVEN** | 98.2 ms |
| `zero-dependency-ast-gate` | 0 | PASS: stdlib only (zero third-party imports). | **PROVEN** | 66.9 ms |
| `ci-guard-self-tests` | 0 | Ran 9 tests | **PROVEN** | 152.4 ms |
| `public-demo-contract-tests` | 0 | 12 demo contract tests passed | **PROVEN** | 46.6 ms |

Commands (reproducible from repository root):

```bash
/usr/bin/python3 -m unittest discover -s projects/agi-system/tests -t projects/agi-system
/usr/bin/python3 live-agent/test_server.py
/usr/bin/python3 scripts/ci_guard.py imports
/usr/bin/python3 scripts/test_ci_guard.py
node website/kernel-demo.test.js
```

## 3. Architecture map

| Layer | Path | Role | Verification |
|---|---|---|---|
| L0 · Deterministic kernel | `projects/agi-system` | Goal→Plan→Policy→Execute→Verify→Evidence→Ledger. Zero third-party imports. | 86 unit tests executed in this audit + AST gate |
| L1 · Live wrapper | `live-agent` | Stdlib HTTP wrapper importing the kernel in place; per-run sandbox + evidence JSON. | 6 wrapper tests executed in this audit |
| L2 · Public surface | `website` | Static bilingual site + JS reference port of the loop (labelled as a port). | 12 demo contract tests executed in this audit |
| L3 · Governance / CI | `.github/workflows + scripts` | Tests, AST zero-deps gate, measured benchmarks, Pages deploy. | 9 guard self-tests executed in this audit |
| L4 · Launch kit | `launch + docs` | Show HN / case study / benchmarks / readiness corrections. | documents only — no runtime claim |
| L5 · Archive | `projects/ (48 projects)` | Byte-identical upstream imports, each with its own license. | AUDIT.md (48/48 byte-identical) — NOT re-verified here |

Categories: `agents`=16, `fashion-ecommerce`=8, `seeds`=7, `specialized-apps`=8, `web-platforms`=9

## 4. Repo-level checks

- **secret_redaction_marker_present:** `True`
- **certification_json_files:** `61`
- **certification_json_parsed:** `60`
- **certification_json_invalid:** 1 — `projects/12pro/certification/v1.0.0-raw/health-raw.json` (JSONDecodeError)
- **catalog_matches_directory_count:** `True`
- **audit_md_exists:** `True`

## 5. HIGH-severity findings (ranked)

| Project | Issue |
|---|---|
| `12pro/certification` | invalid/empty JSON artifact: projects/12pro/certification/v1.0.0-raw/health-raw.json (JSONDecodeError) |
| `WEB` | proprietary — exclude from open-source claims |
| `ccelia2027` | license undecided |
| `celia-pro-deployment` | proprietary — exclude from open-source claims |
| `new-agent` | LICENSE file corrupt |

## 6. Project-by-project health (48)

| Project | Status | Files | License | Tests | CI | Simulation |
|---|---|---:|---|---:|---:|---|
| `12pro` | DECLARED | 457 | MIT | 15 | 12 | — |
| `1pro` | SIMULATED | 125 | Apache-2.0 | 22 | 6 | yes |
| `agentic-ai-system-2026` | DECLARED | 5 | NONE | 0 | 0 | — |
| `agi-system` | PROVEN | 27 | NONE | 2 | 1 | — |
| `ai-store` | DECLARED | 1 | MIT | 0 | 0 | — |
| `ai-store2` | DECLARED | 21 | NONE | 0 | 0 | — |
| `ai-task` | DECLARED | 142 | MIT | 4 | 0 | — |
| `ai.2027` | DECLARED | 37 | MIT | 5 | 1 | — |
| `arbic` | DECLARED | 30 | NONE | 0 | 0 | — |
| `blank-app` | DECLARED | 10 | Apache-2.0 | 0 | 0 | — |
| `ccelia2027` | DECLARED | 36 | PENDING | 1 | 5 | — |
| `cela.ai` | DECLARED | 1 | NONE | 0 | 0 | — |
| `cela.h` | DECLARED | 2 | MIT | 0 | 0 | — |
| `cela.nimna` | SIMULATED | 204 | MIT | 28 | 5 | yes |
| `Cela2` | DECLARED | 8 | NONE | 0 | 0 | — |
| `cela2027` | SIMULATED | 86 | MIT | 21 | 1 | yes |
| `Celia-2027` | DECLARED | 68 | NONE | 1 | 4 | — |
| `celia-fashion-2026.` | DECLARED | 90 | NONE | 0 | 1 | — |
| `celia-fashion-design` | DECLARED | 51 | NONE | 0 | 0 | — |
| `celia-fashion-live-master` | DECLARED | 14 | NONE | 0 | 0 | — |
| `celia-pro-deployment` | DECLARED | 164 | PROPRIETARY | 15 | 1 | — |
| `celia.sh` | DECLARED | 126 | MIT | 15 | 4 | — |
| `CELIA22` | DECLARED | 22 | MIT | 0 | 0 | — |
| `celia_elazamey-pro` | DECLARED | 1 | MIT | 0 | 0 | — |
| `CELIA_SERVICES` | DECLARED | 31 | NONE | 0 | 0 | — |
| `CeliaFashionDesign` | DECLARED | 22 | MIT | 0 | 0 | — |
| `celiaos-agent-framework` | DECLARED | 57 | NONE | 0 | 0 | — |
| `dr.mukhtar` | SIMULATED | 237 | MIT | 0 | 14 | yes |
| `easy-ai` | DECLARED | 38 | NONE | 6 | 2 | — |
| `ELAZAMET1` | SIMULATED | 79 | NONE | 0 | 0 | yes |
| `firebase-ai-app` | DECLARED | 2 | NONE | 0 | 0 | — |
| `gethip_agen` | DECLARED | 97 | NONE | 26 | 1 | — |
| `if-you-can` | DECLARED | 1 | MIT | 0 | 0 | — |
| `leader2027` | DECLARED | 190 | MIT | 33 | 2 | — |
| `My-Independent-Virtual-World` | DECLARED | 29 | NONE | 0 | 0 | — |
| `new-agent` | FAILED | 31 | CORRUPT (binary/garbled/placeholder) | 0 | 0 | — |
| `new-new` | DECLARED | 142 | NONE | 7 | 1 | — |
| `new-project` | DECLARED | 68 | NONE | 4 | 0 | — |
| `new.pro` | DECLARED | 128 | MIT | 24 | 1 | — |
| `New33` | DECLARED | 1 | MIT | 0 | 0 | — |
| `open_source-` | DECLARED | 609 | MIT | 82 | 10 | — |
| `platform` | DECLARED | 76 | NONE | 2 | 0 | — |
| `SICA-Self-Improving-Coding-Agent-` | DECLARED | 10 | NONE | 0 | 0 | — |
| `Software-Agent-Architect` | DECLARED | 37 | NONE | 0 | 0 | — |
| `support-tickets` | DECLARED | 7 | Apache-2.0 | 0 | 0 | — |
| `system-` | DECLARED | 126 | NONE | 2 | 0 | — |
| `ui` | SIMULATED | 158 | Apache-2.0 | 0 | 10 | yes |
| `WEB` | DECLARED | 63 | PROPRIETARY | 1 | 0 | — |

## 7. What this report does NOT prove

- It does **not** build or run the 47 other projects (they need Node/npm, external services, API keys or proprietary runtimes). DECLARED ≠ working.
- It does **not** re-verify the 48/48 byte-identical audit; that claim is inherited from `AUDIT.md`.
- It does **not** test external live URLs. Declared demo links remain NOT VERIFIED.
- It does **not** grant licenses. 22 projects have no LICENSE and 2 are proprietary.

## 8. Tasks requiring owner approval (nothing was done automatically)

- [ ] **Decide the license for `agi-system`** — blocks PyPI publication.
- [ ] **Replace `projects/new-agent/LICENSE`** — 4-byte corrupt placeholder.
- [ ] **Repair `projects/12pro/certification/v1.0.0-raw/health-raw.json`** — empty file, JSON parse fails.
- [x] **Harden cross-platform containment** — closed 2026-11-17: `core/policy.py` refuses backslashes, Windows drive prefixes and percent-encoded separators (`%2f %5c %2e %00`) on every platform; 16/25 former literal cases now DENIED, 9 are literal POSIX names with nothing to traverse.
- [ ] **Choose Phase 2** — cloud LLM / local model / defer. No model code was added.
- [ ] **Repo metadata + Discussions** — integration token returns 403 on admin settings.
- [ ] **Approve merging the HUD PR into `main`** — this session opens a reviewable PR and does NOT merge it.

---

*Full machine-readable version: `GET /api/audit` on the Command HUD, or `python3 command-hud/audit.py`.*
