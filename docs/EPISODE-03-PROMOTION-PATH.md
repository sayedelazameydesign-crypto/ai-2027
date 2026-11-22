<div dir="rtl">

# الحلقة الثالثة — كيف نُرقّي مكوّناً من `DECLARED` إلى `PROVEN`

**الحلقة 3 من 3 — وفيها تصحيح ذاتي منشور عمداً**

| البند | القيمة |
|---|---|
| **السلسلة** | 3 حلقات — هذه الأخيرة |
| **الحلقة السابقة** | `docs/EPISODE-02-ZERO-COST-FIRST.md` |
| **المصدر الحاكم** | `docs/ENGINEERING-INVENTORY-2027.md` — تعريفات الوسوم §4.4 · الأوامر §12 |
| **تنفيذ هذه الحلقة** | نُفِّذ فعلياً: `memory/test_memory.py` · `memory/evaluate.py --markdown` |
| **حالة المستودع** | `HEAD = f2aa9a8` · `projects/` بلا مساس |

---

## المقدمة — لماذا هذه الحلقة تبدأ باعتراف

خططتُ لهذه الحلقة أن تكون درساً نظرياً في الترقية من `DECLARED` إلى `PROVEN`، واخترتُ `memory/` كمثال عملي.

ثم قرأتُ الجرد فعلياً، ونفّذتُ الأوامر. والنتيجة:

> **`memory/` ليس مرشحاً للترقية. هو مصنَّف `PRESENT` صراحةً في الجرد — بتصنيف صحيح.**

مسودتي كانت مبنية على افتراض لم أتحقق منه. لو كتبتُ الحلقة دون تشغيل، لنشرتُ ادعاءً خاطئاً في أول سطر. **هذا بالضبط ما تحاربه المنظومة** — فليكن أول ما يُنشر فيها.

---

## 1. ما يعنيه كل وسم — حرفياً

| الوسم | التعريف الحرفي في التقرير | ما لا يعنيه |
|---|---|---|
| **`PROVEN`** | أمر نُفِّذ في هذه البيئة ونجح، والنتيجة مسجّلة | **ليس** حكماً بالجاهزية للإنتاج ولا بالأمان |
| **`DECLARED`** | الكود/الوثائق موجودة، لكن لم يُنفَّذ الأمر هنا | **ليس** دليلاً على أنه يعمل |
| **`SIMULATED`** | الوضع المُعلن محاكاة صريحة | **ليس** قدرة حيّة |
| **`FAILED`** | خلل محدد ومُوثَّق | **ليس** إخفاءً — بل تسجيلاً |
| **`BLOCKED`** | يحتاج ما لا يمكن تشغيله مجاناً/محلياً | **ليس** فشلاً هندسياً، بل فشلاً بيئياً |

**الخلاصة:** الترقية ليست حكماً على الجودة. هي إجابة على سؤال واحد: **هل نُفِّذ الأمر، وهل النتيجة مسجّلة؟**

---

## 2. التنفيذ الفعلي — كما جرى، لا كما يُتمنّى

### الأمر الأول

```bash
python3 memory/test_memory.py
```

**المخرَج:**

```
Ran 19 tests in 0.998s
OK
```

### الأمر الثاني

```bash
python3 memory/evaluate.py --markdown
```

**المخرَج — منقول حرفياً:**

| Metric | Value |
|---|---:|
| Corpus (real kernel runs) | 20 admitted / 0 refused |
| Noise floor p95 | 0.2 |
| Recall@3 — EASY set (corpus vocabulary) | 16/16 = 1.0 |
| **Recall@3 — HARD paraphrase set** | **11/16 = 0.688** |
| False-positive rate — unrelated | 0/5 = 0.0 |
| **False-positive rate — near-miss** | **0/7 = 0.0** |

### الخمسة التي فشلت — منشورة عمداً

| Query | Wanted | Retrieved |
|---|---|---|
| how much paste is used per hundred garments | pricing | — |
| overhead photography of finished pieces | design | — |
| how long before an order reaches the press | ops | — |
| ملابس نوم قطنية لطيفة على الجلد | arabic | — |
| مراجعة القطع قبل وضعها في الكراتين | arabic | — |

**لماذا هذا هو الجزء المهم:** أداة القياس نفسها تقول إن `EASY 16/16` لا يثبت شيئاً — لأنه يعيد استخدام مفردات المخزون. الرقم الصادق هو `HARD 11/16 = 0.688`، مع نشره للحالات الخمس الفاشلة بالاسم.

---

## 3. التصحيح — ماذا كان خطأً

| ما كتبتُه في المسودة | ما يقوله المستودع |
|---|---|
| «`memory/` مذكور في §12 بلا تصنيف `PROVEN`» | الجرد يصنّفه **`PRESENT`** صراحةً (سطر 173) |
| «مرشح ترقية نموذجي» | **ليس مرشحاً** — هو مثال على تصنيف يعمل صحيحاً |
| — | والجرد يسجّل مسبقاً: `19 اختباراً · recall@3 = 11/16` |

**الدرس:** التصنيف `PRESENT` كان صحيحاً أصلاً. لو رقّيتُه، لكنتُ **أُفسد** تصنيفاً سليماً — لأن `PRESENT` يقول شيئاً أدق من `PROVEN`: يعمل، بحدود مقيسة ومعلنة. الترقية إلى `PROVEN` كانت ستُخفي الحدود.

---

## 4. بروتوكول الترقية — الصحيح، بعد التصحيح

| # | الخطوة | الشرط |
|---|---|---|
| 1 | **اقرأ تصنيفه الحالي أولاً** | ليس كل شيء `DECLARED`. قد يكون `PRESENT` أو `BLOCKED` — ولكلٍّ مسار مختلف |
| 2 | **اختر مرشحاً واحداً** بدليل حاجة حقيقية | لا ترقية لمجرد الترقية |
| 3 | **اجتز بوابة المجانية** | أيعمل محلياً بـ`$0`؟ لا → `BLOCKED` بصدق |
| 4 | **نفّذ الأمر من §12 حرفياً** | لا استنتاج، لا «يعمل عندي» |
| 5 | **انسخ المخرَج كما هو** | نجاح → `PROVEN` · فشل → `FAILED` بوصف الخلل |
| 6 | **صحّح أي ادعاء قديم** | يُضمّ إلى سجل التصحيحات `21 → 15` |

**قاعدة صارمة:** `README` لا يرقّي شيئاً. هو `DECLARED` بحكم التعريف.

---

## 5. لماذا المرشحون الحقيقيون `BLOCKED` هنا

| المشروع | ما يقوله المصدر | لماذا لا يُرقّى الآن |
|---|---|---|
| `projects/12pro` | 12 طبقة · 20 صفحة · 24 حزمة | يحتاج `npm ci` + شبكة → **`BLOCKED`** |
| `projects/leader2027` | 298 اختبار وحدة · 49 smoke | نفس السبب → **`BLOCKED`** |
| `projects/cela2027` | يدّعي وكيلاً كاملاً | المصدر يعلن المحاكاة → `SIMULATED` |

بالمقابل، ما نُفِّذ هنا فعلاً وأُثبت: `projects/agi-system` (**104**) · `live-agent/` (**23 + 12**) · `command-hud/` (**19** + `100` هجوم) · `memory/` (**19** اليوم).

**والاعتراف الأهم:** البيئة هنا لا تصل إلا إلى `github.com` و`pypi.org` و`registry.npmjs.org`. لذلك **معظم الـ`DECLARED` يبقى `BLOCKED`** — والقول بذلك أصدق من ادعاء ترقية مستحيلة.

---

## 6. جرّب الترقية بنفسك

```bash
git clone https://github.com/sayedelazameydesign-crypto/ai-2027.git
cd ai-2027

# اختبار الذاكرة (19 اختباراً · $0)
python3 memory/test_memory.py

# القياس — وانظر الرقم الصادق: HARD لا EASY
python3 memory/evaluate.py --markdown

# النواة الحتمية (104 اختباراً)
python3 -m unittest discover -s projects/agi-system/tests -t projects/agi-system
```

على جهازك — بشبكة — يمكنك محاولة ما هو `BLOCKED` هنا. عندها فقط، وبعد تسجيل المخرَج، يصبح `PROVEN`.

---

## الخاتمة

الفارق بين `DECLARED` و`PROVEN` ليس معرفة ولا نيّة — بل **سطر واحد في سجل الأوامر**.

وهذه الحلقة نفسها دليل على القاعدة: كتبتُ افتراضاً، ثم شغّلتُ الأمر، فسقط الافتراض. لو لم أنفّذ، لنشرتُ تصنيفاً خاطئاً. **التنفيذ ليس خطوة في نهاية العمل — هو ما يجعل العمل صادقاً.**

</div>

---

<div dir="ltr">

## Episode 3 (EN) — How we promote a component from `DECLARED` to `PROVEN`

**This episode opens with a published self-correction.**

I planned this episode as a clean lesson on promotion and picked `memory/` as the example. Then I actually read the inventory and ran the commands. `memory/` is explicitly labelled **`PRESENT`** in the report — a correct label. It was never a promotion candidate. My draft rested on an assumption I had not verified; publishing it unrun would have put a false claim in the first line. That is exactly what this system exists to prevent, so it belongs here first.

**What the labels mean, literally.** `PROVEN` = a command ran in this environment and succeeded, result recorded — *not* a production or security claim. `DECLARED` = the code exists, the command was never run here — *not* evidence that it works. `SIMULATED` = declared simulation. `FAILED` = a specific, documented defect — not a concealment. `BLOCKED` = needs something that cannot run free or locally — an environmental limit, not an engineering one.

**What actually ran.** `python3 memory/test_memory.py` → `Ran 19 tests in 0.998s — OK`. `python3 memory/evaluate.py --markdown` → corpus 20 admitted / 0 refused · noise floor p95 0.2 · Recall@3 EASY 16/16 = 1.0 · **Recall@3 HARD 11/16 = 0.688** · false positives 0/5 and 0/7. The five HARD misses are published by name.

**Why the honest number is the HARD one:** the EASY set reuses the corpus vocabulary, so 16/16 only proves the plumbing is connected. 11/16 is what happens when wording deliberately avoids the stored tokens.

**The correction, stated plainly:** `memory/` is `PRESENT` — it works, with measured and published limits. Promoting it to `PROVEN` would have *destroyed* information, because `PRESENT` says something more precise than `PROVEN` does.

**The promotion protocol, corrected:** read the current label first (not everything is `DECLARED`) → pick one candidate with a real need → pass the free gate → run the §12 command verbatim → copy the output as-is (success → `PROVEN`, failure → `FAILED` with the defect named) → fold the correction into the 21→15 log. A README promotes nothing.

**Why the real candidates are BLOCKED here:** `projects/12pro` and `projects/leader2027` both need `npm ci` and network. This environment reaches only `github.com`, `pypi.org`, and `registry.npmjs.org` — so most of the 40 `DECLARED` projects stay `BLOCKED`, and saying so is more honest than claiming an impossible promotion. On your machine, with network, they can be attempted; only after the output is recorded do they become `PROVEN`.

**Conclusion:** the gap between `DECLARED` and `PROVEN` is one line in a command log. This episode is its own proof: I wrote an assumption, ran the command, and the assumption fell. Execution is not the last step of the work — it is what makes the work true.

</div>
