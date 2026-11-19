<div dir="rtl">

# كتالوج النماذج (Model Catalogue) — ما هو حيّ، وما تقاعد، وما يُعدّ مجانيًا

**القاعدة الحاكمة:** لا يُصنَّف نموذج كـ`FREE_TIER` لمجرد وجود مفتاح API، ولا يبقى معرّف متقاعد داخل قائمة المجاني. المصدر الوحيد للحقيقة في الكود هو `python-2027/src/ai2027_protocol/model_catalog.py`، والنسخة المطابقة في Workers داخل `worker_guard.py` و`edge-gateway/src/index.mjs`.

```bash
cd python-2027
.venv/bin/ai2027-py27 models           # جدول الافتراضيات + المتقاعد
.venv/bin/ai2027-py27 models --json    # نفس البيانات للقراءة الآلية
.venv/bin/ai2027-py27 providers        # التصنيف الفعلي والحجب قبل أي طلب
```

## 1️⃣ الافتراضيات الحالية (تُستعمل فقط عند فراغ المتغيّر)

| المزوّد | المتغيّر | الافتراضي | التصنيف تحت `MAX_SPEND=0` |
|---|---|---|---|
| Gemini | `GEMINI_MODEL` | `gemini-2.5-flash` | `FREE_TIER` (قائمة مسموحة معلنة) |
| NVIDIA NIM | `NVIDIA_MODEL` | `meta/llama-3.3-70b-instruct` | `FREE_TIER` (قائمة مسموحة معلنة) |
| Groq | `GROQ_MODEL` | `openai/gpt-oss-20b` | `UNKNOWN_COST` ⟵ **محجوب افتراضيًا** |
| OpenRouter | `OPENROUTER_MODEL` | `openrouter/free` | `FREE_TIER` (موجّه مجاني ديناميكي) |

**لماذا Groq محجوب افتراضيًا؟** كل نماذج Llama المجانية عليه أُوقفت، والبديل الرسمي `openai/gpt-oss-20b` منشور له سعر لكل مليون توكن. لا يصح «تصحيح» فحص مجاني بنموذج مدفوع. إن كنت تعتمد على الخطة المجانية المحدودة المعدّل (rate-limited) فصرّح بذلك صراحةً:

```
GROQ_MODEL=openai/gpt-oss-20b
AI2027_FREE_MODELS=openai/gpt-oss-20b
```

بهذا التصريح فقط يُصنَّف النموذج `FREE_TIER` مع `cost_known=false` و`cost_status=declared_free_tier_unbilled` — أي «معلن مجاني بلا فاتورة»، وليس «إثبات صفر».

**وأي معرّف حيّ غير مدرج؟** يُصنَّف `UNKNOWN_COST` ويُحجب تحت `$0` حتى تصرّح به في `AI2027_FREE_MODELS` (مثال: `qwen/qwen2.5-72b-instruct` على NVIDIA). القائمة المسموحة صغيرة عن قصد: كل معرّف يبقى فيها يجب أن يكون حيًّا ومُعلنًا.

**لماذا `openrouter/free` بدل نموذج مثبّت؟** المعرّفات المثبّتة مثل `meta-llama/llama-3.2-3b-instruct:free` قد تُسحب أو تتغيّر. الموجّه `openrouter/free` يختار نموذجًا مجانيًا متاحًا وقت الطلب، مع حدود استخدام ومعدلات. ويظل المعرّف المنتهي بـ`:free` مقبولًا أيضًا.

## 2️⃣ معرّفات متقاعدة — تُحجب قبل أي طلب شبكي

| المزوّد | المعرّف المتقاعد | تاريخ التقاعد | البديل الموصى به | الدليل |
|---|---|---|---|---|
| NVIDIA NIM | `meta/llama-3.1-8b-instruct` | 2026-08-26 | `meta/llama-3.3-70b-instruct` | النقطة تُجيب `HTTP 410 Gone` |
| Groq | `llama-3.1-8b-instant` | 2026-08-16 | `openai/gpt-oss-20b` | متقاعد للخطط المجانية وDeveloper؛ مفتاح مجاني يعيد `HTTP 404` |
| Groq | `llama-3.3-70b-versatile` | 2026-08-16 | `openai/gpt-oss-120b` | كما أعلاه |
| Groq | `meta-llama/llama-4-scout-17b-16e-instruct` | 2026-07-17 | `openai/gpt-oss-120b` | نموذج معاينة مسحوب |
| Groq | `qwen/qwen3-32b` | 2026-07-17 | `openai/gpt-oss-120b` | نموذج معاينة مسحوب |

عند رصد معرّف متقاعد:

- `BudgetGuard` يعيد `UNKNOWN_COST` مع سبب يذكر **تاريخ التقاعد والبديل**، فلا يُنفَّذ أي طلب.
- `ai2027-py27 live` يعرض الحالة `model_retired` بزمن استجابة `0.0 ms` (لم تُصرف أي محاولة).
- أي `HTTP 410 Gone` قادم من المزوّد يُترجم إلى `ProviderError(code="model_retired")` مع توجيه لتحديث المتغيّر، بدل خطأ `http_410` غامض.
- `AI2027_FREE_MODELS` **لا** يستطيع تجاوز التقاعد: التصريح الصريح يرفع الحجب عن نموذج حيّ غير مسموح، لا عن معرّف لم يعد له وجود.

## 3️⃣ كيف تُضيف أو تُقاعد معرّفًا

1. أضف/احذف السطر في `model_catalog.py` (`DEFAULT_MODELS` أو `RETIRED_MODELS`).
2. طابق القائمة في `workers/python-worker/src/worker_guard.py` و`edge-gateway/src/index.mjs` (Workers لا تستورد حزمة Python).
3. شغّل `pytest` — اختبار `tests/test_model_catalog.py` يفشل إذا بقي معرّف متقاعد داخل قائمة المجاني أو داخل الافتراضيات.
4. حدّث `.env.example` و`docs/SECRETS.md` وهذه الصفحة.

## 4️⃣ حدود هذا الكتالوج

- الكتالوج يمنع **الأخطاء المعروفة** (معرّف مسحوب، تصنيف مجاني كاذب)، لكنه **لا يثبت** أن المزوّد حيّ الآن، ولا أن الخطة المجانية ما زالت تخدم النموذج، ولا أن الحصة لم تُستهلك.
- الإثبات الحقيقي يبقى `ai2027-py27 live --require-any` على `main` بمفاتيح Actions، ونتيجته تخص لحظة التشغيل فقط.
- `OLLAMA_API_KEY` (Ollama Cloud) ليس بديلًا عن Ollama المحلي، ويظل `UNKNOWN_COST` محجوبًا تحت `$0`.

</div>
