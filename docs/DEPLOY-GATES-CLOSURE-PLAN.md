# خطة إغلاق بوابات النشر المجاني الحقيقي
**الهدف:** إيصال `ai-2027-studio` إلى Render (مجاني، بلا فيزا) مع إثبات التشغيل الفعلي.
**البوابات الثلاث:** تصحيح ruleset → دمج P4 → إنشاء خدمات Render → النشر والتحقق.
**التاريخ:** 2026-11-25
**الفرع:** `arena/de047a78-ai-2027` (وثيقة تحليلية محلية — لا تدخل PR #185)

> **مبدأ:** لا ادعاء إنجاز بلا دليل. كل بوابة لها مُنفّذ محدد ومعايير قبول قابلة للتحقق.

---

## التداخل بين البوابات

```
[المسار A: فك حجب P4]                    [المسار B: البنية التحتية]
  تصحيح ruleset (يدوي)        ← بالتزامن ←   إنشاء خدمات Render (يدوي)
        ↓                                            ↓
  #185 يصبح قابلاً للدمج                  الخدمتان موجودتان + service_id
        ↓                                            ↓
  دمج P4 --squash (ال agent)  ←───────────────  ضبط أسرار الخدمة
        ↓                                            ↓
        └────────────── تَلاقٍ ────────────────────────┘
                       ↓
              نشر Studio مقصود + تحقق verified_live
```

المساران **مستقلان** — يمكن السير فيهما بالتوازي. النشر النهائي يحتاج كليهما.

---

## البوابة 1 — تصحيح ruleset (تُفك حجب #185)

**المُنفّذ:** **أنت** (GitHub UI). الـ agent لا يستطيع PATCH (الـ token بدون scope `administration:write` — 404/403).

**المكان:** Settings → Rules → Rulesets → **Protect main** (#27819411) → تعديل → Required status checks.

**التغيير المطلوب (تم التحقق من كل فحوصة أنها ناجحة على SHA `e706bb0`):**

- **الإبقاء (6):**
  - `Deployment configs · must parse, must start, must cap the disk`
  - `Command HUD · real kernel + 100-attack gate + 48-project audit`
  - `Cloudflare Workers · Contract Tests + Wrangler Dry-Run ($0)`
  - `BrowserSkill local handoff · Windows ACL and process lock (simulated browser)`
  - `Analyze (python)`
  - `Analyze (javascript-typescript)`
- **الإضافة (4):**
  - `One-command recovery + real task under 60 s (T4 + T6)`
  - `Measured benchmarks + regression guard`
  - `Python 3.13 · uv.lock · ruff · mypy · pytest >90%`
  - `Python 3.14 · uv.lock · ruff · mypy · pytest >90%`
- **الإزالة (7):** `a`, `git`, `docs/GOVERNANCE.md`, `Cloudflare Edge Gateway · Node contract tests ($0)`, `Evidence gate · 11 gates incl. SIGKILL + two-process recovery`, `Dependabot`, `Apply to the configured Kubernetes cluster`

**النتيجة المتوقعة:** 10 فحوص إلزامية، كلها حقيقية وناجحة.

**معايير القبول (ال agent يتحقق):**
- `gh pr view 185 --json mergeStateStatus` → **CLEAN** أو **MERGEABLE** (ليس BLOCKED).
- `gh pr checks 185 --required` → كلها pass.

---

## البوابة 2 — دمج P4 (#185)

**المُنفّذ:** **ال agent** (بموافقة صريحة منك). لا `--admin`، لا `--auto`.

**الشرط المسبق:** إغلاق البوابة 1.

**الخطوة:** `gh pr merge 185 --squash`

**معايير القبول:**
- `gh pr view 185` → `state=MERGED`.
- `main` HEAD أصبح الـ squash commit.
- CI + CodeQL على `main` أخضر.

---

## البوابة 3 — إنشاء خدمات Render (يدوي)

**المُنفّذ:** **أنت** (dashboard Render). الـ agent لا يستطيع إنشاء Render services.

**الخطوات:**
1. في Render: **New → Blueprint → اختر المستودع** `sayedelazameydesign-crypto/ai-2027`.
2. الـ `render.yaml` يُنشئ خدمتين (كلتاهما `plan: free`، بلا فيزا):
   - `ai-2027-live-agent`
   - `ai-2027-studio` ← **الهدف الحقيقي للمنتج**
3. انتظر حتى تنتهي عملية الإنشاء + الـ build (`pip install -e python-2027`).
4. **احفظ `service_id` لكل خدمة** ( `srv-…` ) — مطلوب للنشر المقصود.

**معايير القبول (ال agent يتحقق):**
- الخدمتان موجودتان في Render (ليست قائمة فارغة).
- `ai-2027-studio` لها `service_id` محدد.
- الـ health check path (`/v1/studio/status`) يعطي 200 بعد الإنشاء.

---

## ضبط أسرار Render (يدوي، `sync: false`)

**المُنفّذ:** **أنت** (dashboard Render لكل خدمة). الأسرار في GitHub Actions لا تنتقل تلقائياً.

**في خدمة `ai-2027-studio`:**
- `OPENROUTER_API_KEY` (نموذج مجاني فقط)
- `OPENROUTER_MODEL` (مثل `openrouter/free`)
- `CONNECTORS_HUB_API_TOKEN` (إن وُجد)
- متغيرات غير سرية: `AI2027_READ_ONLY=1`, `AI2027_MAX_SPEND_USD=0`

**لا تُدخل أي قيمة سرية في المحادثة أو Git.**

---

## التَلاقٍ — النشر المقصود والتحقق

**المُنفّذ:** **ال agent**.

**الخطوة:** `gh workflow run "Deploy to Render" --ref main -f service_name="ai-2027-studio" -f confirm="ai-2027-studio"`

**معايير القبول (مثبتة من `render_deploy.py`):**
- exit 0 → `verified_live`: الـ deploy وصل لـ terminal state + `service_is_live()` يؤكد الحياة.
- **لا تُعتبر 200 على طلب الـ deploy نجاحاً** — فقط terminal state + liveness.
- لا أسرار في سجلات الـ Actions أو المخرجات.

---

## بوابات GO النهائية (لا تُتخطى)

لا يُصدر GO قبل إثبات **كل** ما يلي:
1. المفتاح القديم Gemini مبطل (✅ #21 مغلق).
2. P4 مدمج بعد المراجعة (البوابة 2).
3. Ember منفصل عن P4 (`93c3fa0` يُنقل كفرع/PR مستقل بعد الدمج).
4. `ai-2027-studio` موجود فعلياً + `service_id` (البوابة 3).
5. أسرار Render مضبوطة للخدمة الصحيحة.
6. `AI2027_MAX_SPEND_USD=0` + `AI2027_READ_ONLY=1` خلال التحقق.
7. اختبارات Health + API ناجحة.
8. لا أسرار في المخرجات أو سجلات التشغيل.

---

## ما لا يُعتبر نشراً إنتاجياً
- **GitHub Pages** = واجهة ثابتة فقط، ليست تشغيل عقل Python.
- **التشغيل المحلي** = تحقق، ليس نشراً 24/7.
- **وجود أسرار في GitHub** = لا يعني وصول Render للخدمة.

## الحالة الراهنة
- **#21 (P0):** ✅ مغلق.
- **#168 (Ember):** 🟡 تغيير `93c3fa0` محفوظ محلياً، غير دفوع.
- **PR #185 (P4):** 🟡 BLOCKED (13 فحصاً، 7 غير قابلة للتحقق).
- **Ruleset:** 🟡 لم يُعدَّل (13 فحصاً) — يعود تطبيقه للبوابة 1.
- **Render services:** 🟡 غير مُنشأة (dry run أثبت: المفتاح فعال + Render قابل للوصول، لكن `ai-2027-studio` غير موجود).
- **التحقق المحلي:** ✅ كلا الخدمتين تعملان مجاناً (دليل: `FREE-DEPLOYMENT-VERIFIED-PLAN.md`).
