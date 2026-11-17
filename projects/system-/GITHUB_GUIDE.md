# دليل سريع: ربط المشروع بـ GitHub ورفع التحديثات

هذا الدليل يشرح طريقة ربط مشروع **todo-list-app** بالمستودع التالي:

> https://github.com/sayedelazameydesign-crypto/system-

المسار المحلي للمشروع هو `/home/ubuntu/todo-list-app`، والفرع الرئيسي المستخدم هو `main`.

## 1. الانتقال إلى مجلد المشروع

```bash
cd /home/ubuntu/todo-list-app
```

## 2. التحقق من حالة Git الحالية

```bash
git status
git branch --show-current
git remote -v
```

في مشروع WebDev قد يكون `origin` محجوزًا لمستودع داخلي خاص بالبيئة. لذلك استخدم اسمًا منفصلًا هو `github` للمستودع الخارجي، ولا تغيّر رابط `origin` الموجود.

## 3. ربط المشروع بالمستودع

إذا كان المشروع يحتوي على مجلد `.git` بالفعل، أضف المستودع الخارجي باسم `github`:

```bash
git remote add github https://github.com/sayedelazameydesign-crypto/system-.git
```

إذا ظهر الخطأ `remote github already exists`، حدّث رابطه بدلًا من إنشاء Remote جديد:

```bash
git remote set-url github https://github.com/sayedelazameydesign-crypto/system-.git
```

ثم تحقق من الرابط:

```bash
git remote -v
git remote get-url github
```

يجب أن يظهر رابط GitHub تحت Remote باسم `github`. لا تستخدم `git remote set-url origin` داخل مشروع WebDev إلا إذا كنت متأكدًا أن `origin` ليس رابط البيئة المُدار.

## 4. التأكد من عدم رفع الأسرار

قبل الإضافة إلى Git، افحص الملفات التي ستدخل في الـcommit:

```bash
git status --short
git diff -- . ':!pnpm-lock.yaml'
```

لا ترفع أبدًا ملفات `.env` أو `.env.local` أو أي ملف يحتوي على `DATABASE_URL` أو `JWT_SECRET` أو مفاتيح API أو Tokens. المشروع يحتوي على قواعد استبعاد لملفات البيئة في `.gitignore`، لكن يجب مراجعة قائمة الملفات يدويًا دائمًا.

## 5. إضافة التحديثات وإنشاء Commit

```bash
git add .
git status --short
git diff --cached --stat
git commit -m "feat(dashboard): update todo task management"
```

غيّر رسالة الـcommit حسب نوع التغيير. استخدم `fix(dashboard):` لإصلاح عيب، و`feat(dashboard):` لإضافة ميزة، و`docs:` لتحديث التوثيق.

إذا لم توجد تغييرات، سيخبرك Git أن شجرة العمل نظيفة، ولا حاجة لإنشاء Commit جديد.

## 6. رفع التحديثات إلى GitHub

```bash
git branch -M main
git push -u github main
```

بعد أول رفع، تكفي الأوامر التالية عادةً:

```bash
git add .
git commit -m "feat(dashboard): describe the latest change"
git push github main
```

## 7. التحقق من نجاح الرفع

```bash
git status --short --branch
git log -1 --oneline
git ls-remote --heads github main
```

يجب أن يظهر الفرع المحلي مرتبطًا بـ`github/main` بعد أول push، وأن يطابق Commit الأخير الموجود في GitHub.

يمكن فتح المستودع مباشرة من الرابط:

[فتح مستودع system- على GitHub](https://github.com/sayedelazameydesign-crypto/system-)

## مسار بديل: استنساخ المشروع من GitHub

إذا كنت تعمل على جهاز جديد ولا تملك نسخة المشروع، استخدم:

```bash
git clone https://github.com/sayedelazameydesign-crypto/system-.git todo-list-app
cd todo-list-app
```

بعد ذلك ثبّت الاعتماديات وشغّل الفحوص:

```bash
pnpm install
pnpm check
pnpm test -- --run
```

## حل المشكلات الشائعة

| المشكلة | الحل |
|---|---|
| `remote origin already exists` | استخدم `git remote set-url origin ...` بدل `git remote add`. |
| `rejected (non-fast-forward)` | نفّذ `git pull --rebase origin main` بعد التأكد من عدم وجود تغييرات محلية متعارضة، ثم `git push`. |
| `nothing to commit` | لا توجد تغييرات جديدة؛ تحقق من `git status`. |
| طلب تسجيل الدخول | استخدم GitHub CLI بعد تسجيل الدخول عبر `gh auth login`، أو استخدم بيانات اعتماد GitHub المعتمدة في بيئتك. |
| ظهور ملف أسرار في `git status` | أوقف العملية، أضف الملف إلى `.gitignore`، وتحقق من أنه لم يدخل إلى الـstaging قبل تنفيذ `git commit`. |

> لا تستخدم `git push --force` على الفرع `main` إلا بعد مراجعة تاريخ المستودع والتأكد من أن ذلك ضروري، لأن الأمر قد يحذف تغييرات موجودة على GitHub.
