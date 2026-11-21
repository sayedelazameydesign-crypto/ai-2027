<div align="center">

# 🧠 AI-2027

### Open AI Systems. Real Intelligence. Built in Public.

**An open ecosystem of 48 archived AI projects — and one agent kernel that proves its work.**

*Latest local read-only inventory* (`python3 command-hud/audit.py`): **48 projects · 3,981 files ·
1 PROVEN · 40 DECLARED · 6 SIMULATED · 1 FAILED**. A classification of archived projects is not a product-completion score.
<br>*Historical verification on 2026-11-18:* **295 tests green · 15/15 live-HTTP checks · 100/100 attacks, 0 breaches ·
48 projects / 3,967 files · 48/48 archive identity accounted for at that time** → [`docs/GROUND-TRUTH-2026-11-18.md`](docs/GROUND-TRUTH-2026-11-18.md)
<br>*Anti-false-claim guards shipped with it:* [`scripts/network_preflight.py`](scripts/network_preflight.py)
(a blind runner reports **BLOCKED**, never "dead") · [`docs/deviations.json`](docs/deviations.json)
(generated files declared volatile **and** schema-checked, so `drift` still means drift) ·
[`scripts/live_egress_audit.py`](scripts/live_egress_audit.py) (re-measures every public URL claim
on a scheduled runner) · [`scripts/secret_scan.py`](scripts/secret_scan.py) (a credential never
becomes a commit — install with `./scripts/install_hooks.sh`).

[![Projects](https://img.shields.io/badge/projects-48-00e5a0)](#the-ecosystem)
[![Files](https://img.shields.io/badge/files-3%2C981-38bdf8)](catalog.json)
[![Audited](https://img.shields.io/badge/post--merge%20audit-passed-brightgreen)](AUDIT.md)
[![Proven](https://img.shields.io/badge/executed%20here-1%20PROVEN%20%C2%B7%2040%20declared%20%C2%B7%206%20simulated-orange)](docs/GROUND-TRUTH-2026-11-17.md)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Release](https://img.shields.io/github/v/release/sayedelazameydesign-crypto/ai-2027?sort=semver)](https://github.com/sayedelazameydesign-crypto/ai-2027/releases)
[![CodeQL](https://github.com/sayedelazameydesign-crypto/ai-2027/actions/workflows/codeql.yml/badge.svg)](https://github.com/sayedelazameydesign-crypto/ai-2027/actions/workflows/codeql.yml)
[![License](https://img.shields.io/badge/licenses-mixed%20(per--project)-orange)](#-licensing)
[![CI](https://github.com/sayedelazameydesign-crypto/ai-2027/actions/workflows/ci.yml/badge.svg)](https://github.com/sayedelazameydesign-crypto/ai-2027/actions/workflows/ci.yml)
[![Pages](https://github.com/sayedelazameydesign-crypto/ai-2027/actions/workflows/pages.yml/badge.svg)](https://github.com/sayedelazameydesign-crypto/ai-2027/actions/workflows/pages.yml)

[🌐 Live demo](https://sayedelazameydesign-crypto.github.io/ai-2027/demo.html) · [Ground truth](docs/GROUND-TRUTH-2026-11-17.md) · [Deviations](docs/DEVIATIONS.md) · [العربية ⬇](#-ai-2027--المستودع-الجامع-لكل-الأدوات-والميزات) · [Catalog](catalog.json) · [Audit Report](AUDIT.md) · [Phase-0 Readiness](docs/PHASE-0-READINESS.md) · [Launch Kit](launch/) · [🔥 Ember plan](docs/EMBER-PLAN.md) · [Changelog](CHANGELOG.md) · [Governance](docs/GOVERNANCE.md) · [ADRs](docs/adr/)

</div>

---

## What is AI-2027?

Most AI agents say *"done"* — AI-2027 agents must **prove it**.
This repository is the central hub unifying every tool and feature from [@sayedelazameydesign-crypto](https://github.com/sayedelazameydesign-crypto)'s 68 repositories into one verified, categorized ecosystem:

> **48 projects · 3,981 files counted by the latest local audit · archive identity and deviations are tracked separately** (see [AUDIT.md](AUDIT.md), [DEVIATIONS.md](docs/DEVIATIONS.md), and run `python3 command-hud/audit.py` for the current inventory)
>
> **What "archived" means here:** a folder is an upstream snapshot with documented exceptions where applicable, *not* evidence that it runs or remains byte-identical to upstream.
> `catalog.json` now carries an `operational_status` field per project (`PROVEN` / `DECLARED` / `SIMULATED` / `FAILED`),
> measured by `python3 command-hud/audit.py`, so the index cannot be read as "48 working systems".

Every archived project lives under [`projects/`](projects/); its license status varies and may be missing or pending, so check that project's source and notice before reuse. [`catalog.json`](catalog.json) is the machine-readable inventory.

## ⚡ The Evidence-First Cycle

The shared philosophy across the ecosystem — an agent never declares success without independent verification:

```
Understand → Plan → Policy → Execute → Verify → Evidence → Report
```

*"No claims without evidence."* — the agent reaches `VERIFIED` only after an independent verifier checks the world itself.

## 🌟 Flagships

| Project | What it is |
|---|---|
| **[Cela 2027](projects/cela2027)** | Autonomous Manus-style agent: Goal→Plan→Execute→Verify→Evidence loop, coding agent tools, Pyodide sandbox, live 3D pipeline UI, Arabic dark interface — with a clearly-labeled local simulation mode (no API key needed) |
| **[CeliaOS · 12pro](projects/12pro)** | 12-layer Agent OS control plane: benchmarks (latency/memory/planning/tool-use), certification gates G0–G15, attestations, monorepo (api + web + agent-ui) |
| **[agi-system](projects/agi-system)** | Pure-Python AGI kernel, zero dependencies: verified cognitive loop plus an exact small-integer optimizer, safe AST contract parser, independent constraint audit and optional local SQLite audit chain; 104 unit tests |
| **[🧪 Live Agent Experience](live-agent)** | **Runnable now (Phase 1):** zero-dependency web app on the real agi-system kernel — submit a goal, watch Plan→Policy→Execute→Verify→Evidence, try a sandbox escape (DENIED), download the evidence JSON. Honest scope: deterministic planner, no LLM yet |
| **[✨ AI-2027 Workspace UI](workspace-ui)** | Arabic-first RTL workspace over Agent Studio sessions, streamed task events, system/provider/budget status and verified artifacts; its Connectors page reads the authenticated live Hub catalog and distinguishes configured credentials from verified access. The bounded Live Agent kernel workspace remains available as a separate view. See its README to run. |
| **[🚀 AI-2027 Agent Studio](python-2027)** | **Unified Manus + Claude Workspace:** bridges L0 Kernel + L1 Memory + L4 Free-First LLMs (`Ollama`, `Gemini`, `NVIDIA NIM`, `Groq`, `OpenRouter`, honest offline fallback) + L5 Connectors Approval Gate into a streaming web Studio (`./scripts/studio_up.sh`) and CLI agent (`ai2027-py27 agent`) |

## 🗂️ The Ecosystem

| Category | Count | Highlights |
|---|---|---|
| 🤖 AI Agent Systems | 16 | cela2027 · agi-system · CeliaOS · NOVA · Nimna (28 tools) · open_source- · celia.pro · 10-agent services |
| 🌐 Chat & Web Platforms | 9 | ui (agentic-first baseline) · Nawah AI · Waha · leader2027 · Easy AI · celia.sh |
| 👗 Fashion & E-Commerce | 8 | ELAZAMET1 · CELIA FASHION 2026 · AI stylist · smart importing |
| 🏥 Specialized Apps | 8 | Celia Kids clinic · MIVW · educational competencies · BitNet system |
| 🧱 Foundations & Seeds | 7 | ai.2027 · Celia-2027 starter · early seeds |

Full categorized catalog with per-project features: **below in Arabic** ⬇ and machine-readable in [`catalog.json`](catalog.json).

## ✅ Verified — not just claimed

- 🔍 **Post-merge audit re-run 2026-11-17**: 46/48 byte-identical to upstream + 2 deviations documented in [DEVIATIONS.md](docs/DEVIATIONS.md) — the archive is a mirror, not a claim that it runs ([GROUND-TRUTH](docs/GROUND-TRUTH-2026-11-17.md))
- 📊 **Measured, not claimed**: full verified cycle **p50 7.5 ms** · denial at the policy wall **3.1 ms** · zero deps · 104/104 agi-system unit tests → [BENCHMARKS.md](docs/BENCHMARKS.md) (reproducible: `live-agent/bench.py`)
- 📦 **Installable**: 40 KB wheel, CLI smoke-tested → `packaging/agi-kernel` (PyPI publish gated on the pending license decision)
- 🔐 **Secrets scan**: one exposed key found → redacted here (rotation tracked in AUDIT.md)
- 📄 **Readiness review**: what's proven vs. declared vs. simulated → [docs/PHASE-0-READINESS.md](docs/PHASE-0-READINESS.md) (with the 2026-11-17 correction addendum)
- 🧪 **Try it in your browser**: the [public kernel demo](https://sayedelazameydesign-crypto.github.io/ai-2027/demo.html) runs the 7-stage loop with a real policy wall, an independent verifier and SHA-256 evidence. Honest scope: a dependency-free JS **port** on an in-memory sandbox, not the Python kernel and not an LLM (12 contract tests in [`website/kernel-demo.test.js`](website/kernel-demo.test.js)).
- 🤖 **CI on every push** → [`.github/workflows/ci.yml`](.github/workflows/ci.yml): 104 kernel tests + 8 wrapper tests across Python 3.10/3.11/3.12, CLI VERIFIED/DENIED contract, AST zero-dependency gate (+ 9 self-tests for the gates themselves), 12 demo tests, `bench.py` with raw JSON artifacts, and a wheel smoke test. Regression gate is jitter-aware by design → [`scripts/ci_guard.py`](scripts/ci_guard.py).
- 🧭 **Project lifecycle control plane** → [`.github/workflows/project-lifecycle.yml`](.github/workflows/project-lifecycle.yml): one manual entry point for the full CI gate, optional Gemini/NVIDIA/E2B checks, and a gated Render deployment with exact service-name confirmation in the `production` environment (configure required reviewers there for human approval). Provider secrets are passed only to their reusable workflow; model/service routing lives in non-secret Actions Variables. Live checks and deployment are main-only and never run from a PR.

## 🧠 Cumulative memory that only remembers what was proven

L1 memory admits a fact **only** if the kernel returned `VERIFIED`, the independent
verifier's `hash_matches` check passed, and the artifact still hashes to the recorded
SHA-256. `DENIED`/`FAILED` are refused *and logged*.

```bash
python3 memory/store.py demo            # admit · refuse · tamper · measured noise floor
python3 memory/evaluate.py --markdown   # recall + false positives, misses included
python3 memory/test_memory.py -v        # 16 tests
```

**Honest headline:** the backend is stdlib `sqlite3` + a deterministic **lexical**
with IDF concept coverage over a **declared** hand-written thesaurus (no `sqlite-vec`, no model).
Measured paraphrase recall@3 improved from 2/16 to **11/16 (0.688)** while near-miss
false positives stayed at 0/7 —
it misses synonyms. That number is published, not hidden: [`memory/README.md`](memory/README.md).

## 🧾 Engineering discipline — the boring parts, done

| Control | Where | Proof |
|---|---|---|
| A version exists only with a Release + SBOM + checksums | `.github/workflows/release.yml` | tags `v1.0.0`, `v1.0.1` assets |
| Actions pinned to full SHAs (36 components) | every workflow | `python3 scripts/sbom.py --assert-pinned` |
| A beat is judged against its own history, not against itself | `scripts/health_gate.py` | `python3 scripts/test_health_gate.py` (24) |
| Recovery is one command, timed | `scripts/ember_up.sh` | `./scripts/ember_up.sh --check` → 2.46 s · 15/15 |
| The 60 s task promise is falsifiable | `scripts/run_task.py` | `python3 scripts/test_run_task.py` (6) |
| Static analysis on every PR | `.github/workflows/codeql.yml` | CodeQL badge |
| Dependency proposals automated | `.github/dependabot.yml` | Dependabot tab |
| Ownership + review expectations | `.github/CODEOWNERS` | file |
| Decisions recorded, not remembered | `docs/adr/0001…0003` | ADR index |
| What is *not* enforced is also written down | `docs/GOVERNANCE.md` | §"NOT enforced" |

## ▶️ Actually running — verified over HTTP, not asserted

Two servers, one real kernel, and a smoke test that hits them the way a human does:

```bash
python3 live-agent/server.py  --port 8090 &     # real kernel, real sandbox
python3 command-hud/server.py --port 8091 &     # dashboard over the same kernel
python3 scripts/runtime_smoke.py                # 15 checks, PASS/FAIL per promise
```

Latest run against live servers (`2026-11-17`):

```
PASS  live-agent: health + honest scope        [REAL execution — deterministic planner, NO LLM]
PASS  live-agent: create_file -> VERIFIED (5/5 checks)
PASS  live-agent: artifact download matches byte-for-byte     [26 bytes]
PASS  live-agent: evidence retrievable by run_id
PASS  live-agent: POSIX traversal -> DENIED
PASS  live-agent: Windows-style traversal -> DENIED           [cross-platform containment]
PASS  live-agent: VERIFIED run admitted to memory, DENIED run refused   [memories=1 refused=2]
PASS  live-agent: memory search returns the real run above noise        [score=0.7078 thr=0.15]
PASS  live-agent: unrelated query -> no confident hit (honest recall)   [top_score=0.0]
PASS  live-agent: agent card is valid + declares its limits             [9 endpoints · 4 limits]
PASS  live-agent: metrics expose real counters (Prometheus text)        [verified_runs>=1]
PASS  hud: health (stdlib, no LLM)
PASS  hud: dispatch -> real kernel VERIFIED
PASS  hud: built-in escape test -> DENIED
PASS  hud: 100-attack gate (0 breaches, all gates)            [denied=91 contained=9]
15/15 checks passed
```

The memory checks are the **evidence gate in motion**: `POST /api/run` offers every
finished run to `memory/store.py`, and the store admits it only if the kernel's own
verifier says `VERIFIED`, the hash matches, and the artifact really exists on disk.
A `DENIED` traversal run is written to the admission log as *refused*, with its reason.
`GET /api/memory` returns the store stats and `GET /api/memory/search?q=…` returns
lexical scores plus the measured noise-floor threshold — recall is measured
(`memory/evaluate.py`), never asserted.

CI job **`Runtime smoke · real servers over HTTP`** boots both servers and runs exactly this,
so "it runs" cannot regress silently.

**Machine-discoverable, not just human-readable:** `GET /api/agent-card` serves an
A2A-style card (`agent-card.json`) that declares skills **and** `honest_limits`, and
`GET /api/metrics` serves Prometheus counters (runs by status, memories, refusals,
uptime). The smoke test refuses an agent card whose endpoints are not real `/api/` paths
or that hides its limits.

**It also runs without you:** `.github/workflows/ember.yml` beats every 6 hours —
boots both servers, runs the 15 checks, recomputes a measured **Heat Index**, publishes
`ember/status.json` + `STATUS.md` to the `heartbeat` branch, opens one issue when
degraded and closes it on recovery.

**…and it refuses its own good news.** `scripts/health_gate.py` judges every beat
against `ember/history.jsonl`: a beat that says `15/15` while having measured only 3
checks is *degraded*. The ladder is one retry → one issue → `escalation/s3` with the
code owner mentioned (see [ADR 0004](docs/adr/0004-health-gate-escalation.md)).

```bash
./scripts/ember_up.sh --check             # boot everything, 15 checks, shut down (2.46 s)
./scripts/ember_up.sh                     # leave it running: live agent :8090 · HUD :8091
./scripts/ember_up.sh --stop              # stop what it started
python3 scripts/ember_beat.py --write     # one beat, right now
python3 scripts/health_gate.py            # judge the last beat against its history
python3 scripts/run_task.py --boot        # one real task, measured, with an evidence link
```

Measured on 2026-11-17 (`docs/T4-T6-EVIDENCE.md`): cold boot **2.46 s** · smoke
**15/15** · one real task **22.9 ms** with a locally recomputed SHA-256, published
budget **60 s**.

[![Ember beat](https://github.com/sayedelazameydesign-crypto/ai-2027/actions/workflows/ember.yml/badge.svg)](https://github.com/sayedelazameydesign-crypto/ai-2027/actions/workflows/ember.yml)
· [Live status (heartbeat branch)](https://github.com/sayedelazameydesign-crypto/ai-2027/blob/heartbeat/STATUS.md)
· [2027 roadmap](docs/ROADMAP-2027.md)

## 🎛️ Live Command HUD — see the kernel move

A single-screen, stdlib-only dashboard over the **real** kernel: live p50 chart,
`VERIFIED` / `DENIED` / `FAILED` counters, an escape test, a 100-input adversarial
red-team, a dispatch freeze, and a read-only health audit of all 48 projects.

```bash
python3 command-hud/server.py --port 8090 --host 0.0.0.0
```

Open the **live preview URL** (in Arena) or `http://localhost:8090` on your own
machine. ⚠️ The command `python -m live_agent.server` **does not exist** in this
repo — see [`command-hud/README.md`](command-hud/README.md) for the correction,
and [`docs/REPOSITORY-AUDIT.md`](docs/REPOSITORY-AUDIT.md) for the full
project-by-project health report (PROVEN / DECLARED / SIMULATED / FAILED).

Verified results, not claims: `python3 command-hud/test_hud.py -v` (17 tests),
`python3 command-hud/redteam.py --markdown --assert` → **100 attacks, 0 breaches,
10/10 benign controls VERIFIED · 91 DENIED · 9 accepted literal POSIX names that
resolve inside the sandbox on every OS**. The cross-platform gap found on
2026-11-17 is **closed**: `core/policy.py` now refuses backslashes, Windows drive
prefixes and percent-encoded separators (`%2f %5c %2e %00`) on every platform.
See [`docs/GROUND-TRUTH-2026-11-17.md`](docs/GROUND-TRUTH-2026-11-17.md) for what
was executed versus what is only declared.

## 📣 Build in Public — proof-first publishing kit

The first technical case study is **ready to publish**: [`launch/case-study-deterministic-core.md`](launch/case-study-deterministic-core.md) — with freshly measured numbers, published jitter, a "what I got wrong" section, and a [proof ledger](launch/PROOF-LEDGER.md) mapping every claim to a command. Companion assets: [Show HN post](launch/show-hn.md), [HN comment playbook](launch/hn-comments-playbook.md), [Build-in-Public series](launch/build-in-public.md).

```bash
python3 scripts/generate_launch_evidence.py --assert   # regenerate EVERY public number
python3 launch/test_launch_claims.py -v                # a claim without evidence breaks CI
```

> The real-world business case study is deliberately **NOT PUBLISHABLE** until
> measured production data exists — see [`launch/REAL-WORLD-CASE-STUDY.md`](launch/REAL-WORLD-CASE-STUDY.md).
> No invented customers, savings or revenue.

## Arena Productivity Kit 2027

[Arabic setup and test guide](docs/ARENA-PRODUCTIVITY-KIT-2027.md) · [Interactive kit & lab source](website/productivity-kit/)

A standalone kit for selecting Chrome extensions, testing them against public or synthetic data, and handing reviewed results to Arena. It does not install extensions or connect to your browser. Run the static site locally with `python3 -m http.server 8787 --bind 127.0.0.1 --directory website` and open <http://127.0.0.1:8787/productivity-kit/>.

[Verified Browser Integration](docs/VERIFIED-BROWSER-INTEGRATION.md) is a separate Stage 1 handoff: Arena prepares one bounded lab request; you approve and run it in isolated local Chrome and return only a reviewed receipt. No remote access. Live owner-side verification is still pending.

## ⚡ Quick start (zero dependencies)

```bash
# The whole running system, one command (boots both services + 15 real HTTP checks)
./scripts/ember_up.sh --check

# One real task, measured, with an evidence link
python3 scripts/run_task.py --boot

```bash
# The canonical Python kernel
cd projects/agi-system
python3 -m unittest discover -s tests -t .        # 104 tests
python3 -m core doctor
python3 -m core run --goal-type create_file --param path=a.txt --param content=hi   # VERIFIED
python3 -m core run --goal-type create_file --param path=../../x --param content=x  # DENIED, exit 3

# Reproduce every published number (jitter published beside each timing)
cd ../../live-agent && python3 bench.py
```

## ⚖️ Licensing

Each project keeps **its own license** inside its folder. Most are MIT/Apache-2.0; two projects (`WEB`, `celia-pro-deployment`) carry proprietary notices, one is undecided (`agi-system`), one is pending (`ccelia2027`). This is **not** an all-MIT archive — check each project's LICENSE before reuse. See the [license inventory](docs/PHASE-0-READINESS.md#2-license-inventory).

---

<div dir="rtl">

# 🧠 ai-2027 — المستودع الجامع لكل الأدوات والميزات

> **كل أداة وميزة من مستودعات [sayedelazameydesign-crypto](https://github.com/sayedelazameydesign-crypto) في مكان واحد.**
> أحصى آخر تدقيق محلي للقراءة فقط **48 مشروعًا و3,981 ملفًا** (`python3 command-hud/audit.py`).
> ⚠️ **الأرشفة لا تعني التشغيل:** التصنيف الحالي **1 مثبت · 40 موصوفة فقط · 6 محاكاة · 1 به عطل محدد**؛ لا تمثل هذه الأرقام نسبة اكتمال المنتج. راجع سجل التدقيق وأعد تشغيل الأمر للحصول على الجرد الحالي.

المجلدات في `projects/<اسم-المستودع>/` أرشيفات للمصادر مع استثناءات موثقة حيث تنطبق؛ لا يعني ذلك تطابقًا بايتيًا دائمًا أو أن المشروع يعمل. حالة الترخيص تختلف وقد تكون مفقودة أو قيد الحسم، لذا راجع ترخيص المصدر قبل إعادة الاستخدام.
الفهرس الآلي: [`catalog.json`](catalog.json).

---

## 📊 نظرة سريعة

| الفئة | العدد | المشاريع |
|---|---|---|
| 🤖 أنظمة الوكلاء الذكية | 16 | cela2027 · agi-system · 12pro · open_source- · cela.nimna · celia-pro-deployment · CELIA_SERVICES · ai-task · platform · new-agent · gethip_agen · celiaos-agent-framework · agentic-ai-system-2026 · Cela2 · SICA · Software-Agent-Architect |
| 🌐 منصات المحادثة والويب | 9 | ui · new.pro · new-new · system- · WEB · leader2027 · easy-ai · 1pro · celia.sh |
| 👗 الأزياء والتجارة الإلكترونية | 8 | ELAZAMET1 · celia-fashion-2026. · celia-fashion-design · celia-fashion-live-master · CeliaFashionDesign · CELIA22 · ai-store2 · ai-store |
| 🏥 تطبيقات متخصصة | 8 | dr.mukhtar · arbic · My-Independent-Virtual-World · new-project · support-tickets · blank-app · firebase-ai-app · ccelia2027 |
| 🧱 أسس وبذور أولية | 7 | ai.2027 · Celia-2027 · cela.ai · cela.h · New33 · if-you-can · celia_elazamey-pro |

</div>

---

## 🤖 أنظمة الوكلاء الذكية · AI Agent Systems

| المشروع | اللغة | الأدوات والميزات |
|---|---|---|
| [`projects/cela2027`](projects/cela2027) | TypeScript | **وكيل مستقل بأسلوب Manus وعقل Claude**: حلقة Goal→Plan→Policy→Execute→Verify→Evidence→Ledger، شبكة خط أنابيب 3D حية (Three.js)، وكيل برمجي بأدوات read/ls/write/edit/patch/scaffold، صندوق Python معزول (Pyodide)، واجهة عربية داكنة، «لا ادعاءات بدون أدلة» |
| [`projects/agi-system`](projects/agi-system) | Python | **نواة AGI Kernel بـ Python صرفة بدون أي اعتمادات**: حلقة إدراكية كاملة مع Solver صحيح حتمي صغير، عقد AST آمن، تحقق مستقل وسجل SQLite اختياري، 104 اختبار وحدة، `python3 -m core doctor` |
| [`projects/12pro`](projects/12pro) | TypeScript | **CeliaOS v1.0.0 — Agent OS Control Plane**: معمارية 12 طبقة، Benchmarks (latency/memory/planning/tool-use/long-horizon)، Certification + Attestations + SBOM، Monorepo (apps/api + apps/web + apps/agent-ui)، جاهزية نشر Docker/Fly.io/Render/Railway |
| [`projects/open_source-`](projects/open_source-) | Python | **`cela_use_browser` — منصة متصفح مستقلة مفتوحة المصدر**: استدلال وكيلي ReAct، واجهة تصفح خاصة `/cela/v1` + CelaBrowser، Artifacts لحظية بنمط Claude، تنفيذ معزول بنمط Manus، أدلة اختبارات مولّدة (`make evidence`) |
| [`projects/cela.nimna`](projects/cela.nimna) | Python | **Nimna — وكيل بمهارات قابلة لإعادة الاستخدام**: 28 أداة، مكتبة SKILL.md قابلة للتبديل، ذاكرة متجهة (Qdrant + fallback بـ 768 بُعد)، موافقة بصرية للأدوات الخطرة (TTL 5د)، بوابة رؤية، يعمل على مفتاح Gemini المجاني، 510 اختبارات (504 ناجحة، 6 تحتاج google-genai) |
| [`projects/celia-pro-deployment`](projects/celia-pro-deployment) | Python | **celia.pro — نظام وكيل متقدم 2026**: FastAPI + React 19، Gemini + HuggingFace (مجاني)، 304 اختبارات (تعريفات، لم تُشغَّل هنا)، أدوات متعددة |
| [`projects/CELIA_SERVICES`](projects/CELIA_SERVICES) | JavaScript | **نظام 10 وكلاء متخصصين**: Orchestrator، دعم عملاء، توليد محتوى، تحليلات، مراقبة، أمان، تكامل، تعلّم، UX، DevOps — مع REST APIs + WebSocket + PostgreSQL/MongoDB + Redis/Bull + Docker + Prometheus/Grafana |
| [`projects/ai-task`](projects/ai-task) | TypeScript | **NOVA · Personal AI OS**: واجهة محادثة عربية RTL داكنة، Agent Runtime بحد أقصى لدورات الأدوات، طبقة LLMProvider (مدار / NVIDIA NIM)، Tool Registry بمستويات خطورة، ذاكرة طويلة المدى، Queue/Worker بـ leases وretry وidempotency، Approval Engine، تكامل Google Calendar/Drive/Sheets عبر OAuth |
| [`projects/platform`](projects/platform) | TypeScript | **Single-Worker Agent Loop**: مخطط LLM (2–4 خطوات) → منفّذ أدوات تسلسلي → بث SSE → حفظ بالقاعدة، Next.js 14 + Drizzle ORM + SQLite + Zod (تحقق مزدوج) + Playwright، مخطط بديل حتمي يعمل دون مفاتيح |
| [`projects/new-agent`](projects/new-agent) | TypeScript | **Celia AI Agent v2.5 (Production)**: Gemini 3 Pro، تواصل صوتي لحظي (Native Audio)، توليد فيديو Veo 3.1 وصور Gemini Image، Maps Grounding، نمط محاكاة دون اتصال، React 19 + FastAPI + Docker، واجهة Neumorphism عربية |
| [`projects/gethip_agen`](projects/gethip_agen) | TypeScript | **وكيل GitHub عربي RTL**: دورة آمنة (فهم → تخطيط → فحص سياسة → موافقة → تنفيذ → تحقق → أدلة)، Level 0 قراءة تلقائية / Level 1 تعديل بموافقة، Vite + API |
| [`projects/celiaos-agent-framework`](projects/celiaos-agent-framework) | Python | **إطار وكلاء CeliaOS**: وحدات agents (planner/executor/analyzer/learner/reflector/reporter/delegation/communication)، planning، reasoning، memory، tools، verification، runtime، collaboration |
| [`projects/agentic-ai-system-2026`](projects/agentic-ai-system-2026) | Python | **تنسيق وكلاء متعددين مع Gemini**: main.py + config.py، بنية خفيفة للتوسعة |
| [`projects/Cela2`](projects/Cela2) | Python | **CeliaOS Browser Agent**: Streamlit + LangGraph + browser-use + Playwright، أرشفة النتائج على Google Drive، نشر Netlify |
| [`projects/SICA-Self-Improving-Coding-Agent-`](projects/SICA-Self-Improving-Coding-Agent-) | TypeScript | **SICA — وكيل معماري ذاتي التحسين**: حلقة Plan-Execute-Critique عبر Gemini Function Calling، كفاءة الكود وأفضل الممارسات المعمارية، Vite + React |
| [`projects/Software-Agent-Architect`](projects/Software-Agent-Architect) | TypeScript | **لوحة هندسة السياق لوكيل برمجي**: تصوير معمارية 7 طبقات، تدفقات بيانات متحركة، لوحة عمليات بمؤشرات لحظية، محاكاة دردشة حية بـ Gemini 2.5 Flash (React 19 + Tailwind + Framer Motion + Recharts) |

## 🌐 منصات المحادثة والويب · Chat & Web Platforms

| المشروع | اللغة | الأدوات والميزات |
|---|---|---|
| [`projects/ui`](projects/ui) | JavaScript | **أساس واجهات توكيلي (Agentic-First) لعام 2026**: قابل للقراءة آليًا، بحث هجين، جودة مقاسة، fail-closed، PII guard، نماذج مثبتة، عرض بلا تثبيت، rollback + تدقيق، RTL-first، workflows (guard/security/provenance/dependency-audit) |
| [`projects/new.pro`](projects/new.pro) | TypeScript | **نواة AI — Nawah**: منصة محادثة كاملة مجانية حتى النشر، Next.js 16 (App Router) + Tailwind v4، مزودون مجانيون (Gemini/Groq/HuggingFace)، بث مباشر Streaming، عربي/إنجليزي RTL/LTR، اختبارات وحدة + Playwright E2E |
| [`projects/new-new`](projects/new-new) | TypeScript | تطبيق ويب كامل (client + Dockerfile) مع خطة نشر مجانية، بوابات CI خارجية، توثيق تحقق بصري |
| [`projects/system-`](projects/system-) | TypeScript | هيكل تطبيق ويب متكامل: React client مع AIChatBox وDashboardLayout وuseAuth، جاهز للتوسع |
| [`projects/WEB`](projects/WEB) | Python | **AI Providers System 2026 — Neural Quantum Interface**: تطبيق Flask (app/agent + app/api)، واجهة عصبية كمية بخلفية جسيمات تفاعلية وتأثيرات هولوجرامية، أدلة مزودين مجانيين وخطط تسويق |
| [`projects/leader2027`](projects/leader2027) | TypeScript | **Leader 2027 — منصة إدارة حملة انتخابية** (بلا تفضيلات سياسية): أشخاص/متطوعون/عمل ميداني/تقارير/مؤشرات، محوّل PostgreSQL، بوابة وكلاء MCP، محرك مهام دائم بتنفيذ موزّع (إيجار/fencing/exactly-once)، 197 اختبارًا + README مولّد آليًا من manifest |
| [`projects/easy-ai`](projects/easy-ai) | Python | **Easy AI — مساعد ذكي ثنائي اللغة**: واجهة عربية/إنجليزية داكنة بلا أطر (HTML/CSS/JS خالص)، Glassmorphism، **قناة مزامنة آمنة** Google Drive → المستودع → GitHub Pages كل ساعة مع تعقيم البيانات |
| [`projects/1pro`](projects/1pro) | Python | **واحة · Waha MVP**: Flask backend مع طبقات R1→R6، RAG (بحث + نصوص + فهارس قابلة للفحص)، 452 اختبار Python + فحوص عقود متصفح/مكوّنات/تكاملات، منشور على Vercel، CI على sqlite وpostgres |
| [`projects/celia.sh`](projects/celia.sh) | TypeScript | **لك. — 3 أدوات عربية في تطبيق واحد**: مولّد محتوى (3 مسودات حسب المنتج/الجمهور/المنصة/النبرة)، ملخّص نصوص استخلاصي، وأداة ثالثة — وضع محلي مجاني بالكامل دون مفتاح API، وضع Groq اختياري |

## 👗 الأزياء والتجارة الإلكترونية · Fashion & E-Commerce

| المشروع | اللغة | الأدوات والميزات |
|---|---|---|
| [`projects/ELAZAMET1`](projects/ELAZAMET1) | JavaScript | **CELIA FASHION — متجر إلكتروني ذكي متكامل**: مساعد محادثة يقترح منتجات ويحلل السلة، مساعد مقاسات AI (الطول/الوزن→المقاس)، مدونة SEO بتوليد تلقائي، نظام توصيات، فلترة متقدمة، مفضلات، Express backend + frontend + لقطات شاشة |
| [`projects/celia-fashion-2026.`](projects/celia-fashion-2026.) | TypeScript | **CELIA FASHION 2026 // Retail OS**: Next.js 16 + Supabase + HuggingFace، منسّق أزياء افتراضي (Virtual AI Stylist)، تخصيص Aura (طقس/وقت/موقع)، Smart Sizing AI، واجهات هولوجرامية |
| [`projects/celia-fashion-design`](projects/celia-fashion-design) | TypeScript | **منصة تجارة إلكترونية للموضة**: كونسيرج ذكي «سيليا» مبني على المخزون الفعلي (Gemini)، Supabase/PostgreSQL، Express API، استيراد دولي ذكي من Amazon/AliExpress بهامش ربح 15% تلقائي، بحث بصري بالصور |
| [`projects/celia-fashion-live-master`](projects/celia-fashion-live-master) | HTML/Python | متجر أزياء حي: Flask + صفحات HTML/JS، ai_assistant + ai_recommender + ai_routes |
| [`projects/CeliaFashionDesign`](projects/CeliaFashionDesign) | Python | **منصة أزياء ببنية ذكاء محلي**: Ollama (Phi-3 Mini) + Qdrant (ذاكرة متجهة) + OpenRouter احتياطي، AL-Agent، Directus CMS، N8n أتمتة، Vault أسرار، Traefik، Prometheus/Grafana/Loki، Docker على Oracle Free Tier |
| [`projects/CELIA22`](projects/CELIA22) | Python | نفس عائلة منصة CELIA للأزياء: ai-core + infrastructure (Docker Compose/Traefik/Vault/N8n) + توثيق نشر YunoHost |
| [`projects/ai-store2`](projects/ai-store2) | TypeScript | متجر Next.js (App Router) — قاعدة قابلة للتوسع |
| [`projects/ai-store`](projects/ai-store) | — | ترخيص فقط (بذرة أولية) |

## 🏥 تطبيقات متخصصة · Specialized Apps

| المشروع | اللغة | الأدوات والميزات |
|---|---|---|
| [`projects/dr.mukhtar`](projects/dr.mukhtar) | JavaScript | **Celia Kids — بوابة مواعيد عيادة أطفال**: «الطلب لا يعني التأكيد» — مراجعة بشرية قبل إنشاء حدث Google Calendar، بلا جمع بيانات طبية، ديمو حي على Pages، CI + E2E + Lighthouse |
| [`projects/arbic`](projects/arbic) | HTML/Next.js | **اختبار الكفاءات التربوية**: 250 سؤالًا تطبيقيًا مصنفًا (إدارة صف، طرق تدريس…) مع الإجابة والتفسير، صفحات ثابتة، جاهز لـ Vercel |
| [`projects/My-Independent-Virtual-World`](projects/My-Independent-Virtual-World) | JavaScript | **MIVW — عالم افتراضي مستقل + مركز قيادة رقمي**: «عالمك، بياناتك، ذكاؤك — دون ارتهان لمزود واحد»، توثيق معماري ثقيل (ADR، عقود، trusted-host، vault-and-memory)، packages/kernel، صفر تكلفة افتراضيًا، محلي أولًا |
| [`projects/new-project`](projects/new-project) | Python | **Ultimate BitNet Smart System v2.0**: مساعد ذكي (نصوص+صور)، تكامل Twitter/Facebook/Telegram، إدارة مستودعات GitHub آلية، قاعدة معرفة وبحث ذكي، نظام تعلّم من التفاعلات، تقارير فورية |
| [`projects/ccelia2027`](projects/ccelia2027) | JavaScript | **أساس Ccelia 2027 الآمن**: Cloudflare Worker (GET /health)، اختبارات عقد، CI + CodeQL + Pages، Devcontainer قابل لإعادة الإنتاج |
| [`projects/support-tickets`](projects/support-tickets) | Python | تطبيق Streamlit لتذاكر الدعم: إنشاء وإدارة وتصوير التذاكر |
| [`projects/blank-app`](projects/blank-app) | Python | قالب Streamlit فارغ للانطلاق السريع (uv) |
| [`projects/firebase-ai-app`](projects/firebase-ai-app) | HTML | تطبيق صفحة واحدة مبني على Firebase AI |

## 🧱 أسس وبذور أولية · Foundations & Seeds

| المشروع | الوصف |
|---|---|
| [`projects/ai.2027`](projects/ai.2027) | مساحة عمل مستقلة لتجارب الذكاء الاصطناعي: `src/ tests/ config/ prompts/ docs/`، hooks محلية، فحوص CI |
| [`projects/Celia-2027`](projects/Celia-2027) | قالب تطوير منضبط بمساعدة الذكاء: `make setup/test/build/verify`، AGENTS.md، بوابات CI وأمان وstaging وحدّ إنتاج يدوي، سكربتات verification حتمية |
| [`projects/cela.ai`](projects/cela.ai) · [`projects/cela.h`](projects/cela.h) · [`projects/New33`](projects/New33) · [`projects/if-you-can`](projects/if-you-can) · [`projects/celia_elazamey-pro`](projects/celia_elazamey-pro) | بذور أولية (README/ترخيص) محفوظة للتوسع المستقبلي |

<div dir="rtl">

---

## 🗂️ مستودعات فارغة (محجوزة للمستقبل)

هذه المستودعات موجودة في الحساب لكنها بلا محتوى بعد (0 KB)، فلم يُنسخ منها شيء:

`cela.mcp` · `ELAZAMEY` · `celia-pro-ai` · `celia_tv-pro` · `caeli_elazamey` · `Strong_Agent_AI` · `Strong.agent` · `CELIA-2026` · `Celia.2027` · `ai.2028a` · `29-8-pro` · `new-repo2026` · `aborofyda` · `celia.pro` · `cela` · `cloab` · `STORE3` · `cautious-garbanzo`

**استُبعد عمدًا:** `skills-introduction-to-github` — تمرين تعليمي رسمي من GitHub Skills وليس أداة من تطويرك.

## ⚖️ التراخيص والملكية

- كل مشروع يحتفظ بترخيصه الأصلي داخل مجلده (MIT / Apache-2.0 / ملكية خاصة).
- مشروعا `WEB` و`celia-pro-deployment` يحملان إشعارات ملكية خاصة صارمة — راجع ملفاتهما قبل أي استخدام خارجي.
- لا توجد أسرار أو مفاتيح حقيقية: وُجدت ملفات `.env.example` فقط، ولا تُحفظ الأسرار في Git.

## 🔁 كيفية التحديث لاحقًا

```bash
# لتحديث مشروع واحد من مصدره على GitHub:
curl -sL https://codeload.github.com/sayedelazameydesign-crypto/<repo>/tar.gz/refs/heads/main | tar xz
rsync -a --delete --exclude __pycache__ <repo>-main/ projects/<repo>/
```

---

**الإحصاء الكلي:** 48 مشروعًا · 3,981 ملفًا وفق آخر تدقيق محلي للقراءة فقط · 16 مشروع وكلاء · 9 منصات ويب · 8 مشاريع تجارة أزياء · 8 تطبيقات متخصصة · 7 بذور — **منها مشروع واحد مُثبت بالتشغيل في هذا التدقيق فقط**
**تاريخ الدمج:** 2026-11-17 · **المصدر:** [github.com/sayedelazameydesign-crypto](https://github.com/sayedelazameydesign-crypto?tab=repositories)

---

## 👨‍💻 المؤلف والمُشرف / Author & Maintainer

**Sayed El-Azamey (سيد العزامي)** — Independent AI Systems Builder · Cairo, Egypt

- 🌐 **Professional Profile & Community:** [facebook.com/sayed.elazamy](https://www.facebook.com/sayed.elazamy)
- 🛠️ **Brand:** Sayed El-Azamey Design / CELIA FASHION DESIGN
- 🤝 **Contribute:** see the [good first issues](https://github.com/sayedelazameydesign-crypto/ai-2027/issues?q=is%3Aissue+label%3A%22good+first+issue%22) — translations, benchmarks on your hardware, capability proposals

</div>
