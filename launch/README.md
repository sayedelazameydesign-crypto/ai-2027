# 🚀 Launch Kit — AI-2027

> **Status: 2026-11-17 — 🚀 READY TO PUBLISH (proof-first track).**
> Focus: الشهرة عبر **الشفافية الجذرية + الكود القابل للتشغيل فورًا + الأرقام المقاسة**،
> لا عبر صفحات ثابتة ولا عناوين تسويقية. Phase 2 (سحابي/محلي/تأجيل) ما زال مؤجلاً بقرارك.
>
> **القاعدة:** لا منشور بلا صف في [`PROOF-LEDGER.md`](PROOF-LEDGER.md).
> كل رقم في أي مادة هنا أُنتج بأمر واحد: `python3 scripts/generate_launch_evidence.py --assert`
> (آخر تشغيل: `9ad27fc` · p50 **7.416ms** · رفض **2.964ms** · 126 اختبارًا · 100 هجوم/0 اختراق).

## أصول النشر (جاهزة)

| الملف | القناة | الحالة |
|---|---|---|
| [`case-study-deterministic-core.md`](case-study-deterministic-core.md) | المقال التقني الأول — DEV.to / Medium / مدونة | ✅ **READY TO PUBLISH** — أرقام حيّة + قسم «ما أخطأتُ فيه» |
| [`show-hn.md`](show-hn.md) | Show HN (عنوان تقني بديل + متن) | ✅ **READY TO COPY** |
| [`hn-comments-playbook.md`](hn-comments-playbook.md) | 14 سؤالًا متشككًا وإجابات صادقة بالأدلة | ✅ READY |
| [`PROOF-LEDGER.md`](PROOF-LEDGER.md) | كل ادعاء ← أمر ← دليل خام + «ادعاءات نرفضها» | ✅ READY |
| [`build-in-public.md`](build-in-public.md) | أول منشور Build in Public (EN+AR) + خطة سلسلة 12 مقالًا ببوابات أدلة | ✅ READY |
| [`producthunt.md`](producthunt.md) | نسخة Product Hunt | DRAFT |
| [`founder-posts.md`](founder-posts.md) | 6 سكربتات مقالات/فيديو | DRAFT |
| [`manual-github-settings.md`](manual-github-settings.md) | إعدادات يدوية موثّقة + ما صار آليًا | READY |
| [`REAL-WORLD-CASE-STUDY.md`](REAL-WORLD-CASE-STUDY.md) | قالب قصة الإنتاج الحقيقي (Pro-Print / CELIA…) | 🔴 **NOT PUBLISHABLE — UNFILLED عمدًا** |
| [`production_evidence.py`](production_evidence.py) | أداة قياس ترفض أي بيانات بلا طريقة قياس ومصدر | ✅ عاملة + 10 اختبارات |

## ✅ أعد إنتاج كل رقم قبل أي نشر

```bash
python3 scripts/generate_launch_evidence.py --assert   # benchmarks + 126 tests + redteam + audit + wheel
python3 launch/test_launch_claims.py -v                # كل رابط/ادعاء له دليل، وإلا يفشل البناء
python3 command-hud/redteam.py --markdown --assert
python3 command-hud/audit.py --report docs/REPOSITORY-AUDIT.md --assert-probes
```

تكتب الأداة `launch/evidence/latest.json` و`REPORT.md` (مستثنيان من Git) وتفشل عند كسر أي بوابة.
**لا تنسخ رقمًا من هنا إلى مقال دون إعادة القياس** — هذه هي الاستراتيجية كلها.

## Pre-publish checklist — actual state

- [x] نواة حتمية + مُتحقق مستقل (86/86 اختبارًا، عقد CLI في CI)
- [x] أرقام مقاسة **مع نشر الضوضاء بجانب كل قياس** → [`docs/BENCHMARKS.md`](../docs/BENCHMARKS.md)
- [x] دراسة حالة تقنية كاملة + سجل ادعاءات/أدلة + خطة تعليقات شفافة
- [x] حزمة 39.1KB قابلة للبناء محليًا، CLI مفحوص في CI (**ليست على PyPI** — ترخيص معلّق)
- [x] CI/CD على كل دفع: 126 اختبارًا + بوابة AST + 100 هجوم + تدقيق 48 مشروعًا + رفع أدلة
- [x] تجربة عامة حيّة: [`website/demo.html`](../website/demo.html) (موسومة كمنفذ JS لا كنواة بايثون)
- [x] Pages يعمل، والرابط مُتحقَّق منه → <https://sayedelazameydesign-crypto.github.io/ai-2027/demo.html>
- [x] **الشفافية الجذرية**: Windows gap، ملف شهادة فارغ، ترخيص تالف، 1/48 PROVEN، وأخطائي الخاصة — منشورة لا مخفية
- [ ] GitHub description + topics + homepage + Discussions (403 موثّق — 4 نقرات يدوية)
- [ ] **بيانات إنتاج حقيقية** قبل أي قصة نجاح تجارية (القالب فارغ عمدًا)
- [ ] قرار ترخيص `agi-system` (يحجب PyPI، لا يمنع HN)
- [ ] فيديو 60–90 ثانية (لـ Product Hunt لا HN)
- [x] `WEB` و`celia-pro-deployment` مستبعدان من أي ادعاء «مفتوح المصدر»

## 🚫 ما لن نفعله (وهو جزء من الاستراتيجية)

- لن أنشر عنك على HN أو X أو DEV.to — لا يوجد تكامل لذلك، والقاعدة: **انشر من حسابك وبصوتك**.
- لن أكتب «100/100 DENIED» ولا «10ms مضمونة» ولا «خفّضنا الأخطاء 100%» ولا قصة عميل/إيراد بلا قياس.
- لن أضيف نموذجًا لغويًا لإبهار العرض — المخطط حتمي، وPhase 2 قرار لم يُحسم.

> **الصدق حول ما لا نملكه أقوى من أي ادعاء إضافي.**
