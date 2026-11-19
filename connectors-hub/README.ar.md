# Python Unified Connectors Hub — أساس MVP

خدمة Python محلية للبحث في الحسابات المصرّح بها وقراءة الموارد المحددة وتمرير التغييرات التي يقترحها الوكيل عبر موافقة بشرية منفصلة. مكان المصدر هو `connectors-hub/` في جذر هذا المستودع، خارج أرشيف `projects/`، مع قابلية التشغيل على Windows وإضافة تكامل CeliaOS لاحقاً.

> **الحالة بدقة:** هذه بداية آمنة للـ API والموافقات، وليست ادعاءً بأن الحسابات الاثني عشر متصلة أو أن OAuth والقراءة والكتابة اختُبرت فعلياً. أسماء الموصلات ومسارات البحث الأولية موجودة؛ لكن تفويض OAuth لكل خدمة وفحوص الاتصال الحقيقية ما زالت مطلوبة. لا نعتبر أي خدمة «متصلة» حتى ينجح اتصال وقراءة حقيقيان.

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

يطلب `credentials set` رمزاً أصدرته الخدمة عبر الطرفية دون إظهاره، وليس كلمة مرور حسابك. الأسماء المدعومة: `dropbox_access_token` و`github_token` و`google_access_token` و`linear_api_key` و`notion_integration_token` و`supabase_publishable_key` و`supabase_user_jwt` و`vercel_token`. تستخدم خدمات Google الست رمزاً واحداً، لكن ذلك لا يعني أن الصلاحيات الست ممنوحة. OAuth عبر المتصفح وتجديد رمز Google غير منفذين بعد؛ إدخال رمز الوصول يدوياً مؤقت فقط.

لـ Supabase اضبط `CONNECTORS_HUB_SUPABASE_URL` و`CONNECTORS_HUB_SUPABASE_SEARCH_TABLES` و`CONNECTORS_HUB_SUPABASE_SEARCH_COLUMNS` في بيئة التشغيل قبل بدء Hub. قوائم السماح فارغة افتراضياً ويجب ضبطها صراحة. استخدم مفتاحاً publishable/anon مع JWT مستخدم وRLS؛ تُرفض مفاتيح service-role/secret.

يعرض `credentials status` وجود مادة اعتماد محلية فقط ولا يثبت صحة الصلاحيات أو الاتصال. لا نعتبر أي خدمة متحققة حتى تنجح قراءة فعلية مصرح بها.

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

تغطي الاختبارات مسارات البحث وقراءة الموارد للخدمات الاثنتي عشرة وضوابط API والموافقة، لكنها ليست اختبار اتصال حي. لم يُختبر أي حساب مزود من هذا المستودع. نضيف اختبارات الاتصال خدمة خدمة بعد التفويض، بدءاً بالقراءة فقط. أي اختبار تعديل يجب أن يستخدم مورداً تجريبياً وموافقة محلية، لا صندوق بريد أو تقويماً أو قاعدة بيانات أو نشر إنتاجي.

## ما تبقى قبل MVP كامل

1. OAuth رسمي في المتصفح وتجديد الرموز، بدءاً من Google وDropbox.
2. فحص اتصال وقراءة آمن لكل خدمة من الحساب الذي يصرح به المالك.
3. التحقق من البحث والقراءة والترقيم والأخطاء لكل موصل، وتوثيق ما لا يمكن بحثه موحداً.
4. بناء عمليات تعديل typed لكل خدمة مع معاينة واضحة واختبار يثبت أن كل عملية تتطلب موافقة.
5. إضافة adapter لـ CeliaOS بعد اجتياز اختبارات القراءة.

إلى أن تنجح هذه الخطوات، المشروع أساس آمن وكتالوج للموصلات، وليس Hub مكتملاً ومتحققاً لكل الخدمات الاثنتي عشرة.
