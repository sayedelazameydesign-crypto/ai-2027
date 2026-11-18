<div dir="rtl">

# Verified Browser Integration — تجربة واحدة بموافقة المالك

**الحالة: `REVIEW ONLY` — لا يوجد بعد إيصال من Chrome على جهاز المالك.** الاختبارات الآلية تحاكي BrowserSkill وتستخدم خادم HTTP محليًا حقيقيًا. لا تثبت اتصالًا بمتصفحك. هذا امتداد للمرحلة الأولى `local` من [خطة BrowserSkill](BROWSERSKILL-PLAN.md)، وليس تفعيلًا لـ`skill_mcp` أو `remote_wss`.

**المهمة:** افتح صفحة اختبار يولّدها البرنامج على `127.0.0.1` في تبويب واحد داخل نافذة متخفّية جديدة. اقرأ علامة فريدة لتلك المحاولة وأغلق الجلسة. لا يفتح الاختبار GitHub أو صفحة تسجيل دخول، ولا يُرسل نموذجًا أو يستعير تبويبًا أو ينزّل ملفًا. Arena يقدّم طلبًا محدودًا، وأنت توافق وتشغّله على جهازك ثم تختار ما تُعيده. لا توجد قناة تحكّم من Arena إلى Chrome ولا رفع تلقائي لأي نتيجة.

## ١. جهّز مختبرًا على جهازك، لا في Sandbox

- استخدم نسخة محلية **مراجَعة** من كود هذه المرحلة وPython 3.10 فأحدث. أنشئ ملف Chrome جديدًا دون حساب أو مزامنة أو استيراد أو إضافات أخرى. لا تُقرن ملف العمل. أغلق أي نافذة متخفّية أخرى في المختبر ولا تتفاعل مع نافذة المهمة أثناء التشغيل؛ نوافذ التصفّح المتخفّي في الملف نفسه قد تتشارك الكوكيز.
- ثبّت CLI من [مثبّت المستودع المثبّت الإصدار](../browser_skill_adapter/README.md#quick-start-on-your-machine-stage-1--mode-1) وإضافة BrowserSkill من [المتجر الرسمي](https://chromewebstore.google.com/detail/hhcmgoofomhgciiibhipgmgkgnoenaoi) **في ملف المختبر فقط**. الفحص يقبل CLI والإضافة `0.5.0` وبروتوكول `2.0` فقط. إن اختلف إصدار المتجر، توقّف أو استخدم [أرشيف Chrome الرسمي للإصدار المحدد](https://github.com/Tencent/BrowserSkill/releases/tag/ext-v0.5.0) بعد فحص SHA‑256 ومراجعة سياسة جهازك. لا تُعطّل التحقق لتجاوز عدم التوافق.
- في طرفية محلية، استخدم `BSK_HOME` جديدًا وخاصًا خارج Git. المثال يستعمل المنفذ `52801` لئلا يتداخل مع المنفذ القياسي. لا تُنشئ نفقًا أو تعرّضه على الشبكة.

**Linux / macOS** — من جذر النسخة المحلية:

```bash
./scripts/install_browserskill.sh
export PATH="$HOME/.local/bin:$PATH"
export BSK_HOME="$HOME/.bsk/ai-2027-handoff"
mkdir -p "$BSK_HOME" && chmod 700 "$BSK_HOME"
bsk daemon trust-local off
bsk daemon start --mode local --port 52801
bsk daemon pair
```

**Windows PowerShell 5.1 (`powershell.exe`)** — من حسابك العادي، في ملف مستخدم محلي غير مشترك على قرص ثابت بنظام NTFS:

```powershell
$ErrorActionPreference = 'Stop'
.\scripts\install_browserskill.ps1
$env:Path = "$HOME\.local\bin;$env:Path"
$env:BSK_HOME = Join-Path $HOME '.bsk-ai-2027-handoff'
$replayState = Join-Path $HOME '.ai-2027-browser-handoff'
$owner = [Security.Principal.WindowsIdentity]::GetCurrent().User
$acl = [Security.AccessControl.DirectorySecurity]::new()
$acl.SetOwner($owner)
$acl.SetAccessRuleProtection($true, $false)
$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
    $owner, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow'))
if (Test-Path -LiteralPath $env:BSK_HOME) { throw 'Choose a fresh BSK_HOME under your local profile.' }
[IO.Directory]::CreateDirectory($env:BSK_HOME, $acl) | Out-Null
if (-not (Test-Path -LiteralPath $replayState)) {
    [IO.Directory]::CreateDirectory($replayState, $acl) | Out-Null
}
py -3 browser_skill_adapter/verified_handoff.py check-private-dirs
if ($LASTEXITCODE -ne 0) { throw 'Private directory check failed. Do not pair.' }
bsk daemon trust-local off
if ($LASTEXITCODE -ne 0) { throw 'Required pairing mode could not be set.' }
bsk daemon start --mode local --port 52801
if ($LASTEXITCODE -ne 0) { throw 'Lab daemon could not be started.' }
bsk daemon pair
```

على Windows يفحص البرنامج ملكية `BSK_HOME` ومجلد منع إعادة الاستخدام وقواعد السماح في DACL، بما فيها القواعد الموروثة وصلاحيات الملفات الموجودة داخلهما. يرفض مسارًا شبكيًا أو نقطة إعادة توجيه (`reparse point`)، أو ACL غير مقروءة أو تمنح الوصول لغير حسابك و`SYSTEM` و`Administrators` المحليين. يفترض الثقة بمسؤولي الجهاز وبمجلدات النظام فوق ملف المستخدم؛ لا يحمي من برمجية تعمل بحسابك. لا ينشئ البرنامج مجلد حالة Windows تلقائيًا ولا يغيّر ACL قائمة. إذا حُظر مجلد الحالة، لا تحذفه ولا تحذف سجلات الطلبات لحل المشكلة. `PRIVATE_DIRS_OK` نتيجة محلية للصلاحيات فقط، وليس `READY_LOCAL` ولا دليل تشغيل.

إذا فشل أي أمر، توقّف قبل متابعة الأوامر التالية. الصق رابط الاقتران **محليًا فقط** في BrowserSkill ← **Connection settings** ← **Pairing link**. انتظر **Connected · Verified**، واضبط **Browser name** على `arena-handoff-lab`، ثم انسخ **Instance ID** من النافذة نفسها. أبقِ المعرّف على جهازك (ليس Chrome extension ID). في `chrome://extensions` امنح BrowserSkill مؤقتًا **Allow in incognito** داخل المختبر فقط. لا تمنحه صلاحية الوصول إلى عناوين الملفات.

في **الطرفية نفسها**، استبدل `POPUP_ID` بالمعرّف المحلي (ثمانية أحرف hex صغيرة):

```bash
python3 browser_skill_adapter/verified_handoff.py check-local --browser-instance POPUP_ID
```

على Windows استخدم `py -3` بدل `python3`. يكفي إبلاغ Arena بـ`READY_LOCAL` أو بحالة الفشل ورمز السبب المختصر؛ لا تُرسل معرّف الملف أو مخرجات `bsk` الخام. `READY_LOCAL` لا يفتح نافذة ولا يثبت تنفيذ المهمة أو سماح Chrome بالإذن المتخفّي.

## ٢. وافق على طلب واحد ونفّذه

**في Arena:** أصدر طلبًا بعد جاهزية المختبر. اطلب مراجعة مهمة `observe-loopback-fixture` فقط. شارك `request.json`، وانقل قيمة `request_sha256` من جلسة Arena الموثوقة التي أصدرت الطلب، عبر قناة مستقلة عن نقل الملف. لا تعتمد على ملف بصمة داخل الأرشيف نفسه أو على مرسل يستطيع استبدال الاثنين. لا يوثّق البرنامج القناة أو هوية المُرسل؛ إن لم تستطع التحقق من مصدر البصمة فلا تنفّذ. الطلب لا يحمل رابطًا أو أوامر أو تصريحًا مفتوحًا. مدة الصلاحية الافتراضية 30 دقيقة (الحد الأقصى ساعة).

```bash
mkdir -p browser_skill_adapter/runtime
python3 browser_skill_adapter/verified_handoff.py prepare \
  --out browser_skill_adapter/runtime/request.json
```

**على جهاز المالك:** ضع `request.json` في `browser_skill_adapter/runtime/` (المجلد مستثنى من Git؛ أنشئه إذا لم يوجد). في الطرفية المحلية التي فيها `BSK_HOME`، استبدل `POPUP_ID` بمعرّف المختبر، و`ORIGINAL_SHA256` بالبصمة التي أرسلها Arena **خارج الملف**. لا تحسبها من النسخة التي استلمتها:

```bash
python3 browser_skill_adapter/verified_handoff.py execute --browser-instance POPUP_ID --request browser_skill_adapter/runtime/request.json --expected-request-sha256 ORIGINAL_SHA256 --out browser_skill_adapter/runtime/receipt.json
```

إذا لم يطابق محتوى الطلب البصمة الأصلية، يرفضه البرنامج بـ`request_digest_mismatch` قبل عرض الموافقة. إذا تطابق، تحقّق من المهمة ومعرّف المثيل المعروضَين، واكتب `APPROVE` متبوعة بالبصمة الكاملة **فقط إذا وافقت**. Enter وحده يرفض. لا يوجد خيار لتخطّي الموافقة ولا قبول للمدخلات من pipe.

بعد الموافقة مباشرةً، يعيد البرنامج حساب SHA‑256 من **نسخة الطلب الثابتة التي سيستخدمها** ويقارنها بالبصمة المرجعية عبر `hmac.compare_digest`. يعيد أيضًا فحص انتهاء الصلاحية. أي إخفاق يوقف التنفيذ قبل أول استدعاء لـBrowserSkill ولا يُنشئ إيصالًا. لا يُعاد فتح ملف الطلب: تغييره على القرص لا يغيّر ما وافقت عليه. تغطي البصمة جميع حقول الطلب؛ المخطط لا يقبل عنوان صفحة أو خيارات BrowserSkill. البرنامج يولّد صفحة الاختبار على `127.0.0.1`، وأنت تختار ملف المختبر محليًا ولا يحدّده الملف المرسل من Arena. إذا انتهت نافذة الصلاحية فاطلب إصدار طلب جديد؛ لا تعدّل التواريخ أو الملف.

البرنامج يطلب الاقتران الإلزامي ويطابق معرّف الجهاز المحلي الوحيد مع الملف المختار، ويرفض الجلسات الأخرى. يقارن التبويب والنافذة مع الجلسة قبل التنقّل، ويتحقق من عنوان الوصول ومن أن الخادم خدم الصفحة ومن ظهور العلامة في مشاهدة التبويب. يطلب إغلاق **الجلسة نفسها** حتى عند فشل المشاهدة، ويتحقق من غياب الجلسة على الاتصال نفسه قبل كتابة إيصال `VERIFIED_LOCAL`. لا يعيد التنقّل إذا كانت نتيجة المحاولة مجهولة.

**قبل المشاركة، تأكّد بنفسك من اختفاء نافذة المهمة.** رد BrowserSkill وحالة الـdaemon ليسا شهادة مستقلة بأن Chrome أغلق كل نافذة. بعد الفحص، عطّل اتصال BrowserSkill في المختبر واسحب إذن التصفّح المتخفّي. تحت `BSK_HOME` المخصّص نفسه، شغّل `bsk daemon devices` واقرأ حقل `device_id` الخاص بجهاز المختبر محليًا. استخدمه في `bsk daemon revoke DEVICE_ID`، لا معرّف النافذة المنبثقة، ثم أوقف الـdaemon بـ`bsk daemon stop`. لا تشارك أيًا من المعرّفين أو مخرجات الأمر.

### إذا لم يكتمل الاختبار

| الحالة | ما تعنيه |
| --- | --- |
| `SKIPPED` (2) | CLI أو daemon أو متصفح متصل غير متاح؛ لا توجد نتيجة حيّة. |
| `BLOCKED` (3) | شرط أمان أو إذن أو شرط جاهزية غير متحقق؛ لا تخفّضه لتجعل الاختبار يمر. |
| `REJECTED` / `FAILED` (1) | رفض طلب/إيصال أو مخالفة في المهمة؛ لا تشارك نتيجة نجاح. |
| `INCONCLUSIVE` (4) | لا يمكن تأكيد الأثر أو التنظيف. لا تُعد تنفيذ الطلب. |

إذا حاولت تشغيل الطلب مرتين بالتزامن، لا تسمح حالة المستخدم المحلية إلا بمحاولة واحدة. يُستهلك معرّف الطلب قبل فتح النافذة ويظل مستهلَكًا حتى عند الفشل بعدها. لا يمتد هذا المنع إلى جهاز آخر. إذا ظهر `cleanup_not_confirmed` أو بقي `active.lock` بعد انقطاع العملية، لا تبدأ مهمة أخرى. الجلسة تُترك غير مُطالَب بها (`unclaimed`) ليحاول الـdaemon تنظيفها بانتهاء مهلة البداية، لكن لا تعتمد على ذلك إذا انقطع الاتصال. تأكّد أن برنامج التنفيذ انتهى، وأغلق نوافذ **المختبر فقط**، وعطّل الإضافة وأوقف الـdaemon المخصّص. بعد التأكد من عدم بقاء المهمة يمكنك إزالة **القفل فقط** (`~/.ai-2027-browser-handoff/active.lock`)، ثم طلب طلب جديد. لا تحذف ملفات منع إعادة الاستخدام ولا تعاود تشغيل طلب مستهلَك.

## ٣. راجع الإيصال ثم احفظه في GitHub

1. إذا حصلت على `VERIFIED_LOCAL` وتحققت من إغلاق النافذة، راجع `receipt.json` وشاركه يدويًا مع Arena. يحتوي على معرّف الطلب، أوقات UTC، قيم اصطناعية وبصمات ونتائج بوابات. **لا تشارك** `BSK_HOME` أو روابط/مفاتيح الاقتران أو سجلات المتصفح أو لقطات الشاشة أو بيانات الحساب.
2. في Arena، قارن بالطلب الأصلي الذي أصدرته أنت، لا بنسخة جديدة يرسلها مُنتج الإيصال:

```bash
python3 browser_skill_adapter/verified_handoff.py verify --request browser_skill_adapter/runtime/request.json --receipt browser_skill_adapter/runtime/receipt.json
```

`RECEIPT_CONSISTENT` يعني تطابق الطلب والتوقيت وعلامة الاختبار والبصمات. لا يثبت وحده هوية مرسل الإيصال ولا أن Arena رأى متصفحًا؛ يمكن لأي طرف إعادة حساب SHA‑256. أكّد المصدر مع المالك في قناة موثوقة. اختياريًا، وقّع الإيصال المحلي **بعد مراجعته** بمفتاح توقيع مستقل محفوظ خارج المستودع:

```bash
ssh-keygen -Y sign -f "$HOME/path-to-handoff-signing-key" -n ai-2027-browser-handoff browser_skill_adapter/runtime/receipt.json
python3 browser_skill_adapter/verified_handoff.py verify \
  --request browser_skill_adapter/runtime/request.json --receipt browser_skill_adapter/runtime/receipt.json \
  --signature browser_skill_adapter/runtime/receipt.json.sig --trusted-signers /path/to/independently-trusted-allowed-signers
```

يحتوي `allowed_signers` لدى المتلقّي على سطر `arena-browser-owner` ثم **المفتاح العام** الذي سبق التحقق منه بشكل مستقل. لا تثق بمفتاح مرفق بالإيصال وحده. `OWNER_ATTESTED` يتحقق من توقيع صاحب المفتاح على بايتات الإيصال؛ ليس إثباتًا مستقلًا من Chrome.

3. بعد موافقة المالك على النشر، أضف **فقط** الطلب والإيصال المطابقين (والتوقيع العام إن استُخدم) تحت `docs/evidence/browser-handoff/<request_id>/` في PR واضح. دوّن إصدار الكود الذي نُفّذ وكيف تأكّد مصدر الإيصال. لا تضف مخرجات الاختبارات المحاكية كأنها إثبات حيّ، ولا تعتبر حفظ ملف في GitHub أو اجتياز CI بديلًا عن الفحص المحلي. **لا يوجد إثبات حيّ محفوظ الآن.**

## مراجع وحدود الفحص

- [الاقتران المحلي](https://github.com/Tencent/BrowserSkill/blob/cli-v0.5.0/docs/local-pairing.md)، [اختيار ملف Chrome](https://github.com/Tencent/BrowserSkill/blob/cli-v0.5.0/docs/browser-profiles.md)، [بدء الجلسة القابل للاسترداد](https://github.com/Tencent/BrowserSkill/blob/cli-v0.5.0/docs/recoverable-session-starts.md).
- `session start` في الإصدار المثبّت لا يعيد معرّف التبويب أو حقل تحقق من التخفّي؛ لذلك يُطلب `--incognito` بلا تراجع إلى نافذة عادية، ويُطابق التبويب باستدعاء مستقل. الاقتران لا يحمي من برمجية لديها صلاحيات حساب نظام التشغيل نفسه. سلامة ملف المختبر إجراء على المالك، لا معلومة يمكن للتحقق عن بُعد إثباتها.
- إعادة الاختبارات دون BrowserSkill أو متصفح: `python3 browser_skill_adapter/test_verified_handoff.py -v` (`py -3` على Windows). إضافة إلى فحوص الموافقة والعبث والتنظيف، تتنافس عمليتا Python مستقلّتان (`spawn`) على قفل `O_EXCL`، وتختبر المحاكاة رفض محاولة متداخلة وإعادة تشغيل الطلب من عملية جديدة. تختبر أيضًا انقطاع العملية بعد الحجز وبقاء منع إعادة الاستخدام بعد إزالة القفل يدويًا في بيئة الاختبار. هذا لا يثبت تنظيف Chrome بعد انقطاع حقيقي، أو متانة الحالة بعد فقدان الطاقة، أو السلوك على نظام ملفات شبكي. اختبارات ACL الفعلية تتطلب Windows وNTFS.

</div>
