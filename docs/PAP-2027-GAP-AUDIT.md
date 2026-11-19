<div dir="rtl">

# PAP-2027 — تدقيق الفجوات على الكود الفعلي

**الأساس:** `main` عند `27cb2fa` (بعد دمج [#66](https://github.com/sayedalazameydesign-crypto/ai-2027/pull/66)).
**الغرض:** الإجابة على سؤال واحد — **ماذا يوجد فعلًا من محركات PAP-2027 الاثني عشر، وماذا لا يوجد** — قبل كتابة أي سطر جديد.

هذا المستند ليس وصفًا إنشائيًا؛ جدوله مُشتق من ملف قابل للفحص:

```bash
python3 scripts/pap_gap_audit.py            # الجدول
python3 scripts/pap_gap_audit.py --json     # للمعالجة الآلية
python3 scripts/pap_gap_audit.py --assert   # بوابة CI
python3 scripts/test_pap_gap_audit.py -v    # 14 اختبارًا للبوابة نفسها
```

مصدر الحقيقة: [`docs/pap-2027-engines.json`](pap-2027-engines.json). كل محرك يذكر **ملفات كود** و**ملفات اختبار مع علامة بحث**، والسكربت يفشل إذا اختفى أي منهما. أي محرك بحالة `absent` يجب ألّا يذكر أي ملف — حتى لا تُدّعى قدرة غير موجودة.

النتيجة الآلية الآن: **12 محركًا · 4 `present` · 7 `partial` · 1 `absent` · 0 انحراف**، و**53 ملف كود** و**362 حالة اختبار** مرتبطة بمحركات مسمّاة.

---

## 1️⃣ الجواب المباشر عن «22 / 24» والفحصين المتخطّيين

الرقم يخص فحوص PR #66 (22 ناجحًا، 2 متخطّيان، 0 فاشل). المتخطّيان ليسا مجهولين:

| الفحص المتخطّى | سبب التخطّي (من الكود، لا من الاسم) | هل عمل بعد الدمج؟ |
|---|---|---|
| `Free-First live routing · existing Actions secrets (main only)` | شرط `if` في `python-2027.yml`: `github.ref == 'refs/heads/main'` — لا يعمل على `pull_request` إطلاقًا | **نعم** — نجح في [التشغيل 44525633892](https://github.com/sayedelazameydesign-crypto/ai-2027/actions/runs/44525633892) على `27cb2fa` |
| `Live Gemini call · opt-in (needs GEMINI_API_KEY)` | شرط `if`: `inputs.run_live_gemini` — اختياري صريح عبر `workflow_dispatch` فقط، لحماية الحصة | لم يُشغَّل (لم يُطلب) |

**مهم:** نجاح الفحص الأول على `main` يعني أن الوظيفة أتمّت مسارها؛ ولا يعني أن كل مزوّد أجاب، لأن `--require-any` غير مفعّل افتراضيًا. جدول المزوّدين التفصيلي في ملخّص تلك الخطوة وفي أرتيفاكت `python-2027-live-keys-<sha>`.

---

## 2️⃣ خريطة المحركات الاثني عشر

| المحرك | الحالة | الفجوة المعلنة |
|---|---|---|
| `intelligence-core` | partial | التخطيط داخل وكيل واحد، والاستئناف بعد القطع مدعوم، لكن لا فريق وكلاء ولا تعلّم من الدورات السابقة |
| `python-alchemy` | partial | Protocols/Pydantic/Async موجودة؛ لا توليد كود ولا تحميل إضافات ديناميكي (قرار أمني) |
| `model-router` | **present** | يوجّه بالتكلفة/التوافر لا بجودة الإجابة أو نوع المهمة |
| `agent-fleet` | **absent** | لا توجد طبقة تنسيق بين وكلاء متزامنين |
| `browser-engine` | partial | الوضع `local` فقط، والاختبار على متصفح محاكى |
| `computer-engine` | partial | تنفيذ محلي مقيّد؛ E2B يدوي فقط |
| `skill-engine` | partial | العقود والموافقات مُثبتة؛ المزوّدون محاكاة في CI |
| `memory-engine` | **present** | محلي فقط (SQLite)، لا مزامنة بين أجهزة. استئناف مهمة مقطوعة مُثبت الآن بقتل عملية حقيقي |
| `automation-engine` | partial | يعتمد على Actions/أوامر يدوية، لا مجدول دائم |
| `cloud-runtime` | partial | local + dry-run فقط؛ لا نشر حي مُتحقَّق |
| `security-engine` | **present** | لا عزل شبكي على مستوى OS، ولا تدقيق صلاحيات موزّع |
| `evidence-engine` | **present** | لا يقيس «مهمة مستخدم كاملة» كمقياس مقارن |

> الأعداد (ملفات الكود وحالات الاختبار) لا تُنسخ هنا عمدًا؛ أمر `pap_gap_audit.py` هو مصدرها الوحيد حتى لا يتقادم هذا الجدول.

**كيف تُقرأ الحالة بصدق:** `present` = الكود موجود **واختبار آلي في CI يلمسه**. `partial` = الكود يعمل لكن إثباته محصور (محاكاة، dry-run، وضع واحد، بيئة واحدة). `absent` = لا شيء، والفجوة مكتوبة بدل تجاهلها.

---

## 3️⃣ أي فحوص CI تخدم أي محرك

| الفحص في CI | المحرك/المحركات |
|---|---|
| `Tests · Python 3.10/3.11/3.12` (86 kernel + السياسات + الأدوات) | intelligence-core · security-engine |
| `Python 3.13/3.14 · uv.lock · ruff · mypy · pytest >90%` | python-alchemy · model-router · memory-engine |
| `Cloudflare Workers · Contract Tests + Wrangler Dry-Run` | cloud-runtime (local/dry-run فقط) |
| `Command HUD · real kernel + 100-attack gate + 48-project audit` | security-engine · evidence-engine |
| `L1 memory · admission gate + measured retrieval honesty` | memory-engine · evidence-engine |
| `One-command recovery + real task under 60 s` | automation-engine · memory-engine |
| `Runtime smoke · real servers over HTTP` | cloud-runtime (محلي) |
| `Deployment configs · must parse, must start, must cap the disk` | cloud-runtime · automation-engine |
| `Connectors Hub tests · mock providers only` | skill-engine (محاكاة صريحة) |
| `BrowserSkill local handoff (simulated browser)` | browser-engine (محاكاة صريحة) |
| `Open Source code execution · offline E2B policy tests` | computer-engine (سياسات فقط) |
| `Publish kit · regenerate every claim + verify evidence links` | evidence-engine |
| `Free-First live routing` (على `main` فقط) | model-router (حي، لكنه ليس إثبات جودة) |

---

## 4️⃣ ماذا **لا** يثبته CI الحالي — بالاسم لا بالانطباع

1. **الموصلات الحية:** `Connectors Hub tests` تعمل على `mock providers only`. لا يوجد فحص آلي يثبت اتصالًا حيًا بـGitHub أو Drive أو Gmail.
2. **المتصفح الحقيقي:** اسم الوظيفة نفسه يقول `simulated browser`. لا يوجد تحكم بمتصفح حقيقي تحت الاختبار.
3. **النشر السحابي:** `wrangler deploy --dry-run` يبني الحزمة ولا ينشرها. لا يوجد فحص HTTP بعد نشر فعلي.
4. **E2B:** اختبارات سياسة بدون اتصال؛ التشغيل الحي في `e2b-live-check.yml` يدوي ويحتاج مفتاحًا.
5. **الاستمرارية الطويلة:** `One-command recovery` يثبت استعادة حالة محفوظة عبر إعادة تشغيل — لا يثبت مهمة تعمل ساعات وتُستأنف بعد انقطاع في منتصف خطوة.
6. **التعاون بين الوكلاء:** لا شيء، لأن `agent-fleet` هي `absent`.

---

## 5️⃣ بوابة «PAP-2027 LIVE CAPABILITY GATE» — بشروط قابلة للتكذيب

الفرق بين بوابة جادة وأخرى احتفالية: **ما الذي يجعلها تفشل**. هذا ما أقترحه، وكل بند ينتج أثرًا محفوظًا لا علامة PASS.

| البوابة | شرط النجاح (يُفحص آليًا) | ما الذي يُفشلها | الأثر المطلوب |
|---|---|---|---|
| `live-connector` ✅ **منفّذ** | اتصال حي بمزوّد واحد على الأقل يعيد بيانات حقيقية | `skipped_no_key` يُعدّ **فشلًا** هنا لا تخطيًا؛ أو ردّ محاكاة | `scripts/live_connector_gate.py` → `launch/evidence/live-connector-gate.json` (id + وقت + استجابة منقّحة، وبصمة SHA-256، وفحص تسريب اعتماد) |
| `browser-real-task` | مهمة محددة تنجز على متصفح حقيقي (لا محاكى) خلال مهلة معلنة | أي `ModeNotEnabledError` أو لقطة من fixture | سجل خطوات + لقطة/HTML مُوقّع بـSHA-256 |
| `persistent-task` ✅ **منفّذ** | مهمة تُقطع قسرًا في منتصف خطوة وتُستأنف من نفس النقطة | استئناف من البداية أو فقدان خطوة | `scripts/persistent_task_gate.py` → `launch/evidence/persistent-task-gate.json` (10 فحوص، أوّل محاولة بعد الاستئناف = 3، وليس 1) |
| `fleet-3` | ثلاثة وكلاء يتعاونون على مهمة واحدة بعقد مخرجات/مدخلات | أي تنسيق وهمي أو تسلسل لا تبادل فيه | رسم بياني للاعتماديات + مخرجات كل وكيل |
| `live-deploy` | نشر فعلي ثم فحص HTTP يعيد 200 مع الإصدار الصحيح | نشر وهمي أو 200 من نفق محلي | `deploy_id` + نتيجة الفحص + الوقت |
| `e2e-software-task` | تعديل مشروع حقيقي + اختباراته + تقرير | نجاح بلا اختبار أو بلا diff | `git diff` + مخرجات الاختبار + التقرير |
| `budget-proof` | لا طلب مدفوع واحد عند `MAX_SPEND=0`، مُثبتًا بسجل الطلبات | أي طلب غير مصنّف مدفوع | عدّاد الطلبات المصروفة/المحجوبة |
| `failure-recovery` | تعطّل أداة وسط المهمة ثم استمرار آمن بلا تكرار أثر جانبي | إعادة تنفيذ عملية غير idempotent | سلسلة المحاولات + مفاتيح idempotency |

**قاعدة صريحة:** أي بوابة بلا أثر محفوظ = لم تُشغَّل.

---

## 5️⃣-ب ما نُفّذ فعلًا من البوابة الأولى: `persistent-task`

الادّعاء القديم كان «الحالة تُحفظ بعد إعادة التشغيل». هذا لا يعني أن مهمة مقطوعة **تُستأنف**. الفرق بينهما هو الفرق بين سجل وبين قدرة، وقد أُغلق الآن:

| ما يحدث فعلًا | كيف يُثبت |
|---|---|
| الوكيل يحفظ **نقطة تحقق بعد كل خطوة** (`checkpoint_step`, `checkpoint_at`) | `python-2027/src/ai2027_protocol/agent.py` |
| `resume=True` (عبر API أو `agent --resume SESSION_ID`) يُرفض إذا كانت الجلسة ليست `running`، أو النص مختلفًا، أو لا نقطة تحقق | `AgentResumeError` + 9 اختبارات في `tests/test_agent_resume.py` |
| عملية حقيقية تُقتل بـ`SIGKILL` أثناء الخطوة 3 بعد حفظ الخطوتين 1 و2 | `scripts/persistent_task_gate.py`: phase-1 returncode = **-9** |
| الاستئناف يبدأ من الخطوة **3** لا من 1 | أوّل محاولة في العملية الثانية = 3، والخطوتان 1 و2 لم تُنفَّذا مرتين |
| كل خطوة تكتمل مرة واحدة، والأثر يطابق بصمته | 4 آثار `step-N.txt` مع SHA-256 مطابق للسجل، والجلسة تنتهي `completed` بخطوات `[1,2,3,4]` |

**ما لا يثبته هذا:** لا يثبت أن الاستئناف يعمل بعد ساعات أو عبر جهاز آخر، ولا أن كل أداة في النظام idempotent. يثبت فقط أن الحلقة نفسها تستأنف من نقطة التحقق بلا إعادة تنفيذ، وهذا بالضبط ما كان ناقصًا.

## 5️⃣-ج ما نُفّذ فعلًا من البوابة الثانية: `live-connector`

هذه البوابة كانت ستُكذَّب بسهولة: أي «نجاح» بمزوّد محاكى، أو بـ`skipped_no_key` يُعرض كتخطٍّ. لذلك تُعامل هنا بأشدّ من غيرها:

| ما يُفحص | كيف يُثبت |
|---|---|
| طلب حقيقي إلى نقطة نهاية حقيقية (Gemini/NVIDIA/Groq/OpenRouter) | `python-2027/src/ai2027_protocol/live_probe.py` يبني المزوّد نفسه ويستدعي `complete()` عبر النقل الإنتاجي |
| «مزوّد واحد أجاب» شرط لا رفاهية | لا مفتاح ⇒ `a-provider-answered` = **FAIL**، وليس تخطيًا |
| لا إنفاق: `MAX_SPEND = $0` | المزوّد الذي ليست طبقته `free_tier`/`local` **لا يُستدعى أصلًا** (`refused_unknown_cost`) |
| الردّ من النموذج لا من قالب | يجب أن يعيد الوسم `PAP-2027-LIVE-OK` المطلوب في الطلب |
| الأثر يحمل هوية ووقتًا | `request_id` (أو `server_model_version` عند غياب المعرّف) + `requested_at`/`responded_at` + `response_sha256` |
| لا أسرار في الأثر | فحص أنماط `AIza…`/`nvapi-…`/`gsk_…`/`sk-…`؛ أي تطابق ⇒ FAIL + تنقيح الأثر نفسه |

**ما لا يثبته:** يثبت مزوّدًا واحدًا في لحظة واحدة، ولا يثبت جودة الإجابة، ولا أن البوابة ستعمل إن تغيّر النموذج أو نفدت الحصة. ولهذا يشغّل نفسه على `main` حيث المفاتيح موجودة، لا على كل PR.

## 6️⃣ ترتيب التنفيذ المقترح (بعد هذا التدقيق)

| الأولوية | العمل | لماذا الآن |
|---|---|---|
| **P0** | `live-connector` و`persistent-task` | أرخص بوابتين، وأكثرهما تأثيرًا على مصداقية «نظام ينفّذ» |
| **P1** | `browser-real-task` على Windows (لديك بيئة فعلية) | يحوّل `partial` إلى `present` بدليل، لا بتصريح |
| **P1** | `e2e-software-task` | يقيس ما يهم المستخدم: مهمة كاملة، لا اختبار وحدة |
| **P2** | `fleet-3` + `agent-fleet` كطبقة تنسيق فوق `AutonomousAgent` | أكبر فجوة معمارية (`absent`) |
| **P2** | `live-deploy` | يحوّل `cloud-runtime` من dry-run إلى إثبات |
| **P3** | مقارنة مع منتج آخر على نفس المهام | لا معنى لها قبل وجود المهام نفسها قابلة للتشغيل |

**ما لا أنصح به:** كتابة محركات PAP جديدة فوق الموجود قبل تشغيل البوابتين P0. الفجوة ليست في عدد الطبقات، بل في **غياب إثبات تشغيلي**.

---

## 7️⃣ ما لا يفعله هذا التدقيق

- لا يحكم على جودة الكود ولا على الأداء؛ يحكم فقط على **وجود الدليل واتساقه**.
- لا يثبت أن أي قدرة تعمل الآن؛ يثبت أن الملفات والاختبارات المُعلنة موجودة في هذا الالتزام.
- لا يعوّض الفحوص الحية: `present` تعني «مُثبت باختبار آلي»، لا «مُثبت في الإنتاج».
- لا يغيّر النواة (`projects/agi-system/core/`) ولا صلاحيات `security-engine`.

</div>
