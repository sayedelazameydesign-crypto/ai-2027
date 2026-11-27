<div dir="rtl">

# 🗄️ المرحلة الأولى — تدقيق الاستمرارية والذاكرة الدائمة (Persistence Audit)

**التاريخ:** 2026-11-26 · **النوع:** قراءة وتشغيل اختبارات فقط (READ-ONLY) · **الحالة:** مكتمل — بانتظار اعتماد التصميم
**النطاق:** `live-agent/` (الكتابة/القراءة) + `memory/store.py` + `projects/agi-system/core` (الدفتر) + `deployment/runtime_snapshot.py` + `deployment/PERSISTENCE.md`
**المبدأ الحاكم:** لا ترحيل قبل إثبات الاستعادة، ولا إعلان استمرارية قبل اختبار فقدان العامل فعلياً.

> **حدود الالتزام (نُفِذت):** لم يُعدَّل أي كود تشغيلي · لا جداول Cloudflare · لا `wrangler deploy` · لا تغيير `render.yaml` · لا أسرار في التقرير · لا لمس #190 · لا نقل أو حذف أي بيانات محلية (`live-agent/memory.db` و`runs/` و`sandboxes/` كما هي).

---

## 0. الخلاصة: نتائج البوابات

| # | البوابة | القرار | الخلاصة في سطر |
|---|---|---|---|
| G1 | قتل العامل أثناء المهمة | **BLOCKED** | الحماية من «مكتمل خطأً» مُثبتة (persist-أخير ذرّي + دفتر يُعاد تشغيله)، لكن **لا طابور مهمة دائم** = المهمة الجارية تضيع مع العملية، وأثر خارجي قد يتكرر (فجوة R2 الموثّقة) |
| G2 | إعادة تشغيل الخدمة | **PASS** | اختبار إعادة تشغيل **عملية حقيقية** يمرّ: `test_records_and_artifacts_survive_real_process_restart` + استعادة الفهرس من `runs/*.json` |
| G3 | الكتابة المتزامنة | **PASS** | قفل kernel مفرد + قفل ledger + قفل ذاكرة RLock + كتابة السجل الذرّية — 104+47+19 اختباراً تشمل سيناريوهات التزامن |
| G4 | نسخة احتياطية مستقلة قابلة للتحقق | **BLOCKED** | الأداة تتحقق (SHA-256 + SQLite backup API) لكن **لا توجد أي أرشيف نسخة في المستودع**، ولا تخزين مستقل، ولا تشفير/توقيع — الاستقلالية مسؤولية مشغّل غير مُهيّأة |
| G5 | الاستعادة | **PASS** | جولة backup→restore→تحقق فعلياً تُشغَّل اليوم:9 اختبارات تشمل استعادة سجل+أثر+دفتر+ذاكرة ورفض فحص التوقيع |
| G6 | وضع الأمان `AI2027_READ_ONLY=1` | **PASS** | مفروض في render.yaml (الخدمتان) + `server.py:210` + اختبارات حظر الكتابة + افتراض الحاوية/البلوبرنت |
| G7 | الميزانية (لا مزوّد مدفوع/مجهول) | **PASS** | `live-agent` **بلا أي عميل HTTP لمزوّد** (grep = 0) + مخطط حتمي + لا مفاتيح في الكود — مستحيل استدعاء مزوّد أثناء هذا التدقيق أو أثناء التشغيل |

**الحكم الكلي: الاستمرارية المحلية (نفس القرص) مُثبتة · الاستمرارية الموزّعة (قرص مستقل + طوابير مهام) غير مُثبتة → BLOCKED للترحيل.**

---

## 1. خريطة البيانات (Data Map)

كل عمليات القراءة والكتابة في `live-agent/`:

| البيانات | المسار | الكاتب | القارئ | مصدر الحقيقة | و primitive المتانة |
|---|---|---|---|---|---|
| **سجلات المهام** (DecisionRecords) | `runs/run_*.json` | `_persist_run_record()` — `server.py:714` | `GET /api/runs` + `_restore_run_index()` + الأرشفة | **الملف** (الذاكرة ` _run_index` قابلة لإعادة البناء منه) | ملف مؤقت `O_EXCL` → `flush` → **`fsync`** → **`os.replace`** ذرّي |
| **دفتر الأثر** (append-only, مرتبط تجزيئة) | `runs/ledgers/run_*.jsonl` | `EventLedger._write_line()` — نداء `write()` واحد + `flush` + **`fsync`** تحت قفل حصري | `replay()` / `fsck()` / `verify_chain()` | **الدفتر نفسه** — صراحةً «the ledger is the source of truth» (`kernel.py:12,447`) | JSONL append-only + سلسلة هاش + `threading.RLock` + أمان متعدد العمليات على POSIX |
| **آثار المهام** (sandbox) | `sandboxes/run_*/` | Kernel والأدوات أثناء التنفيذ | `GET /api/file` (Traversal-safe) + فحص الأثر في الذاكرة | مجلد الأثر + **بصمة SHA-256** داخل السجل والذاكرة | فهرس داخل السجل؛ المطابقة تُفحص عند الاسترجاع والنسخ الاحتياطي |
| **الذاكرة المنظمة** (SQLite) | `memory.db` (+ `state/memory.db` مع `--runtime`) | `EvidenceMemoryStore.ingest()` — قفل `RLock` + `commit` صريح بعد القبول **و** بعد الرفض («refusal MUST be durable») | `GET /api/memory` + `/api/memory/search` + `verify()`/`stats()` | **SQLite** (ذاتي الاتساق عبر backup API) | اتصال لكل thread (`threading.local`) + `timeout=30` + commit صريح؛ الأثر يُقبل فقط إذا كان `VERIFIED` ومطابق الهش |
| **فهرس الإقلاع** | مشتق من `runs/*.json` | لا يُكتب كملف أبداً | `_restore_run_index()` عند الإقلاع | مشتق (secondary) — تالف السجل يُتخطّا بتحذير ولا يُسقط الإقلاع | fail-soft: تحذير لكل ملف تالف (`server.py:599`) |
| **بيانات الاستعادة المؤقتة** | `deployment/` snapshot | `runtime_snapshot.py backup/restore` | `test_runtime_snapshot.py` | أرشيف `.tar.gz` + manifest JSON (SHA-256 لكل ملف) | نشر عبر `os.link` ذرّي · يرفض التحاور والـsymlinks |

**ملاحظات حاسمة من الخريطة:**
- **ترتيب التثبيت في `run_goal`** (`server.py:635-705`): `kernel.run` (دفتر + آثار) → `memory().ingest` → **`_persist_run_record` أخيراً** → تحديث الفهرس. أي قتل **قبل** الخطوة الأخيرة = لا يوجد سجل مكتمل أبداً (لا إكمال كاذب).
- **الذاكرة لا تُسقط مهمة أبداً**: `except Exception` حول `ingest` → `MEMORY_STORE_ERROR` مع بقاء المهمة (`server.py:676-684`).
- **الدفتر خارج الـsandbox**: النموذج لا يستطيع إعادة كتابة أدلة نفسه (`server.py:652`).
- `--fresh` **دمّري صريح** فقط؛ الوضع الافتراضي **حفظ** (`_prepare_state`).
- قفل `_lock` واحد = **مهمة واحدة في كل وقت** داخل العملية (server.py:74) — التزامن محمول على هذا القفل لا على القرص.

---

## 2. سلامة التخزين (Storage Integrity)

| المكوّن | ما وُجد | الحكم |
|---|---|---|
| **معاملات SQLite** | `executescript(SCHEMA)` + `commit` صريح بعد كل إدراج/رفض · لا `PRAGMA journal_mode=WAL` صريح ⇒ الوضع الافتراضي للإصدار المثبّت (journal افتراضي، `synchronous` افتراضي = FULL لوضع rollback). `PERSISTENCE.md` يتعامل مع ملفات `-wal/-shm/-journal` دفاعياً (`_SQLITE_SUFFIXES`) | ✅ مقبول — الالتزامات صريحة؛ **ملاحظة تصميمية:** تفعيل WAL صراحةً خيار تحسين (قراءات متزامنة أفضل) وليس شرطاً للسلامة الحالية |
| **القفل** | ذاكرة: `threading.RLock` حول الكتابة + اتصال لكل thread · دفتر: `RLock` + (POSIX متعدد العمليات) · kernel: قفل `_lock` مفرد | ✅ لا سباق كتابة معروف داخل العملية |
| **الكتابة المتزامنة** | سجلات المهام: أسماء فريدة `run_{ts}_{uuid8}` + استبدال ذرّي = لا تعارض بين مهام · دفتر لكل مهمة ملف منفصل | ✅ مُثبت اختبارياً (`test_concurrent_appends_stay_consistent`, `test_run_many_is_thread_safe` ضمن 104) |
| **انقطاع أثناء الكتابة** | سجل: tmp+fsync+replace — قتل قبل `replace` = السجل القديم/الغياب فقط، لا ملف نصف مكتوب · دفتر: سطر واحد `write()` + fsync — سطر ناقص نهائي يُكتشف بفشل `verify_chain`/`replay` ولا يُبتلع صامتاً · SQLite: rollback journal يعيد الحالة السابقة | ✅ مُثبت اختبارياً (انظر G1 أدناه — الدفتر يُعاد بناؤه) |
| **الحماية المكانية** | رفض symlinks في كل مسار تشغيل (`_reject_symlink_components`) + أذونات `0700/0600` + `O_NOFOLLOW` + وضع loopback افتراضي | ✅ |
| **فجوة R2 الموثّقة (خارج live-agent)** | [`PAP-2027-RESILIENCE-AUDIT.md`](PAP-2027-RESILIENCE-AUDIT.md): في مسار الموصلات، الحفظ يحدث **بعد** الأثر الخارجي (`agent.py:506-517`) ⇒ قتل بين الأثر والحفظ = **قد يُعاد الأثر**؛ لا `idempotency_key` في المستودع | ❌ مفتوحة (P0.2) — تخص الوكلاء/الموصلات لا خادم live-agent |

---

## 3. الاستمرارية: دورة النسخ الاحتياطي كاملة

| المرحلة | الواقع | الدليل |
|---|---|---|
| **الإنشاء** | `python3 deployment/runtime_snapshot.py backup <runtime> <archive>` — **يدوياً ويتطلب إسكات الخدمة أولاً** (offline بالتصميم: «stop the service or otherwise quiesce all writes first») | `PERSISTENCE.md` + `runtime_snapshot.py:195` |
| **التقاط متسق** | SQLite عبر **backup API** (وليس نسخ ملف) + تشغيل يشمل `runs/` + `sandboxes/` + `state/` كجيل واحد + manifest بأحجام و**SHA-256** لكل ملف | `_sqlite_backup` + `manifest` (runtime_snapshot.py:226-246) |
| **مكان التخزين** | يُرفض الأرشيف داخل الـruntime (`backup must be outside the runtime directory`) — لكن **الخارج = مسار يختاره المشغّل على نفس القرص غالباً** | `_absolute_no_links` + رفض الوجهة الموجودة |
| **الاستقلال عن قرص العامل** | ❌ **لا يوجد**: (1) صفر أرشيف `.tar.gz` في المستودع كله (find = 0) · (2) لا رفع لأي تخزين (الأداة «does not encrypt/upload backups, provision storage») · (3) بلا تشفير ولا توقيع («not encrypted or signed») | `PERSISTENCE.md:101` + find اليوم |
| **التحقق من البصمة** | ✅ SHA-256 لكل ملف في manifest + تحقق عند الاستعادة يرفض أي عدم تطابق **قبل** النشر + `quick_check` لـSQLite + لا يُنشر جزئياً | `test_restore_detects_hash_mismatch_without_overwriting` ✅ مُشغَّل |
| **الاستعادة** | ✅ إلى مجلد معزول جديد فقط (يرفض الموجود) + يتحقق من المسارات والأنواع والـmanifest ثم ينشر + أذونات owner-only | `restore` + اختبار الجولة الكاملة (G5) |
| **الاختبار** | ✅ `deployment/test_runtime_snapshot.py` (9) + `scripts/test_runtime_restart.py` (1 — إعادة تشغيل **عملية حقيقية**) + `deployment/test_deployment.py` (47، تقتل عمليات فعلياً) | **مُشغَّلة اليوم — كلها OK** |

**الخلاصة:** الأداة والتحقق والاستعادة **مُثبتان**؛ **الاستقلالية والجدولة والتشفير غير موجودين** ⇒ G4 = BLOCKED.

---

## 4. عقد التخزين المقترح: `PersistencePort`

**الوضع الحالي (يجب ألا يتغيّر):** ثلاثة كُتب مباشرين بلا واجهة واحدة:
1. `_persist_run_record()` — ملفات JSON (ذرّي)
2. `EventLedger` — JSONL مرتبط تجزيئة (مصدر حقيقة المهام)
3. `EvidenceMemoryStore` — SQLite (الذاكرة المنظمة)

**التصميم المقترح (دراسة فقط — لا تنفيذ):**

```text
PersistencePort (Protocol / ABC — سلوكي، لا بيانات)
├── save_run_record(payload) -> None            # ذرّي، يرفض run_id غير صالح
├── load_run_records() -> Iterator[Record]      # fail-soft لكل ملف
├── ledger() -> LedgerPort                      # append/replay/fsck (العقد الحالي كما هو)
├── ingest_evidence(payload) -> Verdict         # قبول/رفض + سبب دائم
├── query_memories(...) -> rows
└── generations() -> {backup(archive), restore(archive, dest), verify(archive)}

Adapters:
├── LocalFsAdapter      = السلوك الحالي حرفياً (الافتراضي — لا سلوك جديد)
├── D1Adapter           = مستقبلي: السجلات + صفوف الذاكرة (بنية منظمة)
└── R2Adapter           = مستقبلي: الآثار + الأرشيفات (ملفات)
```

**قواعد عدم كسر العقد:**
- `LocalFsAdapter` هو الافتراضي ويُثبَّت باختبارات اليوم كما هي (نفس الملفات، نفس الأذونات، نفس الرفض) — **صفر تغيير سلوك**.
- الاستخدام الحالي (`server.py`, `memory/store.py`) لا يُعدَّل؛ يُضاف الواجهة **فوق** الكود القائم، والتحويل إلى Adapter يكون تدريجياً بمقارنة سلوك (diff test: نفس المخرجات قبل/بعد).
- **لا D1/R2 قبل:** (أ) بوابات G1–G5 تُغلق، (ب) اختبار restore ينجح على الجيل الكامل، (ج) قرار المالك على المزوّد والاحتفاظ والتشفير (كما يشترط `PERSISTENCE.md`).
- كل adapter جديد **يحمل اختباراته معه** (شرط AGENTS.md) ولا يُفعَّل إلا بقرار صريح في الإعداد لا تلقائياً.

---

## 5. تصميم التخزين الخارجي (بافتراض عدم الاتصال)

**تصنيف البيانات — لا يفترض أن D1 أو R2 متصلان أو منشوران:**

| الطبقة | ماذا تحمل | لماذا |
|---|---|---|
| **D1 (SQLite-managed)** | جدول `runs` (metadata: run_id, status, goal_type, timestamps, cleanup) · جدول `memories` + `admission_log` (نسخة عن SQLite الحالي) · جدول `task_states` **الجديد** (لإغلاق G1: `PREPARED→EXECUTING→UNKNOWN→SUCCEEDED` مع `idempotency_key` — تنفيذ P0.2 من تقرير PAP) | بيانات منظمة صغيرة، استعلامات بسيطة، معاملات D1 تكفي |
| **R2 (object storage)** | `sandboxes/**` (آثار) · `runs/ledgers/*.jsonl` (أدلة) · أرشيفات `runtime_snapshot` (ك.objects بنسخ مُرقّمة) | ملفات append-only/كبيرة، لا حاجة لمعاملات، R2 يعطي خصوصية + versioning |
| **يبقى محلياً** | ذاكرة العمل أثناء التنفيذ + SQLite كـcache قابل للإعادة بناء من D1 | عدم جعل القرص المحلي شرط اكتمال المهمة |

**الاتساق (قبل أي نقل):**
1. **جيل واحد = التزام واحد**: نفس نمط `runtime_snapshot` اليوم — D1 يكتب `generation_id` وR2 يُسمّى بالاسم نفسه، ولا يُعلن الجيل كاملاً إلا بعد تحقق الطرفين.
2. **المصدر الحقيقة يبقى واحداً لكل نوع**: السجلات والذاكرة → D1 أولاً؛ الأثر والدفتر → R2 أولاً؛ المحلي يصبح نسخة عمل.
3. **`idempotency_key` إلزامي** على أي أثر خارجي (شرط G1 الإقفال — P0.2).
4. **النسخ الاحتياطي يصبح مزدوجاً**: أرشيف محلي (الأداة الحالية) + جيل D1/R2 في مجال فشل مستقل — ثم اختبار restore من **كلا** الاتجاهين قبل الإعلان.
5. **الوضع `AI2027_READ_ONLY=1`**: أي adapter جديد يرفض الكتابة قبل فحصه (نفس `server.py:210`) — الاختبار يسافر معه.

---

## 6. تحليل حالات الفشل (Failure Modes)

| # | الفشل | السلوك الحالي | النتيجة |
|---|---|---|---|
| F1 | قتل أثناء `kernel.run` | لا سجل يُكتب أبداً (persist أخير) · دفتر يحمل الأحداث الجزئية · sandbox جزئي | ✅ لا إكمال كاذب · ❌ المهمة الجارية تضيع (لا طابور) |
| F2 | قتل بين `ingest` و `_persist_run_record` | ذاكرة قد تحمل صفّاً بلا سجل مقابل — لكن القبول شرطه `VERIFIED` وسجل موجود بعد؛ والاختبار `verify()` يكشف اليتامى (`orphans`) | ⚠️ يتطلب تنظيفاً دورياً — موجود كفحص لا كإجراء تلقائي |
| F3 | قتل أثناء `os.replace` | ذرّي: إما القديم أو الجديد كلياً | ✅ |
| F4 | سطر دفتر ناقص عند الإقلاع | `replay()` يفشل التحقق السلسلة → خطأ صريح ولا يُبتلع | ✅ fail-closed |
| F5 | ملف سجل تالف عند الإقلاع | تحذير + تخطّي، الإقلاع يستمر | ✅ fail-soft (السجل التالف لا يُشطب) |
| F6 | قاعدة ذاكرة تالفة | `verify()`/`quick_check` في النسخ الاحتياطي + F4-style لا يُسقط الخادم (ingest محاط) — لكن رواية PAP R4 تهمّ `agent.py:361` في مسار آخر (بلا try) | ⚠️ R4 مفتوحة خارج هذا النطاق |
| F7 | استعادة جزئية/خبيثة | رفض عدم تطابق الهش قبل النشر + رفض مسارات التحاور + رفض وجهة موجودة | ✅ |
| F8 | فقد القرص بالكامل | لا أرشيف مستقل إطلاقاً | ❌ G4 |

---

## 7. اختبارات الإثبات (شُغِّلت اليوم — كلها OK)

| البوابة | الأمر | النتيجة |
|---|---|---|
| G2/G5 الاستعادة | `python3 deployment/test_runtime_snapshot.py` | **9 OK** (جولة كاملة + رفض فحص التوقيع/التحاور/symlink) |
| G2 إعادة تشغيل فعلية | `python3 scripts/test_runtime_restart.py` | **1 OK** — `test_records_and_artifacts_survive_real_process_restart` |
| G1/G3 قتل وتزامن | `python3 deployment/test_deployment.py` | **47 OK (1 skipped)** — تقتل عمليات حقيقية (`proc.kill()`) وتفحص بقاء السجلات وسلامة عملية الإزالة (pruning) |
| G3 تزامن النواة + استعادة الانقطاع | `python3 -m unittest discover -s projects/agi-system/tests -t projects/agi-system` | **104 OK** — تشمل `test_resume_replays_a_crashed_run` + `test_concurrent_appends_stay_consistent` + `test_run_many_is_thread_safe` |
| ذاكرة SQLite | `python3 memory/test_memory.py` | **19 OK** |
| خادم live-agent | `python3 live-agent/test_server.py` | **23 OK** — تشمل `test_read_only_switch_blocks_a_valid_operator_token` و`test_runtime_is_restored_by_default_and_reset_only_when_explicit` |
| G6 وضع الأمان (إضافي) | `deployment/test_deployment.py::test_06` و`test_24` | ضمن الـ47 — blueprint/حاوية افتراضياً read-only ✓ |
| G7 الميزانية | `grep -nE "httpx|urllib.request|requests\.|http.client" live-agent/*.py` | **صفر نتائج** — لا عميل مزوّد موجود أصلاً |

> **G1 بالتفصيل:** الاختبارات تُثبت: (أ) لا إكمال كاذب عند القتل، (ب) استعادة سجلات وأثر بعد إعادة تشغيل عملية حقيقية، (ج) `resume` يعيد بناء مهمة متعطلة من الدفتر. **ما لا تُثبته:** بقاء مهمة **جارية** كطلب دائم (لا طابور)، ومنع تكرار أثر خارجي (R2/idempotency). لذلك القرار **BLOCKED** — وليس لأسباب تخزينية بل لغياب طبقة الطابور/المفاتيح.

---

## 8. الترتيب المقترح للإغلاق (قبل أي كتابة)

1. **P0.2 أولاً**: سجل عمليات دائم + `idempotency_key` + حالات `PREPARED→EXECUTING→UNKNOWN→SUCCEEDED→RECONCILED→VERIFIED` (يُغلق G1 وجزء R2) — يبدأ كطبقة فوق `PersistencePort` لا كتعديل على الدفتر.
2. **`PersistencePort` + `LocalFsAdapter`** كنقل سلوكي (اختبارات اليوم هي مرجع المطابقة) — لا D1/R2 بعد.
3. **جدولة النسخ الاحتياطي**: مهمة دورية تُسكّل الخدمة أو تستخدم نسخاً متسقة عبر backup API + رفع لمكان مستقل (قرار المالك: أين) — يُغلق G4.
4. **تفعيل G4 بإثبات restore من الموقع المستقل** — ثم فقط يُنظر في D1/R2 كمرحلة تالية بكل قواعد §5.

**بوابة الانتقال إلى التنفيذ:** مراجعة هذا التقرير + اعتماد تصميم `PersistencePort` + قرار المالك على موقع النسخ المستقل. لم يُكتب أي كود في هذه المرحلة.

---

*مبدأ تذكاري: لا ترحيل قبل إثبات الاستعادة · لا إعلان استمرارية قبل اختبار فقدان العامل فعلياً · الأرقام أعلاه مُشغَّلة في2026-11-26 على `1b3d8ad` (التقرير نفسه untracked وغير مُدمج مع #190).*
