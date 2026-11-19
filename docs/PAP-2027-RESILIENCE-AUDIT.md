# PAP-2027 — تدقيق المرونة واحتواء الأعطال (Phase A، قراءة فقط)

- **التاريخ:** 2026-11-19
- **النطاق:** `python-2027/` · `projects/agi-system/core/` · `memory/` · `live-agent/` · `command-hud/` · `connectors-hub/` · `deployment/` · `.github/workflows/`
- **طبيعة العمل:** **قراءة وتشغيل أدوات فحص فقط** — لم يُعدَّل أي كود منتج في هذه المرحلة. الملف الوحيد المضاف للكود هو أداة إعادة إنتاج يدوية.
- **ما لم يُفعل هنا:** لا تغيير في `Kernel`/`PolicyEngine`/`Executor`/`Verifier`/`EventLedger`، ولا محرّك جديد، ولا بدء لـ`agent-fleet`.

## كيف يُقرأ هذا المستند

كل ادّعاء يحمل تصنيفًا واحدًا، ولا تُخلط التصنيفات:

| التصنيف | المعنى |
|---|---|
| **PROVEN** | مُقاس بتنفيذ حقيقي، والأمر معه لإعادة إنتاجه |
| **TESTED LOCALLY** | اختبار قائم في المستودع أو أداة شُغّلت هنا ونجحت |
| **READ FROM CODE** | مقروء من الكود بمرجع سطر؛ لم يُنفَّذ سيناريو له |
| **SIMULATED** | محاكاة صريحة، ليست دليلًا حيًّا |
| **NOT EXECUTED** | لم يُختبر في هذه الجلسة |

---

## 1️⃣ المعمارية كما وُجدت (A)

```
                         ┌──────────────────────────────────────────┐
   المستخدم ─────────────▶│  Control Plane                           │
                         │  Studio (FastAPI HTTP+SSE) · CLI · HUD   │
                         └───────────────┬──────────────────────────┘
                                         │ AgentTaskRequest
                         ┌───────────────▼──────────────────────────┐
                         │  python-2027 · AutonomousAgent           │
                         │  stream_run → plan → step → tool → save   │
                         └───┬───────────┬───────────┬──────────────┘
             ┌───────────────┘           │           └───────────────┐
             ▼                           ▼                           ▼
   ┌──────────────────┐      ┌────────────────────┐      ┌────────────────────┐
   │ L1 Memory        │      │ L4 Router          │      │ ToolExecutor       │
   │ memory/ (SQLite) │      │ Gemini/NVIDIA/Groq │      │ tools.py (10 أدوات)│
   │ admission gate   │      │ OpenRouter/local   │      │ timeout · caps     │
   └────────┬─────────┘      │ breaker · budget   │      └─────────┬──────────┘
            │                └─────────┬──────────┘                │
            │                          │                           ▼
            │                          │                ┌────────────────────┐
            │                          │                │ L0 Kernel          │
            │                          │                │ Executor·Verifier  │
            │                          │                │ Policy·EventLedger │
            │                          │                └─────────┬──────────┘
            ▼                          ▼                          ▼
   ┌──────────────────┐      ┌────────────────────┐      ┌────────────────────┐
   │ SessionStore     │      │ مزوّدو النماذج      │      │ Connectors (L5)    │
   │ sessions/*.json  │      │ شبكة خارجية        │      │ approvals (SQLite) │
   │ ★ بلا قفل كتابة  │      └────────────────────┘      └────────────────────┘
   └──────────────────┘
```

## 2️⃣ رسم الاعتماديات (B)

| المكوّن | يعتمد على | نوع الاعتماد |
|---|---|---|
| Studio / CLI | `AutonomousAgent`, `SessionStore` | مباشر، داخل العملية |
| `AutonomousAgent` | `MemoryBridge`, `FreeFirstRouter`, `ToolExecutor`, `SessionStore` | بروتوكولات قابلة للاستبدال |
| `ToolExecutor` | `L0 Kernel` (عبر `bridges.py`) | استيراد كسول مع قفل استيراد |
| `FreeFirstRouter` | مزوّدون خارجيون + `BudgetGuard` | شبكة، يفشل إلى المُخطِّط الحتمي |
| `SessionStore` | نظام الملفات | ملف واحد لكل جلسة، **بلا حجز كتابة** |
| `ConnectorsBridge` | SQLite (WAL) + مزوّدو الخدمات | كتابة خارجية بموافقة بشرية |
| `remote-core` (Worker) | Kernel عبر HTTP | شبكة |

**ملاحظة محقّقة:** `L0 Kernel` خالٍ من التبعيات الخارجية فعليًا (`ADR-0002`, `scripts/ci_guard.py imports` → PASS)، و`EventLedger` يستخدم `flock` مع بديل `.lock` عبر `O_CREAT|O_EXCL` (`projects/agi-system/core/event_ledger.py:360,381`). أي حلّ للحجز يجب أن يتبع نفس النمط لا أن يخترع نمطًا ثانيًا.

## 3️⃣ كيف ينتشر الفشل اليوم (C)

| ما يسقط | ما يحدث فعليًا | الدليل | الحكم |
|---|---|---|---|
| كل مزوّدي النماذج | الوكيل يكمل بمُخطِّط حتمي (`simulated=true`) | `planner_engine.py:487` | **محتوى** ✅ — لا يسقط النظام |
| مزوّد واحد | دائرة مفتوحة له وحده ثم تجاوز | `router.py:101-118` | **محتوى** ✅ |
| أداة واحدة | `ToolOutcome(ok=False)` والمهمة تكمل | `tools.py:229-246` | **محتوى** ✅ |
| ذاكرة L1 غير مثبّتة | بحث يعيد `[]` والمهمة تكمل | `bridges.py:427-428` | **محتوى** ✅ |
| ذاكرة L1 موجودة لكن معطوبة (SQLite تالف) | الاستثناء يخرج من `memory_bridge.search` ويقتل التشغيل | `agent.py:361` بلا `try` | **فجوة R4** ❌ |
| موصل خارجي | موافقة بشرية مطلوبة قبل الكتابة؛ الخطأ يظهر في الأثر | `bridges.py:480+`, `connectors-hub/.../approvals.py:157` | **محتوى جزئيًا** |
| عمليتان على نفس الجلسة | تكتبان `session.tmp` نفسه؛ كتابات تُفقد وملف يُنشر نصف مكتوب | **مقاس** — §5 | **فجوة R1** ❌ |
| تعطّل بعد أثر خارجي ناجح وقبل نقطة التحقق | الاستئناف قد يعيد الأثر نفسه | `agent.py:506-517` (الحفظ بعد التنفيذ) | **فجوة R2** ❌ |

## 4️⃣ جرد الحمايات الموجودة (D)

| # | الحماية | الموضع | كيف تحقّقت |
|---|---|---|---|
| 1 | سجل أحداث ملحق مع قفل بين العمليات | `core/event_ledger.py:360,381` | READ FROM CODE |
| 2 | مهلة لكل مزوّد (8 ث افتراضيًا) | `router.py:31,192` | READ FROM CODE |
| 3 | إعادة محاولة محدودة + backoff متساند **مع jitter** | `router.py:32,75,241` | READ FROM CODE |
| 4 | دائرة قاطع لكل `provider:model` مع فترة تهدئة | `router.py:34,101-118` | READ FROM CODE |
| 5 | `BudgetGuard` يمنع أي مزوّد غير مثبت الصفر | `budget.py`, `router.py:133` | TESTED LOCALLY (اختبارات الميزانية) |
| 6 | مُخطِّط حتمي بديل عند سقوط كل المزوّدين | `planner_engine.py:487` | READ FROM CODE |
| 7 | سقف إخراج العملية 24 KB، قراءة 48 KB، مهلة تنفيذ ≤10 ث، `kill` عند التجاوز | `tools.py:40-41,459,491-510` | READ FROM CODE |
| 8 | بوابة قبول الذاكرة بالأدلة + سجل القبول | `memory/store.py:186` | TESTED LOCALLY (19 اختبارًا) |
| 9 | احتواء فشل الأداة داخل نتيجة لا استثناء | `tools.py:229-246` | READ FROM CODE |
| 10 | موافقة بشرية للكتابة الخارجية + SQLite WAL | `bridges.py:480+`, `approvals.py:157` | TESTED LOCALLY |
| 11 | تشغيل أحادي في `live-agent` (`threading.Lock`) | `live-agent/server.py:70` | READ FROM CODE |
| 12 | `worker_guard`: جسم طلب محدود 32 KB، مصادقة، تنقيح أسرار، `FAIL_CLOSED` للميزانية | `workers/python-worker/src/worker_guard.py:11,118,165` | TESTED LOCALLY |
| 13 | استئناف بعد قتل حقيقي (SIGKILL) من نقطة تحقق | `agent.py` + `scripts/persistent_task_gate.py` | PROVEN (بوابة `persistent-task`) |
| 14 | اتصال حقيقي بمزوّد مع أثر | `live_probe.py` + `scripts/live_connector_gate.py` | بوابتها في `main` تُصلَح في PR منفصل — **NOT EXECUTED هنا** |

## 5️⃣ الفجوات مرتّبة بالخطر (E)

### R1 — جلسة بلا حجز كتابة (حرجة، **PROVEN**)

`SessionStore` يوصف بأنه «Thread-safe» (`agent.py:162`) لكنه **لا يحمل أي قفل**، والملف المؤقت اسمه ثابت `session.tmp` لكل الكتّاب (`agent.py:208`). و`Studio` يعرّض `/run` و`/stream` لنفس الجلسة دون أي حارس (`studio.py:162-199`).

إعادة الإنتاج (أداة يدوية في المستودع):

```bash
python-2027/.venv/bin/python python-2027/tests/session_race_demo.py --assert
```

القياس (4 كتّاب × 60 كتابة = 240 كتابة مقصودة، 5 تشغيلات):

| التشغيل | كتابات فُقدت باستثناء | قراءات JSON مبتورة |
|---|---:|---:|
| 1 | 48 | 0 |
| 2 | 61 | 0 |
| 3 | 61 | 13 |
| 4 | 23 | 5 |
| 5 | 41 | 0 |

السبب المباشر: `Path.replace` يفشل لأن كاتبًا آخر نقل `session.tmp` أولًا، أو ينشر ملفًا نصف مكتوب كتبه غيره — وهذا هو البتر الذي ظهر فعلًا. **الأثر:** ضياع تقدّم مهمة وإمكان ظهور حالة غير صالحة للقراءة.

### R2 — نقطة التحقق ليست «تنفيذًا مرة واحدة» (حرجة، READ FROM CODE)

الحفظ يحدث **بعد** تنفيذ الخطوة (`agent.py:506-517`). إذا نُفِّذ أثر خارجي (إرسال، نشر، كتابة على GitHub) ثم تعطّلت العملية قبل الحفظ، فالاستئناف يبدأ من خطوة لم تُسجَّل — أي **قد يعيد الأثر**. لا يوجد في المستودع أي سجل عمليات أو مفاتيح idempotency:

```bash
grep -rn "idempoten\|action_journal" --include=*.py python-2027/src connectors-hub live-agent
# لا نتائج ذات صلة (سوى journal_mode في SQLite)
```

### R3 — قاطع الدائرة للنماذج فقط (عالية، READ FROM CODE)

لا توجد دوائر للموصلات ولا للأدوات، والحالة `HALF_OPEN` غير معلنة (انتهاء التهدئة يعمل كبديل ضمني)، ولا تُصدَّر أي عدّادات للفتح/الإغلاق إلى الصحة أو الـHUD.

### R4 — فشل الذاكرة يقتل المهمة (عالية، READ FROM CODE)

`agent.py:361` يستدعي `memory_bridge.search` بلا `try`. الفرع «الذاكرة غير مثبّتة» يعيد `[]`، لكن خطأ SQLite حقيقيًا يخرج كاستثناء ويُسقط التشغيل — خلافًا للمبدأ: سقوط مكوّن اختياري لا يوقف ما لا يحتاجه.

### R5 — لا حدود تزامن (bulkhead) (متوسطة، READ FROM CODE)

`execute_parallel` يشغّل قراءات متوازية بـ`TaskGroup` بلا سقف؛ ولا سقف لعدد الجلسات المتزامنة في Studio ولا لعدد العمليات الفرعية. الضغط يُترجم إلى تنافس على المعالج والقرص، لا إلى رفض منظّم.

### R6 — الصحة ليست لكل مكوّن (متوسطة، READ FROM CODE)

`live-agent` وحده يعرّض `/api/health` وعدّادات Prometheus. لا يوجد تمييز `LIVE / READY / DEGRADED / UNAVAILABLE` لـStudio أو الذاكرة أو الموصلات أو العمال. أي تدهور يُكتشف من فشل المهمة لا من حالة معلنة.

### R7 — لا رجوع ولا Canary في النشر (متوسطة، READ FROM CODE)

`.github/workflows/deploy.yml` ينشر عبر بوابة سياسة ثم يحكم على القرار؛ لا خطوة Canary ولا `rollback` ولا مقارنة إصدار بعد النشر.

### R8 — لا بوابة تعافٍ (عالية كمصداقية، NOT EXECUTED)

`failure-recovery` مذكورة في جدول البوابات الثماني (`docs/PAP-2027-GAP-AUDIT.md`) ولم تُنفَّذ. أي ادّعاء عن المتانة الآن بلا أداة تُكذّبه.

## 6️⃣ الخطة مرتّبة بالمخاطر (F)

| المرحلة | المطلوب | لماذا هذا الترتيب | بوابة القبول (يجب أن تفشل اليوم) |
|---|---|---|---|
| **P0.1** | حجز كتابة لكل جلسة: SQLite معاملات + مهلة + تجديد + `fencing token`، ورفض الكاتب القديم | أرخص إصلاح وأعلى أثر مقاس (R1) | `session_race_demo.py --assert` → **0** أخطاء و**0** بتر |
| **P0.2** | سجل عمليات دائم + idempotency_key على كل أثر خارجي، وحالات `PREPARED→EXECUTING→UNKNOWN→SUCCEEDED→RECONCILED→VERIFIED` | لا معنى للحجز دون منع تكرار الأثر (R2) | سيناريو «قتل بعد أثر خارجي وقبل نقطة تحقق» لا يعيد الإرسال |
| **P0.3** | دوائر لكل موصل/أداة + `HALF_OPEN` صريح + `bulkhead` + ميزانية محاولات لكل قدرة | يجعل سقوط مكوّن محصورًا (R3, R5) | فتح دائرة الموصل لا يوقف Studio |
| **P0.4** | `scripts/failure_recovery_gate.py` بسيناريوهات حقيقية | يحوّل كل ما سبق من كلام إلى أثر (R8) | تفشل اليوم لعدم وجود الحجز/السجل |
| **P1** | صحة لكل مكوّن + قياس زمن التعافي | يجعل التدهور مرئيًا (R4, R6) | حالة `DEGRADED` تظهر قبل فشل المهمة |
| **P2** | `agent-fleet` (3 وكلاء) | **محجوب حتى P0.1–P0.4** — تشغيل ثلاثة وكلاء بلا حجز ولا سجل يضاعف الأعطال | — |
| **P3** | نشر تدريجي + رجوع آلي | (R7) | نشر إصدار معيب يعود تلقائيًا |
| **P4** | مقياس مقارن بمهام حقيقية | لا يُفتح قبل استقرار P0 | — |

## 7️⃣ الملفات المقترحة للتعديل (G)

**P0.1 — حجز الجلسة**

- جديد: `python-2027/src/ai2027_protocol/lease.py` — `LeaseStore` بروتوكول + `SqliteLeaseStore` (stdlib `sqlite3`، معاملة واحدة للحيازة، `expires_at`, `owner`, `fence`).
- تعديل: `python-2027/src/ai2027_protocol/agent.py` — `SessionStore` يطلب الحجز قبل الكتابة ويُمرّره إلى `save`؛ رفض الكاتب الذي فقد الحجز.
- تعديل: `python-2027/src/ai2027_protocol/studio.py` — `409` عند جلسة مشغولة، وحقل `fence` في الاستجابة.
- تعديل: `python-2027/src/ai2027_protocol/cli.py` — `--resume` يفشل بوضوح إذا كانت الجلسة محجوزة.
- جديد: `python-2027/tests/test_session_lease.py` (تزامن داخل العملية وبين عمليات).
- تحويل الأداة اليدوية إلى بوابة: `python-2027/tests/session_race_demo.py --assert` يجب أن يعطي 0.

**P0.2 — سجل العمليات**

- جديد: `python-2027/src/ai2027_protocol/action_journal.py` (SQLite WAL، أعمدة كما في التكليف، ولا إعادة تلقائية لحالة `UNKNOWN`).
- تعديل: `python-2027/src/ai2027_protocol/tools.py` — كل أداة كتابة تمرّ عبر السجل.
- تعديل: `python-2027/src/ai2027_protocol/bridges.py` — الموصلات تُمرّر `idempotency_key` وتحفظ مرجع المزوّد.
- جديد: `python-2027/tests/test_action_journal.py`.

**P0.3 — الاحتواء**

- جديد: `python-2027/src/ai2027_protocol/reliability.py` (`CircuitBreaker` صريح، `Bulkhead`, `RetryBudget`).
- تعديل: `router.py` (إعادة استخدام الدائرة الحالية عبر الواجهة الجديدة بدل نسخة ثانية)، `bridges.py` (دائرة لكل موصل)، `tools.py` (سقف تزامن).

**P0.4 — بوابة التعافي**

- جديد: `scripts/failure_recovery_gate.py` (14 سيناريو، تقرير JSON في النجاح والفشل، `--assert`).
- جديد: `.github/workflows` خطوة للبوابة على كل PR بعد نجاح الاختبارات.

**P1 — الصحة**

- تعديل: `studio.py` (مسار `/v1/studio/health` لكل مكوّن)، `live-agent/server.py` (توحيد الحالات)، `docs/` تحديث العقد.

## 8️⃣ حدود هذا التدقيق

- لم يُشغَّل أي نداء حيّ لمزوّد نموذج من هذه الجلسة؛ بوابة `live-connector` تعمل على `main` فقط.
- سيناريوهات الفشل (R1) مُقاسة على مستوى **نظام الملفات والعمليات**؛ قتل عملية داخل CI سيأتي في P0.4.
- لم تُختبر استعادة قاعدة بيانات `connectors-hub` بعد تلف (R6/R7) — تحتاج مرحلة P1.
- `agent-fleet` لم يُبدأ، ولا ينبغي أن يبدأ قبل P0.1–P0.4.

## 9️⃣ كيف تتحقق من هذا المستند بنفسك

```bash
python3 scripts/pap_gap_audit.py --assert                  # 12 محركًا، 0 انحراف
python3 scripts/persistent_task_gate.py --assert           # الاستئناف بعد SIGKILL
python-2027/.venv/bin/python python-2027/tests/session_race_demo.py --assert   # R1 (يفشل الآن)
grep -rn "except" python-2027/src/ai2027_protocol/agent.py                     # R4: سطر واحد فقط
```
