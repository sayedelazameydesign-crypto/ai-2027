# Runbook: تشغيل بوت الموافقات عبر تيليجرام (والرفيق واتساب)

> **الحالة بعد التنفيذ (2026-11-27)**:
> - `.env` جاهز في جذر المستودع (مُتجاهل من git — التحقق: `git check-ignore .env`) بجميع مفاتيح الأقسام 8–10.
> - التبعيات مثبتة محلياً: `gspread 6.2.1` + `google-auth` + `google-api-python-client`.
> - اختبار المنطق hermetic: **`python3 -m unittest tests.test_approval_bot` → 25/25 OK**.
> - تجربة حية fail-closed (بلا توكنات): كل المسارات رفضت **بلا أي اتصال شبكة وبلا أي رسالة**:
>   `send_card` ✓ · `fetch_updates` ✓ (حُسِّن — كان يهمل الفحص) · allowlist فارغ = رفض ✓ · `waapi.notify_approval` = `notify_disabled` ✓.

---

## 1. القاعدة المعمارية (لا تُكسر أبداً)

```
أسرار GitHub Actions ──▶ مسار CI فقط (probes قراءة فقط)
القيم تُلصق يدوياً في ‎.env ──▶ مسار النظام المحلي (البوت + المراقب + المنفّذ)
```
- **التوكنات تُلصق في ملف `.env` فقط — لا في الشات، لا في السجلات، لا في أي commit.**
- `.env` مُتجاهل من git (`.gitignore:4`) — أبداً لا تزيل هذا السطر.
- النظام **fail-closed** بكل بواباته: لا توكن = لا إرسال · allowlist فارغ = **لا أحد يصوّت** · توقيع غائب = لا تنفيذ.
- **استثناء واحد — مسار CI (§8)**: الـworkflow المؤقت يقرأ الأسرار داخل job واحد وينتهي — النظام المحلي لا يرى الأسرار إطلاقاً، والتوكن لا يغادر GitHub أبداً.

## 2. ماذا تحصل عليه (ومع أين) — ثم تلصقه في `.env`

| # | القيمة | المصدر | مفتاح `.env` |
|---|---|---|---|
| 1 | توكن البوت | تيليجرام → `@BotFather` → `/newbot` → انسخ التوكن | `PA_APPROVAL_BOT_TOKEN` |
| 2 | معرّف الشات (`chat_id`) | أرسل `/start` للبوت، ثم افتح:<br>`https://api.telegram.org/bot<TOKEN>/getUpdates` واقرأ `message.chat.id` (أرقام، قد يكون سالباً) | `PA_APPROVAL_CHAT_ID` |
| 3 | معرّفك الشخصي (`user id`) | نفس الـgetUpdates → `message.from.id` (أو `@userinfobot`) | `PA_APPROVAL_ALLOWED_SENDERS` |
| 4 | معرّف الورقة | ورقة Google فيها جدول `pa_approvals` (مشتركة مع المراقب) | `PA_MEMORY_SHEET_ID` |
| 5 | حساب خدمة Google | Google Cloud → Service Accounts → مفتاح JSON · **ثم شارك الورقة مع البريد الإلكتروني للحساب (Editor)** | مسار الملف (انظر §4) |
| 6 | (اختياري) رابط التصويت passkey | العامل `worker/approval-worker/` على Cloudflare | `PA_APPROVAL_VOTE_BASE_URL` + `PA_APPROVAL_SIGNING_PUBKEY` |

> **القاعدة 3** (المصاغة فشلاً): أضف معرّفك إلى `PA_APPROVAL_ALLOWED_SENDERS` (افصل بفواصل). بدونه تصلك البطاقة لكن **صوتك يُرفَض**.

## 3. اختبارات قبل التشغيل (دقيقة واحدة)

```bash
cd /path/to/ai-2027
# أ) منطق الموافقات كاملاً (بلا شبكة، بلا توكنات):
python3 -m unittest tests.test_approval_bot        # → 25 tests OK

# ب) رفض حي (يظهر أن المسارات مغلقة بلا قيم):
python3 - <<'PY'
import os, pathlib, sys
for line in pathlib.Path(".env").read_text().splitlines():
    s = line.strip()
    if s and not s.startswith("#") and "=" in s:
        k, v = s.split("=", 1); os.environ.setdefault(k.strip(), v.strip())
sys.path.insert(0, "src/watchers")
import approval_bot
try:
    approval_bot.TelegramTransport().send_card({"text": "x", "buttons": []})
    print("FAIL: أُرسلت رسالة بلا توكن!")
except RuntimeError as e:
    print("OK fail-closed:", e)
PY
```

## 4. تشغيل دورة واحدة (run_once) ثم التشغيل المستمر

ضع بيانات الاعتماد في مسار مُتجاهل من git (الأنماط مغطاة في `.gitignore` — قسم «Google credential files»)، ثم:

```python
# run_once.py — دورة واحدة: يرسل البطاقات المعلّقة ويطبق أصوات الـgetUpdates
import os, pathlib, sys

for line in pathlib.Path(".env").read_text().splitlines():
    s = line.strip()
    if s and not s.startswith("#") and "=" in s:
        k, v = s.split("=", 1); os.environ.setdefault(k.strip(), v.strip())

import gspread
from google.oauth2 import service_account

CREDS_PATH = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]  # مسار ملف JSON
creds = service_account.Credentials.from_service_account_file(
    CREDS_PATH, scopes=[gspread.auth.DEFAULT_SCOPES])

sys.path.insert(0, "src/watchers")
import approval_bot

print(approval_bot.ApprovalBot(creds).run_once())
# المتوقع بعد تعبئة كل القيم: {"cards_sent": N, "callbacks_handled": M, ...}
```

**التشغيل المستمر** — الحلقة نفسها بلا توقف:
```python
approval_bot.start_daemon(creds)   # دورة كل APPROVAL_BOT_INTERVAL_SEC (افتراضي 300s)
```
شغّلها كخدمة على جهاز يعمل دائماً (systemd/`tmux`/cron كل 5 دقائق). ملاحظة: **Actions لا تستمع لرسائلك** — الاستقبال من هذه الحلقة فقط.

## 5. استكشاف الأخطاء

| العَرَض | السبب | العلاج |
|---|---|---|
| `not configured … fail-closed` | `.env` غير معبّأ أو لم يُحمَّل | املأ المفاتيح §2 |
| `telegram getUpdates/sendMessage failed: …401` | توكن خاطئ/منتهٍ | انسخه مجدداً من BotFather |
| الرد `403: bot was blocked` | المستخدم حظر البوت | افتح البوت واضغط `/start` |
| البطاقات لا تصل والتوكن سليم | `PA_APPROVAL_CHAT_ID` خاطئ أو البوت ليس في الشات | أعد قراءة getUpdates |
| `400: Bad Request: chat not found` في Actions | قيمة `PA_APPROVAL_CHAT_ID` المخزّنة لا تطابق محادثة حقيقية: علامات تنصيص، أو `@username`، أو رابط، أو معرّف البوت نفسه، أو توكن لُصق مكانها، أو رقم خاطئ | خطوة الفشل «Check stored chat id against real chats» (`approval_bot_ci.py --mode chatcheck`) تسمّي السبب والسر الذي جاءت منه والرقم الصحيح، دون طباعة القيمة المخزّنة |
| الصوت يُرفَض بصمت | `PA_APPROVAL_ALLOWED_SENDERS` فارغ/غير يضمّ معرّفك | أضف معرّفك (§2.3) |
| خطأ فتح الورقة | الحساب غير مشترك أو `PA_MEMORY_SHEET_ID` فارغ | شارك الورقة بحساب الخدمة (Editor) |
| `WhatsApp notify: notify_disabled` | `PA_WAAPI_NOTIFY=0` (الافتراضي الآمن) | لا شيء — إشعارات واتساب مرحلة منفصلة (§6) |

## 6. الرفيق واتساب (غير مفعّل — بوابات متتابعة)

المسار موجود (`src/connectors/waapi.py::notify_approval` → `send_text` بوابتان) لكنه **مغلق عمداً**:
1. إقرار مالي: اشتراك $10/شهر/رقم (المرحلة 0 في `docs/WAAPI-INTEGRATION-PLAN.md`) ·
2. ربط instance (مسح QR في waapi.app) ·
3. إعادة `waapi-live-probe` حتى `LIVE CONNECTION VERIFIED` ·
4. انسخ `WAAPI_API_TOKEN` من Actions إلى `.env` يدوياً + اضبط `WAAPI_OWNER_CHAT_ID` ·
5. أعد `PA_WAAPI_NOTIFY=1` (المرحلة 4: إرسال حي واحد للإثبات).

حتى تلك اللحظة: `PA_WAAPI_NOTIFY=0` و`WAAPI_API_TOKEN=` فارغ = **صفر إرسال** (مُثبَّت تجريبياً أعلاه).

## 7. أرقام التحقق السريعة

| الفحص | النتيجة |
|---|---|
| `python3 -m unittest tests.test_approval_bot tests.test_approval_bot_ci` | **39 OK** |
| تجربة fail-closed حية (4 مسارات) | **كلها رفضت، صفر شبكة، صفر رسائل** |
| `python3 scripts/approval_bot_ci.py --mode test\|run` بلا أسرار | **rc=2** + أسماء الناقصة فقط (لا قيم) |
| `git check-ignore .env` | `.gitignore:4` ✓ |
| توكنات في المستودع/السجلات | **لا** (أسماء فقط في أي مكان) |

## 8. التشغيل المتكامل المجاني عبر Actions (المُوصى به)

المسار الذي يعمل **مجاناً بالكامل** (بوت تيليجرام مجاني + مستودع عام = دقائق Actions غير محدودة) **ومن أسرار Actions مباشرة** — بلا أي تشغيل محلي:

| المكوّن | الملف |
|---|---|
| الـworkflow (كل 5 دقائق + تشغيل يدوي) | `.github/workflows/approval-bot.yml` |
| المُشغّل (test/run، preflight مغلق) | `scripts/approval_bot_ci.py` |
| اختباراته | `tests/test_approval_bot_ci.py` (14 اختباراً) |

**خطوات التفعيل (مرة واحدة):**
1. أنشئ الأسرار في **Settings → Secrets and variables → Actions** (القيم **هناك** — لا في الشات ولا في ملف):
   - `PA_APPROVAL_BOT_TOKEN` (أو `TELEGRAM_BOT_TOKEN`) — إلزامي
   - `PA_APPROVAL_CHAT_ID` (أو `TELEGRAM_CHAT_ID`) — إلزامي
   - `PA_APPROVAL_ALLOWED_SENDERS` (أو `TELEGRAM_ALLOWED_SENDERS`) — معرّفك بفواصل، وإلا **كل الأصوات مرفوضة**
   - `PA_MEMORY_SHEET_ID` + `GOOGLE_SERVICE_ACCOUNT_JSON` (نص JSON كامل للحساب) — لوضع `run`
2. من تبويب **Actions** → «Approval bot · Telegram» → **Run workflow** → `mode=test` ← يجب أن تصلك رسالة ✅.
3. بعد الدمج إلى `main` يبدأ **الجدول التلقائي** (كل 5 دقائق): بطاقات الموافقة تصل، والأصوات تُطبَّق على الورقة، **state** (البطاقات المُرسلة + offset تيليجرام) يبقى في `actions/cache` فلا تكرار ولا إعادة تصويت.

**ضمانات:** `permissions: contents: read` فقط · `concurrency` بلا إلغاء وسط تصويت · الأسرار عبر `env:` (تُخفَّي تلقائياً في السجلات) · واتساب `PA_WAAPI_NOTIFY=0` (مدفوع — راجع §6) · عند نقص أي سرّ: **rc=2 وأسماء فقط**.

**ملاحظتان:** الجدول التلقائي يتوقف بعد 60 يوماً من خمول المستودع (يفعّله GitHub بزر) · وباء state نادر: إعادة إرسال بطاقة واحدة فقط إذا فشل حفظ الـcache بعد إرسال ناجح.
