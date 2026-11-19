<div dir="rtl">

# 🗺️ خريطة PR Lens لأحدث تغيير على `main` (PR #64 · `cbabaa4`) + دليل الإعداد الكامل

> تاريخ التحقق: `2026-11-19` · أداة `@coldtea/pr-lens-cli@0.15.2` · المهارة المثبّتة: `coldteadotai/pr-lens` (`pr-lens`)
> حالة `Coldtea PR Lens` App (`https://github.com/apps/coldtea-pr-lens`): **Suspended** في الحساب/المنظمة وقت الفحص.

---

## 1️⃣ سبب ظهور `Coldtea PR Lens` بحالة `Suspended` وما الذي يغطيه البديل

1. **لماذا يظهر `Suspended`؟**
   - تطبيق GitHub App الرسمي (`Coldtea PR Lens`) يكون معلّقاً (`Suspended`) من إعدادات حساب/منظمة GitHub أو من جهة التطبيق لحين إعادة تفعيله أو منحه صلاحية الوصول للمستودع عبر `GitHub → Settings → Integrations → Applications → Coldtea PR Lens → Unsuspend / Configure`.
2. **ماذا يَفوتنا عند تخطّي الخطوة 1 (تثبيت GitHub App)؟**
   - **لا** توجد مراجعة كود تلقائية (`PR code reviews`) على طلبات الدمج الجديدة.
   - **لا** تتحدث خرائط PR Lens تلقائياً عند كل `push` على الـPR.
   - **لا** تُنشأ صفحة PR تفاعلية تلقائية على `prlens.dev` لكل Pull Request.
3. **ماذا يغطي مسار GitHub Action / CLI هنا (`.github/workflows/pr-lens.yml`) مقابل ما يتخطاه؟**
   - **يغطي:** فحص `.github/pr-lens.yml`، وتحليل التغييرات ورسم مخططات العمارة وتدفق البيانات (`architecture` + `data-flow`) بصيغ `SVG` (نهاري/ليلي + Walkthrough متحرك) وإخراج `.github/pr-lens.map.json` داخل GitHub Actions عبر `@coldtea/pr-lens-cli@0.15.2`.
   - **يتخطى:** مراجعة الكود بالذكاء الاصطناعي في الخطوة 1 وتحديثات الدفع التلقائية على الـPRs المفتوحة من الفروع الخارجية.
   - **سبب التصميم الأمني المتوافق مع CI المستودع:** النسخة القياسية `coldteadotai/pr-lens/packages/action@v0` تمرّر `api-key` تحت `with:` على حدث `pull_request`، وهو ما يرفضه `scripts/workflow_secret_audit.py --assert` و`docs/SECRETS.md`. لذلك يستعمل `.github/workflows/pr-lens.yml` حزمة `@coldtea/pr-lens-cli@0.15.2` مباشرةً عبر `env: PR_LENS_API_KEY: ${{ secrets.GEMINI_API_KEY }}` ومقيداً بـ`workflow_dispatch` على `refs/heads/main` فقط.

---

## 2️⃣ الملفات المضافة والمعتمدة في المستودع

| الملف | الدور | التحقق المنفَّذ |
|---|---|---|
| `.github/pr-lens.yml` | قواعد التصحيح والتجميع (`10` corrections: rename, lane, group, exclude `uv.lock`) | `✓ validate` بدون أي تحذير |
| `.github/workflows/pr-lens.yml` | مسار تشغيل CLI يدوي على `main` يمرّر `GEMINI_API_KEY` عبر `env:` فقط | `✓ workflow_secret_audit.py --assert` (`30/30` tests OK) |
| `.github/pr-lens.map.json` | خريطة الحالة المخزنة للمستودع عند الالتزام `cbabaa45f12cb1213b80ef1f3b9d62a5982691f4` (`13` عقدة، `11` رابطاً، `4` مسارات) | `✓ validate` صالح (`schemaVersion: 0.3.1`) |
| `.pr-lens/python-2027-protocol-production-hardening-pr-64/` | نواتج الرسم المولَّدة محلياً (غير مُلتَزَمة في Git وفق `.gitignore`): `8` ملفات SVG للواجهات + `2` ملف SVG للـWalkthrough المتحرك + `manifest.json` + `drawn.graph.json` | `✓ render` كامل (`light` + `dark`) |

---

## 3️⃣ خريطة أحدث تغيير: PR #64 (`Python 2027 Protocol — Production Hardening`)

- **المرجع الأساسي (`base`):** `8fc85c7cadd0ae407895b03b3b53a31634c3f519` (`main`)
- **رأس التغيير (`head`):** `67899a1b209ceb9f5a243ff4e9cbd999b16f170a` (`arena/115d4087-ai-2027`)
- **التزام الدمج (`mergeCommit`):** `cbabaa45f12cb1213b80ef1f3b9d62a5982691f4` (`+4049 / -782` عبر `35` ملفاً)
- **ترابط المكوّنات (`4 lanes · 14 graph nodes / 13 stored nodes · 12 graph edges · 1 data flow · 4 walkthrough steps`):**
  1. **`studio-client`:** `Studio Web UI` (`static/studio.html`) و`ai2027-py27 CLI` (`cli.py`) لعرض فحص الميزانية `$0` وتقارير الامتثال لـ48 مشروعاً.
  2. **`python-protocol`:** `FastAPI Studio API` ← `FreeFirstRouter` ← **`BudgetGuard`** (`budget.py` لفرض `AI2027_MAX_SPEND_USD=0`) + **`Compliance Registry`** (`compliance.py` لفحص ADR-0002) + **`OTel Telemetry Sink`** (`telemetry.py`) + `Provider Adapters` + `Pydantic V2 Contracts`.
  3. **`cloudflare-edge`:** `Edge Gateway` (`index.mjs`) ← `Python Edge Worker` (`entry.py`) ← **`worker_guard.py`** لعزل فحص SSRF وحماية المسارات وتنقيح الترويسات قبل Pyodide.
  4. **`ci-pipeline`:** `Python 2027 CI` (`.github/workflows/python-2027.yml`) يعتمد `uv.lock` المقفل ويشغّل فحوص `wrangler deploy --dry-run` ومسار الأسرار على `main` فقط.

---

## 4️⃣ أوامر إعادة التوليد والعرض محلياً

```bash
# التحقق من الإعدادات والخريطة المخزنة
npx --yes @coldtea/pr-lens-cli@0.15.2 validate .github/pr-lens.yml .github/pr-lens.map.json

# إعادة تصيير ملفات SVG (النهاري والليلي) محلياً من .pr-lens/graph.json
npx --yes @coldtea/pr-lens-cli@0.15.2 validate .pr-lens/graph.json
npx --yes @coldtea/pr-lens-cli@0.15.2 render .pr-lens/graph.json

# (اختياري عند توفر اتصال خارجي ورفع لوحة تفاعلية على prlens.dev)
npx --yes @coldtea/pr-lens-cli@0.15.2 canvas push .pr-lens/python-2027-protocol-production-hardening-pr-64/drawn.graph.json
```

</div>
