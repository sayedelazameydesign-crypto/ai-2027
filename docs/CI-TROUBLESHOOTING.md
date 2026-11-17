<div dir="rtl">

# 🧯 دليل تشخيص CI — المشاكل التي أوقفت التشغيل فعلًا

هذا الملف يوثّق أعطالًا **حقيقية** حدثت في هذا المستودع وكيف شُخِّصت بالتجربة، لا بالتخمين.

---

## 1) `startup_failure` بلا jobs وبلا logs — سياسة الإجراءات

### العَرَض
```
$ gh run list
startup_failure | workflow_dispatch | Deploy static site to GitHub Pages
startup_failure | workflow_dispatch | Evidence-First CI
```
`gh run view <id> --log-failed` → `failed to get run log: log not found`، و`/jobs` يُرجع قائمة فارغة.
أي أن الـworkflow **لم يدخل طابور التنفيذ أصلًا**، فلا يوجد فشل في خطوة يمكن قراءته.

في صفحة Pages تظهر لافتة حمراء:
> *"Actions is currently unavailable for your repository, and your Pages site requires a Jekyll build step."*

### التشخيص الذي أثبت السبب
بدل التخمين في ملفات YAML، عُزل المتغيّر:

| تجربة | النتيجة | الاستنتاج |
|---|---|---|
| workflow **بلا أي `uses:`** (خطوات shell فقط) | ✅ `success` | Actions نفسه سليم، والـrunner يعمل |
| نفس الـworkflow + `actions/checkout` **مثبَّت بكامل SHA** | ❌ `startup_failure` | الحجب على **اسم الإجراء** لا على الملف ولا على الـSHA |

⇒ **السبب: سياسة "Allowed actions" في المستودع تمنع إجراءات GitHub (`actions/*`).**
عندها لا يستطيع `checkout` ولا `setup-python` البدء، فتفشل كل الـworkflows قبل أول خطوة.

### الإصلاح
**Settings → Actions → General → Actions permissions:**
- الأسهل: **"Allow all actions and reusable workflows"** → Save
- أو الإبقاء على التقييد: أشّر **"Allow actions created by GitHub"** وأضف الإجراءات المستخدمة إلى القائمة المسموحة

### ملاحظات مهمة
- هذه **ليست** مشكلة في `pages.yml` ولا في `ci.yml`: نفس الملفين كانا ينجحان قبل تغيير الإعداد.
- Pages يحتاج `build_type = workflow` **و** أن يكون Actions متاحًا؛ تحويل المصدر وحده لا يكفي.
- لا يمكن لأي أداة آلية تغيير هذا الإعداد: `GET/PUT /repos/{owner}/{repo}/actions/permissions` يُرجع **403** للتوكن المتاح.

---

## 2) `Require actions to be pinned to a full-length commit SHA`

عند تفعيل هذا الخيار، كل `uses: owner/repo@v4` يُرفض. الحل المطبَّق هنا: تثبيت كل إجراء على الـcommit SHA الذي يشير إليه الوسم، مع إبقاء الوسم في تعليق.

| الإجراء | المثبَّت عليه | الوسم |
|---|---|---|
| `actions/checkout` | `11d5960a326750d5838078e36cf38b85af677262` | `v4` |
| `actions/setup-python` | `a26af69be951a213d495a4c3e4e4022e16d87065` | `v5` |
| `actions/setup-node` | `49933ea5288caeca8642d1e84afbd3f7d6820020` | `v4` |
| `actions/upload-artifact` | `ea165f8d65b6e75b540449e92b4886f43607fa02` | `v4` |
| `actions/configure-pages` | `983d7736d9b0ae728b81ab479565c72886d7745b` | `v5` |
| `actions/upload-pages-artifact` | `56afc609e74202658d3ffba0e8f6dda462b719fa` | `v3` |
| `actions/deploy-pages` | `d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e` | `v4` |

> الأرقام مأخوذة من `git ls-remote https://github.com/<repo> refs/tags/<tag>^{}` — أي **commit** لا **tag object**.
> تحديثها لاحقًا: نفس الأمر، ثم `sed` على `.github/workflows/*.yml`.

---

## 3) `pages-build-deployment` يفشل على كل دفعة (legacy Jekyll)

هذا workflow يُولّده GitHub تلقائيًا طالما `build_type = legacy`. بعد تحويل المصدر إلى **GitHub Actions** يتوقف دوره، ويبقى النشر عبر `pages.yml` وحده.

التحقق:
```bash
gh api repos/sayedelazameydesign-crypto/ai-2027/pages --jq '{status,build_type}'
# المطلوب: {"status":"built","build_type":"workflow"}
```

---

## بعد إصلاح الإعداد — كيف نتحقق فورًا

```bash
gh workflow run pages.yml --ref main
gh run watch                      # أو: gh run list --limit 3
gh api repos/sayedelazameydesign-crypto/ai-2027/pages --jq '{status,build_type}'
```

</div>

---

## 4) بعد إصلاح السياسة: Pages فشل بسبب **إجراء مركّب** — والحل بلا تغيير أي إعداد

### العَرَض
```
X The action actions/upload-artifact@v4 is not allowed in .../ai-2027 because all
  actions must be from a repository owned by ..., created by GitHub, or verified in
  the GitHub Marketplace. All actions must also be pinned to a full-length commit SHA.
```
بينما `pages.yml` **لا يذكر** `upload-artifact` إطلاقًا.

### السبب
`actions/upload-pages-artifact` هو **composite action**: ملف `action.yml` داخله يستدعي
`actions/upload-artifact@v4` **بالوسم**. سياسة "كل الإجراءات مثبَّتة بـSHA" تُطبَّق على
الاستدعاءات الداخلية أيضًا، فترفضه من الداخل. نفس الشكل ينطبق على `actions/configure-pages`.

> الخلاصة: **السياسة سليمة، والعيب في تصميم الإجراء المركّب.** إلغاء السياسة حلّ، لكنه
> يضحّي بضمان أمني حقيقي مقابل مشكلة قابلة للحل.

### الحل المطبَّق (صفر إعدادات، صفر إجراءات غير مثبَّتة)
```yaml
- run: tar --dereference --hard-dereference -cvf artifact.tar -C website .
- uses: actions/upload-artifact@<pinned-sha>   # v4
  with:
    name: github-pages      # الاسم الذي يتوقعه deploy-pages
    path: artifact.tar
```
`upload-pages-artifact` يفعل بالضبط هذا: tar لمجلد الموقع + رفع باسم `github-pages`.
البناء نجح من أول محاولة، والنشر تم على `main`.

### تحذير البيئة (ليس خطأ)
```
Branch "arena/..." is not allowed to deploy to github-pages due to environment protection rules
```
هذا سلوك صحيح: بيئة `github-pages` تسمح بالنشر من الفرع الافتراضي فقط. لذلك النشر يُختبر على `main` لا على فرع العمل.

### الحالة النهائية (مقيسة)
```
gh api repos/sayedelazameydesign-crypto/ai-2027/pages --jq '{status,build_type}'
{"status":"built","build_type":"workflow"}
```
+ `Deploy static site to GitHub Pages` = success على `main` · الصفحة `demo.html` تُخدَم فعلًا.
