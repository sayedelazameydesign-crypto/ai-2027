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

### سجل آلي (2026-11-18) · الفرق بين «انحراف» و«مولَّد» و«انحرف»

لم يبقَ هذا القسم نثرًا يُقرأ بالعين: مصدره الآن ملف آلي
[`docs/deviations.json`](deviations.json) يقرأه `scripts/verify_archive_identity.py`،
ويُصنّف كل مشروع إلى خمس حالات **لا تُخلط**:

| الحالة | المعنى | هل هي عيب؟ |
|---|---|---|
| `identical` | نفس blob SHA لكل ملف | لا |
| `deviation` | اختلاف محصور في ملفات هذا السجل (`D-1`, `D-2`) | لا — مقصود ومعلن |
| `generated` | اختلاف محصور في ملف **المصدر نفسه يعيد توليده** | لا — متقلّب بطبيعته |
| `drifted` | أي اختلاف آخر، أو ملف زائد/ناقص | **نعم — الوحيد** |
| `blocked` | تعذّر السؤال **وأثبتت `network_preflight` أن البيئة لا ترى الشبكة** | لا — عمى، لا انحراف |

**مثال حقيقي (2026-11-18):** `easy-ai/data/manifest.json` **أعاد المصدر توليده** في
`2026-11-17T22:20:22Z` بعد أن زامنّاه في `13:21:22Z`، فالـblob تغيّر بلا أن يلمس أحد نسختنا.
التصنيف القديم كان يسمّي هذا «انحرافًا» فيسقط ضمان الأرشيف بلا سبب؛ التصنيف الجديد يسمّيه
`generated` **ويبقى الملف مرئيًا في التقرير** — لا يُخفى، بل لا يُحتسب عيبًا.
وكذلك `projects/WEB` حين يرجّع `contents` API خطأ `502`: يُسجَّل `failed`/`blocked`
**لا** «مطابق» ولا «منحرف».

**لماذا هذا مهم؟** لأن «48/48 مطابق» يجب أن تعني *شيئًا*: أن أي فرق غير مسجّل
سيُفشل `--strict`. أما الملفات التي تتحرك وحدها فتُعلَن صراحةً بدل أن تُلوّث الرقم.

```bash
python3 scripts/verify_archive_identity.py            # جدول: 45 + 2 + 1 · 48/48 accounted
python3 scripts/verify_archive_identity.py --strict   # يفشل على drifted فقط
python3 scripts/verify_archive_identity.py --strict-all  # يفشل أيضًا على blocked/failed
python3 scripts/verify_archive_identity.py --json     # للـCI
python3 scripts/test_verify_identity.py               # 17 اختبارًا للتصنيف نفسه
```

**قاعدة الإضافة:** أي ملف مولَّد جديد يُضاف إلى `generated` في
`docs/deviations.json` **قبل** دمجه، وإلا صار `drifted` وأفشل الفحص — وهذا هو المطلوب.

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
