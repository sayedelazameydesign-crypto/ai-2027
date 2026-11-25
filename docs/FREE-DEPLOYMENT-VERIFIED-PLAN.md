# دليل تحقق النشر المجاني + خطة النشر الحقيقي
**نوع الوثيقة:** دليل تنفيذي — تحقق محلي فعلي + خطة نشر حقيقي (مجاني، بلا فيزا).
**التاريخ:** 2026-11-25
**الفرع:** `arena/de047a78-ai-2027` (وثيقة تحليلية محلية — لا تدخل PR #185)

> **القاعدة:** لا ادعاء إنجاز بلا دليل. كل نتيجة أدناه مُثبتة بأمر تم تنفيذه، وليس وعد.

---

## 1. تحقق محلي فعلي — تم، مجانا، بلا فيزا

تم تشغيل الخدمتين الحقيقيتين على هذا الآلين وتُحقّقا عبر HTTP فعلي:

### الخدمة A — `ai-2027-live-agent` (كود stdlib-only)
التشغيل: `AI2027_READ_ONLY=1 PORT=8095 python3 deployment/live_server.py`
النتيجة المقاسة:

| اختبار | الطلب | النتيجة | المتوقع |
|---|---|---|---|
| الصحة العامة | `GET /api/health` | **200** `{"ok":true,"service":"ai-2027-live-agent"}` | ✓ |
| قراءة خاصة fail-closed | `GET /api/runs` | **401** `authentication required` | ✓ |
| كتابة read-only fail-closed | `POST /api/run` | **403** `READ-ONLY: POST /api/run is disabled` | ✓ |

### الخدمة B — `ai-2027-studio` (الهدف الحقيقي للمنتج)
التشغيل: `AI2027_READ_ONLY=1 AI2027_MAX_SPEND_USD=0 ai2027-py27 studio --host 0.0.0.0 --port 8096`
النتيجة المقاسة:

| اختبار | الطلب | النتيجة | الملاحظة |
|---|---|---|---|
| صحة Studio | `GET /v1/studio/status` | **200** (JSON كامل) | `read_only:true`, `cost_usd:0.0`, `kernel_available:true` |
| الذاكرة | `memory.available` | **true** | `total_memories:0`, `refusals:0` |
| الميزانية | `budget.max_spend_usd` | **0.0** | `unknown_cost_blocked`, `paid_blocked` مفعلان |
| المزوّدون | `providers[]` | قائمة جاهزة | `ollama qwen3.5:0.8b` (configured:false — مزوّد محلي، لا تكلفة) |

**الاستنتاج المثبت:** كود النشر يعمل فعلاً، مجاناً، بلا فيزا، والبوابات fail-closed كما صُممت.

## 2. خطة النشر الحقيقي المجاني (Render free tier)

النشر يستهدف **`ai-2027-studio`** (ليس `ai-2027-live-agent`، الذي هو عرض محدود).

### الشروط المسبقة (لا تتخطى)
1. **P0 (#21):** ✅ مغلق (تم تأكيد الإبطال والتدوير).
2. **P4 (#185):** ⏳ يتطلب دمج محمي. **لا يُنشر قبل دمج P4.**
3. **Ruleset تصحيح:** ⏳ يتطلب تطبيقك في UI (API لا يسمح — الـ token بدون scope `administration:write`).

### خطوات النشر
1. في **Render**: New → Blueprint → اختر المستودع. `render.yaml` يُنشئ خدمتين (`ai-2027-live-agent` و`ai-2027-studio`)، كلاهما `plan: free`.
2. **تثبيت الاعتماديات:** خدمة Studio تستخدم `buildCommand: pip install -e python-2027` (تم التحقق من أنه يعمل محلياً في venv).
3. **أسرار Render (يدوياً، `sync: false`):**
   - `OPENROUTER_API_KEY` + `OPENROUTER_MODEL` (نموذج مجاني فقط، مثل `openrouter/free`).
   - `CONNECTORS_HUB_API_TOKEN` (إن وُجد).
   - متغيرات غير سرية: `AI2027_READ_ONLY=1`, `AI2027_MAX_SPEND_USD=0`.
4. **لا تُدخل أسرار GitHub Actions في Render تلقائياً** — `OPENROUTER_API_KEY` في render.yaml هو `sync: false`، يُدخل يدوياً في dashboard Render.
5. **التحقق من وجود الخدمة:** قبل أي deploy، أثبت أن `ai-2027-studio` موجود فعلياً في Render وحدد `service_id`.
6. **النشر المقصود والمراقب:** استخدم `deploy.yml` (يدوي) أو غيّر `DEFAULT_SERVICE_NAME` لـ `ai-2027-studio`. نشر Render free لا يحتاج فيزا.

### بوابات GO للإنتاج
لا يُصدر GO قبل إثبات:
- `ai-2027-studio` موجود فعلياً + `service_id` محدد.
- أسرار Render مضبوطة للخدمة الصحيحة.
- `AI2027_MAX_SPEND_USD=0`, `AI2027_READ_ONLY=1` خلال التحقق.
- اختبارات Health + API ناجحة (تم إثباتها محلياً).
- لا أسرار في المخرجات أو السجلات.
- P4 مدمج + ruleset مصحّح.

## 3. ما لا يُعتبر نشراً إنتاجياً
- **GitHub Pages** = واجهة ثابتة فقط، ليست تشغيل عقل Python.
- **التشغيل المحلي** = تحقق، ليس نشراً 24/7.
- **وجود أسرار في GitHub** = لا يعني وصول Render للخدمة.

## 4. الحاجز الحالي (الواقع الصادق)
- **لا أستطيع تطبيق تصحيح ruleset** عبر API (404/403 — الـ token بدون scope الكتابة). يعود التطبيق لك في UI.
- **لا أستطيع النشر إلى Render** مباشرة من هنا (يتطلب خطوة يدوية في dashboard Render + دمج P4 أولاً).
- **ما أثبته:** الكود يعمل فعلاً ومجاناً. **ما يبقى:** إيصاله إلى Render مع إغلاق P4 + تصحيح ruleset.
