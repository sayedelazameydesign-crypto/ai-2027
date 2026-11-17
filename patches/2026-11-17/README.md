<div dir="rtl">

# 🩹 حِزم الإصلاح — 2026-11-17

كل ما هنا **مُتحقَّق**: شُغِّل `git apply --check` على نسخة طازجة من كل مستودع أصلي، والنتيجة **6/7 نجحت** والـ7 (رخصة `new-agent`) لا يمكن تمثيلها كـpatch لأن ملفها الأصلي **4 بايت بلا سطر جديد** ويحتوي `NUL` — فيُعالَج بالسكربت.

## لماذا patches وليست تعديلات مباشرة؟

التوكن المتاح لهذه الجلسة هو **GitHub App installation token**: القراءة تعمل على كل المستودعات، والكتابة **مرفوضة 403** على كل شيء عدا `ai-2027`. أُثبت ذلك بمحاولة دفع حقيقية:

```
remote: Permission to sayedelazameydesign-crypto/agi-system.git denied
fatal: ... error: 403
```

> بديل مرفوض: تعديل الملفات داخل `projects/` محليًا. هذا يكسر ضمانة "أرشيف بايت-ببايت" ويكذب على المصدر — وأي أداة تدقيق كانت ستكشفه.

## كيفية التطبيق

```bash
# 1) سلّم الحِزم على المستودعات (بعد استنساخها بتوكن فيه صلاحية كتابة)
cd /path/to/agi-system      && git apply /path/to/ai-2027/patches/2026-11-17/agi-system/core__policy.py.patch
cd /path/to/12pro           && git apply /path/to/ai-2027/patches/2026-11-17/12pro/certification__v1.0.0-raw__health-raw.json.patch
cd /path/to/ELAZAMET1       && git apply /path/to/ai-2027/patches/2026-11-17/ELAZAMET1/*.patch
cd /path/to/celia-pro-deployment && git apply /path/to/ai-2027/patches/2026-11-17/celia-pro-deployment/*.patch
cd /path/to/platform        && git apply /path/to/ai-2027/patches/2026-11-17/platform/*.patch

# 2) أو نفّذ كل شيء بأمر واحد (يشمل LICENSE و env.local)
GH_TOKEN=<token-with-write> python3 /path/to/ai-2027/scripts/upstream_fixes_2026-11-17.py --apply
```

بعدها داخل `ai-2027`: `python3 scripts/verify_archive_identity.py` — المتوقع **48/48** بلا أي انحراف (لأن المصدر صار مطابقًا).

## جدول الحِزم

| المستودع | الملف | البند | تحقق `git apply --check` |
|---|---|---|---|
| `agi-system` | `core/policy.py` | تقوية العزل عبر المنصات (+41 سطرًا) | ✅ |
| `12pro` | `certification/v1.0.0-raw/health-raw.json` | JSON صالح مع الحفاظ على الحمولة | ✅ |
| `ELAZAMET1` | `QUICK_DEPLOY.md` | رابط 404 → placeholder | ✅ |
| `ELAZAMET1` | `DEPLOYMENT_GUIDE_KOYEB.md` | رابط 404 → placeholder | ✅ |
| `celia-pro-deployment` | `خطوات-إتمام-النشر-المجاني.md` | onrender Not Found → placeholder | ✅ |
| `platform` | `deploy/render/README.md` | onrender Not Found → placeholder | ✅ |
| `new-agent` | `LICENSE` | 4 بايت تالفة → إشعار PENDING | ➖ بالسكربت (الملف ليس نصًا صالحًا) |
| `ai-store2` | `env.local` | تعمية مفتاح مكشوف | ➖ **لا patch مقصود**: الحزمة كانت ستكتب المفتاح الحقيقي في مستودع ثانٍ |

## ⚠️ تحذير أمني غير قابل للترقيع

تعمية `ai-store2/env.local` **لا تُبطل** المفتاح: هو موجود في تاريخ Git وفي HEAD الحالي. الإجراء الوحيد الفعّال هو **إبطاله** من [AI Studio](https://aistudio.google.com/apikey) — وهذا يحتاج حسابك، ولا يمكن لأي أداة هنا أن تفعله.

</div>
