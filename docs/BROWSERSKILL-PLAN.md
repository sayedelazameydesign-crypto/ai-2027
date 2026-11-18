<div dir="rtl">

# 🌐 خطة وتنفيذ تكامل `Tencent/BrowserSkill` — الأنماط الثلاثة عبر ثلاث مراحل

**التاريخ:** 2026-11-18 · **القرار المعماري:** [`docs/adr/0005-browserskill-adapter-boundary.md`](adr/0005-browserskill-adapter-boundary.md) · **المصدر:** [`Tencent/BrowserSkill`](https://github.com/Tencent/BrowserSkill) (ترخيص **MIT**، مراجعة `aeebd100`، الإصدار المثبَّت **`cli-v0.5.0`** / **`ext-v0.5.0`**، البروتوكول **`2.0`**)

> **قاعدة المستند:** لا ادعاء بلا أمر يثبته. كل مرحلة تُعلن صراحةً ما هو **مُثبَت بالتنفيذ الآن** وما ينتظر تشغيل المالك على جهازه أو بوابة المرحلة التالية.

---

## 1️⃣ خريطة الأنماط الثلاثة عبر المراحل الثلاث

| المرحلة | النمط (`mode`) | الحالة اليوم | ما يُسلَّم | علاقته بمفاتيح GitHub Actions (`GEMINI_API_KEY` / `RENDER_API_KEY`) | بوابة الانتقال للمرحلة التالية |
|---|---|---|---|---|---|
| **المرحلة 1** | **`local`** — المتصفح والجهاز المحلي | ✅ **مُنفَّذة ومُختبَرة offline** (بانتظار فحصك الحي على جهازك) | `browser_skill_adapter/` (`adapter.py`, `probe.py`, `run_local_task.py`, `fixtures/`, `test_adapter.py`) + `scripts/install_browserskill.sh`/`.ps1` + تحصين `secret_scan.py` و`.gitignore` + `/api/browser/status` في `command-hud` + `--with-browser` في `ember_up.sh` + بوابات CI | **عزل كامل:** المحوّل يحذف `GEMINI_API_KEY` و`RENDER_API_KEY` و`GITHUB_TOKEN` من بيئة عملية `bsk` وينقّح أشكالها من أي مخرج أو سجل؛ لا يحتاج النمط المحلي أي مفتاح سحابي | خروج `probe.py` بـ`0 (READY)` + نجاح `run_local_task.py` بحالة `VERIFIED` على جهازك |
| **المرحلة 2** | **`skill_mcp`** — مهارة وكلاء التطوير + خادم MCP + جسر التخطيط الاختياري | 🟡 **مُعلَنة ومحمية ببوابة** (`ModeNotEnabledError`) | حزمة `skills/browser-skill/` (مع إشعار MIT وقواعد AI-2027) + أتمتة `bsk install-skill --harness … --mcp` (11 أداة `bsk_*` + `bsk_recipe` اختياريًا) + جسر مخطط اختياري يقترح خطوات تمرّ إلزاميًا عبر `DomainPolicy` و`ApprovalEngine` و`OutcomeVerifier` | يستهلك **`GEMINI_API_KEY`** اختياريًا (عبر `env:` وترويسة `x-goog-api-key` فقط، بلا `?key=`) كطبقة اقتراح `L4` معزولة عن `bsk`، بعد تأكيد نجاح وظيفة `live-gemini` | ثبات المرحلة 1 حيًّا + أخضرية `live-gemini` + اجتياز اختبارات حقن الصفحات (Prompt Injection) |
| **المرحلة 3** | **`remote_wss`** — الخادم البعيد عبر WSS المصادَق + المراقبة الموحّدة | 🟡 **مُعلَنة ومحمية ببوابة** (`ModeNotEnabledError`) | دعم `bsk daemon start --mode server` خلف TLS (`wss://<host>/extension` و`/extension/authorize`) + إدارة الأجهزة المقترنة (`pair` TTL `5m`، `devices`، `revoke`، صلاحية `90d`) + معاينة `ui.task_preview` / `ui.task_focus` في `command-hud` + تسجيل أي سر إضافي في `docs/SECRETS.md` | يستهلك **`RENDER_API_KEY`** عبر `render_control_adapter` و`deploy.yml` (نشر يدوي مؤكد بالاسم + فحص حياة) بشرط استضافة **لا تنام** ودعم WebSocket/TLS، مع بقاء متصفحك وكوكيزك على جهازك | إتمام المرحلة 2 + تقرير `render_control_adapter/probe.py` + توفر مضيف WSS دائم لا ينام |

---

## 2️⃣ ما أُنجز وأُثبت فعليًا في المرحلة 1 (النمط 1: `local`)

### أ) الحماية المسبقة للأسرار والأرشيف والنواة
- **عدم الاستنساخ داخل المستودع:** بقيت `projects/` (48 مشروعًا) و`projects/agi-system/core` (`L0`) و`live-agent/` و`requirements.txt` و`Dockerfile` دون أي تعديل.
- **بوابة الأسرار (`scripts/secret_scan.py` — 18 اختبارًا):** تمنع قبل الـcommit وفي CI تسريب `bsk-local.v1.*`، و`bsk-auth.*`، وروابط الإقران `ws(s)://…#<secret>` (حتى لو كان النطاق `example.com`)، وملف `local-pairings.json`، مع إخفاء القيمة المكتشفة دائمًا.
- **التثبيت المقيّد (`scripts/install_browserskill.sh` + `.ps1` — 10 اختبارات):** يثبّت الإصدار المحدد `cli-v0.5.0` خارج Git (`~/.local/bin/bsk`) بلا `sudo` وبلا تعديل صامت لملفات الـshell، ويرفض التثبيت فورًا (**fail-closed**) عند غياب أو اختلاف بصمة `SHA-256` أو عدم تطابق الإصدار أو وجود مسار traversal داخل الأرشيف.

### ب) المحوّل المعزول (`browser_skill_adapter/` — 34 اختبارًا، صفر اعتماديات)
1. **جدار النطاقات (`DomainPolicy`):** يرفض `file://`، `chrome://`، `chrome-extension://`، `edge://`، `javascript:`، `data:`، `view-source:`، عناوين metadata (`169.254.169.254` وغيرها)، الشبكات الخاصة RFC1918، بيانات الاعتماد داخل الروابط، و`http://` غير المشفّر افتراضيًا؛ ويسمح بـ`localhost`/`127.0.0.1` فقط عند تمرير `--allow-localhost` صراحةً لفحص خدماتك المحلية (`:8090` / `:8091`).
2. **محرك الموافقات (`ApprovalEngine`):** القراءات داخل `Agent Window` تلقائية؛ كل فعل تغييري (`navigate`, `click`, `fill`, `resolve_dialog`) يتطلب تصريحًا أحادي الاستخدام بـHMAC+nonce ومهلة صلاحية + `context_id` حيّ من `sync()`؛ والعمليات الحدودية (`borrow_tab`, `upload`, `download`, `evaluate`) تتطلب تأكيدًا مطابقًا حرفيًا للهدف، مع تعطيل `evaluate` افتراضيًا ومنع تمرير كلمات المرور عبر `argv`.
3. **حارس عدم الإعادة العمياء (`IntentGuard`):** عند `effect_state == "unknown"` أو `timeout` أو `dialog_pending` تُصنَّف الخطوة `INCONCLUSIVE` (`verified: false`) ويُمنع تكرار أي كتابة على نفس التبويب حتى تتم المصالحة عبر `sync()` (أو `resolve_dialog()` للحوارات العالقة).
4. **التنظيف الإلزامي (`BrowserSession`):** يُشغَّل `bsk session stop <id>` داخل `finally` حتى عند الاستثناءات أو تفعيل مفتاح التجميد (**Freeze**)؛ وفشل الإغلاق أو فشل إرجاع تبويب مستعار يمنع إصدار `VERIFIED`.
5. **عزل الأسرار وتغليف المحتوى غير الموثوق:** حذف مفاتيح `GEMINI_API_KEY` و`RENDER_API_KEY` و`EMBER_ALERT_WEBHOOK` و`GITHUB_TOKEN` وأي `*_API_KEY`/`*_SECRET`/`*_TOKEN` قبل استدعاء `bsk`، فرض `BSK_AUTO_START=0` دائمًا، تغليف كل قراءة بـ`untrusted_page_data: true` مع كشف استرشادي لإشارات حقن التعليمات، وسجل تدقيق متسلسل ببصمات SHA-256 (`AuditLedger`) خارج مجلد المهمة.
6. **التكامل مع ذاكرة `L1` (`memory/store.py`) وغرفة العمليات `L2` (`command-hud/`):**
   - `verify_and_record()` يتحقق من 7 شروط مستقلة ويعيد حساب `SHA-256` من القرص؛ المهمة `VERIFIED` تُقبَل في `EvidenceMemoryStore` بدرجة `trust = 1.0` و`artifact_verified = true`، بينما `DENIED` و`INCONCLUSIVE` و`FAILED` تُرفَض وتُسجَّل.
   - `GET /api/browser/status` في `command-hud` يعرض الحالة المقيسة بصدق (`READY` / `SKIPPED` / `BLOCKED` / `FAILED`) مع `mutations_performed = 0` وحالة مفتاح التجميد `hud_frozen`.
   - `./scripts/ember_up.sh --with-browser` يضيف فحص المتصفح المحلي اختياريًا دون أي تغيير على السلوك الافتراضي `15/15`.

---

## 3️⃣ ملاحظة مقيسة حول `GEMINI_API_KEY` و`RENDER_API_KEY` في GitHub Actions

التزامًا بقاعدة الشفافية:
- **`GEMINI_API_KEY`:** في آخر تشغيل على `main` (`run #44366752060`) نجحت جميع الوظائف الأساسية الـ12، بينما أعادت الوظيفة الاختيارية `Live Gemini call` رمز الخروج `1` (أحد ثلاثة أسباب يصنّفها `scripts/gemini_live_check.py`: صلاحية المفتاح، نفاد الحصة `429`، أو عدم توفر النموذج الافتراضي `gemini-2.0-flash`). ربطنا في هذه الدفعة متغيّر `vars.GEMINI_MODEL` داخل `ci.yml` كما يوثّقه `docs/SECRETS.md` بحيث يمكنك تبديل اسم النموذج (مثلًا `gemini-2.5-flash`) من `Settings → Variables → Actions` دون تعديل كود. **لا يؤثر ذلك على المرحلة 1 إطلاقًا** لأن النمط المحلي لا يستدعي أي نموذج لغوي.
- **`RENDER_API_KEY`:** مسجَّل ومربوط بأمان في `.github/workflows/deploy.yml` (تشغيل يدوي فقط، `0` تشغيلات حتى الآن). يُستَخدم في النشر المقيّد وفي المرحلة 3 (`remote_wss`)، مع مراعاة أن الخطط النائمة لا تصلح لخادم WebSocket مستمر (انظر [`docs/PHASE-2-DECISION.md`](PHASE-2-DECISION.md)).
- فحص الأمان الآلي `python3 scripts/workflow_secret_audit.py --assert` أخضر بالكامل.

---

## 4️⃣ أوامر التشغيل على جهازك (لتفعيل المرحلة 1 · النمط 1 حيًّا)

### 1. تثبيت `bsk v0.5.0` المقيّد ببصمة SHA-256
**Linux / macOS:**
```bash
./scripts/install_browserskill.sh
export PATH="$HOME/.local/bin:$PATH"
./scripts/install_browserskill.sh --check
```
**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_browserskill.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\install_browserskill.ps1 -Check
```

### 2. تثبيت الإضافة وبدء الديمون والإقران المحلي
- **Chrome:** `https://chromewebstore.google.com/detail/hhcmgoofomhgciiibhipgmgkgnoenaoi`
- **Edge:** `https://microsoftedge.microsoft.com/addons/detail/browserskill/emacgiaaaiojkkpkddmmdfhmokgmnikg`

```bash
bsk daemon start
bsk daemon pair          # الصق الرابط الخاص في الإضافة -> Connection settings -> Pairing link (لا تشاركه ولا تحفظه في Git)
```

### 3. فحص الجاهزية الصادق (قراءة فقط، صفر تغييرات)
```bash
python3 browser_skill_adapter/probe.py --report browser_skill_adapter/phase0-report.json
```

### 4. تنفيذ أول مهمة متصفح محلية موثّقة ببصمة SHA-256 + ذاكرة L1
```bash
# مهمة معزولة على صفحة عامة
python3 browser_skill_adapter/run_local_task.py \
  --url https://example.com \
  --incognito \
  --memory-db browser_skill_adapter/runtime/memory.db

# أو تشغيل منظومة AI-2027 محليًا وفحصها بالمتصفح
./scripts/ember_up.sh --with-browser --no-browser
python3 browser_skill_adapter/run_local_task.py \
  --url http://127.0.0.1:8090/ \
  --allow-localhost
./scripts/ember_up.sh --stop
```

---

## 5️⃣ أوامر الإثبات الآلي (تعمل الآن بلا متصفح وبلا شبكة وبلا مفاتيح)

```bash
python3 browser_skill_adapter/test_adapter.py -v        # 34 اختبارًا (سياسة، عزل، عدم إعادة، تنظيف، L1)
python3 scripts/test_install_browserskill.py -v         # 10 اختبارات للمثبّت المقيّد بـSHA-256
python3 scripts/test_secret_scan.py -v                  # 18 اختبارًا (شاملة توكنات وروابط BrowserSkill)
python3 scripts/secret_scan.py --all --scope code       # فحص كامل الكود: 0 تسريبات
python3 scripts/workflow_secret_audit.py --assert       # تدقيق أسرار GitHub Actions
python3 scripts/sbom.py --assert-pinned                 # جميع GitHub Actions مقيّدة بـSHA كامل
python3 command-hud/test_hud.py -v                      # 16 اختبارًا (شاملة /api/browser/status + Freeze)
./scripts/ember_up.sh --check --no-browser              # 15/15 فحص HTTP حقيقي دون أي انحدار
```

</div>
