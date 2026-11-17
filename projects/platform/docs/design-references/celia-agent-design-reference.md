# مرجع تصميم celia-agent-v1 → قرارات النقل إلى `platform`

> مصدر: مستخرج تصميمي من `app/autonomy.py` و`app/runtime.py` في celia-agent-v1.
> **مرجع تصميم فقط** — لا copy-paste للكود، ولا نقل أسماء Python الحرفية.

## المبادئ المنقولة فعليًا (نُفذت في Phase 2)

| المبدأ | التنفيذ في platform |
|---|---|
| **الموافقة مربوطة بالخطة والخطوة** (task_id + step_id + tool + plan hash) | جدول `approvals`: (runId, stepIndex, tool, planHash=sha256(plan)) — التحقق من التطابق الحرفي قبل المتابعة، وأي mismatch → block |
| **deny-by-default والسياسة قبل التنفيذ** | حقل `risk: 'safe' \| 'sensitive'` في سجل الأدوات — الحساسية تُقرأ من الـ registry حصرًا، **ليس من رأي النموذج**. `shell.exec` حساسة |
| **التوقف ليس نجاحًا** | `awaiting_approval` حالة معلقة صريحة؛ الرفض → `cancelled` بسبب مسجل |
| **حتمية التحقق من الخطة** | Zod: رفض أداة غير مسجلة، حدود الخطوات، repair retry واحد ثم `plan_rejected` |

## المبادئ المؤرشفة كمرجع لمراحل لاحقة

| المبدأ | القرار |
|---|---|
| **Execution ledger بسلسلة hash** (proof hash يتسلسل من GENESIS) | مرجع Phase 3 — مع الإقرار الصريح بأنه tamper-evidence داخل العملية فقط، **لا يثبت durability عبر restart** |
| **TaskDAG بترتيب topological** | مؤجل — platform تسلسلية عمدًا؛ يُفتح فقط إن ثبتت حاجة خطوات متوازية |

## المبادئ المرفوضة صراحة

| المبدأ | سبب الرفض |
|---|---|
| حد 8 خطوات الافتراضي | platform معتمدة على 4 خطوات كحد أقصى — قرار مستقل لا يُستورد |
| أسماء وحقول Python classes حرفيًا | عقود TypeScript مستقلة صُممت وفق Module 2 |
| حالة `PENDING_APPROVAL` الاسمية | platform تستخدم مسميات Module 2: `awaiting_approval` — الاختلاف الاسمي مقصود وموثق |

## ملاحظة نطاق

التقارير المرفقة عن مستودع `new-new` (SHA `1609062`, المقارنات المحلية/البعيدة) تخص **مستودعًا آخر** ولا تنطبق على `platform`؛ أُرشفت هنا كسياق تاريخي فقط.
