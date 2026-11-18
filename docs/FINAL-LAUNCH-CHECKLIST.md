<div dir="rtl">

# ✅ قائمة الإطلاق النهائي — AI-2027

**التاريخ:** 2026-11-18 · **الحالة:** جاهز للتنفيذ بعد بوابات المالك · **النطاق:** إطلاق صادق بالدليل، لا ادعاءات تسويقية بلا قياس

> **تصحيح معماري مهم:** الافتراض «GitHub Pages ثابتة + Cloudflare Workers مجانًا» **لا ينطبق على خدمة الوكيل الحالية كما هي**.
> `live-agent/server.py` هو Python stdlib يستخدم ملفات محلية وSQLite و`ThreadingHTTPServer`؛ Cloudflare Workers يعمل بـ JavaScript ولا يوفر نظام ملفات دائمًا أو SQLite محليًا. لذلك:
>
> - **GitHub Pages** = الموقع الثابت والعرض التفاعلي `website/demo.html` فقط.
> - **خدمة الوكيل الحية** = تحتاج مضيف Python (Render / Hugging Face Spaces / Docker) أو إعادة كتابة JS كاملة لـ Workers مع D1/R2/KV ومصادقة وحد معدل.
> - لا تُعلن «خدمة وكيل على Cloudflare Workers» قبل تنفيذ إعادة الكتابة واختبارها.

---

## 1) لقطة الحالة المُتحقق منها

**آخر تحديث:** 2026-11-18 · SHA المفحوص `0860caab868652d4698c7a6b2dee4ca5c520faf5`.

| العنصر | الحالة | الدليل |
|---|---|---|
| SHA الفحوص | `0860caab868652d4698c7a6b2dee4ca5c520faf5` (كان على `arena/5c3f2a4a-ai-2027` عند الفحص) | `git log -1`؛ نتائج CI وCodeQL مرتبطة بهذا SHA فقط |
| PR #46 | مدمج في `main` | أُغلق بالدمج في 2026-11-18T15:44:23Z |
| CI | ناجح على SHA المفحوص | Evidence-First CI run `44339102463` |
| CodeQL | ناجح على SHA المفحوص | CodeQL run `44339102319` |
| GitHub Pages | API: `built`، `build_type=workflow`، HTTPS مفروض؛ **استجابة الرابط العام غير متحققة** | Pages API؛ آخر تشغيل Pages ناجح `44129279960` على SHA أقدم `fc4fb3d` (2026-11-17) |
| الإصدار | `v1.0.1` هو الأحدث | `gh release list -L 1` |
| تدقيق الروابط الحية | لا تشغيلات بعد | `gh run list --workflow live-egress-audit.yml` أعاد `[]` |
| المفتاح المكشوف | Issue #21 ما زالت **OPEN**؛ لا وقت إبطال موثق فيها حتى آخر تحقق | `gh issue view 21`؛ لا يُعد إغلاق/تعليق التذكرة وحده دليلًا على الإبطال |
| `new-agent` | `LICENSE` تالف (4 بايت)، وقرار المالك ما زال معلقًا | `docs/LICENSE-DECISIONS.md`; مصنّف `FAILED` بالفحص لا بفشل تشغيل مثبت |

---

## 2) بوابات مانعة قبل أي إعلان نهائي

### 🔴 P0 — مفتاح Gemini المكشوف في `ai-store2`
- [ ] مالك حساب Google يُبطل المفتاح القديم من Google AI Studio؛ يُنشئ بديلًا فقط إن استمر الاحتياج.
- [ ] يُوثّق في Issue #21: **وقت الإبطال UTC + عبارة «تم الإبطال» فقط** — ممنوع نشر أي قيمة مفتاح.
- [ ] تنظيف تاريخ `ai-store2` من المفتاح القديم إجراء اختياري للنظافة وتقليل ظهوره، وليس شرطًا لإبطال صلاحيته بعد الإبطال، ولا يغني عن الإبطال.
- [ ] لا يُضاف أي مفتاح صالح إلى ملف متعقَّب؛ `.env.local` يبقى خارج Git.

**متابعة ما بعد الإبطال:** يراجع المالك سجلات الاستخدام/الفوترة التي تتيحها Google للفترة السابقة للإبطال بحثًا عن نشاط غير متوقع، ويوثّق النتيجة وحدود السجلات المتاحة في #21 من دون قيم أسرار. الإبطال ينهي صلاحية المفتاح مستقبلًا، لكنه لا يثبت عدم استخدامه قبل ذلك ولا يمحو أثر استخدام سابق.

> حتى اكتمال بوابة الإبطال والتوثيق: **لا إطلاق نهائي**، حتى لو كان الموقع الثابت يعمل.

### 🔴 ترخيص `new-agent`
- [ ] إما استبعاده صراحةً من مواد الإطلاق، أو تحديد الترخيص الأصلي من المالك واستبدال `projects/new-agent/LICENSE` به.
- [ ] ممنوع تخمين MIT أو أي ترخيص آخر.

### 🟠 حدود التسويق المرتبطة بالتراخيص
- [ ] لا يُقال «المنظومة مفتوحة المصدر بالكامل» أو «48 مشروعًا مفتوحًا».
- [ ] `WEB` و`celia-pro-deployment` ملكية خاصة ومستبعدان من أي ادعاء مفتوح المصدر.
- [ ] `agi-system` بلا ترخيص محسوم: يمنع PyPI والتسويق كمفتوح المصدر حتى قرار المالك.

### 🟠 أمان خدمة الوكيل العامة
- [ ] النشر العام الافتراضي: `AI2027_READ_ONLY=1`.
- [ ] تفعيل الكتابة (`POST /api/run`) فقط خلف مصادقة حقيقية + حد معدل + حصة لكل IP/مستخدم.
- [ ] لا يُكشف `live-agent/server.py` خامًا على الإنترنت: لا مصادقة، لا حد معدل، وسقف القرص وحده ليس بديلًا عن المصادقة.

---

## 3) فحص ما قبل الدمج/النشر

```bash
git status --short
python3 -m unittest discover -s projects/agi-system/tests -t projects/agi-system
python3 live-agent/test_server.py
python3 deployment/test_deployment.py -v
python3 scripts/generate_launch_evidence.py --assert
python3 launch/test_launch_claims.py -v
python3 command-hud/audit.py --assert-probes
python3 scripts/secret_scan.py --all --scope code
python3 scripts/sbom.py --assert-pinned
gh run list --branch main -L 5
gh api repos/sayedelazameydesign-crypto/ai-2027/pages --jq '{build_type,html_url,https_enforced,status}'
```

معايير القبول:
- [ ] كل الأوامر أعلاه تنجح أو تُوثّق استثناؤها بدليل.
- [ ] Evidence-First CI وCodeQL أخضر على نفس الـ SHA الذي سيُنشر.
- [ ] لا أسرار جديدة في `git diff` ولا في مخرجات الأدلة.

---

## 4) نشر الموقع الثابت (GitHub Pages)

1. ادمج التغييرات إلى `main`.
2. لاحظ: `pages.yml` يعمل عند تغيير `website/**` أو ملف الـ workflow نفسه، أو يدويًا عبر `workflow_dispatch`.
3. إن لم يتغير `website/`، شغّل يدويًا:
   ```bash
   gh workflow run pages.yml --ref main
   gh run watch --exit-status
   ```
4. تحقق من:
   - `GET /pages` يعيد `status: built` و`build_type: workflow`.
   - `https://sayedelazameydesign-crypto.github.io/ai-2027/` يعيد 200.
   - `https://sayedelazameydesign-crypto.github.io/ai-2027/demo.html` يعيد 200.
   - `kernel-demo.js` يُجلب بنجاح ولا توجد سكربتات خارجية في حزمة `website/`.
5. إن ظهرت تشغيلات Jekyll قديمة حمراء: لا تعني فشل الموقع؛ أصلحها بنقرة المالك `Settings → Pages → Source → GitHub Actions` إن أراد إزالة الضوضاء.

---

## 5) نشر خدمة الوكيل الحية (اختياري لكنه مسار الإطلاق الكامل)

### الخيار الموصى به الآن: مضيف Python
- Render أو Hugging Face Spaces أو أي مضيف Docker.
- المتغيرات:
  ```bash
  AI2027_READ_ONLY=1
  AI2027_MAX_DISK_MB=256
  AI2027_MAX_RUNS=5000
  PORT=<provided-by-host>
  HOST=0.0.0.0
  ```
- ملاحظة صدق: `deployment/README.md` يذكر `render.yaml` و`Dockerfile` غير موجودين في الشجرة الحالية؛ قبل استخدام Blueprint/Docker أنشئهما وتحقق منهما بالاختبارات، أو استخدم إعدادات المضيف اليدوية.

### تحقق ما بعد النشر
- [ ] `GET /` يعيد الواجهة.
- [ ] `GET /api/health` يعيد `ok: true`.
- [ ] `GET /api/metrics` يعيد عدادات التشغيل/الذاكرة.
- [ ] في الوضع القرائي: `POST /api/run` مرفوض بسبب صريح معلن (READ-ONLY).
- [ ] لا أسرار في متغيرات البيئة العامة ولا في السجلات.

### إن أردت Cloudflare Workers لاحقًا
- [ ] أعد كتابة النواة/الغلاف بـ JavaScript أو TypeScript.
- [ ] استبدل SQLite/الملفات بـ D1/R2/KV مع سياسة احتفاظ واضحة.
- [ ] أضف مصادقة وحد معدل قبل أي `POST` عام.
- [ ] أعد تشغيل اختبارات العقد والدخان على بيئة Workers قبل تغيير الادعاء.

---

## 6) تدقيق الروابط الحية بعد النشر

```bash
gh workflow run live-egress-audit.yml --ref main
gh run watch --exit-status
```

ثم راجع فرع `live-egress`: `EGRESS.md` و`egress/status.json`.

- [ ] لا `regression` للروابط المُدّعاة `live`.
- [ ] أي `recovered` يُحدَّث يدويًا في `docs/public-urls.json` بعد مراجعة بشرية.
- [ ] لا تُحدَّث الادعاءات آليًا من نتيجة تشغيل واحدة.
- [ ] نجاح البناء وحده لا يُسجَّل كـ«رابط حي».

---

## 7) إعدادات GitHub اليدوية (4 نقرات)

1. `Settings → General → About → Description`
2. `Topics`: `ai-agents agent-framework agentic-ai autonomous-agents llm open-source python typescript verification deterministic arabic`
3. `Website`: `https://sayedelazameydesign-crypto.github.io/ai-2027/`
4. `Settings → General → Features → Discussions` ✅
5. اختياري: صورة معاينة اجتماعية 1280×640.

---

## 8) بوابة الإعلان

نص الإعلان في `launch/static-launch-announcement.md` نسخة قابلة للنشر ولا يحتوي تذكيرًا داخليًا بالبوابات؛ هذه القائمة هي المرجع المانع للنشر المبكر.

لا يُنشر إلا بعد كل ما يلي:
- [ ] Issue #21 مُعلَّق عليه بوقت الإبطال ومُغلق أو موثّق كمنجز من المالك.
- [ ] قرار/استبعاد `new-agent` موثّق.
- [ ] Pages حي ومُتحقق منه خارجيًا.
- [ ] تدقيق `live-egress` مُراجع ولا توجد انحدارات غير مفسَّرة.
- [ ] إن نُشرت خدمة الوكيل: الرابط العام يعمل في الوضع القرائي أو خلف المصادقة.
- [ ] كل رقم في مادة النشر له دليل في `launch/PROOF-LEDGER.md` وأُعيد قياسه.

---

## 9) بعد الإطلاق: أول 24 ساعة / 7 أيام

- [ ] راقب Actions: CI/CodeQL/Pages/live-egress.
- [ ] راقب `live-egress` كل 6 ساعات؛ أي `regression` يُعالج بإصلاح النشر أو تصحيح الادعاء.
- [ ] راقب `/api/health` و`/api/metrics` وميزانية التشغيل/القرص لخدمة الوكيل.
- [ ] أعد فحص الأسرار بعد أي تغيير في `website/` أو `live-agent/` أو `deployment/`.
- [ ] حدّث `docs/public-urls.json` فقط عندما يوجد قياس جديد موثّق.
- [ ] أغلق/حدّث مهام الترخيص المعلقة (`agi-system`, `ccelia2027`, 22 مشروعًا بلا ترخيص) فرادى عند الحاجة.

### التراجع السريع
- خدمة الوكيل: اجعلها `AI2027_READ_ONLY=1` أو أوقفها.
- الموقع: أعد إصدار `website/` السابق أو أعد تشغيل `pages.yml` على الـ SHA السليم.
- الادعاءات: إن مات رابط مُدّعى، صحّح `docs/public-urls.json` ولا تترك رابطًا ميتًا يُقدَّم كحي.

---

## تعريف «تم»
الإطلاق مكتمل فقط عندما يكون الموقع الثابت حيًا، تدقيق الروابط مُراجعًا، المفتاح مُبطلًا وموثّقًا، قرار `new-agent` محسومًا أو مستبعدًا، وخدمة الوكيل — إن وُجدت — تعمل بالوضع الآمن المعلن. غير ذلك فهو **جاهزية تقنية** لا إطلاق نهائي.

</div>
