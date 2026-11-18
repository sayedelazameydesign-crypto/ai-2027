<div dir="rtl">

# 🔀 الانحرافات الموثّقة عن الأرشيف (Deviations)

**القاعدة الأصلية:** كل ملف داخل `projects/` نسخة بايت-ببايت من المستودع الأصلي، والتدقيق في `AUDIT.md` يعتمد على ذلك.

**الواقع بعد 2026-11-17:** **46 من 48** مطابقة بايت-ببايت، و**انحرافان مقصودان** مذكوران أدناه (`D-1`, `D-2`). أي انحراف آخر يجب أن يُسجَّل هنا **قبل** دمجه، وإلا سقطت الضمانة.

---

## D-1 · `projects/agi-system/core/policy.py` — تقوية العزل عبر المنصات

| البند | التفصيل |
|---|---|
| **الأصل** | blob `20aafaddb02e6808acbceea73a1d17c6edd8e61b` (المستودع `sayedelazameydesign-crypto/agi-system`) |
| **النسخة المعدّلة** | blob `4d0f7ce8d60c8ef3d8f63a58540edabce2ae8817` |
| **حجم التغيير** | +41 سطرًا، **صفر تعديل** على منطق قائم، صفر اعتماديات جديدة |
| **السبب** | `Path.resolve()` يفسّر الفاصل على POSIX فقط: `..\escape.txt` و`C:\Windows\win.ini` و`%2e%2e/x` كانت **أسماء ملفات حرفية** على Linux، لكنها **فواصل مسارات على Windows** أو بعد أي URL-decode. حصر يعمل على نظام واحد ليس حصرًا. |
| **الإصلاح** | `SandboxPolicy._cross_platform_reason()`: رفض `\` · بادئة مشغّل Windows (`C:`) · الفواصل المشفّرة `%2f %5c %2e %00` — على **كل** نظام تشغيل، قبل أي `resolve()` |
| **التحقق** | 86/86 اختبار نواة · 6/6 wrapper · red-team: **91 DENIED · 9 CONTAINED · 0 BREACH** · 10/10 ضوابط سليمة · كل البوابات ✅ |
| **الأثر على الادعاء العام** | ادعاء "48/48 بايت-ببايت" أصبح **47/48 + انحراف موثّق** |
| **⚠️ الحالة** | الإصلاح **محلي فقط**: الدفع إلى `agi-system` رفض بـ **403 (Resource not accessible by integration)** — التوكن هنا للقراءة. سكربت التطبيق جاهز: `scripts/upstream_fixes_2026-11-17.py` |

**التراجع (rollback):**
```bash
git checkout 4d8c8dd -- projects/agi-system/core/policy.py
```

---

## D-2 · `projects/ai-store2/env.local` — تعمية مفتاح مكشوف

| البند | التفصيل |
|---|---|
| **التغيير** | `NEXT_PUBLIC_GEMINI_API_KEY=AIza…` → `NEXT_PUBLIC_GEMINI_API_KEY=REDACTED-rotate-this-key-see-AUDIT.md` |
| **السبب** | مفتاح حقيقي (صيغة `AIza`) مرفوع في المستودع الأصلي. إبقاء نسخة ثانية منه هنا يضاعف التعرض بلا فائدة |
| **الأثر** | انحراف عن التطابق البايتي — مقصود ومعلن (مذكور أصلًا في `AUDIT.md`) |
| **⚠️ لم يُحلّ** | المفتاح **ما زال في تاريخ** المستودع الأصلي وعلى HEAD. التعمية ليست إبطالًا: **أبطِل المفتاح** من AI Studio |

---

## إصلاحات مستحقة على المصدر — **محجوبة بـ 403**، لا تنفيذ هنا

| # | المستودع | الملف | الإصلاح | سبب عدم التنفيذ |
|---|---|---|---|---|
| U-1 | `agi-system` | `core/policy.py` | نفس D-1 | 403 |
| U-2 | `new-agent` | `LICENSE` | الملف 4 بايت (`NUL` + `U+FFFD`) → إشعار "PENDING — لا ترخيص" | 403 |
| U-3 | `12pro` | `certification/v1.0.0-raw/health-raw.json` | رأسه ليس JSON (`--- Health ---`) → JSON صالح مع الحفاظ على الحمولة | 403 |
| U-4 | `ai-store2` | `env.local` | مفتاح Gemini مكشوف → تعمية (HEAD فقط؛ **التاريخ يبقى** → الإبطال إلزامي) | 403 |
| U-5 | `ELAZAMET1` | `QUICK_DEPLOY.md`, `DEPLOYMENT_GUIDE_KOYEB.md` | `elazamet1.vercel.app` (404) → placeholder `YOUR-APP` | 403 |
| U-6 | `celia-pro-deployment` | `خطوات-إتمام-النشر-المجاني.md` | `celia-pro.onrender.com` (Not Found) → placeholder + ملاحظة | 403 |
| U-7 | `platform` | `deploy/render/README.md` | `nawah-agent.onrender.com` (Not Found) → placeholder | 403 |

**لماذا لم تُعدّل داخل `projects/`؟** لأنها أرشيف مرآة: تعديلها محليًا يجعل النسخة تكذب على المصدر بلا سبب، ويكسر الضمانة لصالح تجميل. الإصلاح الصحيح مكانه المصدر، والانحراف الوحيد المسموح هنا هو ملف **الكيرنل الذي نُشغّله فعلًا** (D-1).

**التطبيق عند توفر توكن كتابة:**
```bash
GH_TOKEN=<token-with-repo-write> python3 scripts/upstream_fixes_2026-11-17.py --apply
python3 scripts/verify_archive_identity.py     # يجب أن يطبع 48/48
```

أو يدويًا بحِزم جاهزة ومختبَرة: [`patches/2026-11-17/`](../patches/2026-11-17/README.md) —
`git apply --check` نجح على **6/7** من نسخ طازجة (الرخصة تُعالَج بالسكربت وحده).

### ملاحظة (2026-11-18) · الملفات المولَّدة آليًا لا تُقاس بالبايت

`verify_archive_identity.py` أعاد في 2026-11-18: **44 مطابق · 2 انحراف موثّق (`D-1`, `D-2`) ·
1 انحرف · 1 محجوب (502)**.

- **المنحرف:** `easy-ai/data/manifest.json` — المصدر **أعاد توليده** في `2026-11-17T22:20:22Z`
  بعد أن زامنّاه في `13:21:22Z`. الملف يحمل `generated_at` و`run_id` داخله، فمطابقته **لحظية بطبيعتها**.
- **الحكم:** هذا **ليس انحرافًا صنعناه** ولا تلاعبًا بالمصدر — بل مخرج مولَّد يتحرك وحده.
  القاعدة الصحيحة: التطابق البايتي يُدَّعى على **الملفات الثابتة**، وتُعلَن الملفات المولَّدة استثناءً صريحًا.
  بعد أي مزامنة جديدة، شغّل `python3 scripts/verify_archive_identity.py` قبل كتابة أي رقم «48/48».
- **المحجوب:** `projects/WEB` — بيانات المستودع تُقرأ، لكن `contents` API يرجّع 502 في كل المحاولات
  ⇒ **غير محسوب** ضد التطابق، ولا يُقال عنه «مطابق» بلا دليل.

### ما أُثبت عن الصلاحيات (2026-11-17)

| القناة | القراءة | الكتابة | الدليل |
|---|---|---|---|
| GitHub (installation token) | ✅ كل المستودعات العامة | ❌ **403** لكل ما عدا `ai-2027` | `git push` إلى `agi-system` → `Permission ... denied` |
| GitHub Pages (إعدادات) | ✅ | ❌ 403 | `PUT /repos/.../pages` |
| Vercel (connector) | ✅ (`0 مشروع` في الحساب) | ❌ `Access denied ... token may not have the required permissions` | `create_project` |
| Google AI Studio (إبطال مفتاح) | — | ❌ ليس ضمن الأدوات | — |

لذلك: ما يمكن تنفيذه داخل `ai-2027` **نُفِّذ**، وما يحتاج صلاحية خارجية **جُهِّز وتُوثِّق** في
[#21](https://github.com/sayedelazameydesign-crypto/ai-2027/issues/21) ·
[#22](https://github.com/sayedelazameydesign-crypto/ai-2027/issues/22) ·
[#23](https://github.com/sayedelazameydesign-crypto/ai-2027/issues/23).

</div>
