# النشر بلا بطاقة: Hugging Face Spaces + Turso — دليل كامل

> البديل الرسمي لمسار Oracle بعد قيد «بدون فيزا». نفس عقود الـ enforcement:
> SHA مثبت داخل الصورة، fail-closed، أسرار عبر قناة آمنة فقط.

## لماذا هذا التركيب؟

| | HF Spaces (Docker) | Turso |
|---|---|---|
| بطاقة | ❌ غير مطلوبة | ❌ غير مطلوبة |
| الموارد | 2 vCPU / **16GB RAM** (Playwright مرتاح) | 5GB · 500M قراءة/شهر |
| القيد | قرص مؤقت + نوم بعد 48h خمول | — |
| الحل | **قاعدة البيانات خارج الحاوية (Turso)** ⇒ السجل الدائم يصمد | يستقبل runs/approvals/messages |

الـ artifacts (ملفات المهام واللقطات) تبقى مؤقتة داخل الحاوية — مقبول ومُعلن:
الأثر الرسمي (trace/الخطط/القرارات) في Turso.

## الخطوات — 20 دقيقة، صفر بطاقة، صفر أسرار في المحادثة

### 1) Turso (قاعدة البيانات الدائمة)
1. سجّل في **turso.tech** (GitHub login — بلا بطاقة)
2. أنشئ Database (اختر أقرب منطقة: `fra` فرانكفورت)
3. من لوحة التحكم خذ:
   - `TURSO_DATABASE_URL` (يبدأ بـ `libsql://…`)
   - `TURSO_AUTH_TOKEN` (Generate Token)
4. **احتفظ بهما — لا ترسلهما في أي محادثة**

### 2) مفتاح Groq جديد
console.groq.com/keys → Create (القديم مكشوف — لا يُستخدم)

### 3) Hugging Face Space
1. سجّل في **huggingface.co** (بلا بطاقة)
2. New Space → الاسم: `nawah-agent` → License: mit → SDK: **Docker** → Blank → Public → Create
3. في تبويب **Files** بالـ Space:
   - عدّل `README.md` ليكون رأسه:
     ```yaml
     ---
     title: Nawah Agent
     emoji: ◈
     sdk: docker
     app_port: 7860
     pinned: false
     ---
     ```
   - أنشئ ملف `Dockerfile` والصق محتوى
     [`deploy/hf-space/Dockerfile`](Dockerfile) من مستودع GitHub كما هو
     (يستنسخ المستودع العام عند SHA المثبت — لا يحتاج أي توكن)
4. **Settings → Variables and secrets** → أضف كـ **Secrets**:
   ```
   OPENAI_API_KEY     = gsk_...            (مفتاح Groq الجديد)
   OPENAI_BASE_URL    = https://api.groq.com/openai/v1
   OPENAI_MODEL       = openai/gpt-oss-120b
   TURSO_DATABASE_URL = libsql://...
   TURSO_AUTH_TOKEN   = eyJ...
   ```
5. سيبدأ البناء تلقائيًا (~5–8 دقائق أول مرة) → الرابط العام:
   `https://<username>-nawah-agent.hf.space`

### 4) التحقق (خطوات 11–17 من ترتيب النشر)
من جهازك:
```bash
curl https://<space>.hf.space/api/health          # {"ok":true}
```
ثم من الواجهة: مهمة حية متصفح→ملف، وموافقة، وفحص «سجل التشغيلات»
**بعد Restart للـ Space** (Settings → Factory reboot) — بقاء السجل = دليل
استمرارية Turso (بديل rollback drill المحلي؛ نسخ Turso الاحتياطي عبر
`turso db shell <db> .dump` من جهازك).

## حدود معلنة لهذا الهدف (مقابل صفر بطاقة)

1. **نوم بعد 48h خمول** + إيقاظ ~30-90ث — يُخفف بـ ping أسبوعي (cron-job.org
   مجاني) أو زيارة دورية؛ يُقبل في tier-0
2. artifacts مؤقتة داخل الحاوية (الأثر الرسمي دائم في Turso)
3. Space عام الرؤية (الكود أصلًا عام) — **ولا يحوي أي سر**؛ الأسرار في
   Secrets حصرًا
4. إعادة النشر بنسخة أحدث = تعديل `DEPLOY_SHA` في الـ Dockerfile يدويًا —
   وهذا **ميزة** بموجب سياسة الـ SHA المثبت، لا عيبًا

## مسارات بديلة بلا بطاقة (مرجع)

| المسار | متى |
|---|---|
| جهازك + `cloudflare-tunnel.sh quick` | عرض فوري اليوم؛ يعمل ما دام جهازك يعمل |
| Render + Turso | إن رُفض Playwright في 512MB → تعطيل browser.run مؤقتًا |
| Oracle ببطاقة مسبقة الدفع (Meeza وغيرها تُقبل أحيانًا؛ حجز ~$1 يُرد) | إن توفرت لاحقًا — يبقى الأقوى |
