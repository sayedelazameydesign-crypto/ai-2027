# 📋 تقرير الإنجاز التفصيلي — منصة Single-Worker Agent Loop

**التاريخ:** 2026-08-23 · **المستودع:** github.com/sayedelazameydesign-crypto/platform · **الفرع:** `main`

---

## 1. ملخص تنفيذي

بُنيت منصة وكيل ذكي (Agent Platform) كاملة من الصفر وفق نطاق MVP صارم:
**مهمة عبر المحادثة → خطة من 2–4 خطوات → تنفيذ أدوات متسلسل → بث مباشر للأثر عبر SSE → إجابة نهائية محفوظة في قاعدة البيانات.**

| المؤشر | النتيجة |
|---|---|
| اختبار القبول E2E | **12/12 ناجح** |
| تدقيق الفجوات مقابل الوثيقة | **28/28 مبني · 0 مفقود** |
| المزامنة مع GitHub | ✅ `main` مدفوع ومتطابق |
| انتهاكات الحدود الصارمة (Redis/orchestrators/migrations) | **صفر** |

---

## 2. المراحل الزمنية

### المرحلة 1 — التوثيق والحوكمة
- **`docs/MVP-BUILD-PROMPT.md`** — محفّز البناء الأول (8 أقسام: الهدف، القواعد، الحلقة، السجل، الـ sandbox، التوصيل، المستبعدات، شروط الإنجاز).
- **`docs/EXECUTION-ORDER-MVP.md`** — النسخة النهائية المعتمدة بعد مراجعة سطر-بسطر رصدت وأصلحت 5 مشاكل:
  1. 🔴 توقيع TypeScript مكسور (`Tool<I O="any">` → `Tool<I, O>`)
  2. 🟠 استعادة قيود أمنية ساقطة: مهلة 30s + سقف 1MB stdout + تنظيف البيئة لـ PATH
  3. 🟠 إلزام إغلاق متصفح Playwright في `finally`
  4. 🟡 إضافة بند تحقق من محتوى `plan:created` في اختبار القبول
  5. 🟡 تصحيح توصيف العزل (run-scoped sandbox، لا حاويات)
- **`scripts/gap-audit.sh`** — أداة تدقيق آلية: 5 أقسام، 28 فحصًا، عدّاد built/partial/missing.

### المرحلة 2 — قرار البنية
- المستودع الأصلي على GitHub كان **فارغًا تمامًا** (تحقق متكرر: صفر refs) — فتقرر البناء من الصفر.
- **التقنية المختارة:** Next.js 14 (App Router) + Drizzle ORM + SQLite (`better-sqlite3`) + Zod + Playwright.
- **مبرر SQLite:** صفر إعداد، والانتقال لـ MySQL إنتاجيًا = تغيير driver فقط دون مساس بكود الوكيل.

### المرحلة 3 — بناء الـ MVP (31 ملفًا)

#### آلة الحالة `src/agent/loop.ts`
```
RECEIVED → PLANNING → EXECUTING (step i of n) → SUMMARIZING → DONE / FAILED
                          └─ step error → RETRY (max 1) → FAILED
```
- مخطِّط: نداء LLM واحد + **repair retry واحد** عند فشل تحقق Zod، ثم فشل صريح بالخطأ الخام في الأثر.
- منفّذ: تسلسلي صارم، **سقوف:** 4 خطوات كحد أقصى، 60 ثانية/أداة (`AbortController`)، 5 دقائق/تشغيل، retry واحد/خطوة.
- ملخِّص: نداء LLM واحد + حفظ الإجابة في DB (تبقى بعد تحديث الصفحة حتى لو انقطع SSE).
- حل قوالب بين الخطوات: `{{steps.N.KEY}}`.

#### سجل الأدوات `src/agent/tools/` — 4 أدوات فقط، تحقق Zod مزدوج (دخل وخرج)
| الأداة | الضوابط الأمنية |
|---|---|
| `shell.exec` | `execFile` حصرًا (صفر shell interpolation)، قائمة بيضاء (node, python3, ls, cat, grep)، cwd مثبت على `/tmp/agent-runs/<runId>/`، مهلة 30s، سقف 1MB، بيئة منظفة لـ PATH فقط |
| `web.fetch` | مهلة 10s، قص الجسم عند 100KB |
| `browser.run` | chromium headless، متصفح واحد/تشغيل يُغلق في `finally`، مهلة تنقل 15s، منع تنزيلات ومصادقة |
| `fs.readwrite` | محصور بمجلد التشغيل، رفض path traversal |

#### محوّل LLM `src/lib/llm.ts`
- واجهة واحدة: `complete(messages, opts)`.
- مزود حقيقي عبر `OPENAI_API_KEY` (+ لاحقًا `OPENAI_BASE_URL` لأي مزود متوافق: OpenRouter/Groq/DeepSeek).
- **مخطِّط حتمي احتياطي** يثبت الحلقة كاملة بلا مفتاح — أساس نجاح الاختبارات دون اتصال خارجي.

#### البث والواجهة
- `src/lib/bus.ts` — ناقل أحداث بالذاكرة + KV مسطح لكل تشغيل (§2: يزول عند إعادة التشغيل، مقبول للـ MVP).
- SSE endpoint: `GET /api/runs/[runId]/events` — 7 أنواع أحداث: `run:start, plan:created, step:start, step:result, step:error, run:done, run:failed` مع إعادة تشغيل buffer للمنضمين متأخرًا.
- واجهة محادثة (`src/app/page.tsx`): عمود رسائل + **لوحة أثر SSE حية** ملونة حسب نوع الحدث.
- APIs مساندة: `POST /api/tasks`، `GET /api/messages`، `GET /api/tools`، `GET /api/health`.

#### قاعدة البيانات
- جدول واحد `messages` (Drizzle/SQLite) — **صفر migrations إضافية** التزامًا بالوثيقة.

### المرحلة 4 — التثبيت والإصلاحات (موثقة بالأثر الفعلي)
| # | العلة | الجذر | الإصلاح |
|---|---|---|---|
| 1 | فشل إطلاق chromium | مكتبات نظام ناقصة (`libnspr4.so`) | `playwright install-deps chromium` |
| 2 | المسار السلبي ينجح بدل أن يفشل | المخطِّط الحتمي قرأ رسالة الإصلاح كمهمة | القراءة دومًا من أول رسالة user |
| 3 | `ERR_NAME_NOT_RESOLVED` | regex الروابط التقط فاصلة ذيلية (`example.com,`) | قص علامات الترقيم الذيلية |

### المرحلة 5 — اختبار القبول `e2e/mvp-loop.test.ts` — النتيجة النهائية 12/12
- **المسار السعيد:** `run:start → plan:created → step:start/result ×2 → run:done`، خطة صالحة من السجل، `result.txt = "Example Domain"`، رسالة assistant في DB.
- **المسار السلبي:** أداة وهمية (`teleport`) → `run:failed` بخطأ Zod الخام، والعملية HTTP حية بعده.

### المرحلة 6 — المزامنة مع GitHub
- إنشاء `platform-repo.bundle` (نسخة كاملة قابلة للدفع من أي جهاز).
- محاولة أولى بتوكن fine-grained → **403** (صلاحية قراءة فقط) → تشخيص وإرشاد.
- توكن classic بصلاحية repo → **دفع ناجح:** `main → 26b2e61`، تطابق refs موثق.
- تنظيف: صفر توكنات في `.git/config`، تتبع `main...origin/main` سليم، **وحُذفت التوكنات من GitHub** (إجراء المالك).

### المرحلة 7 — تجهيز المفتاح الحي
- دعم `OPENAI_BASE_URL` في المحوّل + `.env.example` موثق.
- إعادة اختبار بعد التعديل: **12/12** (صفر انحدار).
- commit `a5a5af9` — محلي، ينتظر دفعة قادمة بتوكن جديد قصير العمر.

---

## 3. الحالة الراهنة

```
✔ الكود:      a5a5af9 (محلي) ← 26b2e61 (مدفوع على GitHub)
✔ الخادم:     حي — معاينة مباشرة على المنفذ 3000
✔ الاختبارات: e2e 12/12 · gap-audit 28/28
⏳ المعلق:     (أ) مفتاح LLM حقيقي  (ب) دفع a5a5af9 عند توفر توكن جديد
```

## 4. ما استُبعد عمدًا (الحدود الصارمة — §5)
Redis/BullMQ/queues · منسق 14-agent · إعادة تخطيط ديناميكي · CircuitBreaker · embeddings دلالية · جداول DB جديدة (حزمة 50+ جدول، 18 view) · عزل حاويات لكل مهمة — **كلها مؤجلة حتى يثبت الاستخدام الواقعي حاجة فعلية.**
