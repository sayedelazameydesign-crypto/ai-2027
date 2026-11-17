# AUDIT-002 — Deployment Enforcement Audit

| البند | القيمة |
|---|---|
| النطاق | `deploy/oracle-setup.sh` + `deploy/smoke-test.sh` عند `1bdae47` |
| **الحكم** | 🟢 **PASS WITH FINDINGS** |
| طبيعة الحكم | على **enforcement code** منطقيًا — ليس على Oracle runtime (لم يُنفذ بعد) |

## الضوابط الاثنا عشر — كلها PASS

DEPLOY_SHA إلزامي · detach · HEAD==SHA · رفض moving branch · لا git pull ·
.deploy-lock · تحقق SHA في smoke قبل الاختبارات · fingerprint · external إلزامي ·
rollback إلزامي · GATE=BLOCKED افتراضيًا · الحكم مرتبط بالنسخة.

خاصية معتمدة: **التحقق في مرحلتين** (setup ثم smoke) — الانحراف بعد القفل يسبب BLOCK.

## Findings

### F1 — `.deploy-lock` · LOW / ACCEPTED
**deployment guard** ضد الانحراف العرضي — **ليس** cryptographic attestation ولا
حماية من مهاجم يملك SSH/root (خارج نموذج التهديد المعلن: مالك واحد).
**قيد تسمية دائم:** يُحظر وصفه بغير ذلك.

### F2 — detach ليس الحماية الوحيدة · LOW / DOCUMENTED
`git checkout <sha>` أو `reset --hard` ممكنان تقنيًا لمستخدم SSH — الحماية
الفعلية هي الثلاثية: `.deploy-lock` + smoke Step 0 + شرط `HEAD == locked SHA`.

### F3 — إجراء إلزامي · INFORMATIONAL / REQUIRED PROCEDURE
`1bdae47` (ورأس السلسلة بعده) = **Deployment Candidate** فقط.
لا يصبح **Official Deployment Source SHA** إلا بعد:
`push → git ls-remote مستقل → remote main == candidate SHA`.

## سلوك fail-closed المعتمد

```
No SHA → BLOCK · Wrong SHA → BLOCK · Branch checkout → BLOCK
Smoke ضد نسخة خاطئة → BLOCK · لا external → BLOCK · لا rollback → BLOCK
```

## ملاحظة إجرائية (سابقة AUDIT-001)

commit إغلاق هذا التدقيق يتقدم برأس السلسلة — فيصبح **هو** المرشح النهائي
للدفع، أسوة بـ `2879e51` الذي كان commit إغلاق AUDIT-001 ودُفع رأسًا للحزمة.

---

## ملحق: إغلاق F3 (الدورة الثانية) — 2026-08-23

| البند | القيمة |
|---|---|
| **Official Deployment Source SHA** | `e352167c14d6bf8c772b551bc65783388c30a6c0` |
| البروتوكول | push → `git ls-remote` مستقل → مطابقة تامة ✅ |
| يحتوي | دعم Turso/libSQL + طابور كتابة مرتب + HF Dockerfile (SHA enforcement داخل الصورة) |
| قرار الهدف | **HF Spaces + Turso + Groq** (بلا بطاقة) — **Oracle VM خارج المسار الحالي** ويُعاد فتحه بقرار مستقل فقط |
| قيد صياغة | «صفر تكلفة» = ضمن حدود الخطط المجانية الموثقة في PROVIDER-CATALOG بتاريخها — ليس ضمانًا دائمًا |
| SHA السابق `a8a87e8` | ⚠️ لا يصلح لنشر HF (بلا دعم Turso) — موثق لمنع الالتباس |
