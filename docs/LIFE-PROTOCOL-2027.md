<div dir="rtl">

# بروتوكول الحياة 2027 — من الهندسة إلى اليوم

**حالة الملف:** أداة عمل، لا دستور. إن صار طقساً، يُحذف.

| البند | القيمة |
|---|---|
| **المصدر** | البروتوكول الخماسي (`docs/DIRECTOR-DIRECTIVES-2027.md`) |
| **النطاق** | الوقت · العمل · العلاقات · الصحة — مجال واحد في كل دورة |
| **التكلفة** | `$0` — ورقة وثلاثة أسئلة |
| **عدد الأدوات** | **ثلاث، لا أكثر** |

> **قاعدة هذا الملف:** لا تحوّل قراراً صغيراً إلى إجراء كبير. أول علامة على فشل المنهج أن تصبح أدواته عبئاً.

---

## الفرق الجوهري — ولماذا لا تُنسخ الهندسة كما هي

| في المستودع | في اليوم |
|---|---|
| الاختبار يُعاد بلا كلفة | الساعة لا تُعاد |
| `git revert` متاح | القرار لا يُستَرجع |
| الحدود معروفة مسبقاً | الطاقة تُكتشف بعد استنزافها |

**النتيجة:** البروتوكول الخماسي يُستخدم للقرارات المؤثرة فقط. الباقي يُدار بقاعدة واحدة.

---

## الأداة ١ — تصفية القرار

| الحجم | المعيار | الإجراء | الزمن |
|---|---|---|---|
| **روتيني** | يتكرر · خطؤه رخيص | قاعدة واحدة ثابتة | `0` دقيقة |
| **متوسط** | أثره يمتد لأيام | هدف + خطوة أولى | `5` دقائق |
| **مؤثر** | يصعب التراجع عنه | المراحل الخمس كاملة | `25` دقيقة |

**مقياس الفشل:** تجاوز زمن التخطيط `10%` من زمن التنفيذ → أوقف الأداة.

---

## الأداة ٢ — ثلاث كتل يومية

| الكتلة | المحتوى | القيد |
|---|---|---|
| **الأثر** | مهمة واحدة | قبل أي رسالة · بلا مقاطعة |
| **الصيانة** | بريد · إداري · تواصل | بوقت محدَّد |
| **الاستشفاء** | راحة · حركة · نوم | ليست ترفاً — مورد إنتاج |

**قاعدة السلامة:** نقص النوم ليلتين متتاليتين → **تُلغى كتلة الأثر**. لا استثناء.

---

## الأداة ٣ — مراجعة أسبوعية (لا يومية)

**مراجعة يومية تُلغي التنفيذ.** ثلاثة أسئلة، لا أكثر:

1. ما الذي أنجزته **بأثر** قابل للقياس؟
2. ما الذي استهلك وقتاً **بلا أثر**؟
3. ما **القاعدة الواحدة** التي أُغيّرها؟

---

## العتبات — مُشتقّة، لا مُخترعة

**تصحيح موثّق:** في النسخة الأولى وضعتُ حداً مطلقاً لوقت الأثر (`30%`). جرّبتُ الحساب على يوم بنسبة `28.6%` — رفضه الحدّ بفارق `1.4` نقطة مئوية. **الحدّ المطلق كان خاطئاً.**

| المؤشر | كيف يُحدَّد |
|---|---|
| **وقت الأثر** | متوسط أسبوعك الأول `+20%` — لا رقم من عندي |
| **زمن التخطيط** | `< 10%` من زمن التنفيذ (حدّ ثابت — التخطيط ليس مورداً متغيّراً) |
| **عدد الأدوات** | `≤ 3` (حدّ ثابت) |
| **النوم** | `≥ 5` ليالٍ من `7` |

---

## القالب اليومي — ٧ أسطر، ٣ دقائق

```
التاريخ:
وقت الأثر (د):
الوقت الكلي (د):
وقت التخطيط (د):
نوم كافٍ (نعم/لا):
كتل أثر ناجحة (/3):
عدد الأدوات:
ملاحظة سطر واحد:
```

### مثال محسوب (بيانات توضيحية، لا مقيسة)

| اليوم | وقت الأثر | الكلي | تخطيط | نوم | كتل | أدوات | الحكم |
|---|---|---|---|---|---|---|---|
| A | 120 | 420 | 15 | 5/7 | 15/20 | 2 | مراجعة (تخطيط `2.9%` سليم · أثر `28.6%` قريب من العتبة) |
| B | 40 | 480 | 70 | 3/7 | 8/20 | 5 | **مراجعة — ثلاث إخفاقات** |

**الدرس من B:** التخطيط `17.1%` والتخطيط الزائد هو ما أكل وقت الأثر. العَرَض والسبب في الرقم نفسه.

---

## خطة ٣٠ يوماً — أداة واحدة كل أسبوع

| الأسبوع | الأداة | المخرج | المحظور |
|---|---|---|---|
| **١** | لا شيء — قياس فقط | نسبة وقت الأثر الحقيقية | أي عادة أو نظام جديد |
| **٢** | تصفية القرار | صفر قرارات مُغلَّفة بإجراء أكبر من حجمها | زيادة عدد الأدوات |
| **٣** | كتلة الأثر | `5` كتل ناجحة من `7` | التضحية بالنوم للإنجاز |
| **٤** | التقليم — حذف أداة | أداة محذوفة | إضافة أداة بدلاً منها |

---

## شروط الإيقاف — قاطعة

توقف وأعد التصميم عند أي من:

- انقطاع السجل **٣ أيام** متتالية
- تخطيط `> 15%` لأسبوع كامل
- عدد الأدوات `> 3`
- **الشعور بأن المراجعة صارت العمل نفسه**

**الأخير ليس فشلاً — بل الإشارة الأهم.**

---

## ما لا يدخله هذا البروتوكول

| مستبعَد | السبب |
|---|---|
| تتبع كل ساعة | يُنتج بيانات لا قرارات |
| تطبيق أو اشتراك | `$0` — الورقة تكفي |
| أربعة مجالات في وقت واحد | تفشل الأربعة |
| تغيير العادة والنظام معاً | متغيّران، فلا يُعرف أيّهما أثّر |

</div>

---

<div dir="ltr">

## Life Protocol 2027 (EN) — from engineering to the day

**Status: a working tool, not a constitution. If it becomes a ritual, delete it.**

**Why the engineering method cannot be copied as-is:** in a repo a test is free to re-run, `git revert` exists, and limits are known in advance. In a day, the hour is not refunded, a decision cannot be reverted, and energy is discovered only after it is spent. So the five-stage protocol is used for **consequential decisions only**; everything else is handled by one rule.

**Tool 1 — decision filter.** Routine (repeats, cheap to get wrong) → one rule, 0 minutes. Medium (effect lasts days) → goal + first step, 5 minutes. Consequential (hard to undo) → all five stages, 25 minutes. *Failure signal:* planning exceeds 10% of execution time → stop using the tool.

**Tool 2 — three daily blocks.** Impact (one task, before any message, uninterrupted) · Maintenance (inbox, admin, bounded time) · Recovery (rest, movement, sleep — an input, not a luxury). *Safety rule:* two consecutive short nights → the Impact block is cancelled. No exception.

**Tool 3 — a weekly review, never daily.** A daily review cancels execution. Three questions: what produced measurable impact · what consumed time with no impact · which single rule changes.

**Thresholds are derived, not invented.** My first version set an absolute 30% impact floor; running a day at 28.6% got rejected by 1.4 points, which proved the absolute number was wrong. The impact threshold is now *your first week's average +20%*. Planning stays fixed at <10%, tools at ≤3, sleep at ≥5 of 7.

**30-day plan — one tool per week.** Week 1: measure only, add nothing. Week 2: decision filter. Week 3: impact block. Week 4: pruning — delete one tool. Adding three tools at once means adding none.

**Stop conditions.** Three consecutive days without a log · planning >15% for a full week · more than three tools · the review itself becoming the work. The last is not a failure — it is the most important signal.

</div>
