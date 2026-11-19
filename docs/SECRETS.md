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
| `OPENROUTER_API_KEY` | `.github/workflows/python-2027.yml` في وظيفة `live-actions-keys` (على `main` فقط عبر `env:`) | فحص نموذج `:free` صريح (`openrouter/free` أو `OPENROUTER_MODEL` المنتهي بـ`:free`). أي نموذج مدفوع أو غير معلوم التكلفة يُحجب تلقائياً بـ`BudgetGuard` عند `MAX_SPEND=0`. لا يُشغَّل على `pull_request` | يُسجَّل `skipped_no_key` بوضوح، أو `budget_blocked` إذا خالف النموذج شرط `:free` | OpenRouter → Keys |
| `OLLAMA_API_KEY` | اختياري محلياً/يدوياً عبر `OllamaCloudProvider` فقط — **غير مفعّل في CI التلقائي** ومفصول تماماً عن Ollama المحلي (`OLLAMA_BASE_URL` مجاني `$0`) | مصنَّف **`UNKNOWN_COST`** في `ai2027_protocol/budget.py`: محجوب تلقائياً تحت `AI2027_MAX_SPEND_USD=0` حتى عند وجود المفتاح، ولا يعمل إلا بتجاوز صريح (`max_spend_usd > 0` و`allow_unknown_cost=True`) | لا يؤثر على التشغيل المحلي (`qwen3.5:0.8b` على loopback يعمل بـ`$0` دون مفتاح)؛ وعند وجوده تحت `$0` يُسجَّل `UNKNOWN_COST_BLOCKED` دون أي اتصال خارجي | Ollama Account Settings → API Keys |
| `E2B_API_KEY` | `.github/workflows/e2b-live-check.yml` (يدوي أو من لوحة دورة الحياة) | تشغيل ثابت واحد `print(40 + 2)` عبر `E2BBackend` في microVM مؤقتة؛ يطلب backend إنهاء الـsandbox في `finally`. لا يُطبع إخراج التنفيذ أو خطأ SDK. قد يستهلك رصيد/وقت E2B | التشغيل اليدوي يتخطى غياب المفتاح؛ الخيار الصريح في لوحة دورة الحياة يفشل بدلاً من نجاح زائف | E2B Dashboard → API Keys |
| `RENDER_API_KEY` | `.github/workflows/deploy.yml` (يدوي أو من لوحة دورة الحياة) | نشر خدمة واحدة عبر `scripts/render_deploy.py` بعد مطابقة اسم الخدمة حرفياً؛ يعمل في Environment باسم `production`، ويمكن تفعيل مراجعين مطلوبين من إعداداته، ثم لا يُعلَن النجاح إلا بعد الفحص النهائي | التشغيل اليدوي يتخطى غياب المفتاح؛ طلب النشر الصريح في لوحة دورة الحياة يفشل بوضوح. لا يرسل شيئاً قبل اجتياز بوابة التأكيد | Render Dashboard → Account Settings → API Keys |
| `EMBER_ALERT_WEBHOOK` | `.github/workflows/ember.yml` | إشعار المرحلة الثالثة عند تكرار التدهور | يُسجَّل `not-configured` — معلن، لا صامت | أي endpoint جديد |

### متغيّرات (ليست أسرارًا، ولا تحتاج تشفيرًا)

| المتغيّر | الاستعمال | الافتراضي |
|---|---|---|
| `RENDER_SERVICE_ID` | يختار خدمة Render مباشرة في النشر اليدوي أو لوحة دورة الحياة بدل البحث بالاسم | فارغ ⇒ البحث بالاسم |
| `RENDER_SERVICE_NAME` | اسم الخدمة المستعمل في البحث والتأكيد؛ قيمة الإدخال اليدوي تتجاوز المتغيّر | `ai-2027-live-agent` |
| `GEMINI_MODEL` | النموذج المستعمل في فحص Gemini الحي | `gemini-2.0-flash` |
| `NVIDIA_MODEL` | معرّف نموذج NVIDIA غير السري عند استخدام لوحة دورة الحياة؛ يمكن تجاوزه بمدخل التشغيل | فارغ؛ يجب تمرير معرّف عند طلب فحص NVIDIA |
| `GROQ_MODEL` | معرّف نموذج Groq المستخدم في فحص Free-Tier على `main` | `llama-3.1-8b-instant` |
| `OPENROUTER_MODEL` | معرّف نموذج OpenRouter؛ يجب أن يكون `openrouter/free` أو ينتهي بـ`:free` تحت سياسة `$0` | `openrouter/free` |
| `OLLAMA_MODEL` | نموذج Ollama المحلي لبيئة `4gb-laptop` (يعمل بلا مفتاح سحابي) | `qwen3.5:0.8b` |
| `OLLAMA_CLOUD_MODEL` | نموذج Ollama Cloud عند الإذن المدفوع الصريح فقط | `qwen3.5:0.8b` |

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
- `GROQ_API_KEY` و`OPENROUTER_API_KEY` يُستعملان فقط على `main` عبر `env:` داخل `python-2027.yml` لفحوص Free-Tier (`:free` لـOpenRouter) تحت حراسة `AI2027_MAX_SPEND_USD=0`.
- `OLLAMA_API_KEY` يبقى معزولاً بوصفه `UNKNOWN_COST` لخدمة Ollama Cloud البعيدة ولا يُصرف تلقائياً تحت `$0`، بينما يظل Ollama المحلي (`OLLAMA_BASE_URL` على loopback) مجانياً دون مفتاح.
- `E2B_API_KEY` يختبر backend العزل بتنفيذ ثابت `print(40 + 2)` في microVM مؤقتة؛ اختيار الفحص صريح وقد يستهلك رصيداً.
- `RENDER_API_KEY` يسمح بطلب نشر خدمة واحدة. لا يُعدّ قبول الطلب نجاحاً: يلزم `wait_for_deploy()` ثم فحص حياة الخدمة.
- `.github/workflows/project-lifecycle.yml` يجمع البوابات: CI أولاً، ثم الفحوص التي يختارها المستخدم، ثم Render فقط بعد نجاح البوابات المطلوبة والتأكيد الحرفي لاسم الخدمة. يعمل النشر ضمن Environment باسم `production`؛ أضف مراجعين مطلوبين في إعدادات Environment إذا أردت موافقة بشرية إضافية. تستخدم متغيرات المستودع لإعداد النموذج والخدمة دون وضعها في الكود.

**لا تفعله:**
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
