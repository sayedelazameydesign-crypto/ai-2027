<div dir="rtl">

# سيناريو الجولة المرئية — 3 إلى 5 دقائق (OBS Studio)

**دليل عملي لقطة-بلقطة لتسجيل عرض شاشة — مجاني بالكامل، لا يمس `projects/`**

| البند | القيمة |
|---|---|
| **المدة** | 3–5 دقائق |
| **الأداة** | OBS Studio (مجاني/مفتوح المصدر) — تسجيل شاشة + صوت |
| **المصدر المعروض** | `docs/engineering-inventory-2027.html` (لوحة الجرد) |
| **القاعدة** | كل رقم يُقرأ من الشاشة — لا أرقام من الذاكرة |

> **قبل التسجيل:** افتح اللوحة في المتصفح بملء الشاشة. أغلق الإشعارات. جهّز الصوت. لا تُظهر أي ملف خارج `docs/`.

---

## اللقطات

### اللقطة 1 — الافتتاح (0:00 – 0:30)

| العنصر | التفصيل |
|---|---|
| **ما يُعرض** | عنوان اللوحة + بطاقة «الحكم في ثلاثة أسطر» |
| **ما يُقال** | «هذا جرد هندسي صادق لمنظومة AI-2027. القاعدة: لا رقم بلا أمر نُفِّذ. كل ادعاء مصنّف: PROVEN أو DECLARED أو SIMULATED أو FAILED.» |
| **ما يُفعَل** | تمرير بطيء على البطاقة التنفيذية |

### اللقطة 2 — الأرقام المقيسة (0:30 – 1:30)

| العنصر | التفصيل |
|---|---|
| **ما يُعرض** | بطاقات المؤشرات + مصفوفة الاختبارات |
| **ما يُقال** | «نفّذنا 2,054 اختباراً: صفر فشل. أكبر مجموعة في بروتوكول Python بـ832. وفحص أمان من 100 هجوم: صفر اختراق.» |
| **ما يُفعَل** | التمرير على صفوف المصفوفة (python-2027 · fleet-2027 · kernel-2027) |

### اللقطة 3 — الأرشيف بصدق (1:30 – 2:30)

| العنصر | التفصيل |
|---|---|
| **ما يُعرض** | قسم الأرشيف — التوزيع 1 / 40 / 6 / 1 |
| **ما يُقال** | «48 مشروعاً في الأرشيف، لكن واحداً فقط مُثبت. 40 موثّقة فقط. الأرشفة ليست إنجازاً تشغيلياً — ونقول ذلك علناً.» |
| **ما يُفعَل** | إبراز صفوف جدول التصنيف التشغيلي |

### اللقطة 4 — التصحيحات (2:30 – 3:30)

| العنصر | التفصيل |
|---|---|
| **ما يُعرض** | سجل التحقق والتصحيح (§06) |
| **ما يُقال** | «فحصنا 21 ادعاءً من مسودة سابقة: 15 غير موجود أو مُصحَّح. لا NEXA بتشفير Ed25519، لا توأم رقمي، لا مراقبة RTSP، لا خط وسائط مُنفَّذ. كل غياب مُعلن.» |
| **ما يُفعَل** | التمرير على صفوف السجل — إبراز علامات ❌ |

### اللقطة 5 — قاعدة المجانية والبرومبت (3:30 – 4:30)

| العنصر | التفصيل |
|---|---|
| **ما يُعرض** | قسم «أسطورة 2027» + قسم البرومبت (v1.2) |
| **ما يُقال** | «القاعدة الحاكمة: Zero-Cost First. كل تشغيل في مرحلة التأسيس مجاني 100%. أي مكوّن مدفوع = BLOCKED. البرومبت العمومي يفرض هذا على كل وكيل.» |
| **ما يُفعَل** | إبراز قاعدة §10 في نص البرومبت |

### اللقطة 6 — جرّب بنفسك + الختام (4:30 – 5:00)

| العنصر | التفصيل |
|---|---|
| **ما يُعرض** | قسم «الكتيّب المجاني» — مسار التجربة الآمن |
| **ما يُقال** | «كل هذا قابل لإعادة الإنتاج مجاناً: استنسخ المستودع، افتح اللوحة، شغّل التدقيق والـred-team. الروابط في الوصف. الصدق البنيوي — دائماً.» |
| **ما يُفعَل** | إبراز أوامر مسار التجربة · إنهاء التسجيل |

---

## قائمة تحقق قبل النشر

- [ ] كل رقم قُرئ من الشاشة (لا من الذاكرة)
- [ ] لم يُعرض أي ملف خارج `docs/`
- [ ] لم يُدَّعَ أي نظام غير موجود
- [ ] الحدود مذكورة (40 DECLARED · المسار الرابع غير مُنفَّذ)
- [ ] الروابط في الوصف تشير إلى ملفات المستودع

---

<div dir="ltr">

## Visual Tour Script (EN) — 3–5 minutes (OBS Studio)

**Shot-by-shot guide for a free screen recording. Rule: every number is read from the screen — never from memory.**

1. **Opening (0:00–0:30):** Show the dashboard title + verdict card. Say: "This is an honest engineering inventory of AI-2027. One rule: no number without an executed command."
2. **Measured numbers (0:30–1:30):** Show metric cards + test matrix. Say: "We ran 2,054 tests with zero failures, and a 100-attack red-team with zero breaches."
3. **The archive, honestly (1:30–2:30):** Show the 1 / 40 / 6 / 1 distribution. Say: "48 archived projects — only one is PROVEN. Archiving is not operational achievement, and we say so publicly."
4. **The corrections (2:30–3:30):** Show the verification log. Say: "We checked 21 claims from an earlier draft: 15 were not found or corrected. Every absence is stated plainly."
5. **The free-first rule (3:30–4:30):** Show Legend-2027 + the v1.2 prompt. Say: "The governing rule is Zero-Cost First: everything in the foundational phase is 100% free. Any paid component is BLOCKED."
6. **Try it yourself (4:30–5:00):** Show the safe trial path. Say: "All of this is reproducible for free: clone the repo, open the dashboard, run the audit and red-team. Links in the description."

**Pre-publish checklist:** every number read from screen · no file outside `docs/` shown · no non-existent system claimed · limits stated · description links point to repo files.

</div>

</div>
