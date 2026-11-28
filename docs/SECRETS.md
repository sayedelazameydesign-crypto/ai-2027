<div dir="rtl">

# 🔐 الأسرار (Repository Secrets) — سجلّها وقواعد استعمالها

**قاعدة واحدة تحكم هذا الملف:** السرّ لا يُذكر قيمته في أي مكان، بل **اسمه** فقط. ومن لا يستطيع تسمية سرّه لا يستطيع تدويره — وهذا هو الفرق بين مفتاح يديره إنسان ومفتاح يُنسى مكشوفًا (انظر [#21](https://github.com/sayedelazameydesign-crypto/ai-2027/issues/21)).

يفحص هذا الملف آليًا: `python3 scripts/workflow_secret_audit.py --assert`
(كل `${{ secrets.X }}` يجب أن يُمرَّر عبر `env:` أو عبر mapping مسمّى إلى reusable workflow، لا داخل نص سكربت أو `with:` أو شرط `if:`، وأن يكون **مذكورًا هنا**).

---

## 1️⃣ الأسرار المعرَّفة

| السرّ | من يستعمله | ما يفعله بالضبط | عند غيابه | التدوير |
|---|---|---|---|---|
| `GEMINI_API_KEY` | `.github/workflows/approval-bot.yml` (عبر `env:` — مساعد المحادثة فقط: وضع `assistant` اليدوي، أو كل دورة عند `AI2027_ASSISTANT_ENABLED=1`؛ نماذج Free-Tier تحت `AI2027_MAX_SPEND_USD=0`)، و`.github/workflows/gemini-live-check.yml`، يستدعيه CI ولوحة دورة الحياة، و`.github/workflows/python-2027.yml` (على `main` فقط)، و`.github/workflows/pr-lens.yml` (تشغيل يدوي على `main` فقط عبر `env: PR_LENS_API_KEY`) | **نداء واحد** صغير (`maxOutputTokens=24`, `temperature=0`) يثبت أن المفتاح يعمل وأن النقطة تُجيب، أو رسم خريطة PR عبر `pr-lens` على `main` فقط. لا يُشغَّل على `pull_request` ولا يُستعمل في أي مسار وكيل تلقائي | تشغيل CI المباشر يبقي سلوك `SKIPPED` عند غياب المفتاح؛ طلبه صراحةً في لوحة دورة الحياة يجعل غيابه فشلاً واضحاً | AI Studio → Keys |
| `NVIDIA_API_KEY` | `.github/workflows/approval-bot.yml` (عبر `env:` — مساعد المحادثة فقط: وضع `assistant` اليدوي، أو كل دورة عند `AI2027_ASSISTANT_ENABLED=1`؛ نماذج Free-Tier تحت `AI2027_MAX_SPEND_USD=0`)، و`.github/workflows/nvidia-live-check.yml` (يدوي أو من لوحة دورة الحياة)، و`.github/workflows/python-2027.yml` (على `main` فقط) | نداء NIM واحد إلى prompt ثابت، `temperature=0` و`max_tokens=16`؛ النموذج مُعرّف غير سري. لا يُطبع النص أو جسم خطأ المزوّد ولا تُعاد المحاولة | التشغيل اليدوي يتخطى غياب المفتاح؛ الخيار الصريح في لوحة دورة الحياة يفشل بدلاً من تسجيل نجاح زائف | NVIDIA Build / API Catalog → API Keys |
| `GROQ_API_KEY` | `.github/workflows/approval-bot.yml` (عبر `env:` — مساعد المحادثة فقط: وضع `assistant` اليدوي، أو كل دورة عند `AI2027_ASSISTANT_ENABLED=1`؛ نماذج Free-Tier تحت `AI2027_MAX_SPEND_USD=0`)، و`.github/workflows/python-2027.yml` في وظيفة `live-actions-keys` (على `main` فقط عبر `env:`) | فحص Free-Tier واحد قصير (`Reply with exactly the word: pong`, `max_tokens=24`) عبر `GroqFreeProvider` ضمن حد `AI2027_MAX_SPEND_USD=0`. لا يُشغَّل على `pull_request` ولا يطبع قيمة المفتاح | يُسجَّل `skipped_no_key` بوضوح؛ يفشل فقط عند تمرير `--require-any` صراحةً دون مزوّد نشط آخر | Groq Console → API Keys |
| `OPENROUTER_API_KEY` | `.github/workflows/approval-bot.yml` (عبر `env:` — مساعد المحادثة فقط: وضع `assistant` اليدوي، أو كل دورة عند `AI2027_ASSISTANT_ENABLED=1`؛ نماذج Free-Tier تحت `AI2027_MAX_SPEND_USD=0`)، و`.github/workflows/real-model.yml` على `main` فقط (قياس نموذج حقيقي، `openrouter/free`، والرفض يخرج 2)، و`.github/workflows/python-2027.yml` في وظيفة `live-actions-keys` (على `main` فقط عبر `env:`) | فحص توجيه مجاني صريح: `openrouter/free` (الموجّه الديناميكي الافتراضي) أو أي `OPENROUTER_MODEL` منتهٍ بـ`:free`. أي نموذج مدفوع أو غير معلوم التكلفة يُحجب تلقائياً بـ`BudgetGuard` عند `MAX_SPEND=0`. لا يُشغَّل على `pull_request` | يُسجَّل `skipped_no_key` بوضوح، أو `budget_blocked` إذا خالف النموذج شرط `:free` | OpenRouter → Keys |
| `OLLAMA_API_KEY` | اختياري محلياً/يدوياً عبر `OllamaCloudProvider` فقط — **غير مفعّل في CI التلقائي** ومفصول تماماً عن Ollama المحلي (`OLLAMA_BASE_URL` مجاني `$0`) | مصنَّف **`UNKNOWN_COST`** في `ai2027_protocol/budget.py`: محجوب تلقائياً تحت `AI2027_MAX_SPEND_USD=0` حتى عند وجود المفتاح، ولا يعمل إلا بتجاوز صريح (`max_spend_usd > 0` و`allow_unknown_cost=True`) | لا يؤثر على التشغيل المحلي (`qwen3.5:0.8b` على loopback يعمل بـ`$0` دون مفتاح)؛ وعند وجوده تحت `$0` يُسجَّل `UNKNOWN_COST_BLOCKED` دون أي اتصال خارجي | Ollama Account Settings → API Keys |
| `OMNIROUTERS_API_KEY` | `ai2027_protocol/ai_providers.py` — `create_omnirouters_provider()` فقط. **غير مستعمل في أي Workflow ولا في CI تلقائي**؛ لا يوجد له فحص حي بعد | مفتاح `Bearer` لمزوّد OpenAI-compatible على `https://omnirouters.com/v1/chat/completions`. **المزوّد ليس مجانياً مضموناً**: الحسابات الجديدة لا تحصل على رصيد مجاني افتراضي، والحد الأدنى للشحن 10 دولارات. لذلك يُصنَّف **`UNKNOWN_COST`** ويُحجب تحت `AI2027_MAX_SPEND_USD=0` حتى لو وُجد المفتاح. **الحماية مركزية في `ai2027_protocol/budget.py`**: `ProviderKind.OMNIROUTERS` مُدرج في `NON_RECLASSIFIABLE_PROVIDERS`، فترفض طبقة التصنيف كل محاولة لإعادة تصنيفه — تمرير `billing_tier` صريحاً (بما فيه `FREE_TIER` و`LOCAL`، وهو أخطر لأنه يُعامَل الاتصال البعيد كصفر تكلفة) أو إدراجه في `AI2027_FREE_MODELS`. أيّاً كان المسار الذي يبني المزوّد، فلا يتجاوز الحارس إلا بسياسة إنفاق صريحة غير صفرية. ووجود المفتاح وحده لا يعني `$0` ولا صلاحية إنفاق | **لا يُستخدم** — المزوّد خارج سلسلة المزوّدات الافتراضية، ولا يُبنى إلا بطلب صريح ومع `OMNIROUTERS_MODEL` مضبوط. غياب `OMNIROUTERS_MODEL` يمنع بناء المزوّد أصلاً فلا يمكن إرسال أي طلب | OmniRouters → Dashboard → API Keys |
| `E2B_API_KEY` | `.github/workflows/e2b-live-check.yml` (يدوي أو من لوحة دورة الحياة) | تشغيل ثابت واحد `print(40 + 2)` عبر `E2BBackend` في microVM مؤقتة؛ يطلب backend إنهاء الـsandbox في `finally`. لا يُطبع إخراج التنفيذ أو خطأ SDK. قد يستهلك رصيد/وقت E2B | التشغيل اليدوي يتخطى غياب المفتاح؛ الخيار الصريح في لوحة دورة الحياة يفشل بدلاً من نجاح زائف | E2B Dashboard → API Keys |
| `RENDER_API_KEY` | `.github/workflows/deploy.yml` (يدوي أو من لوحة دورة الحياة) | نشر خدمة واحدة عبر `scripts/render_deploy.py` بعد مطابقة اسم الخدمة حرفياً؛ يعمل في Environment باسم `production`، ويمكن تفعيل مراجعين مطلوبين من إعداداته، ثم لا يُعلَن النجاح إلا بعد الفحص النهائي | التشغيل اليدوي يتخطى غياب المفتاح؛ طلب النشر الصريح في لوحة دورة الحياة يفشل بوضوح. لا يرسل شيئاً قبل اجتياز بوابة التأكيد | Render Dashboard → Account Settings → API Keys |
| `GITHUB_TOKEN` | `.github/workflows/agent-substrate-images.yml`؛ رمز قصير العمر ينشئه GitHub Actions تلقائياً بصلاحية `packages: write` | تسجيل الدخول إلى GHCR لنشر صور Agent Substrate ذات الإصدارات. لا يحتاج PAT ولا يُضبط كـRepository Secret | Actions يوفّره تلقائياً؛ إذا لم تمنح صلاحية الحزمة يفشل النشر. لا يوجد تدوير يدوي | GitHub Actions token permissions؛ راجع `permissions` في workflow |
| `AGENT_SUBSTRATE_KUBECONFIG` | `.github/workflows/agent-substrate-deploy.yml` (تشغيل يدوي على `main` عبر Runner ذاتي الاستضافة) | محتوى kubeconfig للعنقود؛ يُكتب مؤقتاً بصلاحية `0600`، يستخدمه `kubectl` للنشر ثم يحذفه عند انتهاء الخطوة | يفشل النشر قبل الاتصال بالعنقود | أبطِل بيانات الدخول القديمة من العنقود ثم حدّث Repository Secret |
| `SUBSTRATE_API_TOKEN` | `.github/workflows/agent-substrate-deploy.yml` (تشغيل يدوي على `main`) | يضع رمز Bearer للـGateway في Secret باسم `agent-substrate-api`؛ الحد الأدنى 32 حرفاً | يفشل النشر إذا غاب أو كان أقصر من 32 حرفاً | حدّث القيمة في Actions Secret وأعد تشغيل Workflow؛ يعاد تشغيل Controller ليقرأها |
| `SUBSTRATE_ACTOR_MASTER_KEY` | `.github/workflows/agent-substrate-deploy.yml` (تشغيل يدوي على `main`) | مفتاح اشتقاق HMAC لعناوين Agent؛ الحد الأدنى 32 حرفاً ويجب أن يبقى ثابتاً | يفشل النشر إذا غاب أو كان أقصر من 32 حرفاً | لا تدوّره تلقائياً: تغييره يتطلب ترحيل أسرار Actor immutable وإعادة إنشاء Pods؛ احتفظ به ثابتاً |
| `EMBER_ALERT_WEBHOOK` | `.github/workflows/ember.yml` | إشعار المرحلة الثالثة عند تكرار التدهور | يُسجَّل `not-configured` — معلن، لا صامت | أي endpoint جديد |
| `CONNECTORS_HUB_API_TOKEN` | `ai2027_protocol/studio.py` — مسار `/v1/studio/connectors/catalog` فقط. **غير مستعمل في أي Workflow ولا في CI تلقائي**؛ يُقرأ في `render.yaml` بـ`sync: false` أي يُدخل في لوحة Render ولا يُحفظ في Git | رمز Bearer لواجهة Connectors Hub. **الاستعمال اختياري تماماً**: غيابه لا يمنع تشغيل Studio ولا يوقف `/v1/studio/status` — يُبلَّغ عنه فقط كـ`token_missing` في مسار الكتالوج | مسار الكتالوج يعيد `token_missing` صراحةً بدل أن يتصرّف وكأن الكتالوج فارغ. بقية المسارات لا تتأثر | Connectors Hub → Dashboard → Tokens |
| `PA_APPROVAL_BOT_TOKEN` | `src/watchers/approval_bot.py` و`scripts/live_smoke_runner.py` و`.github/workflows/approval-bot.yml` (عبر `env:` فقط — دورة كل 5 دقائق + تشغيل يدوي `test`/`run`) و`.github/workflows/watchdog-24x7.yml` (تنبيه المراقبة عند تغيّر الحالة فقط، مع `PA_APPROVAL_CHAT_ID`) — بوت Telegram لبوابة الموافقات (P1). يقبل الـworkflow أيضاً الاسم البديل `TELEGRAM_BOT_TOKEN` | توكن BotFather يرسل بطاقات الموافقة لصفوف `pa_approvals` المعلّقة ورسالة `test` لإثبات المسار | لا بطاقة ولا تصويت — والقراءة تبقى fail-closed؛ وعند غياب أي اسم يفشل `approval_bot_ci.py` بـ`rc=2` ويطبع الأسماء الناقصة فقط | Telegram @BotFather → /newbot |
| `APPS_SCRIPT_ID` | `.github/workflows/deploy-apps-script.yml` (تشغيل يدوي على `main` فقط عبر `env:`) — معرّف مشروع Apps Script الهدف (`Celia Fashion Design`، `1UNRaWAvGNYI6lYnJ1nlQt3PyMsG3eZkI--WASZrhMPoBzB6ZqiBXG-5z`) | يوجّه `clasp push` إلى المشروع الصحيح؛ الـworkflow يرفض النشر إذا لم يطابق إدخال التأكيد اليدوي القيمة حرفيًا. المعرّف نفسه ليس سرًا، لكنه يُمرَّر عبر `env:` لتوحيد قاعدة التدقيق | يفشل النشر قبل أي اتصال بـ`APPS_SCRIPT_ID secret is missing` — لا نشر جزئي | Google Apps Script → Project Settings → Script ID |
| `CLASPRC_JSON` | `.github/workflows/deploy-apps-script.yml` (تشغيل يدوي على `main` فقط عبر `env:`) — محتوى `~/.clasprc.json` بعد `clasp login` | مصادقة `clasp` لرفع `integrations/apps-script/memory-p0-bridge/` إلى مشروع Apps Script. يُكتب مؤقتًا بصلاحية `0600` ثم يُستخدم؛ لا يُطبع أبدًا | يفشل النشر قبل الاتصال بـ`CLASPRC_JSON secret is missing` | شغّل `clasp login` محليًا ثم انسخ `~/.clasprc.json` إلى Secret |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | `.github/workflows/approval-bot.yml` (عبر `env:`) و`.github/workflows/watchdog-24x7.yml` (فحص سلسلة التدقيق، قراءة فقط بنطاق `spreadsheets.readonly`) — نص مفتاح Service Account كامل (JSON) لقراءة ورقة `pa_approvals` في وضع `run` | الـrunner يبني creds عبر `from_service_account_info` **بلا كتابة ملف على القرص**، ولا يطبع سوى `stage=`؛ القيم لا تظهر في السجلات (تُخفى تلقائياً) | بدونه يفشل وضع `run` بـ`rc=2` (أسماء ناقصة)؛ وضع `test` لا يحتاجه | Google Cloud → IAM → Service Accounts → Create key (JSON)، ثم شارك الورقة ببريد الحساب (Editor) |
| `WAAPI_API_TOKEN` | `src/connectors/waapi.py` (تشغيل محلي عبر `.env`) و`.github/workflows/waapi-live-probe.yml` (فحص حي **للقراءة فقط** عبر أسرار Actions) — بوابة waapi.app. **القيمة نفسها في الموضعين**: تُنسخ يدوياً مرة من Actions إلى `.env`، ولا تُرفع أبداً | رمز `Bearer` لإشعار المالك عبر WhatsApp (اختياري) ولأداة `whatsapp.send` المنفَّذة عبر بوابة الموافقات فقط؛ لا يُطبع ولا يُسجَّل ولا يدخل استثناءات خام | لا إشعار ولا إرسال: الموصل يرفض `waapi_token_missing` قبل أي شبكة، والماسح الحي يفشل بوضوح — لا نجاح زائف | GitHub Settings → Secrets → Actions (وموضع محلي مطابق في `.env`) |
| `AI2027_WHATSAPP_WEBHOOK` | `src/connectors/whatsapp_cloud.py` (`resolve_webhook_binding` و`verify_ycloud_signature`) — رابط الـWebhook الآمن (`https://...`) أو سر التوقيع/التحقق المشترك لبوابة WhatsApp (`YCloud` / `Meta`). لا تُطبع قيمته أبداً. يمرّ كذلك و`.github/workflows/supabase-secret-sync.yml` (تشغيل يدوي على `main` فقط عبر `env:`) — ينقل القيمة مرة واحدة إلى **Supabase Vault** عبر OIDC من GitHub، بلا أي بيانات اعتماد مشتركة (قيمة `https://` تُتخطّى لأنها ليست سرّ توقيع) | يربط مسار استقبال أحداث `whatsapp.inbound_message.received` بـ`HMAC-SHA256` ويرفض أي عنوان `http://` غير مشفر (`insecure_http_refused`) | يفشل التحقق من التوقيع بـ`missing_signing_config` قبل معالجة أي حدث وارد | GitHub Settings → Secrets → Actions وبيئة خادم الاستقبال |
| `YCLOUD_API_KEY` | `src/connectors/whatsapp_cloud.py` — مصادقة `X-API-Key` لإرسال ردود نصية مجانية (`type=text` فقط ضمن نافذة الـ24 ساعة، `MAX_SPEND=0`) عبر `https://api.ycloud.com/v2/whatsapp/messages/sendDirectly` خلف بوابات `GovernedExecutor` الأربع؛ و`.github/workflows/supabase-secret-sync.yml` (تشغيل يدوي على `main` فقط، الخيار `ycloud_api`، عبر `env:`) — يبحث في `GET /v2/webhookEndpoints` عن نقطة الـWebhook التي تشير إلى عنوان Supabase أو ينشئها، ثم ينقل سر توقيعها مباشرة إلى **Supabase Vault** عبر OIDC؛ لا يُكتب المفتاح ولا السر في السجلات أبداً (ولا حتى بأمر إخفاء) | يفعّل مسار YCloud الرسمي المجاني لخدمة العملاء دون خصم من محفظة القوالب المدفوعة | يرفض الفحص المسبق بـ`missing_access_credential` قبل أي استهلاك موافقة أو اتصال شبكي | YCloud Console → Developers → API Keys |
| `YCLOUD_WEBHOOK_SECRET` | `src/connectors/whatsapp_cloud.py` (`verify_ycloud_signature`) — سر توقيع ترويسة `YCloud-Signature: t=...,s=...` لنقطة الـWebhook في YCloud، و`.github/workflows/supabase-secret-sync.yml` (تشغيل يدوي على `main` فقط عبر `env:`) — ينقل القيمة مرة واحدة إلى **Supabase Vault** عبر OIDC من GitHub، بلا أي بيانات اعتماد مشتركة | يتحقق بزمن ثابت (`hmac.compare_digest`) مع نافذة حماية من الإعادة (`300s`)؛ يسقط تلقائياً إلى `AI2027_WHATSAPP_WEBHOOK` إن خُزّن السر هناك | يرفض الطلبات الواردة بـ`missing_signing_config` أو `invalid_signature` | YCloud Console → Developers → Webhooks → Signing Secret |
| `WHATSAPP_CLOUD_ACCESS_TOKEN` · `WHATSAPP_CLOUD_APP_SECRET` · `WHATSAPP_CLOUD_VERIFY_TOKEN` | `src/connectors/whatsapp_cloud.py` — بيانات اعتماد Meta WhatsApp Cloud API المباشر (`Bearer` + `X-Hub-Signature-256` + تحدي `hub.verify_token`) عند استخدام Graph API مباشرةً دون وسيط BSP. `APP_SECRET` و`VERIFY_TOKEN` (لا `ACCESS_TOKEN`) يمرّان أيضاً و`.github/workflows/supabase-secret-sync.yml` (تشغيل يدوي على `main` فقط عبر `env:`) — ينقل القيمة مرة واحدة إلى **Supabase Vault** عبر OIDC من GitHub، بلا أي بيانات اعتماد مشتركة | مصادقة وتوقيع مسار Meta المباشر ضمن نافذة الـ24 ساعة المجانية فقط (`MAX_SPEND=0`) | غيابها يوقف مسار Meta المباشر بـ`missing_access_credential` / `missing_signing_config` | Meta for Developers → WhatsApp → API Setup |
| `DASHSCOPE_API_KEY` | `python-2027/src/ai2027_protocol/ai_providers.py` — `create_qwen_model_studio_provider()` فقط. **غير مستعمل في أي Workflow ولا في CI تلقائي**؛ يُقرأ من بيئة التشغيل فقط | مفتاح Alibaba Cloud Model Studio (Qwen) للواجهة المتوافقة مع OpenAI. **المزوّد ليس مجانياً مضموناً**: رصيد مجاني ثم اشتراك مدفوع، لذلك يُصنَّف **`UNKNOWN_COST`** ويُحجب تحت `AI2027_MAX_SPEND_USD=0`. الحماية مركزية: `ProviderKind.QWEN` في `NON_RECLASSIFIABLE_PROVIDERS` (`budget.py`) — لا إعادة تصنيف عبر `billing_tier` صريح ولا عبر `AI2027_FREE_MODELS` | لا يُبنى المزوّد أصلاً بلا `QWEN_MODEL` و`DASHSCOPE_WORKSPACE_ID`؛ وحتى مع المفتاح، الحارس يرفض قبل أي شبكة (`unknown_cost_blocked`) | Alibaba Cloud → Model Studio → API Key |

| `AI2027_ASSISTANT_TELEGRAM_TOKEN` | `python-2027/src/ai2027_protocol/channel_assistant.py` — `TelegramReplySender` (مساعد المحادثة العام للقناتين). **لا يُستعمل في أي Workflow ولا في CI تلقائي**؛ يُقرأ من بيئة التشغيل فقط. يُرسل ردود المساعد النصية عبر `sendMessage` فقط (لا `getUpdates`)، ويرجع إلى `PA_APPROVAL_BOT_TOKEN` إن غاب. المسار يعتمد حصرياً على `FreeFirstRouter`/`BudgetGuard` (`FREE_TIER`/`LOCAL`، `MAX_SPEND=0`) فلا يتصل بأي مزود مدفوع | توكن بوت Telegram لإرسال ردود المحادثة العام باللغة العربية (مقسّمة إلى ≤ 4096 حرفاً). لا يُطبع ولا يُسجَّل ولا يدخل استثناءات خام | لا إرسال: المرسل يرفض `not_configured` قبل أي شبكة (fail-closed) | Telegram @BotFather → /newbot |

### متغيّرات (ليست أسرارًا، ولا تحتاج تشفيرًا)

| المتغيّر | الاستعمال | الافتراضي |
|---|---|---|
| `RENDER_SERVICE_ID` | يختار خدمة Render مباشرة في النشر اليدوي أو لوحة دورة الحياة بدل البحث بالاسم | فارغ ⇒ البحث بالاسم |
| `RENDER_SERVICE_NAME` | اسم الخدمة المستعمل في البحث والتأكيد؛ قيمة الإدخال اليدوي تتجاوز المتغيّر | `ai-2027-live-agent` |
| `GEMINI_MODEL` | النموذج المستعمل في فحص Gemini الحي | `gemini-2.5-flash` |
| `NVIDIA_MODEL` | معرّف نموذج NVIDIA غير السري؛ `meta/llama-3.1-8b-instruct` و`meta/llama-3.3-70b-instruct` تقاعدا (HTTP 410 منذ 2026-08-26) ويُحجبان كـ`model_retired`. يمكن تجاوزه بمدخل التشغيل | `z-ai/glm-5.3-flash` |
| `GROQ_MODEL` | معرّف نموذج Groq. المعرّفان `llama-3.1-8b-instant` و`llama-3.3-70b-versatile` تقاعدا (2026-08-16) ويُحجبان كـ`model_retired`. البديل الرسمي `openai/gpt-oss-20b` مسعّر لكل توكن، لذا يبقى `UNKNOWN_COST` حتى تصرّح به في `AI2027_FREE_MODELS` | `openai/gpt-oss-20b` |
| `OPENROUTER_MODEL` | معرّف نموذج OpenRouter؛ يجب أن يكون `openrouter/free` أو منتهيًا بـ`:free` تحت سياسة `$0` | `openrouter/free` |
| `OMNIROUTERS_MODEL` | معرّف نموذج OmniRouters **مطلوب** (لا قيمة افتراضية): الكتالوج خاص بكل حساب، فاختيار تخميني لا يمكن التحقق منه. يُقرأ من كتالوج حسابك عبر `GET https://omnirouters.com/v1/models`، وتُراجَع تكلفته قبل اعتماد أي اختبار حي | **لا شيء** — غياب المتغيّر يمنع بناء المزوّد ويمنع أي طلب شبكة |
| `AI2027_FREE_MODELS` | قائمة مفصولة بفواصل تعلن نماذج إضافية مجانية على خطة المزوّد المعدّلة. لا تتجاوز تقاعد معرّف، ولا تُثبت `$0` | فارغة |
| `OLLAMA_MODEL` | نموذج Ollama المحلي لبيئة `4gb-laptop` (يعمل بلا مفتاح سحابي) | `qwen3.5:0.8b` |
| `OLLAMA_CLOUD_MODEL` | نموذج Ollama Cloud عند الإذن المدفوع الصريح فقط | `qwen3.5:0.8b` |
| `CONNECTORS_HUB_URL` | عنوان Connectors Hub الذي يقرأه مسار `/v1/studio/connectors/catalog`. ليس سرّاً — عنوان فقط | `http://127.0.0.1:8765` |
| `AI2027_READ_ONLY` | يفعّل الوضع القرائي: يرفض كل كتابة على مستوى التطبيق كله | `1` في المخطّط العام (Blueprint) |
| `AI2027_MAX_SPEND_USD` | سقف الإنفاق.Fail-closed: الافتراضي `0` يعني رفض كل نداء مدفوع أو غير معروف التكلفة | `0` |
| `AI2027_ALLOW_UNKNOWN_COST` | يسمح بالنداءات التي لا يمكن إثبات تكلفتها. **هذا تفويض وليس سقفاً مالياً**: النداء ينفق مالاً حقيقياً، لكنه يُمتنع عن الرفض. **يُتجاهل بالكامل إذا كان `AI2027_MAX_SPEND_USD=0`** — لأن `BudgetPolicy.from_env()` يُجبره على `False` عند سقف صفري، فلا يمكنه تجاوز الحراسة Fail-closed | `0` |
| `AI2027_UNKNOWN_COST_DAILY_LIMIT` | الحد اليومي القابل للقياس للنداءات غير المعروفة التكلفة (عدد نداءات مكتملة لكل يوم UTC). ضروري لأن `MAX_SPEND` لا يُستهلك من هذه النداءات (لا تقدير تكلفة فيُرسَل بدون أن يزداد `spent_usd`) — فالسقف المالي وحده لا يقيّدها. الافتراضي `5`؛ `0` يعني الرفض حتى عند `AI2027_ALLOW_UNKNOWN_COST=1`؛ القيمة غير الرقمية تُغلق الفشل إلى `0` | `5` |
| `AI2027_CELIA_MEMORY_DB` | مسار قاعدة بيانات ذاكرة CELIA. مسار محلي، ليس سرّاً | فارغ ⇒ الذاكرة في الوضع غير الدائم |
| `AI2027_TELEMETRY_DIR` | مجلد كتابة أحداث التتبّع (Telemetry). مسار محلي، ليس سرّاً | فارغ ⇒ التتبّع معطّل |
| `AI2027_SESSION_LEASE` | وضع leases الجلسات. `off` يمنعها صراحةً؛ أي قيمة أخرى تُشغّلها | `off` |
| `AI2027_OBSERVABILITY_MODE` | نمط الملاحظة (`off` / `local` / …). ليس سرّاً | فارغ ⇒ الوضع الافتراضي |
| `AI2027_OLLAMA_PROFILE` | ملف تعريف Ollama المحلي (`4gb-laptop` …). ليس سرّاً | `4gb-laptop` |
| `PA_MEMORY_SHEET_ID` | `src/watchers/proactive_watcher.py` و`src/watchers/approval_bot.py` و`scripts/live_smoke_runner.py` و`.github/workflows/approval-bot.yml` — الورقة الذاكرة `pa_*` (source/tasks/goals/approvals/grants/audit). معرّف علني للورقة، ليس سرّاً | فارغ ⇒ المراقبة وبوابة الموافقات لا تفتحان شيئاً (fail-closed) |
| `WATCHER_INTERVAL_SEC` | فاصل نداءات المراقب بالثواني بين دورة فحص وأخرى | `900` |
| `APPROVAL_BOT_INTERVAL_SEC` | فاصل نداءات بوابة الموافقات بالثواني | `300` |
| `PA_APPROVAL_CHAT_ID` | معرّف محادثة Telegram التي تُرسل إليها بطاقات الموافقة | فارغ ⇒ لا إرسال (تخطّي صريح) |
| `PA_APPROVAL_ALLOWED_SENDERS` | معرّفات المسموح لهم بالتصويت، مفصولة بفواصل. **القائمة الفارغة تمنع الجميع** — fail-closed بقصد | فارغ ⇒ يُمنع الجميع |
| `TELEGRAM_BOT_TOKEN` · `TELEGRAM_CHAT_ID` · `TELEGRAM_ALLOWED_SENDERS` | أسماء بديلة يقبلها `.github/workflows/approval-bot.yml` عن `PA_APPROVAL_BOT_TOKEN` / `PA_APPROVAL_CHAT_ID` / `PA_APPROVAL_ALLOWED_SENDERS` (المدقّق يقيس `secrets.*`؛ ينشأ أي اسم من الزوجين بشرط توثيقه) | نفس القيم ونفس السلوك | كالأساسية — `rc=2` بالأسماء عند الغياب | تُدوَّر مع الأساسية |
| `PA_APPROVAL_API_BASE` | عنوان Telegram API البديل (وكيل/اختبار) | `https://api.telegram.org` |
| `WAAPI_INSTANCE_ID` | معرّف instance عند تعددها في waapi.app؛ يتجاوز الاكتشاف التلقائي (واحد فقط يُقبل) | فارغ ⇒ instance واحد بالضبط يُقبل |
| `WAAPI_OWNER_CHAT_ID` | المستلم الثابت لمسار إشعار WhatsApp — رقم المالك فقط (`011…` أو `2011…@c.us`) | فارغ ⇒ الإشعار يفشل `owner_chat_missing` |
| `PA_WAAPI_NOTIFY` | يفعّل رفيق WhatsApp لبطاقات الموافقة (يرسل رابط تصويت passkey) | `0` (معطّل — fail-closed) |
| `WHATSAPP_PROVIDER` | يحدد مزوّد `whatsapp_cloud.send_reply` صراحةً (`ycloud` أو `meta`)؛ عند تركه فارغاً يُكتشف تلقائياً من المفتاح المتاح | فارغ ⇒ اكتشاف تلقائي |
| `YCLOUD_WHATSAPP_FROM` | رقم هاتف واتساب للأعمال المسجّل في YCloud بصيغة E.164 (مثل `+201...`) المستخدم في حقل `from` عند الإرسال المجاني داخل نافذة الـ24 ساعة | فارغ ⇒ يفشل الفحص المسبق بـ`invalid_phone_number_id` |
| `WHATSAPP_CLOUD_PHONE_NUMBER_ID` | معرّف الرقم الرقمي (`Phone Number ID`) في Meta Graph API عند استخدام مسار Meta المباشر | فارغ ⇒ يفشل الفحص المسبق بـ`invalid_phone_number_id` |
| `AI2027_FREE_STACK_DB` | مسار قاعدة بيانات `SQLite (WAL)` المحلية لصندوق الوارد والتذاكر والكوبونات ورموز التحقق المجانية في `scripts/free_stack_runner.py` | `:memory:` في الاختبارات أو `free_stack.db` محلياً |
| `AI2027_MAX_FREE_REPLIES_PER_DAY` | السقف اليومي الإضافي لعدد الردود الخدمية المجانية المجهزة/المرسلة في `scripts/free_stack_runner.py` | `50` (`<= 0` يغلق الإرسال fail-closed) |
| `DASHSCOPE_WORKSPACE_ID` | معرّف مساحة Model Studio — يُبنى منه base_url: `https://{WorkspaceId}.{region}.maas.aliyuncs.com/compatible-mode/v1` | فارغ ⇒ لا يُبنى المزوّد (يرفض `MissingProviderConfigError`) |
| `DASHSCOPE_REGION` | منطقة نقطة النهاية (وثائق رسمية: `ap-southeast-1` و`cn-beijing` وغيرهما) | `ap-southeast-1` |
| `QWEN_MODEL` | معرّف نموذج Qwen **مطلوب** (لا قيمة افتراضية): المعرّفات تدور — التوثيق الرسمي أظهر `qwen3.8-max` بينما الحسابات قد تخدم `qwen4-max`؛ يُختار من قائمة نماذج حسابك | **لا شيء** — غيابه يمنع بناء المزوّد ويمنع أي طلب شبكة |
| `PA_APPROVAL_VOTE_BASE_URL` | `src/watchers/approval_bot.py` و`scripts/live_smoke_runner.py` — عنوان صفحة التصويت بـ Passkey التي يبنيها Worker في `worker/approval-worker/` (ADR-0006). يُضاف إلى البطاقة رابط `…?approval=<id>` | فارغ ⇒ البطاقة بلا رابط (الأزرار تبقى وحدها) |
| `PA_APPROVAL_SIGNING_PUBKEY` | `src/executors/governed_executor.py` — المفتاح العام Ed25519 (hex،64 حرفاً) للتحقق من توقيع الـWorker على قرار الموافقة (ADR-0006) | فارغ ⇒ مسار الـassertion يفشل بـ`assertion_verification_key_missing` (fail-closed) ويعمل المسار البديل فقط |

| `AI2027_ASSISTANT_TELEGRAM_CHAT_ID` | `python-2027/src/ai2027_protocol/channel_assistant.py` — محادثة Telegram التي يُرسل إليها ردود المساعد العام. يرجع إلى `PA_APPROVAL_CHAT_ID` إن غاب | فارغ ⇒ لا إرسال (`not_configured`) |
| `AI2027_ASSISTANT_MAX_HISTORY_TURNS` | `python-2027/src/ai2027_protocol/channel_assistant.py` — عدد دورات (user, assistant) المحتفظ بها في سجل المحادثة لكل محادثة. قيمة غير رقمية أو سالبة ترجع إلى الافتراضي (fail-closed) | `8` |
| `AI2027_ASSISTANT_ENABLED` | **مفتاح التشغيل** (`docs/CHANNEL-ASSISTANT.md` §11). في GitHub يُضبط **Repository Variable** (`Settings → Secrets and variables → Actions → Variables`) لا Secret. `1` ⇒ دورة `approval-bot.yml` كل 5 دقائق ترد على نصوص المالك في Telegram (`scripts/approval_bot_ci.py`)، و`scripts/free_stack_runner.py` يصيغ مسودات واتساب بنموذج مجاني تبقى `pending_approval` | فارغ/`0` ⇒ السلوك القديم حرفياً، لا يُثبَّت `python-2027` ولا يُستدعى أي نموذج |
| `AI2027_ASSISTANT_PROBE_TEXT` | `scripts/approval_bot_ci.py --mode assistant` — نص رسالة الاختبار الحي (مدخل `probe_text` في التشغيل اليدوي) | فارغ ⇒ رسالة تعريفية ثابتة |

### أسرار Supabase Edge — سيليا الفورية على Telegram (`supabase/functions/celia-telegram`)

تُضبط في **Supabase → Project Settings → Edge Functions → Secrets** (لا في GitHub). لا يمرّرها أي workflow؛ الدالة تقرأها من `Deno.env` فقط، ولا تطبع أي قيمة أو نص رسالة (السجل = تسميات مرحلة من قائمة ثابتة + اسم المزوّد).

| السرّ | ما يفعله | عند الغياب |
|---|---|---|
| `CELIA_TELEGRAM_TOKEN` | توكن **بوت مستقل** لسيليا (BotFather → /newbot). يُربط بـwebhook عبر `GET …/celia-telegram/setup`، ومنه يُشتقّ سرّ الـwebhook: `sha256("celia-telegram-webhook:"+token)` ويُقارن بزمن ثابت مع ترويسة `X-Telegram-Bot-Api-Secret-Token`. **لا تستعمل توكن `PA_APPROVAL_BOT_TOKEN`**: تفعيل webhook يوقف `getUpdates` الذي تعتمد عليه بوابة الموافقات | كل طلب webhook ⇒ `503`، و`/setup` يرفض |
| `CELIA_OWNER_IDS` | معرّفات Telegram الرقمية المسموح لها (مفصولة بفواصل). غير المدرجين لا يتلقّون شيئاً؛ المجموعات والرسائل غير النصية تُتجاهل | قبل ضبطه: المحادثة الخاصة تتلقى معرّفها فقط (للإعداد)، بلا نموذج ولا ذاكرة |
| `GEMINI_API_KEY` · `GROQ_API_KEY` · `NVIDIA_API_KEY` · `OPENROUTER_API_KEY` | نسخ مستقلة من مفاتيح Free-Tier نفسها (الجدول أعلاه). الترتيب `CELIA_PROVIDER_ORDER` (افتراضي `gemini,groq,nvidia,openrouter`). Gemini بنماذج `flash` فقط؛ OpenRouter بـ`openrouter/free` أو `:free` فقط — غير ذلك يُرفض (تكلفة مجهولة = رفض) | المزوّد يُتخطّى؛ بلا أي مزوّد ⇒ ردّ سيليا الصادق «لا يتوفر نموذج مجاني الآن» |

متغيّرات اختيارية (ليست أسراراً): `GEMINI_MODEL` · `GROQ_MODEL` · `NVIDIA_MODEL` · `OPENROUTER_MODEL` · `CELIA_PROVIDER_ORDER` · `CELIA_ANIMATION=0` (يوقف رسالة «التفكير» المتحركة ويُبقي «يكتب…»). `SUPABASE_URL` و`SUPABASE_SERVICE_ROLE_KEY` يوفّرهما Supabase تلقائياً؛ الجداول `celia_*` مغلقة أمام `anon`/`authenticated` (RLS + REVOKE).

### أسرار Cloudflare / R2 المعرَّفة وغير المستعملة بعد

هذه الأسرار موجودة في `Settings → Secrets and variables → Actions` **ولا يقرأها أي workflow في المستودع حاليًا** — لا كود Python ولا Worker ولا سكربت نشر. وجودها لا يعني أن التخزين أو النشر إلى Cloudflare مدعوم.

| السرّ | ما يفعله فعلًا | ما لا يفعله |
|---|---|---|
| `CLOUDFLARE_ACCOUNT_ID` | معرّف الحساب، ويدخل في تركيب نطاق R2: `https://ACCOUNT_ID.r2.cloudflarestorage.com` | ليس مفتاحًا سريًا، ولا يمنح صلاحية قراءة أو كتابة |
| `CLOUDFLARE_API_TOKEN` | مصادقة REST إدارية لدى Cloudflare (`Authorization: Bearer …`) لإدارة الموارد حسب الأذونات | **ليس** بديلًا عن مفاتيح S3، ولا يُستعمل في أي مسار هنا |
| `R2_ACCESS_KEY_ID` | معرّف مفتاح S3 لمساحة R2 | لا يكفي وحده للدخول |
| `R2_SECRET_ACCESS_KEY` | توقيع طلبات S3 إلى R2 (أعلى حساسية في المجموعة) | لا يُستعمل في المتصفح ولا في أي ملف يُرفع إلى المستودع |

**إذا أردت تفعيل R2 فعليًا:** أضف كودًا يقرأ `R2_ENDPOINT` و`R2_BUCKET_NAME` و`R2_REGION` (ليست أسرارًا)، ثم أضف صفًا لهذه الأسرار في الجدول أعلاه وحدّث `scripts/workflow_secret_audit.py` قبل تمريرها إلى أي وظيفة. حتى ذلك الحين، إضافتها إلى `env:` بلا مستهلك تُعدّ سطح هجوم بلا فائدة.

### أسرار أخرى معرَّفة في GitHub ولا يقرأها أي workflow

فُحصت بـ`git grep -w` على كامل المستودع (2026-11-28). لا يمرّرها أي workflow، ولا يقرأها `python-2027` ولا `src/`. وجودها في `Settings → Secrets` لا يُفعّل شيئًا.

| السرّ | الحالة الفعلية | التوصية |
|---|---|---|
| `HF_TOKEN` | يُقرأ **فقط** من بيئة تشغيل مشاريع `projects/*` المستقلة (`new.pro` على Vercel · `celia-pro-deployment` على Render · `ELAZAMET1/ml-backend`) — كل منصة تحفظ نسختها في لوحتها، لا من GitHub Actions | أبقِه إن كنت ستضيف workflow يحتاجه؛ وإلا فهو غير ضروري هنا. توكن **Read** يكفي لكل الاستعمالات |
| `DIT_API_KEY` | لا مرجع له في أي ملف | احذفه، أو وثّق مستهلكه هنا قبل استعماله |
| `AGENT_SUBSTRATE_PREFLIGHT_KUBECONFIG` | لا مرجع له في أي ملف أو workflow | احذفه؛ ملف kubeconfig يمنح وصولاً إلى الـcluster ولا يجب أن يبقى بلا مستهلك |
| `MYAPP_LOCAL_DEV` | ليس اسمًا يقرأه الكود (انظر `RUNBOOK.md`) — غالبًا اسم في لوحة المفاتيح لمفتاح OpenRouter | احذفه؛ المستودع يقرأ `OPENROUTER_API_KEY` |

**ملفات `.env.example`:** قيمها فارغة دائمًا (`KEY=`). البادئات الجزئية التي كانت في `projects/ELAZAMET1/{backend,ml-backend}/.env.example` (`hf_…` و`xai-…`) أُزيلت؛ تاريخ هذا المستودع لا يحتوي القيم الكاملة، لكن الملفين جُمِعا من مستودع آخر (`36ba694`) — إن كانت البادئتان لمفتاحين حقيقيين فطبّق قائمة التدوير أدناه.

### التشغيل الآمن لدورة الحياة

1. من **Actions → AI-2027 Project Lifecycle → Run workflow** اختر `main`.
2. الإعداد الافتراضي يشغّل CI الكامل فقط؛ Gemini وNVIDIA وE2B وRender كلها غير محددة ولا تُستدعى.
3. حدّد فحوص المزوّد المطلوبة يدوياً. إذا اخترت فحصاً ولم يكن مفتاحه متاحاً، يفشل هذا الخيار صراحةً بدلاً من ادّعاء النجاح. قد يستهلك E2B رصيداً.
4. للنشر، حدّد `deploy_render` واكتب اسم الخدمة الفعلي حرفياً في `render_confirm`. يمكن إعداد `RENDER_SERVICE_NAME` و`RENDER_SERVICE_ID` كـVariables؛ وإن رغبت بموافقة بشرية ثانية، أضف مراجعين مطلوبين إلى Environment `production`.
5. لا يمكن تشغيل الفحوص الحية أو النشر من فرع آخر، ولا تُمرر مفاتيح المزوّد إلى طلبات Pull Request.

---

## 2️⃣ القواعد الخمس — مُنفَّذة في الكود، لا مكتوبة فقط

| # | القاعدة | كيف تُنفَّذ |
|---|---|---|
| 1 | **لا interpolation داخل السكربت** | `scripts/workflow_secret_audit.py` يسمح بتمرير السرّ إلى `env:` أو mapping مسمّى لـreusable workflow فقط، ويرفض وضعه في نصّ السكربت أو `with:` أو `if:` (GitHub لا يسمح بـ`secrets.*` مباشرة داخل `if`) |
| 2 | **لا `argv` ولا `echo`** | السكربتات تقرأ `os.environ` فقط؛ و`set +x` صريح في خطوتَي النشر والفحص |
| 3 | **لا مفاتيح في الروابط** | Gemini يُقرأ من الترويسة `x-goog-api-key` لا من `?key=` — الروابط تُنسخ إلى سجلات الوكيل وإلى تقارير الأخطاء |
| 4 | **لا طبع، ولا كتابة في التقارير** | لا تُطبع القيمة أو بصمتها أو رسالة خطأ خام. فحصا NVIDIA وE2B يذكران `present`/`absent` فقط؛ فحص Gemini القديم يذكر الوجود والطول فقط، وتُنقّح رسائل المزوّد قبل عرض أي تشخيص |
| 5 | **لا سرّ بلا سجل** | كل سرّ مستعمل يجب أن يكون في الجدول أعلاه، وإلا فشل `workflow_secret_audit --assert` في CI |

**قاعدة عملية إضافية:** إذا ظهر مفتاح في `git status` أو في سجل تشغيل، فالخطوة الأولى ليست «امسحه» بل **أبطِله** — المسح لا يُلغي نسخة سبق أن نُسخت.

---

## 3️⃣ ما تفعله الأسرار ولا تفعله

**تفعله:**
- `GEMINI_API_KEY` يتيح فحص endpoint صغيراً، لا يجعل الوكيل يستخدم Gemini تلقائياً. Connectors Hub يبقى محلياً افتراضياً؛ أما NVIDIA في الاستدلال فتتطلب موافقة صريحة مستقلة. فحص Gemini في لوحة دورة الحياة اختياري ويُشترط له المفتاح عند طلبه.
- `NVIDIA_API_KEY` يختبر نقطة NIM بطلب ثابت واحد؛ لا يرسل prompt المستخدم ولا يثبت جودة النموذج أو توافقه مع جهاز المستخدم.
- `GROQ_API_KEY` و`OPENROUTER_API_KEY` يُستعملان فقط على `main` عبر `env:` داخل `python-2027.yml` لفحوص Free-Tier (`openrouter/free` لـOpenRouter) تحت حراسة `AI2027_MAX_SPEND_USD=0`. معرّفات النماذج المتقاعدة تُحجب قبل أي طلب — انظر [`MODEL-CATALOG.md`](MODEL-CATALOG.md).
- `OLLAMA_API_KEY` يبقى معزولاً بوصفه `UNKNOWN_COST` لخدمة Ollama Cloud البعيدة ولا يُصرف تلقائياً تحت `$0`، بينما يظل Ollama المحلي (`OLLAMA_BASE_URL` على loopback) مجانياً دون مفتاح.
- `E2B_API_KEY` يختبر backend العزل بتنفيذ ثابت `print(40 + 2)` في microVM مؤقتة؛ اختيار الفحص صريح وقد يستهلك رصيداً.
- `RENDER_API_KEY` يسمح بطلب نشر خدمة واحدة. لا يُعدّ قبول الطلب نجاحاً: يلزم `wait_for_deploy()` ثم فحص حياة الخدمة.
- `.github/workflows/project-lifecycle.yml` يجمع البوابات: CI أولاً، ثم الفحوص التي يختارها المستخدم، ثم Render فقط بعد نجاح البوابات المطلوبة والتأكيد الحرفي لاسم الخدمة. يعمل النشر ضمن Environment باسم `production`؛ أضف مراجعين مطلوبين في إعدادات Environment إذا أردت موافقة بشرية إضافية. تستخدم متغيرات المستودع لإعداد النموذج والخدمة دون وضعها في الكود.

**لا تفعله:**
- `real-model.yml` يشغّل `scripts/run_real_model.py --free openrouter` على `main` فقط. المفتاح يُقرأ من السر عبر `env:` ولا يُطبع. النموذج يجب أن يكون `openrouter/free` أو منتهيًا بـ`:free`، وهو شرط يفرضه السكربت نفسه قبل أي اتصال (`--spend-ok` وحده يفتح غيره). إن غاب المفتاح سُجّل `skipped` صريح، وإن طُلب `require_live` فشل. ونتيجة القياس تُرفع كـartifact وتُلخَّص في صفحة الوظيفة.
- لا تعمل أي فحوص موفّر على `pull_request`؛ NVIDIA وE2B لا يعملان إلا يدوياً أو عبر لوحة دورة الحياة اليدوية. لوحة دورة الحياة نفسها تقصر الفحوص الحية والنشر على `main`؛ وتضيف خطوات حماية مماثلة فحوص NVIDIA/E2B والنشر المنفردة.
- لا يُشغَّل النشر تلقائياً على `push` — يظل `deploy.yml` بلا مشغّل `push`.
- لا يُكتب أي سرّ في artifact أو ملخص؛ فحوص NVIDIA/E2B لا ترفع artifacts ولا تطبع مخرجات/أخطاء خام.

---

## 4️⃣ قائمة التدوير عند الشك في تسريب

1. **أبطِل** المفتاح من لوحة المزوّد (AI Studio · NVIDIA Build · Groq · OpenRouter · Ollama · E2B · Render · Hugging Face · xAI).
2. أنشئ مفتاحًا جديدًا وأضفه إلى `Settings → Secrets and variables → Actions`.
3. شغّل `python3 scripts/workflow_secret_audit.py --assert` للتأكد أن كل مرجع ما زال مسنَدًا إلى `env:`.
4. شغّل الوظيفة المعنية يدويًا (`live-gemini` أو `NVIDIA NIM live smoke check` أو `E2B sandbox live smoke check` · `Deploy to Render` مع `confirm` فارغ = dry run) لتأكيد أن المفتاح الجديد يعمل.
5. إن كان المفتاح ظهر في مستودع آخر: سجّل ذلك في Issue، لأن الإبطال وحده لا يمحو النسخ المنسوخة.

**ما لا يفعله هذا المستودع:** لا يستطيع إبطال مفتاح في حسابك، ولا محو تاريخ مستودع آخر. هذا ما بقي مفتوحًا في #21.

</div>
