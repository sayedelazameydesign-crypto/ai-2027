<div dir="rtl">

# الحلقة الثانية — لماذا اعتمدنا «المجانية أولاً» وحظرنا الادعاءات غير المختبرة

**Zero-Cost First: why we made "free" a build rule, not a budget line**

| البند | القيمة |
|---|---|
| **السلسلة** | 3 حلقات — هذه الحلقة **2 من 3** |
| **الحلقة السابقة** | `docs/EPISODE-01-HONEST-INVENTORY.md` |
| **المصدر الحاكم** | البرومبت العمومي v1.2 — §10 (`docs/ENGINEERING-INVENTORY-2027.md`) |
| **المرجع المقاس** | `docs/ZERO-COST-PLAYBOOK-2027.md` §0 |
| **الأرقام** | `2,054` اختباراً · `100` هجوم · `48` = `1/40/6/1` · `13` = `5/8/0` — لا غيرها |

---

## المقدمة — الاعتراض المتوقع

أول ما يُقال لأي مشروع يبدأ بقاعدة «لا دفع»:

> «هذه قاعدة تسويقية. ستُلغى عند أول فاتورة.»

الردّ في هذا النظام ليس وعداً، بل **قاعدة مكتوبة في البرومبت العمومي نفسه** — القسم العاشر. وأي وكيل يعمل بهذه المنظومة مُلزَم بها. هذا الفرق بين نيّة وقيد.

---

## القاعدة، كما هي — أربعة بنود

| البند | ما يفرضه فعلياً |
|---|---|
| **Free-by-Default** | مرحلة التأسيس مجانية 100%: تنفيذ محلي، أو أدوات مفتوحة المصدر، أو طبقة مجانية حقيقية. |
| **Unify Honestly** | كل مكوّن يبقى بتصنيفه: `PROVEN` · `DECLARED` · `SIMULATED` · `FAILED` · `BLOCKED`. الدمج لا يمحو التصنيف. |
| **Execution Alignment** | ما لا يمكن تشغيله مجانياً أو محلياً يُعلَن قبل البدء — لا بعد إنفاق الوقت. |
| **Free-Gate Before Adoption** | أي أداة أو خدمة جديدة تُفحص عبر بوابة المجانية **قبل** الاعتماد، لا بعده. |

> **شفافية:** البند الرابع لم يكن في الصياغة الأصلية للقاعدة؛ أُضيف صراحةً لتحويل شرط «المجانية» من مبدأ إلى **فحص قابل للتنفيذ**. مُسجَّل في §10 من البرومبت v1.2.

---

## البوابة أثناء العمل — من عبر ومن لم يعبر

هذا ليس تنظيراً. البوابة طُبِّقت فعلياً على الأدوات المستخدمة في هذا المستودع:

| الأداة | التكلفة | القرار |
|---|---|---|
| GitHub — المستودع العام | `$0` | **عبرت** — منشور فعلاً |
| لوحة الجرد الذاتية (`engineering-inventory-2027.html`) | `$0` | **عبرت** — تعمل من الملف بلا أصول خارجية |
| Python + `unittest` / `pytest` | `$0` | **عبرت** — بيئة الجرد نفسها |
| `command-hud/audit.py` · `redteam.py` | `$0` | **عبرت** — أنتجا `48` و`100` |
| OBS Studio | `$0` (مفتوح المصدر) | **عبرت كأداة** — لكن التسجيل الفعلي **لم يُنفَّذ بعد** |
| استضافة ثابتة (GitHub Pages) | `$0` | **خيار متاح** — لم يُفعَّل، فلا يُدَّعى |
| أي أداة تتطلب بطاقة ائتمان للبدء | مدفوعة | **`BLOCKED`** — لا استثناء |

**الملاحظة الصادقة:** العمود الأخير مهم أكثر من الأولين. «عبرت» تعني «يمكن استخدامها»، لا «استُخدمت وأثبتت». الفرق بينهما هو الفرق بين هندسة وتسويق.

---

## لماذا هذا ميزة تنافسية لا قيداً

| الوضع الشائع | ما يفعله هذا النظام |
|---|---|
| ادعاء جاهزية بلا تشغيل | `48` مشروعاً ← **`1` فقط `PROVEN`** — والباقي مُصنَّف علناً |
| «يعمل عندي» | `2,054` اختباراً نُفِّذت · `0` فشل |
| «آمن» | `100` هجوم red-team · `0` اختراق (91 DENIED + 9 CONTAINED) |
| «كل شيء جاهز» | `40 DECLARED` · `6 SIMULATED` · `1 FAILED` — بالأسماء |
| ادعاءات من مسودة قديمة | `21` ادعاءً فُحص ← `15` غير موجود أو مُصحَّح |

النتيجة: `1` من `48` يبدو رقماً ضعيفاً في عرض تسويقي، وقوياً جداً في محادثة مع مهندس. نحن نستهدف الثانية.

---

## ما لا تعنيه هذه القاعدة

- **ليست رفضاً للربح.** الكتيّب يحدد مسار ربح تدريجياً (خدمات تطبيقية · وحدات مخصصة · دعم اختياري) — لكنه يبدأ بعد التأسيس، لا قبله.
- **ليست مجانية أبدية.** هي **مجانية في مرحلة التأسيس**. المرحلة التالية تُقاس بقدرتها على تمويل نفسها، لا بقرار مسبق.
- **لا تعني أن كل شيء مجاني مُثبت.** `40` مشروعاً `DECLARED` لم تُشغَّل بعد — بعضها يحتاج `npm ci` وشبكة. تبقى `DECLARED` حتى تُنفَّذ.

---

## جرّب البوابة بنفسك

```bash
git clone https://github.com/sayedelazameydesign-crypto/ai-2027.git
cd ai-2027

# 1) اقرأ القاعدة نفسها
#    docs/ENGINEERING-INVENTORY-2027.md — §9 ثم §10

# 2) أعد إنتاج أرقام خط الأساس (قراءة-فقط · $0)
python3 command-hud/audit.py
python3 command-hud/redteam.py --markdown
```

كل ما تحتاجه: `python3` ومستودع مستنسخ. لا اشتراك، لا بطاقة، لا خادم.

---

## الخاتمة

المجانية هنا ليست شعاراً، بل **بوابة تُغلق الباب على ما لا يمكن اختباره مجاناً**. والسبب عملي بحت: ما لا تستطيع تشغيله بنفسك، لا تستطيع التحقق منه — وما لا تستطيع التحقق منه، لا يحق لك ادعاؤه.

**الحلقة القادمة:** كيف نُرقّي مكوّناً من `DECLARED` إلى `PROVEN` — بالخطوات والدليل، لا بالتصريح.

</div>

---

<div dir="ltr">

## Episode 2 (EN) — Zero-Cost First: why we made "free" a build rule, not a budget line

**The objection:** "Zero-cost is a marketing line. It dies at the first invoice."
**The answer:** it is not a promise here — it is a written constraint in the master prompt itself (§10), and every agent working inside this system is bound by it.

**The rule, in four clauses:** *Free-by-Default* (the foundational phase runs 100% free: local, open-source, or a genuinely free tier) · *Unify Honestly* (labels survive unification: PROVEN · DECLARED · SIMULATED · FAILED · BLOCKED) · *Execution Alignment* (anything that cannot run free or locally is declared before work starts, not after) · *Free-Gate Before Adoption* (every new tool passes the free gate before adoption). The fourth clause was added explicitly to turn "free" from a principle into an executable check — stated openly in §10 of prompt v1.2.

**The gate applied in practice:** GitHub repo ($0, passed, published) · self-contained dashboard ($0, passed, no external assets) · Python + unittest/pytest ($0, passed, the audit environment itself) · `audit.py` and `redteam.py` ($0, passed — they produced 48 and 100) · OBS Studio ($0 open source — **passed as a tool, but the recording was never made**) · static hosting ($0 — available, not activated, so not claimed) · anything requiring a card to start (**BLOCKED**).

**Honest note:** the last rows matter more than the first. "Passed" means "usable", not "used and proven". That gap is the difference between engineering and marketing.

**Why it competes rather than constrains:** 48 projects, only **1 PROVEN** — stated publicly. 2,054 tests, 0 failures. 100 red-team attacks, 0 breaches (91 DENIED + 9 CONTAINED). 40 DECLARED · 6 SIMULATED · 1 FAILED — named. 21 claims checked, 15 corrected or not found. "1 of 48" is a weak marketing number and a strong engineering one. We target the second.

**What it does not mean:** not a rejection of revenue (the playbook defines a gradual path that starts after the foundation, not before) · not free forever (free in the foundational phase; the next phase must fund itself) · not "everything free is proven" (40 DECLARED projects have not been executed).

**Try the gate yourself:** clone the repo, read §9–§10 of the inventory, then run `python3 command-hud/audit.py` and `python3 command-hud/redteam.py --markdown`. Requires only `python3` and a clone — no subscription, no card, no server.

**Next episode:** how we promote a component from `DECLARED` to `PROVEN` — with steps and evidence, not assertion.

</div>
