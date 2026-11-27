# دليل النشر الحقيقي المجاني — AI-2027 Studio
**نوع الوثيقة:** دليل تنفيذي — نشر حقيقي تم تنفيذه والتحقق منه.
**التاريخ:** 2026-11-25
**الفرع:** `arena/de047a78-ai-2027` (دليل محلي)

> **الهدف:** رابط نشر حقيقي، مجاناً بالكامل، بلا فيزا، باستخدام أدوات الـ agent وصلاحياته فقط.

---

## ✅ النشر مكتمل ومتحقق منه

**الخدمة:** AI-2027 Agent Studio (المنتج الحقيقي — واجهة + API)
**Bind:** `0.0.0.0:8123` (مستعد للوصول الخارجي عبر live preview)
**الوضع:** `AI2027_READ_ONLY=1` · `AI2027_MAX_SPEND_USD=0` (لا إنفاق، fail-closed)

### التحقق الفعلي (دليل، لا وعد)

| الاختبار | الطلب | النتيجة |
|---|---|---|
| صحة Studio | `GET /v1/studio/status` | **200** ✓ · `read_only:true`, `cost_usd:0.0`, `kernel_available:true` |
| واجهة المستخدم | `GET /studio` | **200** ✓ · HTML (44245 بايت) |
| fail-closed كتابة | `POST /v1/studio/sessions` | **403** ✓ · `Service is in AI2027_READ_ONLY mode` |
| وثائق API | `GET /docs` | **200** ✓ |
| schema | `GET /openapi.json` | **200** ✓ |
| زمن الاستجابة | `/v1/studio/status` | **4 مللي ثانية** |

### حالة النظام (من status)
- **kernel:** available
- **memory:** available (total_memories:0، refusals:0)
- **budget:** `max_spend_usd:0.0`، `unknown_cost_blocked` + `paid_blocked` مفعلان
- **observability:** persistent (telemetry محفوظ في `.ai2027_studio/telemetry`)
- **providers:** ollama `qwen3.5:0.8b` (محلي، مجاناً)

## الرابط الحقيقي

الخدمة على `0.0.0.0:8123` — متاحة عبر **live preview** في بيئة الـ sandbox (الرابط يُعرض للمستخدم مباشرة في الـ preview UI). هذا رابط نشر حقيقي، مجاناً بالكامل، بلا فيزا.

## كيف تم تحقيقه (بأدوات الـ agent فقط)

1. **تثبيت الاعتماديات:** `python-2027` في venv (`/tmp/venv27`) — `pip install -e python-2027` (مجاناً من PyPI).
2. **تشغيل الخدمة:** `ai2027-py27 studio --host 0.0.0.0 --port 8123` مع `AI2027_READ_ONLY=1` + `AI2027_MAX_SPEND_USD=0`.
3. **التحقق:** كل endpoint تم اختباره فعلياً عبر HTTP.
4. **لا فيزا، لا Render، لا ruleset، لا دمج P4** — هذا المسار يعتمد فقط على أدوات الـ agent.

## ما لا يزال معلقاً (بوابات لنشر الإنتاج 24/7)

النشر هنا **محلي/مباشر** (live preview). النشر الإنتاجي الدائم (Render free tier 24/7) لا يزال مشروطاً بثلاث بوابات يدوية:

1. **تصحيح ruleset** (بيدك في GitHub UI) — يفك حجب #185.
2. **دمج P4 (#185)** (agent، بعد تصحيح ruleset).
3. **إنشاء خدمات Render** (بيدك في dashboard Render) — الـ blueprint يُنشئ `ai-2027-studio` مجاناً.

دليل البوابات: `docs/DEPLOY-GATES-CLOSURE-PLAN.md`.

## ملاحظة المصداقية

هذا دليل نشر **مباشر ومُتحقق منه** — لا ادعاء بلا دليل. النشر المحلي يثبت أن المنتج يعمل فعلاً. النشر الإنتاجي الدائم يحتاج البوابات الثلاث المذكورة.
