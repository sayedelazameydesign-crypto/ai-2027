# تدقيق تكامل Qwen — Alibaba Cloud Model Studio

> **الحالة**: تكامل **مستقل ومُختبَر hermetic** — بلا اتصال حي بعد، وبلا سرّ
> في CI. المزوّد خارج السلسلة الافتراضية، يُبنى بطلب صريح فقط، ويُحجب
> تحت سقف صفر ما دام `AI2027_MAX_SPEND_USD=0`.
> **قرار المالك**: دمج كمزوّد مستقل دون تعديل Core أو كسر عقود المزوّدين
> الحاليين — نُفِّذ بهذا المعنى (إضافة تراكمية فقط).

## 1. الحقائق المستخرجة من التوثيق الرسمي (فُحصت2026-11-26)

| البند | القيمة | المصدر |
|---|---|---|
| الواجهة | متوافقة مع OpenAI — `chat/completions` و`responses` | [OpenAI-compatible - Chat](https://docs.modelstudio.console.alibabacloud.com/en/model-studio/qwen-api-via-openai-chat-completions) |
| المفتاح | `DASHSCOPE_API_KEY` في ترويسة `Authorization: Bearer` | [Model service overview](https://docs.modelstudio.console.alibabacloud.com/en/model-studio/what-is-model-studio) |
| base_url | `https://{WorkspaceId}.{region}.maas.aliyuncs.com/compatible-mode/v1` | [Overview + Chat docs] (كما في الأعلى) |
| المناطق | `ap-southeast-1` (الأكثر ظهوراً في الأمثلة)، `cn-beijing`، وغيرها — حسب المنطقة | [Responses API docs](https://help.aliyun.com/en/model-studio/compatibility-with-openai-responses-api) |
| النماذج | **`qwen4-max`** (GA2026-11-16، سياق1M) هو الأحدث والأرخص؛ ما زال متاحاً **`qwen3.8-max`** — **لا قيمة افتراضية مُعلنة** | قائمة النماذج: https://www.alibabacloud.com/help/model-studio/getting-started/models |
| التسعير (سنغافورة) | `qwen4-max`: **$1.50 / $4.50** لكل1M مدخلات/مخرجات (أرخص25% من `qwen3.8-max` $2/$6)؛ قراءة الكاش $0.075 لكل1M | https://www.alibabacloud.com/en/campaign/qwen-ai-landing-page |
| الحصة المجانية | **1M رمز مجاناً /90 يوماً** (حسابات سنغافورة فقط)؛ تنتهي ولا تُعاد | نفس الصفحة |
| **Free Quota Only** | إعداد في Console **مُعطَّل افتراضياً**: عند تفعيله تتوقف الخدمة عند انتهاء الحصة **بدلاً من** التحويل التلقائي إلى الدفع — **فعّله إذا كنت تريد حماية من الفوترة غير المقصودة** | إعدادات Model Studio في Console |

**القرار المبني على ذلك**: `QWEN_MODEL` مطلوب بلا افتراض (المعرّفات تدور)،
والمزوّد يُصنَّف `UNKNOWN_COST` ويُوضع في `NON_RECLASSIFIABLE_PROVIDERS`.
لأن السقف المالي (`MAX_SPEND`) **لا يُستهلك** من هذه النداءات (لا تقدير تكلفة)،
أُقرن التفويض بـ**حد يومي قابل للقياس**: `AI2027_UNKNOWN_COST_DAILY_LIMIT`
(الافتراضي5 نداءات مكتملة/يوم UTC، و`0` يعني الرفض حتى عند `ALLOW_UNKNOWN_COST=1`).

## 2. خريطة التكامل (كلها إضافية — لا عقد مكسور)

| الملف | الإضافة |
|---|---|
| `contracts.py` | `ProviderKind.QWEN = "qwen"` (تراكمي، قبل `DETERMINISTIC`) |
| `routing_profiles.py` | `"qwen"` في `SUPPORTED_PROVIDER_KINDS` (اختبار التزامن يفرض كليهما) |
| `budget.py` | `NON_RECLASSIFIABLE_PROVIDERS += QWEN` |
| `model_catalog.py` | `MODEL_ENV_VARS[QWEN] = "QWEN_MODEL"` — وبلا مدخل في `DEFAULT_MODELS` |
| `ai_providers.py` | `create_qwen_model_studio_provider()` + `MissingProviderConfigError` + تحقق hostname-safe للـworkspace/region |
| `cli.py` / `live_probe.py` | بطاقة الحالة `"qwen": "DASHSCOPE_API_KEY"` + تسجيل المتغيّر في المجسّ |
| `.env.example` قسم11 + `docs/SECRETS.md` + `scripts/doctor.py` | التسجيل الكامل (شرط الاختبارات) |
| `python-2027/tests/test_qwen_model_studio_provider.py` | **20 اختبار hermetic** — بلا شبكة |

**غير معدَّل عمداً**: `router.py`، `build_default_providers()`، أي عقد
قائم، أي مسار لمزوّد آخر — السلسلة الافتراضية كما كانت تماماً.

## 3. السياسة المالية (fail-closed)

- المفتاح وحده **لا يعني** `$0` ولا صلاحية: `classify_billing_tier` يرفض
  `FREE_TIER`/`LOCAL` صريحاً ويتجاهل `AI2027_FREE_MODELS`.
- `MAX_SPEND=0` ⇒ `unknown_cost_blocked` قبل أي شبكة (مُثبَت بعدّاد
  نقل `call_count == 0` عبر `FreeFirstRouter`).
- المسار الوحيد للإذن: سقف غير صفري صريح + `AI2027_ALLOW_UNKNOWN_COST=1`.

## 4. الأدلة

| الفحص | النتيجة |
|---|---|
| `ruff check src tests` | نظيف |
| `mypy src tests` (strict) | لا مشكلة في144 ملفاً |
| `pytest` الكامل لـpython-2027 | **997 ناجح +37 subtest** |
| التغطية | **91.58% ≥90** |
| جذور المستودع (doctor/P1/secret-scan) | تُشغَّل قبل الدفع — مذكورة في رسالة الـPR |

## 5. ما لم يُنفَّذ بعد (بصراحة)

1. **لا نداء حي**: لم يُرسل أي طلب فعلي إلى Model Studio (لا مفتاح في CI
   ولا في بيئة الاختبار) — الفحص الحي يأتي فقط بعد إقرار المالك للإنفاق.
2. **لا سرّ في Actions**: `DASHSCOPE_API_KEY` مسجَّل في `.env` المحلي فقط
   (وإن كان بإمكان المالك إضافته كسرّ Actions لفحص يدوي لاحقاً).
3. **السلسلة الافتراضية لم تتغيّر**: للتجربة يُبنى المزوّد صراحة:
   `create_qwen_model_studio_provider()` ثم `FreeFirstRouter(providers=[...])`.
