<div dir="rtl">

# 🔐 الأسرار (Repository Secrets) — سجلّها وقواعد استعمالها

**قاعدة واحدة تحكم هذا الملف:** السرّ لا يُذكر قيمته في أي مكان، بل **اسمه** فقط. ومن لا يستطيع تسمية سرّه لا يستطيع تدويره — وهذا هو الفرق بين مفتاح يديره إنسان ومفتاح يُنسى مكشوفًا (انظر [#21](https://github.com/sayedelazameydesign-crypto/ai-2027/issues/21)).

يفحص هذا الملف آليًا: `python3 scripts/workflow_secret_audit.py --assert`
(كل `${{ secrets.X }}` يجب أن يُمرَّر عبر `env:` أو عبر mapping مسمّى إلى reusable workflow، لا داخل نص سكربت أو `with:` أو شرط `if:`، وأن يكون **مذكورًا هنا**).

---

## 1️⃣ الأسرار المعرَّفة

| السرّ | من يستعمله | ما يفعله بالضبط | عند غيابه | التدوير |
|---|---|---|---|---|
| `GEMINI_API_KEY` | `.github/workflows/gemini-live-check.yml`، يستدعيه CI ولوحة دورة الحياة، و`.github/workflows/python-2027.yml` (على `main` فقط)، و`.github/workflows/pr-lens.yml` (تشغيل يدوي على `main` فقط عبر `env: PR_LENS_API_KEY`) | **نداء واحد** صغير (`maxOutputTokens=24`, `temperature=0`) يثبت أن المفتاح يعمل وأن النقطة تُجيب، أو رسم خريطة PR عبر `pr-lens` على `main` فقط. لا يُشغَّل على `pull_request` ولا يُستعمل في أي مسار وكيل تلقائي | تشغيل CI المباشر يبقي سلوك `SKIPPED` عند غياب المفتاح؛ طلبه صراحةً في لوحة دورة الحياة يجعل غيابه فشلاً واضحاً | AI Studio → Keys |
| `NVIDIA_API_KEY` | `.github/workflows/nvidia-live-check.yml` (يدوي أو من لوحة دورة الحياة)، و`.github/workflows/python-2027.yml` (على `main` فقط) | نداء NIM واحد إلى prompt ثابت، `temperature=0` و`max_tokens=16`؛ النموذج مُعرّف غير سري. لا يُطبع النص أو جسم خطأ المزوّد ولا تُعاد المحاولة | التشغيل اليدوي يتخطى غياب المفتاح؛ الخيار الصريح في لوحة دورة الحياة يفشل بدلاً من تسجيل نجاح زائف | NVIDIA Build / API Catalog → API Keys |
| `GROQ_API_KEY` | `.github/workflows/python-2027.yml` في وظيفة `live-actions-keys` (على `main` فقط عبر `env:`) | فحص Free-Tier واحد قصير (`Reply with exactly the word: pong`, `max_tokens=24`) عبر `GroqFreeProvider` ضمن حد `AI2027_MAX_SPEND_USD=0`. لا يُشغَّل على `pull_request` ولا يطبع قيمة المفتاح | يُسجَّل `skipped_no_key` بوضوح؛ يفشل فقط عند تمرير `--require-any` صراحةً دون مزوّد نشط آخر | Groq Console → API Keys |
| `OPENROUTER_API_KEY` | `.github/workflows/real-model.yml` على `main` فقط (قياس نموذج حقيقي، `openrouter/free`، والرفض يخرج 2)، و`.github/workflows/python-2027.yml` في وظيفة `live-actions-keys` (على `main` فقط عبر `env:`) | فحص توجيه مجاني صريح: `openrouter/free` (الموجّه الديناميكي الافتراضي) أو أي `OPENROUTER_MODEL` منتهٍ بـ`:free`. أي نموذج مدفوع أو غير معلوم التكلفة يُحجب تلقائياً بـ`BudgetGuard` عند `MAX_SPEND=0`. لا يُشغَّل على `pull_request` | يُسجَّل `skipped_no_key` بوضوح، أو `budget_blocked` إذا خالف النموذج شرط `:free` | OpenRouter → Keys |
| `OLLAMA_API_KEY` | اختياري محلياً/يدوياً عبر `OllamaCloudProvider` فقط — **غير مفعّل في CI التلقائي** ومفصول تماماً عن Ollama المحلي (`OLLAMA_BASE_URL` مجاني `$0`) | مصنَّف **`UNKNOWN_COST`** في `ai2027_protocol/budget.py`: محجوب تلقائياً تحت `AI2027_MAX_SPEND_USD=0` حتى عند وجود المفتاح، ولا يعمل إلا بتجاوز صريح (`max_spend_usd > 0` و`allow_unknown_cost=True`) | لا يؤثر على التشغيل المحلي (`qwen3.5:0.8b` على loopback يعمل بـ`$0` دون مفتاح)؛ وعند وجوده تحت `$0` يُسجَّل `UNKNOWN_COST_BLOCKED` دون أي اتصال خارجي | Ollama Account Settings → API Keys |
| `OMNIROUTERS_API_KEY` | `ai2027_protocol/ai_providers.py` — `create_omnirouters_provider()` فقط. **غير مستعمل في أي Workflow ولا في CI تلقائي**؛ لا يوجد له فحص حي بعد | مفتاح `Bearer` لمزوّد OpenAI-compatible على `https://omnirouters.com/v1/chat/completions`. **المزوّد ليس مجانياً مضموناً**: الحسابات الجديدة لا تحصل على رصيد مجاني افتراضي، والحد الأدنى للشحن 10 دولارات. لذلك يُصنَّف **`UNKNOWN_COST`** ويُحجب تحت `AI2027_MAX_SPEND_USD=0` حتى لو وُجد المفتاح. **الحماية مركزية في `ai2027_protocol/budget.py`**: `ProviderKind.OMNIROUTERS` مُدرج في `NON_RECLASSIFIABLE_PROVIDERS`، فترفض طبقة التصنيف كل محاولة لإعادة تصنيفه — تمرير `billing_tier` صريحاً (بما فيه `FREE_TIER` و`LOCAL`، وهو أخطر لأنه يُعامَل الاتصال البعيد كصفر تكلفة) أو إدراجه في `AI2027_FREE_MODELS`. أيّاً كان المسار الذي يبني المزوّد، فلا يتجاوز الحارس إلا بسياسة إنفاق صريحة غير صفرية. ووجود المفتاح وحده لا يعني `$0` ولا صلاحية إنفاق | **لا يُستخدم** — المزوّد خارج سلسلة المزوّدات الافتراضية، ولا يُبنى إلا بطلب صريح ومع `OMNIROUTERS_MODEL` مضبوط. غياب `OMNIROUTERS_MODEL` يمنع بناء المزوّد أصلاً فلا يمكن إرسال أي طلب | OmniRouters → Dashboard → API Keys |
| `E2B_API_KEY` | `.github/workflows/e2b-live-check.yml` (يدوي أو من لوحة دورة الحياة) | تشغيل ثابت واحد `print(40 + 2)` عبر `E2BBackend` في microVM مؤقتة؛ يطلب backend إنهاء الـsandbox في `finally`. لا يُطبع إخراج التنفيذ أو خطأ SDK. قد يستهلك رصيد/وقت E2B | التشغيل اليدوي يتخطى غياب المفتاح؛ الخيار الصريح في لوحة دورة الحياة يفشل بدلاً من نجاح زائف | E2B Dashboard → API Keys |
| `RENDER_API_KEY` | `.github/workflows/deploy.yml` (يدوي أو من لوحة دورة الحياة) | نشر خدمة واحدة عبر `scripts/render_deploy.py` بعد مطابقة اسم الخدمة حرفياً؛ يعمل في Environment باسم `production`، ويمكن تفعيل مراجعين مطلوبين من إعداداته، ثم لا يُعلَن النجاح إلا بعد الفحص النهائي | التشغيل اليدوي يتخطى غياب المفتاح؛ طلب النشر الصريح في لوحة دورة الحياة يفشل بوضوح. لا يرسل شيئاً قبل اجتياز بوابة التأكيد | Render Dashboard → Account Settings → API Keys |
| `GITHUB_TOKEN` | `.github/workflows/agent-substrate-images.yml`؛ رمز قصير العمر ينشئه GitHub Actions تلقائياً بصلاحية `packages: write` | تسجيل الدخول إلى GHCR لنشر صور Agent Substrate ذات الإصدارات. لا يحتاج PAT ولا يُضبط كـRepository Secret | Actions يوفّره تلقائياً؛ إذا لم تمنح صلاحية الحزمة يفشل النشر. لا يوجد تدوير يدوي | GitHub Actions token permissions؛ راجع `permissions` في workflow |
| `AGENT_SUBSTRATE_KUBECONFIG` | `.github/workflows/agent-substrate-deploy.yml` (تشغيل يدوي على `main` عبر Runner ذاتي الاستضافة) | محتوى kubeconfig للعنقود؛ يُكتب مؤقتاً بصلاحية `0600`، يستخدمه `kubectl` للنشر ثم يحذفه عند انتهاء الخطوة | يفشل النشر قبل الاتصال بالعنقود | أبطِل بيانات الدخول القديمة من العنقود ثم حدّث Repository Secret |
| `SUBSTRATE_API_TOKEN` | `.github/workflows/agent-substrate-deploy.yml` (تشغيل يدوي على `main`) | يضع رمز Bearer للـGateway في Secret باسم `agent-substrate-api`؛ الحد الأدنى 32 حرفاً | يفشل النشر إذا غاب أو كان أقصر من 32 حرفاً | حدّث القيمة في Actions Secret وأعد تشغيل Workflow؛ يعاد تشغيل Controller ليقرأها |
| `SUBSTRATE_ACTOR_MASTER_KEY` | `.github/workflows/agent-substrate-deploy.yml` (تشغيل يدوي على `main`) | مفتاح اشتقاق HMAC لعناوين Agent؛ الحد الأدنى 32 حرفاً ويجب أن يبقى ثابتاً | يفشل النشر إذا غاب أو كان أقصر من 32 حرفاً | لا تدوّره تلقائياً: تغييره يتطلب ترحيل أسرار Actor immutable وإعادة إنشاء Pods؛ احتفظ به ثابتاً |
| `EMBER_ALERT_WEBHOOK` | `.github/workflows/ember.yml` | إشعار المرحلة الثالثة عند تكرار التدهور | يُسجَّل `not-configured` — معلن، لا صامت | أي endpoint جديد |

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

### أسرار Cloudflare / R2 المعرَّفة وغير المستعملة بعد

هذه الأسرار موجودة في `Settings → Secrets and variables → Actions` **ولا يقرأها أي workflow في المستودع حاليًا** — لا كود Python ولا Worker ولا سكربت نشر. وجودها لا يعني أن التخزين أو النشر إلى Cloudflare مدعوم.

| السرّ | ما يفعله فعلًا | ما لا يفعله |
|---|---|---|
| `CLOUDFLARE_ACCOUNT_ID` | معرّف الحساب، ويدخل في تركيب نطاق R2: `https://ACCOUNT_ID.r2.cloudflarestorage.com` | ليس مفتاحًا سريًا، ولا يمنح صلاحية قراءة أو كتابة |
| `CLOUDFLARE_API_TOKEN` | مصادقة REST إدارية لدى Cloudflare (`Authorization: Bearer …`) لإدارة الموارد حسب الأذونات | **ليس** بديلًا عن مفاتيح S3، ولا يُستعمل في أي مسار هنا |
| `R2_ACCESS_KEY_ID` | معرّف مفتاح S3 لمساحة R2 | لا يكفي وحده للدخول |
| `R2_SECRET_ACCESS_KEY` | توقيع طلبات S3 إلى R2 (أعلى حساسية في المجموعة) | لا يُستعمل في المتصفح ولا في أي ملف يُرفع إلى المستودع |

**إذا أردت تفعيل R2 فعليًا:** أضف كودًا يقرأ `R2_ENDPOINT` و`R2_BUCKET_NAME` و`R2_REGION` (ليست أسرارًا)، ثم أضف صفًا لهذه الأسرار في الجدول أعلاه وحدّث `scripts/workflow_secret_audit.py` قبل تمريرها إلى أي وظيفة. حتى ذلك الحين، إضافتها إلى `env:` بلا مستهلك تُعدّ سطح هجوم بلا فائدة.

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

1. **أبطِل** المفتاح من لوحة المزوّد (AI Studio · NVIDIA Build · Groq · OpenRouter · Ollama · E2B · Render).
2. أنشئ مفتاحًا جديدًا وأضفه إلى `Settings → Secrets and variables → Actions`.
3. شغّل `python3 scripts/workflow_secret_audit.py --assert` للتأكد أن كل مرجع ما زال مسنَدًا إلى `env:`.
4. شغّل الوظيفة المعنية يدويًا (`live-gemini` أو `NVIDIA NIM live smoke check` أو `E2B sandbox live smoke check` · `Deploy to Render` مع `confirm` فارغ = dry run) لتأكيد أن المفتاح الجديد يعمل.
5. إن كان المفتاح ظهر في مستودع آخر: سجّل ذلك في Issue، لأن الإبطال وحده لا يمحو النسخ المنسوخة.

**ما لا يفعله هذا المستودع:** لا يستطيع إبطال مفتاح في حسابك، ولا محو تاريخ مستودع آخر. هذا ما بقي مفتوحًا في #21.

</div>
