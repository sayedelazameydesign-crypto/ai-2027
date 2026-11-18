<div dir="rtl">

# 🔐 سياسة الأمان

## الإبلاغ عن ثغرة

افتح Issue خاصًا أو استخدم Security → Report a vulnerability على GitHub.
لا تفتح Issue عامًا يحتوي مفتاحًا أو بيانات حساسة.

**الرد المتوقع:** إقرار خلال 72 ساعة، وتصحيح أو توضيح مكتوب.

---

## حادثة مفتوحة — مفتاح Gemini مكشوف

| البند | التفصيل |
|---|---|
| **المكان** | `sayedelazameydesign-crypto/ai-store2` → `env.local` (HEAD + تاريخ Git) |
| **النوع** | `NEXT_PUBLIC_GEMINI_API_KEY=AIza…` بصيغة مفتاح حقيقي |
| **الحالة في هذا المستودع** | معطَّل (D-2 في [`docs/DEVIATIONS.md`](docs/DEVIATIONS.md)) |
| **الحالة في المصدر** | 🔴 **مكشوف** — التوكن المتاح للجلسة لا يملك صلاحية كتابة هناك (403) |
| **الإجراء الوحيد الفعّال** | **إبطال المفتاح** من [AI Studio](https://aistudio.google.com/apikey) — تتبَّع في [#21](https://github.com/sayedelazameydesign-crypto/ai-2027/issues/21) |

> التعمية ليست إبطالًا. أي شخص يقرأ تاريخ المستودع يملك المفتاح حتى يُبطَل.

### كيف نتعامل مع الأسرار هنا

- `.env`, `.env.local`, `env.local` في `.gitignore` — ولو ظهر أحدها في `git status` فهذا عيب يجب الإبلاغ عنه.
- الفحص الدوري: `python3 command-hud/audit.py` يفحص أنماط المفاتيح ويصنّف ما يجده.
- أمثلة الاختبار مسموحة فقط إن كانت قيمًا وهمية معروفة (`ghp_thisIsAFakeToken1234`, `AKIAIOSFODNN7EXAMPLE`).

#### الأسرار في GitHub Actions (2026-11-18)

سجلّها وقواعدها في **[`docs/SECRETS.md`](docs/SECRETS.md)**، ويُفرَض آليًا:

```bash
python3 scripts/workflow_secret_audit.py --assert     # كل مرجع مسنَد إلى env: ومذكور في السجل
```

- `GEMINI_API_KEY` → وظيفة `live-gemini` (نداء واحد صغير، لا يعمل على PR).
- `RENDER_API_KEY` → `deploy.yml` (تشغيل يدوي فقط + تأكيد باسم الخدمة).
- عند غياب أي سرّ: `::notice::` و**SKIPPED** — لا فشل، ولا ادّعاء نجاح.

---

## حدود العزل في النواة (مقيسة، لا مُفترضة)

| الحد | الحالة |
|---|---|
| خروج من المسار (traversal / absolute / root-write) | ✅ مرفوض — 75 حالة في red-team |
| فواصل Windows والترميز `%2f %5c %2e %00` | ✅ مرفوضة الآن على كل نظام (إصلاح 2026-11-17) |
| أسماء حرفية مثل `NUL` / `...` | ✅ مسموحة — تحلّ داخل الصندوق على كل نظام |
| `live-agent/server.py` مكشوفًا للإنترنت | ⚠️ **بلا مصادقة وبلا rate limit** — استخدم `deployment/live_server.py` مع `AI2027_READ_ONLY=1` |
| المدقّق "المستقل" | ⚠️ مكوّن منفصل **داخل نفس العملية** — ليس حدودًا ضد منفّذ خبيث داخل العملية |
| الذاكرة | ⚠️ كاشفة للتلاعب (hash chain) وليست مانعة — بلا توقيع |

القياس الكامل: `python3 command-hud/redteam.py --markdown --assert` · `docs/GROUND-TRUTH-2026-11-17.md`

---

## الصلاحيات في هذه الجلسة (كما قيست)

| القناة | قراءة | كتابة |
|---|---|---|
| GitHub API | ✅ كل المستودعات العامة | ❌ إلا `ai-2027` (installation token) |
| Vercel connector | ✅ | ❌ `Access denied` |
| إبطال مفاتيح Google | — | ❌ ليس ضمن الأدوات |

</div>
