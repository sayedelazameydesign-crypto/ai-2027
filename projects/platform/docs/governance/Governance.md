# Governance

يحتوي هذا المجلد على Operating Protocol وسجلات وقوالب الحوكمة الخاصة بتنفيذ تغييرات Celia.Pro بطريقة مضبوطة وقابلة للتدقيق.

## طريقة الاستخدام

يبدأ العمل بتثبيت Baseline في [سجل Baseline](registries/BASELINE_REGISTRY.md)، ثم يُنشأ Change Request باستخدام [القالب](templates/CHANGE_REQUEST_TEMPLATE.md). بعد الموافقة على النطاق وخطة التنفيذ، تُسجل نتائج الاختبارات والمراجعات في [قالب Evidence](templates/EVIDENCE_REPORT_TEMPLATE.md)، وتُثبت الحالة في [قالب Checkpoint](templates/CHECKPOINT_TEMPLATE.md).

## الملفات

| الملف | الغرض |
|---|---|
| [OPERATING_PROTOCOL_v1.0.md](OPERATING_PROTOCOL_v1.0.md) | الأدوار والقواعد وبوابات التنفيذ والتوقف |
| [BASELINE_REGISTRY.md](registries/BASELINE_REGISTRY.md) | الحالات المرجعية المجمدة |
| [CR-003.md](registries/CR-003.md) | أول طلب تغيير تجريبي مضبوط |
| [CHANGE_REQUEST_TEMPLATE.md](templates/CHANGE_REQUEST_TEMPLATE.md) | قالب تسجيل طلبات التغيير |
| [EVIDENCE_REPORT_TEMPLATE.md](templates/EVIDENCE_REPORT_TEMPLATE.md) | قالب تقرير الأدلة |
| [CHECKPOINT_TEMPLATE.md](templates/CHECKPOINT_TEMPLATE.md) | قالب نقاط التحقق |

## قاعدة الدمج

هذه الملفات لا تمنح صلاحية الدمج تلقائيًا. يجب أن يبقى `main` أو `master` محميًا، وأن تمر تغييرات الكود عبر Pull Request وموافقة بشرية نهائية.
