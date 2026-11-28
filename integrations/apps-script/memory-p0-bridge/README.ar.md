# جسر Google Apps Script المحكوم — ذاكرة P0 وبوت الموافقات (`memory-p0-bridge`)

حزمة Google Apps Script (بيئة `V8`) المرتبطة بمشروع **`Celia Fashion Design`**:
- **معرّف المشروع (`SCRIPT_ID`):** `1UNRaWAvGNYI6lYnJ1nlQt3PyMsG3eZkI--WASZrhMPoBzB6ZqiBXG-5z`
- **ورقة الذاكرة الهدف (`PA_MEMORY_SHEET_ID`):** `1LPceG_K9jPaGOEFVf5wymR4eYyc3Jh4Bpgg9VatNf2I` (`My Personal Agent — Memory (P0)`)
- **التكلفة:** `$0` (حصص Google Apps Script + Google Sheets + Telegram Bot API المجانية — بلا `GOOGLE_SERVICE_ACCOUNT_JSON` وبلا خادم مدفوع، وفق `AD-002`).

## ما الذي تقدّمه الحزمة؟

| الدالة | النوع | الوظيفة |
|---|---|---|
| `watcherPoll()` | قراءة فقط (`writes: 0`) | فحص اتصال Gmail (غير المقروء) وCalendar (48 ساعة) واكتشاف التعارضات دون أي كتابة. |
| `bindDefaultMemorySheet()` | إعداد مرّة واحدة | يتحقق من وجود التبويبات السبعة الحرجة في `1LPceG_K9jPaGOEFVf5wymR4eYyc3Jh4Bpgg9VatNf2I` ثم يربط `PA_MEMORY_SHEET_ID` في `ScriptProperties`. |
| `bridgeDoctor()` | تشخيص آمن | يفحص الجاهزية وتوافر الخصائص وسلسلة التدقيق دون طباعة أي قيمة سرّية (`secret_values_logged: false`). |
| `memoryWatcherCycle()` | دورة P1 محكومة | يجلب رؤوس Gmail الوصفية (`From`, `Subject`, `Date` + `snippet`) وأحداث التقويم، يطبّق حجب `ssGateway`، يكتب في `pa_source_records` (`A:W`) و`pa_tasks` (`A:V`) مع منع التكرار، ويختم في `pa_audit_logs_202611` (`A:Y`) بسلسلة SHA-256 مع تحقّق القراءة الراجعة. |
| `approvalBotCycle()` | بوت موافقات محكوم | يرسل بطاقات `pa_approvals` المعلّقة إلى تيليجرام ويعالج قرارات المالك (`grant` / `deny`) وفق `AD-004` (إغلاق آمن عند غياب `PA_APPROVAL_ALLOWED_SENDERS` أو انتهاء الصلاحية). |
| `runAllGovernedCycles()` | نقطة تشغيل المشغّل الزمني | ينفّذ `memoryWatcherCycle()` ثم `approvalBotCycle()` (إن وُجدت خصائص تيليجرام) تحت قفل واحد `LockService`. |
| `installTrigger30m()` | تفعيل يدوي مرّة واحدة | يحذف أي مشغّلات سابقة منعاً للتكرار وينشئ مشغّلاً زمنياً كل 30 دقيقة لـ `runAllGovernedCycles`. |

## التفعيل اليدوي لمرّة واحدة داخل `script.google.com`

بما أن واجهة Google Drive API ترفع الشفرة والبيان (`appsscript.json` + `Code.gs`) لكنها لا تمنح تفويض OAuth البشري ولا تنشئ مشغّلات الساعة نيابةً عن المالك:

1. افتح المشروع: `https://script.google.com/d/1UNRaWAvGNYI6lYnJ1nlQt3PyMsG3eZkI--WASZrhMPoBzB6ZqiBXG-5z/edit`
2. اختر الدالة **`bindDefaultMemorySheet`** واضغط **Run (تشغيل)** ووافق على نافذة الصلاحيات لمرّة واحدة.
3. (اختياري لبوت تيليجرام) من **Project Settings → Script Properties** أضف:
   - `PA_APPROVAL_BOT_TOKEN`
   - `PA_APPROVAL_CHAT_ID`
   - `PA_APPROVAL_ALLOWED_SENDERS` (معرّف تيليجرام الرقمي للمالك)
   - `PA_APPROVAL_VOTE_BASE_URL` (اختياري لبوابة Passkey)
4. اختر **`bridgeDoctor`** واضغط **Run** للتثبّت من `status: "ok"`.
5. اختر **`installTrigger30m`** واضغط **Run** لتفعيل الدورة التلقائية كل 30 دقيقة.

## الفحص المحلي بدون شبكة

```bash
node --test integrations/apps-script/memory-p0-bridge/test_bridge.cjs
python3 -m unittest scripts/test_apps_script_bundle.py -v
```
