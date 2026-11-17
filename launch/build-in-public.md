# 📡 Build in Public — Proof-First series

> قاعدة 2027: الخوارزميات تكافئ **الشفافية الجذرية والكود القابل للتشغيل فورًا**،
> وتقتل المحتوى الترويجي الفارغ. لذلك كل منشور في هذه السلسلة يجب أن يحمل:
> **رقمًا مقاسًا + الأمر الذي أنتجه + ما فشل في هذه الدفعة**.
>
> **القاعدة الذهبية:** لا منشور بلا صف في [`PROOF-LEDGER.md`](PROOF-LEDGER.md).
> الأرقام أدناه من تشغيل `python3 scripts/generate_launch_evidence.py --assert`
> يوم 2026-11-17 على الكوميت `9ad27fc`.

---

## 🚀 المنشور الأول — جاهز للنسخ (X / LinkedIn / HN comment)

**English version:**

```
I shipped a deterministic agent kernel whose only rule is: no claims without evidence.

Measured today (regenerate in 1 command):
  VERIFIED full cycle   p50 7.416 ms  | p95 10.143 ms | spread 94.9% (n=50)
  DENIED at policy wall p50 2.964 ms
  FAILED read           p50 5.598 ms
  3,526 LOC · 124 KB · ZERO third-party imports (AST-checked in CI)
  86 kernel tests + 40 more across the harness = 126 passing
  100 real adversarial inputs → 75 DENIED, 25 contained, 0 breaches, 10/10 controls VERIFIED

Three things that went wrong this week, because shipping only the green numbers is an ad:
1. My dashboard showed a SHA-256 "proof" on a DENIED run — it had hashed the kernel's own metrics.json. Its own test caught it.
2. My p50 chart read 0 ms because control-plane events are 0 ms. Fixed + regression test.
3. A 100-attack suite did NOT return 100/100 DENIED. 25 Windows-style paths became literal filenames inside the sandbox on Linux. Zero escapes, but cross-platform containment is NOT VERIFIED.

No LLM in the loop. The planner is deliberately deterministic — the discipline has to exist before a model gets near it. Phase 2 is still an undecided decision, not a shipped feature.

Repo + reproducible benchmarks: https://github.com/sayedelazameydesign-crypto/ai-2027
Try it in your browser: https://sayedelazameydesign-crypto.github.io/ai-2027/demo.html
One-command evidence: python3 scripts/generate_launch_evidence.py --assert

I'd rather you attack the verifier than upvote this.
```

**نسخة عربية مختصرة:**

```
أطلقت نواة وكيل حتمية بقاعدة واحدة: لا ادعاء بلا دليل.

أرقام مقاسة اليوم (أمر واحد يعيد إنتاجها):
  دورة VERIFIED كاملة   p50 7.416ms | p95 10.143ms | ضوضاء 94.9% (n=50)
  رفض عند جدار السياسة p50 2.964ms
  3,526 سطرًا · 124KB · صفر اعتماديات خارجية (مفحوص بـ AST في CI)
  126 اختبارًا ناجحًا · 100 هجوم: 75 رفض + 25 احتواء + 0 اختراق

وثلاثة أخطاء حقيقية هذا الأسبوع، لأن نشر الأخضر فقط إعلان:
1. لوحاتي عرضت بصمة SHA-256 لتشغيل «مرفوض» — كانت تحسب بصمة metrics.json الداخلي. اختباري اكتشفها.
2. رسم p50 كان يعرض 0ms بسبب أحداث تحكم زمنها صفر. أُصلح وأُضيف اختبار منع تراجع.
3. الـ100 هجوم لم تُرجع «100/100 مرفوض»: 25 مسارًا بنمط Windows تحوّل لأسماء ملفات داخل الصندوق على Linux. صفر اختراق، لكن الاحتواء عبر المنصات غير مُثبت.

لا يوجد نموذج لغوي في الحلقة. المخطط حتمي عن قصد — الانضباط قبل العقل. Phase 2 قرار لم يُحسم بعد.

المستودع + إعادة إنتاج الأرقام: github.com/sayedelazameydesign-crypto/ai-2027
هاجموا المُتحقق بدل التصويت لي.
```

---

## 📅 «سلسلة الهندسة الصادقة» — 12 مقالًا (كل مقال = أصل هندسي قابل للتشغيل)

| # | العنوان المقترح | الأصل المطلوب (لا يُنشر بدونه) | بوابة النشر |
|---|---|---|---|
| 1 | An agent core that is not allowed to say "done" | [`case-study-deterministic-core.md`](case-study-deterministic-core.md) + evidence pack | ✅ جاهز |
| 2 | Why I publish benchmark jitter instead of one number | [`docs/BENCHMARKS.md`](../docs/BENCHMARKS.md) + CI artifacts | ✅ جاهز |
| 3 | 100 attacks, 75 denials, 0 breaches — and the 25 that weren't denials | `command-hud/redteam.py --markdown` | ✅ جاهز |
| 4 | Auditing my own 48-project archive: only 1 is PROVEN | [`docs/REPOSITORY-AUDIT.md`](../docs/REPOSITORY-AUDIT.md) | ✅ جاهز |
| 5 | The SHA-256 that proved nothing (a bug my own test caught) | `command-hud/test_hud.py::test_03/04` + commit | ✅ جاهز |
| 6 | A gate nobody tests is just a claim (Python 3.10 / `tomllib`) | `scripts/test_ci_guard.py` + CI run 44073143702 | ✅ جاهز |
| 7 | Four failed GitHub Pages deploys and what "live" actually means | `pages.yml` + `pages/builds` + `manual-github-settings.md` §6 | ✅ جاهز |
| 8 | Zero dependencies as a supply-chain decision, enforced by AST | `scripts/ci_guard.py` + workflow | ✅ جاهز |
| 9 | What "independent verification" does and does not protect against | `core/verifier.py` + HN playbook Q3/Q11 | ✅ جاهز |
| 10 | Mixed licences: why the wheel is not on PyPI | `pyproject.toml` + Phase-0 inventory | ⏳ يلزم قرار ترخيص |
| 11 | Deterministic planner vs LLM proposer — the Phase 2 decision | **لا يوجد بعد** — يلزم قرار المالك + Spike | ⏳ مؤجل بقرارك |
| 12 | From kernel to a real production line (Pro-Print / CELIA) | **بيانات إنتاج حقيقية فقط** — [`REAL-WORLD-CASE-STUDY.md`](REAL-WORLD-CASE-STUDY.md) | 🔴 لا يُنشر حتى تُقاس |

> المقالات 1–9 جاهزة بالأدلة الموجودة فعلًا في المستودع. 10–12 **ممنوعة النشر** حتى تتوفر بيانات أو قرارات حقيقية.
> هذا المنع نفسه جزء من الاستراتيجية: الشفافية حول ما لا نملكه أقوى من أي ادعاء إضافي.

---

## 🧭 قواعد التشغيل (لتطبيق «Build in Public» لا مجرد كتابته)

1. **انشر الرقم مع الأمر والضوضاء**، لا الرقم وحده.
2. **انشر الفشل في نفس المنشور** الذي تنشر فيه النجاح — لا في تحديث لاحق.
3. **أجب عن كل تعليق تقني** عبر [`hn-comments-playbook.md`](hn-comments-playbook.md).
4. **عند اكتشاف خطأ**: Issue باسم صاحبه → إصلاح باختبار منع تراجع → رابط في الرد.
5. **أعد توليد الأرقام قبل كل إعادة نشر**: `python3 scripts/generate_launch_evidence.py --assert`.
6. **لا تكتب قصة نجاح تجارية بلا قياس حقيقي.** لا عملاء، لا نسب توفير، لا إيرادات مُختلقة.
7. **لا تطلب upvotes.** اطلب الهجوم التقني على المُتحقق.
8. انشر من **حسابك الشخصي** وبصوتك — لا يوجد تكامل لنشر HN/DEV.to/X من هذا الوكيل، ولن أنشر نيابة عنك.
