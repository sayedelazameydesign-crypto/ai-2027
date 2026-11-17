# النشر على Render (بلا بطاقة) + Turso — الدليل المعتمد

> **البديل الرسمي بعد سقوط مسار HF** (دليل واقعي 2026-08-23: Docker SDK ظهر
> **Paid** على حساب المالك رغم مصادر تقول غير ذلك — الواقع يحكم، موثق في
> PROVIDER-CATALOG).

## ما يعمل وما يتغير على هذا الهدف

| القدرة | الحالة |
|---|---|
| الحلقة الذكية + Groq + الموافقات + SSE + الواجهة كاملة | ✅ كما هي |
| الاستمرارية (runs/approvals/messages) عبر Turso | ✅ كما هي |
| `web.fetch` + `shell.exec` + `fs.readwrite` | ✅ كما هي |
| `browser.run` | ⛔ معطلة بـ `BROWSER_DISABLED=1` (ذاكرة 512MB) — الخطط تسلك `web.fetch` تلقائيًا، والمخطط لا يراها أصلًا |
| النوم بعد 15 دقيقة خمول | ⚠️ إيقاظ بأول زيارة (~30-60ث) — يُخفف بـ ping مجاني (cron-job.org / UptimeRobot) |

## الخطوات — بلا بطاقة، بلا توكنات في أي محادثة

### 1) الحساب والربط (OAuth — قناة آمنة)
1. **render.com** → Sign up → **Sign in with GitHub** (بلا بطاقة)
2. Authorize Render على GitHub (يطلب الوصول للمستودعات — امنحه `platform` فقط إن خُيرت)

### 2) إنشاء الخدمة
1. Dashboard → **New → Web Service** → اختر مستودع `platform`
2. الإعدادات (يقرأ `render.yaml` تلقائيًا؛ وإن سُئلت):
   - Runtime: Node · Instance: **Free**
   - Build: `npm ci --no-audit --no-fund && npm run build`
   - Start: `npx next start -H 0.0.0.0 -p $PORT`
3. **Environment → Add Secret** × 5 (من ملاحظتك):
   ```
   OPENAI_API_KEY · OPENAI_BASE_URL · OPENAI_MODEL
   TURSO_DATABASE_URL · TURSO_AUTH_TOKEN
   ```
   و`BROWSER_DISABLED=1` يأتي من blueprint (تأكد من وجوده).

### 3) سياسة الـ SHA المثبت على Render
- Settings → Build & Deploy → **Auto-Deploy: Off**
- **Manual Deploy → Deploy a specific commit** → اختر **الـ SHA الرسمي المعلن**
  (لا تنشر برأس main المتحرك)

### 4) التحقق (نفس معيار hf-verify، بأداة Render)
```bash
bash deploy/hf-verify.sh https://nawah-agent.onrender.com phase1
#  → Manual Deploy لنفس الـ SHA (يعيد بناء الحاوية = محو القرص المؤقت)
bash deploy/hf-verify.sh https://nawah-agent.onrender.com phase2
#  → نفس runId يعود من Turso = برهان الاستمرارية
```
(المهمة الحية في phase1 ستخطط عبر `web.fetch` تلقائيًا — بلا متصفح.)

## استعادة `browser.run` لاحقًا
تعود تلقائيًا بمجرد النشر على مضيف ≥2GB (Oracle ببطاقة مسبقة الدفع، أو أي VPS
من أول إيراد) — بحذف `BROWSER_DISABLED` فقط. صفر تغيير كود.
