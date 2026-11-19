# Python Unified Connectors Hub — أساس MVP

خدمة Python محلية للبحث في الحسابات المصرّح بها وقراءة الموارد المحددة وتمرير التغييرات التي يقترحها الوكيل عبر موافقة بشرية منفصلة. مكان المصدر هو `connectors-hub/` في جذر هذا المستودع، خارج أرشيف `projects/`، مع قابلية التشغيل على Windows وإضافة تكامل CeliaOS لاحقاً.

> **الحالة بدقة:** هذا أساس آمن للـ API وليس ادعاءً بأن الحسابات الاثني عشر متصلة أو أن OAuth والقراءة والكتابة اختُبرت فعلياً. أُضيف مسار استدلال نصي محمي لـ LM Studio محلياً وNVIDIA اختيارياً بموافقة صريحة، لكن لم يُختبر أي نموذج أو جهاز أو حساب NVIDIA فعلياً من المستودع. لا نعتبر أي خدمة «متصلة» حتى ينجح اتصال وقراءة حقيقيان. لا يحتوي المستودع على مفاتيح.

## ضوابط الأمان

- الخادم مخصص للربط على `127.0.0.1` فقط؛ لا تعرضه على الشبكة أو الإنترنت.
- كل المسارات عدا `/health` تتطلب Bearer token محلياً. ينشئه `connectors-hub init` ويحفظه في مخزن مفاتيح نظام التشغيل، ويعرضه مرة واحدة في الطرفية المحلية فقط. لا تلصقه في المحادثة ولا تضفه إلى Git.
- الـ API لا ينفّذ تغييرات على الخدمات. يمكنه إنشاء **اقتراح معلّق** فقط؛ لا يوجد endpoint للموافقة أو التنفيذ.
- التنفيذ يتطلب تشغيل `connectors-hub approve <id>` محلياً، مراجعة الموصل والطريقة والمسار والـ query والـ payload كاملة، ثم كتابة عبارة تأكيد لمرة واحدة. تنتهي صلاحية الاقتراح ولا ينفذ أكثر من مرة. إذا انتهت مهلة طلب بعد احتمال وصوله للخدمة، يسجل `unknown` ولا يعيد المحاولة تلقائياً.
- تحفظ بيانات الاعتماد ومفتاح التشفير في مخزن مفاتيح نظام التشغيل. تُشفّر حمولات التغييرات المعلّقة في قاعدة البيانات المحلية؛ سجل التدقيق يحفظ البصمة وحالات التغيير، لا رموز الدخول أو أجوبة الخدمات.
- حساب Google المقصود هو `sayedelazameydesign@gmail.com`. البريد تلميح محلي فقط ولا يمنح صلاحية. لم يُنفّذ OAuth عبر المتصفح بعد؛ وعند إضافته يجب التأكد من الحساب المختار ومنح أقل نطاقات صلاحية لازمة عبر شاشة Google الرسمية.
- Supabase يستخدم مفتاح publishable/anon مع JWT لمستخدم وRLS. يُرفض مفتاح `service_role`/admin. لا تضع مفتاح Supabase السري في المتصفح أو المستودع أو prompt الوكيل.
- البحث وقراءة الموارد يمران مباشرة للخدمات؛ يعيدان البيانات للمتصل ولا يخزنانها أو يفهرسانها مركزياً. يتطلب Supabase قوائم جداول وأعمدة صريحة وRLS ولا يمسح كل الجداول تلقائياً.
- موصلات Arena/ChatGPT لا تنقل اعتمادها تلقائياً إلى تطبيق Python؛ التفويض لكل موصل Python منفصل.

## الخدمات المستهدفة

Dropbox، GitHub، Gmail، Google Calendar، Drive، Docs، Sheets، Slides، Linear، Notion، Supabase، Vercel. تتشارك خدمات Google هوية Google واحدة، لكن Docs/Sheets/Slides تستخدم واجهات الموارد الخاصة بها. الوصول إلى GitHub في التطبيق يتم عبر API مباشرة؛ موصل GitHub غير متاح عبر واجهة Arena الحالية.

وجود الخدمة في الكتالوج لا يثبت اتصالها. يعرض `GET /v1/connectors` الحالة `unconfigured` أو `configured` أو `needs_resource_config`. لا نصف خدمة بأنها متحققة حتى ينجح اختبار اتصال وقراءة نموذجية فعليان.

## التشغيل المحلي على Windows

استخدم Python 3.12:

```powershell
cd connectors-hub
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
connectors-hub init
connectors-hub credentials set github_token
connectors-hub serve
```

يطلب `credentials set` رمزاً أصدرته الخدمة عبر الطرفية دون إظهاره، وليس كلمة مرور حسابك. الأسماء المدعومة: `dropbox_access_token` و`github_token` و`google_access_token` و`linear_api_key` و`notion_integration_token` و`nvidia_api_key` و`supabase_publishable_key` و`supabase_user_jwt` و`vercel_token`. تستخدم خدمات Google الست رمزاً واحداً، لكن ذلك لا يعني أن الصلاحيات الست ممنوحة. OAuth عبر المتصفح وتجديد رمز Google غير منفذين بعد؛ إدخال رمز الوصول يدوياً مؤقت فقط.

لـ Supabase اضبط `CONNECTORS_HUB_SUPABASE_URL` و`CONNECTORS_HUB_SUPABASE_SEARCH_TABLES` و`CONNECTORS_HUB_SUPABASE_SEARCH_COLUMNS` في بيئة التشغيل قبل بدء Hub. قوائم السماح فارغة افتراضياً ويجب ضبطها صراحة. استخدم مفتاحاً publishable/anon مع JWT مستخدم وRLS؛ تُرفض مفاتيح service-role/secret.

يعرض `credentials status` وجود مادة اعتماد محلية فقط ولا يثبت صحة الصلاحيات أو الاتصال. لا نعتبر أي خدمة متحققة حتى تنجح قراءة فعلية مصرح بها.

## الاستدلال المحلي عبر LM Studio وNVIDIA اختيارياً

المسار `POST /v1/reasoning` محمي برمز الـ API المحلي نفسه، ويعيد إكمالاً نصياً فقط؛ لا يشغّل أدوات أو إجراءات موصلات. المزود الافتراضي هو `local`: نزّل [LM Studio](https://lmstudio.ai/download)، حمّل نموذجاً وشغّل خادمه المتوافق مع OpenAI على `127.0.0.1:1234`. لا ينزّل Hub النماذج ولا يحمّلها ولا يحتاج مفتاحاً للاستدلال المحلي. استخدم معرّف النموذج كما يظهر في LM Studio، لا اسمه التسويقي المفترض.

اختر الحجم والتكميم بما يناسب VRAM: `4B` تعني عدد المعاملات بالمليارات، بينما خيارات مثل `Q4` و`Q5` توازن بين الذاكرة والجودة. أوزان النموذج وذاكرة السياق/‏KV والبرامج الأخرى تستهلك ذاكرة أيضاً؛ لا توجد سعة VRAM واحدة مضمونة لكل جهاز. لاستخدام نموذج Qwen 3.5 4B المذكور في الفيديو، خذ معرّف API الدقيق من LM Studio. قبل تشغيل `connectors-hub serve` اضبط مثلاً:

```powershell
$env:CONNECTORS_HUB_LM_STUDIO_MODEL = "<model-id-from-LM-Studio>"
$env:CONNECTORS_HUB_REASONING_CONTEXT_TOKENS = "8192"
$env:CONNECTORS_HUB_REASONING_MAX_OUTPUT_TOKENS = "1024"
```

اجعل حد السياق في Hub مساوياً أو أقل من السياق المحمّل فعلياً في LM Studio ومن الذاكرة المتاحة. الحد الافتراضي 8192 يشمل تقدير الإدخال واحتياط تأطير الرسائل والرموز المطلوبة للإخراج. يستخدم Hub طول بايتات UTF-8 كتقدير محافظ مستقل عن tokenizer، وليس عدّاداً دقيقاً لرموز النموذج، ويرفض الطلب الذي يتجاوز الحد قبل الإرسال. لتلخيص عدة كتب، قسّم النص بنفسك إلى فصول/مقاطع، لخّص المقاطع محلياً ثم اجمع الملخصات في طلبات محلية لاحقة؛ هذا المسار لا يقرأ ملفات الكتب ولا يقسمها تلقائياً. ملف `.env` ليس محمّلاً تلقائياً.

مثال طلب محلي:

```powershell
$body = @{ prompt = "لخّص هذا المقطع في خمس نقاط موجزة: ..."; max_tokens = 400 } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8765/v1/reasoning -Method Post `
  -Headers $headers -ContentType 'application/json' -Body $body
```

حقل `mode` اختياري: `general` هو الافتراضي، و`gaming_case_study` يربط أحداث اللعبة الموصوفة بالقيادة والسلوك مع فصل الوقائع عن التحليل، و`life_lessons` يركّز على المسار المهني والعافية، و`tech_as_tool` يقيّم الفائدة والتكلفة والخصوصية والأمان ويكشف المبالغة. هذه التعليمات تكيّف الرد مع هوية Big Bro Fares التي زوّدني بها المستخدم؛ النموذج مساعد تحرير لا يدّعي أنه صاحب القناة، ولا يختلق سيرته أو مشاهد الألعاب أو الأدلة، ويكتب مادة أصلية تحترم ملكيتك. هذه تخصصات تحريرية وليست ادعاءً بالتكافؤ مع Claude أو Manus.

استخدام NVIDIA اختياري ويرسل النص إلى خدمة مستضافة. خزّن المفتاح في مخزن مفاتيح نظام التشغيل عبر `connectors-hub credentials set nvidia_api_key` (إدخال مخفي؛ لا تضعه في `.env` أو Git أو prompt أو جسم طلب HTTP)، واضبط `CONNECTORS_HUB_NVIDIA_MODEL` بمعرّف نموذج مدعوم في NVIDIA NIM. يجب أن يحتوي كل طلب على `provider = "nvidia"` و`allow_remote_processing = $true` صراحةً؛ وإلا يُرفض ولا يتحول تلقائياً إلى المحلي. الموافقة تعني أن النص سيغادر الجهاز إلى NVIDIA:

```powershell
$env:CONNECTORS_HUB_NVIDIA_MODEL = "<NVIDIA-NIM-model-id>"
connectors-hub credentials set nvidia_api_key
$body = @{ provider = "nvidia"; prompt = "لخّص هذا المقطع."; max_tokens = 400; allow_remote_processing = $true } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8765/v1/reasoning -Method Post `
  -Headers $headers -ContentType 'application/json' -Body $body
```

في المسار المحلي لا يُقرأ المفتاح إلا بعد الموافقة الصريحة، وتُرسل بيانات الاعتماد إلى endpoint NVIDIA ثابت عبر HTTPS. سرّ GitHub منفصل عن مخزن مفاتيح Windows/النظام، ولا يُنسخ إليه تلقائياً؛ للاستخدام المحلي خزّن المفتاح بشكل مستقل عبر إدخال `credentials set nvidia_api_key` المخفي. هذا المسار لا يوافق على مقترحات `/v1/actions` ولا ينفذها. توجد أيضاً `.github/workflows/nvidia-live-check.yml` يدوية فقط: تستخدم Secret المستودع `NVIDIA_API_KEY` في خطوة واحدة، وpromptاً ثابتاً صغيراً ومعرّف نموذج غير سري، ولا تعمل على push أو PR ولا تطبع جواب المزوّد. لم يُشغّل هذا الـworkflow من الفرع الحالي؛ الاختبارات المحلية تستخدم مزودين وهميين.

بعد `init`، خزّن رمز الـ API الذي يظهر مرة واحدة في إعداد أسرار الوكيل المحلي، لا في Git. مثال بحث عبر PowerShell:

```powershell
$headers = @{ Authorization = "Bearer <local-token>" }
$body = @{ query = "project roadmap"; limit = 10 } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8765/v1/search -Method Post `
  -Headers $headers -ContentType 'application/json' -Body $body
```

لقراءة مورد واحد استخدم `POST /v1/read` بنوع مورد ومعرّف خاصين بالموصل. يبني التطبيق مسار API ثابتاً؛ لا يقبل URL عشوائياً أو مسار HTTP من العميل. مثال Gmail:

```powershell
$body = @{ connector = "gmail"; resource_type = "message"; resource_id = "<provider-message-id>" } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8765/v1/read -Method Post `
  -Headers $headers -ContentType 'application/json' -Body $body
```

الأنواع المدعومة: Dropbox `file` (ملفات نص UTF-8 فقط)؛ GitHub `issue` بصيغة `owner/repo#number` و`repository` بصيغة `owner/repo` و`file` بصيغة `owner/repo/path`؛ Gmail `message`؛ Calendar `event`؛ Drive `file` (بيانات وصفية وملفات نص UTF-8 صغيرة، لا تنزيل للثنائي)؛ Docs `document`؛ Sheets `spreadsheet` (النطاق المحدود حالياً `A1:Z100`)؛ Slides `presentation`؛ Linear `issue`؛ Notion `page` (الخصائص وما يصل إلى 500 كتلة من المستوى الأول)؛ Supabase `row` مع `resource` كاسم جدول مسموح صراحة ويستخدم عمود `id`؛ وVercel `project` (بيانات المشروع فقط لا متغيرات البيئة أو سجلات النشر). حد محتوى النص 512 KiB. تظل صلاحيات OAuth ومشاركة المورد وRLS في Supabase هي الحاكم.

التغيير الذي يطلبه الوكيل يبقى معلقاً؛ راجعه ووافق عليه محلياً:

```powershell
connectors-hub pending
connectors-hub approve <action-id>
```

## الاختبارات

تستخدم الاختبارات ردود HTTP وهمية ومخزن مفاتيح ذاكراتي وقواعد بيانات مؤقتة؛ لا تتصل بالخدمات ولا تغيّر حساباتك:

```powershell
python -m unittest discover -s tests -v
```

تغطي الاختبارات مسارات البحث والقراءة للخدمات الاثنتي عشرة، وضوابط API والموافقة، ومساري الاستدلال المحلي وNVIDIA بردود HTTP وهمية. ليست اختبارات اتصال حية؛ لم يُختبر نموذج أو جهاز أو حساب مزود فعلي من هذا المستودع. نضيف اختبارات الخدمات بعد التفويض، بدءاً بالقراءة فقط. أي اختبار تعديل يجب أن يستخدم مورداً تجريبياً وموافقة محلية، لا صندوق بريد أو تقويماً أو قاعدة بيانات أو نشر إنتاجي.

## ما تبقى قبل MVP كامل

1. OAuth رسمي في المتصفح وتجديد الرموز، بدءاً من Google وDropbox.
2. فحص اتصال وقراءة آمن لكل خدمة من الحساب الذي يصرح به المالك.
3. التحقق من البحث والقراءة والترقيم والأخطاء لكل موصل، وتوثيق ما لا يمكن بحثه موحداً.
4. بناء عمليات تعديل typed لكل خدمة مع معاينة واضحة واختبار يثبت أن كل عملية تتطلب موافقة.
5. إضافة adapter لـ CeliaOS بعد اجتياز اختبارات القراءة.

إلى أن تنجح هذه الخطوات، المشروع أساس آمن وكتالوج للموصلات، وليس Hub مكتملاً ومتحققاً لكل الخدمات الاثنتي عشرة.
