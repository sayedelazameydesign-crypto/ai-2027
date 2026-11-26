<div dir="rtl">

# 🗺️ المرحلة صفر — تدقيق قراءة فقط (delta · 2026-11-26)

**النطاق:** المستودع على الفرع `arena/23919461-ai-2027` @ `1b3d8ad` (+ `main` عبر سجلات PR).
**المنهج:** كل ادعاء مربوط بمسار ملف أو أمر قابل لإعادة التشغيل. لا قيمة سر مطبوعة. لا تعديل على أي ملف تشغيلي — هذا تقرير فقط.
**يبني على (ولا يكرر):** [`GROUND-TRUTH-2026-11-17.md`](GROUND-TRUTH-2026-11-17.md) · [`GROUND-TRUTH-2026-11-18.md`](GROUND-TRUTH-2026-11-18.md) · [`PHASE-0-READINESS.md`](PHASE-0-READINESS.md) (2026-11-17) — هذا التدقيق فحص محدَّث للفرق التي طرأت منذها.

---

## 0. الخلاصة بالأرقام (مُتحقَّق منها الآن)

| المؤشر | القيمة المُقاسة |
|---|---|
| مشاريع `projects/` | **48 مجلداً** = `catalog.json`: **1 PROVEN · 40 DECLARED · 6 SIMULATED · 1 FAILED** ✓ مطابق لادعاء README |
| PRs مفتوحة | **6** — #190 (هذا الفرع) · #189 · #187 · #186 · #185 · #154 |
| اختبارات python-2027 | **1006 passed · تغطية 91.60%** (CI أخضر على Python 3.13 + 3.14 — run الخاص بـ PR #190) |
| اختبارات الجذر (unittest) | **199/200** — الفشل الوحيد سابق ومعروف (`test_baseline_commit_matches_head_and_blobs`، دليل قديم مثبّت على `f0fa3c88`) |
| workflows | **20** ملفاً في `.github/workflows/` |
| متغيّرات `.env.example` | **51** (منها 4 متغيّرات Qwen في §11 + `AI2027_UNKNOWN_COST_DAILY_LIMIT` الجديد) |
| أسرار موثّقة بالاسم | **55** صفاً في `docs/SECRETS.md` + **12** `secrets.*` مستخدمة في workflows + **3** على لوحة Render (`sync: false`) |
| `.env` في مساحة العمل | **غير موجود** ✓ (لا سر محلي مكشوف) |
| وحدات الخطة المسمّاة (`intent_engine` …) | **0** نتيجة grep لكل من: intent_engine, task_compiler, agent_orchestrator, memory_engine, tool_factory, execution_engine, approval_engine, recovery_engine, verification_engine, evidence_engine — الخطة نفسها اعترفت بأن الشيفرة توضيحية ✓ |

---

## 1. مطابقة ادعاءات الخطة مع الواقع

| # | ادعاء الخطة | الحكم | الدليل |
|---|---|---|---|
| 1 | جرد README: 1 PROVEN · 40 DECLARED · 6 SIMULATED · 1 FAILED | ✅ **صحيح** | `python3` عدّ `catalog.json`: `Counter({'DECLARED': 40, 'SIMULATED': 6, 'PROVEN': 1, 'FAILED': 1})` |
| 2 | `render.yaml` يعرّف خدمتين (`ai-2027-live-agent` + `ai-2027-studio`) | ✅ **صحيح** | `render.yaml` سطرا الخدمتين؛ live-agent stdlib-only، studio يحتاج `fastapi/httpx/uvicorn/pydantic` |
| 3 | الواجهة من `website/` وتنشر عبر `pages.yml` | ✅ **صحيح** + منشورة فعلاً | `.github/workflows/pages.yml` (يُنشر على push لـ `main`) · `has_pages=true` · رابط حي في README: `…github.io/ai-2027/demo.html` |
| 4 | التخزين المستقل والنسخ الاحتياطية **غير منفَّذ بالكامل** | ✅ **صحيح** | `deployment/PERSISTENCE.md:101`: الأداة «لا توقف خدمة ولا ترفع نسخة مشفَّرة ولا تجهّز تخزيناً» |
| 5 | `live-agent/server.py` يستخدم مخططاً حتمياً بلا نموذج لغوي مدمج | ✅ **صحيح** | `live-agent/server.py:14` «the planner is deterministic and rule-based» · يوجد مُحوِّل `live-agent/llm_planner.py` (يعمل كمزوّد خطط، والـKernel يملك السياسة/التنفيذ/التحقق) |
| 6 | المسارات `runs/` و`runs/ledgers/` و`sandboxes/` و`state/memory.db` موجودة | ⚠️ **صحيح المضمون، خاطئ المسار** | الفعلية كلها تحت `live-agent/`: `live-agent/runs/` (+ `run_*.json` حقيقية) · `live-agent/runs/ledgers/` (`metrics.json` + `*.jsonl`) · `live-agent/sandboxes/` · `live-agent/memory.db` (+ `.ai2027_studio/memory.db`). **لا يوجد `state/memory.db`** — المسار `state/` غير موجود أصلاً. كلها في `.gitignore` (لا تُرفع للـgit) |
| 7 | `AI2027_READ_ONLY=1` ثابت في البيئة العامة | ✅ **صحيح ومفروض ومختبر** | `render.yaml` (الخدمتان) · `live-agent/server.py:210` · `scripts/doctor.py:332` · اختبار `test_read_only_switch_blocks_a_valid_operator_token` |
| 8 | «Cloudflare Gateway + D1 + R2 = **إنشاء جديد**» | ⚠️ **نصف صحيح** | **الكود موجود**: `python-2027/workers/edge-gateway/` (بوابة: Bearer ثابت الزمن + rate-limit + تحويل SSRF-safe + fallback $0 + اختبارات) و`python-worker` و`worker/approval-worker` (فيه `[[kv_namespaces]]`). **الناقص فعلاً**: نشر حي (لا يوجد `wrangler deploy` غير dry-run في أي workflow)، و**D1 وR2: صفر ربط** (`d1_databases`/`r2_buckets` = 0 نتيجة)، وعقد `/api/v1/tasks…` المقترح غير موجود (عقد البوابة الحالي `/v1/route` و`/v1/audit`) |
| 9 | وحدات Python مقترحة (`intent_engine` …) | ✅ **غير موجودة — والخطة قالت إنها توضيحية** | grep = 0 لكلها (عدا `observability` ككلمة عامة في `monitoring/`). المكافئات الفعلية: `kernel-2027` (حلقة7 مراحل)، `ai2027_protocol/router.py` + `budget.py` (توجيه/ميزانية)، `src/executors/` + `worker/approval-worker` (موافقة/تنفيذ)، `monitoring/` + `memory/` |
| 10 | Qwen خارج `main` حتى مراجعة مستقلة | ✅ **صحيح** | `build_default_providers()` = OllamaLocal → GeminiFree → NVIDIA NIM → GroqFree → OpenRouterFree → DeterministicFallback — **بلا QWEN** · التكامل في PR #190 فقط |

**ادعاءات خارجية في الخطة لم يُعاد التحقق منها هنا** (شبكة مقيدة في هذه البيئة): حدود OpenRouter `:free` (20/د، 50/يوم) · حصص Groq حسب النموذج · سياسات بيانات Gemini المجانية · سقف Render 750 ساعة. تُعامل كـ«مُعلَن — يحتاج تحققاً يدوياً» قبل أن تصبح قيداً معمارياً.

---

## 2. خريطة القدرات (Capability Map)

الحالة ببوابة الخطة نفسها: **PROVEN = اختبار تنفيذ حقيقي** · **DECLARED = كود/وصف بلا تشغيل موثّق** · **SIMULATED = محاكاة أو dry-run فقط** · **BLOCKED = مُنوع أو معلّق**.

> **شرط الاعتماد:** صفوف `SIMULATED` و`DECLARED` و`BLOCKED` أدناه **ليست قدرات تشغيلية مكتملة** ولا تُعرض كـ«خدمة تعمل». كل صف `PROVEN` له أمر إعادة فحص مُشغَّل فعلاً في قسم [2.1](#21-بوابة-إعادة-الفحص-لكل-proven).

| المكوّن | الدور | الحالة | الدليل |
|---|---|---|---|
| GitHub Pages (`website/`) | واجهة عامة ثابتة | **PROVEN** | منشور على `main` + رابط حي في README + فحص سابق فتح4 مواقع حيّة (`GROUND-TRUTH-2026-11-17.md`) |
| python-2027 (Core + Router + BudgetGuard + Studio API) | محرك المزودين | **PROVEN** | CI أخضر متكرر: 1006 اختباراً + mypy + ruff على 3.13/3.14 (PR #190 runs) |
| kernel-2027 | النواة الحتمية7 مراحل | **PROVEN** | **مُقاس اليوم**: `python3 -m unittest discover -s kernel-2027/tests` → **152 passed, OK** · بوابتا CI: `scripts/test_kernel_verification.py -v` → **11 OK** + مهمة «Command HUD · real kernel + 100-attack gate» ناجحة في run الخاص بـ #190. (تنبيه: ادعاء «86» في GROUND-TRUTH يخص `projects/agi-system` **لا** kernel-2027 — ومجموع agi-system المُقاس اليوم = **104** وهو مطابق لتعليقة `ci.yml:2` «104 kernel tests» ✓) |
| live-agent server | خادم الوكيل الحتمي | **PROVEN محلياً** / نشره **DECLARED** | **مُقاس اليوم**: `python3 live-agent/test_server.py` → **23 passed, OK** · `runs/` و`ledgers/` فيها أثر تشغيل حقيقي · لكن لا دليل نشر حي |
| Render (`ai-2027-live-agent` + `ai-2027-studio`) | استضافة Python | **DECLARED** | `render.yaml` + `deploy.yml` (workflow_dispatch مع تأكيد) — **الحالة الحية تحتاج تحققاً يدوياً** من شبكة خارجية |
| Cloudflare edge-gateway | بوابة حدّية $0 | **SIMULATED** | CI: `wrangler deploy --dry-run` + `wrangler dev --local` فقط — **لا نشر حقيقي في أي workflow** |
| Cloudflare python-worker | عامل Python على Workers | **SIMULATED** | dry-run فقط (same step) |
| approval-worker (WebAuthn/Ed25519) | بوابة موافقات | **DECLARED** | اختبارات node 3/3 تمرّ + `wrangler.toml` + KV binding مُعلن — بلا deploy workflow |
| Cloudflare **D1** (مهام/ذاكرة) | قاعدة بيانات دائمة | **غير مبني** | صفر `d1_databases` في المستودع كله |
| Cloudflare **R2** (ملفات/أدلة) | تخزين كائنات | **غير مبني** | صفر `r2_buckets` |
| SQLite محلي (`live-agent/memory.db` + `runs/`) | ذاكرة أثناء التطوير | **PROVEN محلياً** (أثر موجود) مع **فجوة متانة** | `PERSISTENCE.md` يعترف: لا تخزين مستقل ولا نسخ احتياطي مرفوع |
| Free-First Router + BudgetGuard | توجيه $0 | **PROVEN** | سلسلة افتراضية6 مزوودين + `AI2027_MAX_SPEND_USD=0` يفرض الرفض (اختبارات `test_omnirouters_provider` + `test_unknown_cost_daily_limit`) |
| Qwen (Model Studio) | مزوّد UNKNOWN_COST | **BLOCKED (مقصود)** | خارج `main`؛ في PR #190 مع البوابة اليومية `AI2027_UNKNOWN_COST_DAILY_LIMIT` — محجوب عند `$0` + حد يومي5 |
| WAAPI (WhatsApp) | إشعار المالك | **SIMULATED** | probe `45790624784`: توكن ✓، `instance_count_0` ✗ — لا إرسال حقيقي حتى QR + `LIVE CONNECTION VERIFIED` |
| GitHub Actions workers | مهام خلفية | **PROVEN** |20 workflow تُشغَّل فعلياً على كل PR (27 فحصاً أخضر على #190) |
| الأرشيف (48 مشروعاً) | مشاريع معلَنة | **1 PROVEN فقط** | `catalog.json` — لا تُدمج في الخدمة المركزية (قرار الخطة صحيح) |

### 2.1 بوابة إعادة الفحص لكل PROVEN

كل أمر أدناه **شُغِّل فعلاً في2026-11-26** وأعاد النتيجة المذكورة — أي مراجع يستطيع إعادة تشغيله بنفسه:

| الـPROVEN | أمر إعادة الفحص | النتيجة المُقاسة اليوم |
|---|---|---|
| صفحات GitHub | `gh api repos/sayedelazameydesign-crypto/ai-2027/pages` | `status=built` · `html_url=https://sayedelazameydesign-crypto.github.io/ai-2027/` · `source.branch=main` |
| kernel-2027 | `python3 -m unittest discover -s kernel-2027/tests` | `Ran 152 tests … OK` |
| kernel-2027 (بوابة CI) | `python3 scripts/test_kernel_verification.py -v` | `Ran 11 tests … OK` |
| live-agent | `python3 live-agent/test_server.py` | `Ran 23 tests … OK` |
| python-2027 (محرك المزودين) | `cd python-2027 && .venv/bin/ruff check --target-version py313 src tests && .venv/bin/mypy src tests && .venv/bin/pytest -q` | ruff: All checks passed · mypy: no issues (145 ملفاً) · pytest: **1006 passed · coverage 91.60%** |
| بوابات CI العامة | `gh pr checks 190` | **27 pass / 2 skipping / 0 fail** على `1b3d8ad` ([صفحة الفحوصات](https://github.com/sayedelazameydesign-crypto/ai-2027/pull/190/checks)) |
| جذر المشروع (stdlib-only) | `python3 scripts/ci_guard.py imports` | يمرّ (أي import غير stdlib يفشل) |
| ذاكرة محلية بأثر فعلي | `ls live-agent/runs live-agent/runs/ledgers live-agent/sandboxes && ls -la live-agent/memory.db` | ملفات تشغيل حقيقية موجودة (`run_*.json`, `metrics.json`, `*.jsonl`, `memory.db`) |
| جرد المشاريع | أمر `catalog.json` في قسم8 | `48 … DECLARED:40 SIMULATED:6 PROVEN:1 FAILED:1` |

> **ليست PROVEN (لا تُعرض كتشغيل مكتمل):** نشر Render (حاجة فتح يدوي) · Workers CF (dry-run فقط) · WAAPI (بدون QR) · Qwen (BLOCKED قصد) · D1/R2 (غير مبني) · الأرشيف الأربعةون المشاريع.

---

## 3. خريطة الاعتماديات (Dependency Map)

| الطبقة | الاعتماديات | الدليل |
|---|---|---|
| الجذر (`kernel-2027`, `src/`, `scripts/`, `tests/`) | **صفر حزم — stdlib فقط** | `requirements.txt` فارغ عمداً (16 سطراً تعليقاً) + `scripts/ci_guard.py` يفشل CI عند أي import غير stdlib |
| `python-2027` | `fastapi` · `httpx` · `opentelemetry-api/sdk` · `pydantic` · `structlog` · `uvicorn` — كلها مثبّتة في `uv.lock` (ruff=0.17.5, mypy=2.4.1) | `pyproject.toml` + `uv.lock` · `kernel-2027` **ليس** اعتماداً مُصرَّحاً (يُستورد من checkout) |
| `worker/approval-worker` | `@simplewebauthn/server` فقط (+ `wrangler` dev) | `package.json` |
| `python-2027/workers/edge-gateway` | **صفر** (ESM stdlib JS) | README: «Zero-dependency JavaScript ESM» |
| `website/` | ثابت بلا build | `pages.yml` ينشر كما هو (فشل Jekyll السابق موثّق ومُصلَح) |
| Render studio | يُثبّت الحزمة عند البناء (`pip install -e python-2027`) | `render.yaml` |
| ممنوع | أي حزمة جديدة في الجذر | ADR-0002 + تعليق `requirements.txt` |

---

## 4. جرد الأسرار (Secrets Inventory — أسماء فقط، بلا قيم)

| المجموعة | العدد | التسجيل |
|---|---|---|
| جدول `docs/SECRETS.md` | **55** متغيّراً (مفاتيح + نماذج + إعدادات) | يشمل `DASHSCOPE_API_KEY` و`WAAPI_API_TOKEN` و`AI2027_UNKNOWN_COST_DAILY_LIMIT` الجديد |
| `.env.example` | **51** سطراً (بلا قيم حقيقية) | §11 لـQwen · قسم budget عند سطر45/59+ |
| `secrets.*` في workflows | **12** | GEMINI · GROQ · NVIDIA · OPENROUTER · E2B · WAAPI · RENDER_API_KEY · GITHUB_TOKEN · SUBSTRATE×2 · AGENT_SUBSTRATE_KUBECONFIG · EMBER_ALERT_WEBHOOK |
| لوحة Render (`sync: false`) | **3** | `OPENROUTER_API_KEY` · `OPENROUTER_MODEL` · `CONNECTORS_HUB_API_TOKEN` — لا قيم في Git |
| `.env` محلي | **غير موجود** ✓ | — |

**قواعد مثبتة:** لا قيمة مفتاح في أي ملف · `WAAPI_API_TOKEN` في GitHub Actions فقط ودوران مزدوج مع `.env` المحلي · أسرار Render تُدخل يدوياً في اللوحة.

---

## 5. جاهزية النشر (Deployment Readiness)

| الهدف | موجود ككود | نشر فعلي | الفجوة لتصبح PROVEN |
|---|---|---|---|
| واجهة عامة | ✅ `website/` | ✅ **GitHub Pages منشورة** | لا شيء — PROVEN |
| Python Studio API | ✅ `ai2027-py27 studio` | ❓ `render.yaml` + `deploy.yml` جاهزان | فتح `/v1/studio/status` حياً من الإنترنت وتوثيق النتيجة (خطوة يدوية بـ`workflow_dispatch`) |
| live-agent | ✅ `deployment/live_server.py` | ❓ نفس المسار | فتح `/api/health` حياً + دورة تشغيل/إيقاف |
| بوابة Cloudflare | ✅ edge-gateway + approval-worker | ❌ **dry-run فقط** | `wrangler deploy` حقيقي + توثيق + ربط `UPSTREAM_URL` بـStudio |
| D1 / R2 | ❌ | ❌ | بناء `PersistencePort` (كما تقول الخطة) — البدء بـD1 للمهام لا R2 أولاً |
| ربط Studio ↔ live-agent | نصف موجود (`llm_planner.py` كمزوّد خطط) | ❌ لا استدعاء مباشر بينهما | مهمة تكامل واحدة: Studio يسلّم خطة → Kernel ينفذ → الأثر يعود |

---

## 6. قائمة الفجوات الفعلية (مرتبة)

### P0 — تسبق أي نشر جديد
1. **لا يوجد `PersistencePort` ولا D1/R2** — الذاكرة الدائمة الحالية SQLite محلي فقط (`live-agent/memory.db`)، والنسخ الاحتياطي لا يرفع لأي مكان مستقل (`PERSISTENCE.md:101`). بوابة الخطة «إيقاف العامل وسط المهمة دون ضياعها» = **غير مُختبَر حالياً**.
2. **النشر الفعلي غير موثّق** — لا دليل على خدمة Render حية ولا على Worker منشور؛ كل ما هناك = blueprint وdry-run. (هذا التدقيق لا يستطيع فتحها من شبكته — خطوة يدوية.)
3. **6 PRs مفتوحة** تتقاطع في نفس الملفات (`ci.yml` مثلاً) — ترتيب الدمج قرار مسبق لكل تغيير.
4. فشل سابق معروف في الجذر: `test_baseline_commit_matches_head_and_blobs` (دليل `operational-evidence.json` مثبّت على `f0fa3c88`) — يحتاج تحديث الدليل أو إيقاف ادعاءه، وخارج CI.

### P1 — بعد استقرار #190
5. **عقد بوابة غير موحّد** — البوابة الحالية `/v1/route`، والخطة تقترح `/api/v1/tasks` — اختيار واحد وتوثيقه قبل بناء المزيد.
6. **لا نشر حقيقي لـWorkers** — نقل edge-gateway من dry-run إلى `wrangler deploy` مع سر `GATEWAY_TOKEN`.
7. **ربط Studio ↔ Kernel** (أهم مخرج للمرحلة4) — `llm_planner.py` جاهز كمزوّد، الناقص التكامل والاختبار الحي.
8. تحديث `PHASE-0-READINESS.md` أو ربطه بهذا التدقيق حتى لا يتعدد مصدران للحقيقة.

### P2 — ضمن مراحل الخطة
9. وحدات الخطة المسمّاة غير موجودة — تُبنى فوق العقود الحالية (`kernel-2027`, `router`, `budget`, `executors`) لا بديلاً عنها.
10. الأرشيف (48) يبقى أرشيفاً — لا يُدمج في المنتج المركزي (قرار الخطة مدعوم بالدليل: 40 DECLARED).

---

## 7. ملاحظات على الخطة نفسها (هندسياً)

- ✅ **صحيحة في جوهرها**: فصل الذكاء/التنفيذ/الاستمرارية، بوابة واحدة، ذاكرة واحدة، عقود موحدة، `$0` كعقد، ومنع Qwen غير المُثبت التكلفة.
- ⚠️ **تصدير مطلوب**: مسارات الذاكرة (لا `state/`، بل `live-agent/…`)، و«إنشاء جديد» للبوابة (الكود موجود — الناقص النشر والعقد).
- ⚠️ **لا تكرار**: `PHASE-0-READINESS.md` و`GROUND-TRUTH-*` موجودان — هذا التدقيق delta فوقهما، والخطوة التالية يجب أن تُحدّثهما لا أن تنشئ ثالثاً.
- ⚠️ **`AI2027_READ_ONLY=1`** موجود ومفروض بالفعل — الخطة تقدّمه كجديد، فلتُوصف كـ«مُوَجَّه قائم».
- ⚠️ **Qwen**: قرار الخطة (خارج `main` حتى مراجعة مستقلة) متطابق مع الواقع — والبوابة اليومية الجديدة في #190 تعني أن حتى بعد الدمج يبقى محجوباً عند `$0` و`ALLOW_UNKNOWN_COST=0` افتراضياً.

---

## 8. الأوامر (قابلة لإعادة التشغيل)

```bash
# بوابة إعادة الفحص الأساسية (شُغِّلت كلها في 2026-11-26 — انظر §2.1)
gh api repos/sayedelazameydesign-crypto/ai-2027/pages
python3 -m unittest discover -s kernel-2027/tests          # 152 OK
python3 scripts/test_kernel_verification.py -v             # 11 OK
python3 live-agent/test_server.py                          # 23 OK
python3 scripts/ci_guard.py imports                        # stdlib-only

# جرد المشاريع
python3 -c "import json;from collections import Counter;d=json.load(open('catalog.json'));items=d if isinstance(d,list) else d.get('projects',[]);print(len(items),Counter(i.get('operational_status') for i in items))"

# مسارات الذاكرة الفعلية
ls live-agent/runs live-agent/runs/ledgers live-agent/sandboxes; ls -la live-agent/memory.db

# لا D1/R2
grep -rn "d1_databases\|r2_buckets" worker/ python-2027/workers/ || echo none

# لا نشر حقيقي لـWorkers
grep -rn "wrangler deploy" .github/workflows/ | grep -v "dry-run\|dev --local" || echo "no live deploy"

# سلسلة المزودين الافتراضية (بلا QWEN)
grep -A8 "def build_default_providers" python-2027/src/ai2027_protocol/ai_providers.py

# الجذر stdlib-only
python3 scripts/ci_guard.py imports

# بوابات محلية
cd python-2027 && .venv/bin/ruff check --target-version py313 src tests && .venv/bin/mypy src tests && .venv/bin/pytest -q
python3 -m unittest discover -s tests        # من الجذر
node --test worker/approval-worker/test/*.test.js   # من الجذر
```

---

**الحكم:** النظام = **بنية هندسية حقيقية ومُختبَرة** (CI، نواة، ميزانية، أسرار مسجّلة) فوق أرشيف معلن، مع **ثلاث فجوات مانعة للنشر**: (1) لا ذاكرة دائمة مستقلة، (2) لا نشر فعلي موثّق، (3) لا ربط Studio↔Kernel. هذه بالضبط مراحل صفر→3→4 في الخطة — والترتيب المقترح في الخطة صحيح بعد التصحيحات أعلاه.

*تاريخ الفحص: 2026-11-26 · الفرع: `arena/23919461-ai-2027` · HEAD: `1b3d8ad`*
