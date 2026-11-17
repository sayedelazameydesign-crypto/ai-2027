# المرحلة 2ب — محرك التنبيهات والتحليل الحتمي

**الحالة:** منفَّذ على الفرع `arena/e1d83a26-ai-2027` · **اعتماديات:** مكتبة بايثون القياسية فقط (`stdlib only`) · **إشعارات خارجية:** صفر.

هذا هو المرجع التفصيلي. الخلاصة القصيرة في [PHASE-2B.md](PHASE-2B.md)، وسلفه في [PHASE-2A-WATCHDOG.md](PHASE-2A-WATCHDOG.md).

## أين يوجد الكود فعلاً

| المسار | الدور |
|---|---|
| `monitoring/phase2b.py` | المحرك الحقيقي: التحليل، القواعد، اشتقاق الأحداث، الكتابة في السجل، والـ CLI |
| `monitoring/alerting.py`, `monitoring/alerts.py`, `monitoring/gap_analysis.py`, … | **أسماء مستعارة (aliases)** من 6–14 سطراً تشير إلى `phase2b`؛ واجهة متوافقة، لا منطق فيها |
| `alerting.py`, `alerts.py`, `phase2b.py` (جذر المستودع) | أسماء مستعارة تضيف مجلد `monitoring` إلى `sys.path` ثم تستورد `phase2b` |
| `monitoring/local_watchdog.py` | صاحب سلسلة التجزئة JSONL؛ المرحلة 2ب **تستدعيه** ولا تعيد كتابة السجل بخطوط خام |
| `monitoring/test_watchdog.py` | 44 اختباراً للمرحلة 2أ (السلسلة، الصمت، التلاعب) |
| `monitoring/test_phase2b.py` | 9 اختبارات للمرحلة 2ب (الكشف، الأحداث، سلامة السلسلة) |

## 1. الكشف: «متتالٍ» لا «إجمالي»

| القاعدة | المقياس | العتبة الافتراضية | الخطورة |
|---|---|---|---|
| `repeated_poll_failures` | `consecutive_failures` | **3** سجلات `poll_failed` متجاورة | `warning` |
| `large_gap` | `max_gap_seconds` | **300.0** ثانية صمت **مسجَّل** | `critical` |
| `low_observed_fraction` | `observed_fraction` | `< 0.9` | `warning` |
| `unclassified_errors` | `unclassified_rate` | `> 0.5` | `info` |

الثوابت: `DEFAULT_FAILURE_THRESHOLD = 3` و `DEFAULT_SILENCE_THRESHOLD = 300.0` في `monitoring/phase2b.py`.

- **المتتالي يعني التجاور في السلسلة:** ثلاثة إخفاقات يتخللها `poll` ناجح = ثلاث جولات طول كل منها 1 ≠ تنبيه. أي سجل آخر (`poll`, `silence`, `start`, `stop`) يكسر الجولة. الترتيب هو ترتيب الملف لا ترتيب `seq` بعد فرزه؛ فرز سجل مُعبَث به قد يُخفي العبث.
- `poll_failures` (الإجمالي) ما زال منشوراً في `analyze_gaps` وسياق القواعد، لكنه **لم يعد** مقياس قاعدة التنبيه. مفتاح `--failure-threshold` (و`poll_failures=` للاستدعاء البرمجي) يضبط عتبة المتتالي، لا الإجمالي.
- عتبة الـ 300 ثانية تُطبَّق على `gap_seconds` الذي **كتبه الـ watchdog بالفعل** في سجل `silence`؛ ليست مؤقتاً يخمّن مرور الوقت. صمت 299.9 ث ≠ تنبيه `large_gap`؛ صمت 300.0 ث = تنبيه.
- ما لا يعرف المصنّف سببه يظهر `unclassified` ويُنشر معدّله `unclassified_rate` بدل اختراع تفسير. `diagnose` يصف التصنيف بأنه **فرضية** لا إصلاح ولا دليل.

## 2. تسجيل التنبيه والتعافي داخل سلسلة التجزئة

`append_event(log, kind, data, records=None, ts=None)` هي نقطة الكتابة الوحيدة:

- تستدعي `local_watchdog.EventLog.append(...)`، أي أن `seq` و`prev` و`hash` والقفل الحصري و`fsync` وإصلاح الذيل المقطوع كلها تُدار من صاحب السلسلة. **لا تكتب JSONL يدوياً**؛ سطر خام واحد يكسر `verify()` بصمت.
- الأحداث المشتقة من نوع `alert` و`recovery` فقط (`ALERT_KINDS`).
- **الحتمية واللاتكرار:** `derive_events(records)` تقرأ السجل وتُخرج أوصاف أحداث دون كتابة. مفتاح الحدث `data["key"]` مشتق من `seq` المُحفِّز (`alert:consecutive_failures:3`، `recovery:large_gap:9`)، لذا إعادة تشغيل `alerts` على الملف نفسه تُعيد السجل الموجود بدل إضافة نسخة. اختبار `test_07` يثبت أن التشغيل الثاني يضيف صفراً.
- **التعافي حقيقة لا توقّع:** التنبيه يفتح «سبباً»، وأول `poll` ناجح بعده يغلقه بحدث `recovery`. سجل ينتهي أثناء الانقطاع **لا يحتوي حدث تعافي** — وهذه هي الحقيقة.
- **سلامة الأرقام:** `ts` للحدث المشتق مُثبَّت على `ts` آخر سجل مرصود، لا `time.time()`. لو كُتب «الآن» لتمدّد `span_seconds` في `report()` وتغيّر `observed_fraction` لمجرد أن أحدهم أعاد التحليل. وقت الاكتشاف الفعلي يُحفظ منفصلاً في `data["detected_at_iso"]` بدل تنكّره في صورة زمن السجل. `test_09` يثبت ثبات `span_seconds` و`observed_fraction`.
- **عدم التغذية الراجعة:** `_analysis_records` و`_lines_from_records` يستبعدان أحداث `alert`/`recovery`، وإلا لصنّف التنبيه **نفسه** كخطأ `unclassified` في التحليل التالي (عُوجّه هذا فعلاً أثناء التنفيذ، و`test_08` يحميه).

مثال على حدث مكتوب:

```json
{"v":1,"seq":5,"ts":1010.0,"kind":"recovery","prev":"sha256:…","hash":"sha256:…",
 "data":{"key":"recovery:consecutive_failures:4","kind":"recovery",
         "reason":"consecutive_failures","trigger_seq":4,
         "message":"successful poll after consecutive_failures",
         "derived":true,"detected_at_iso":"2026-11-17T14:40:00Z"}}
```

`alert` يحمل أيضاً `value` و`threshold` و`run_start_seq`. كلا النوعين يمرّان عبر `verify()` كأي سجل آخر: `seq` متتابع، `prev` يشير إلى `hash` السابق، والسجل `clean`.

## 3. الواجهة والطرفية

```bash
# إعادة تحليل سجل الـ watchdog + كتابة أحداث alert/recovery فيه + تنبيه طرفي
python3 monitoring/phase2b.py alerts --log watch.jsonl

# قراءة فقط (بدل كتابة)، مع إظهار التصنيف و unclassified في JSON
python3 monitoring/phase2b.py alerts --log watch.jsonl --no-record --diagnosis

# عتبات مخصّصة، أو سجل أحداث منفصل
python3 monitoring/phase2b.py alerts --log watch.jsonl \
    --failure-threshold 5 --silence-threshold 600 --event-log alerts.jsonl

python3 monitoring/phase2b.py analyze   --log watch.jsonl
python3 monitoring/phase2b.py diagnose  --text $'unknown error: flux capacitor\n'
python3 monitoring/local_watchdog.py verify --log watch.jsonl
```

- **stdout يبقى JSON صالحاً** (قائمة التنبيهات، أو `{alerts, diagnosis, events}` مع `--diagnosis`). العرض البشري يذهب إلى **stderr**: سطور `[ALERT] SEVERITY rule message (metric=value, threshold=…)`، و`[DIAG]` بعدد الخطوط المصنّفة وغير المصنّفة، و`[LOG]` بعدد الأحداث المشتقة/المضافة/الموجودة مسبقاً.
- الكتابة افتراضية عند تمرير `--log`، وتُلغى بـ `--no-record`. الاستدعاء البرمجي `evaluate(...)` يبقى **للقراءة فقط** ما لم يُمرَّر `record=True`.
- `record_alert_events`, `append_event`, `derive_events`, `consecutive_failures`, `consecutive_failure_runs`, `trailing_consecutive_failures` متاحة للاستيراد من `phase2b` ومن الأسماء المستعارة.

## 4. ما لا يدّعيه هذا المحرك

- **لا إشعارات عن بُعد:** لا بريد، لا رسائل، لا `webhook`، لا هاتف. الحدث المشتق حقيقة عن السجل، **لا دليل على أن بشراً رآه**. التنبيه «يُعرض في الطرفية» فقط.
- **لا يخترع صمتاً:** إن لم يكن الـ watchdog شغّالاً فلا يوجد سجل `silence`، وبالتالي لا تنبيه `large_gap`. حدّ المرحلة 2أ (لا بيانات Render حقيقية قد مرّت عبر الحلقة) ما زال قائماً.
- **السلسلة مُثبتة للتلاعب لا محمية منه:** إعادة كتابة الملف كاملاً مع إعادة حساب كل البصمات تظل غير مكتشفة بلا `--anchor`. إضافة أحداث `alert`/`recovery` لا تغيّر هذا الحد.
- **الأحداث المشتقة ليست قياس أداء:** أرقام `p50` في `generate_launch_evidence.py` تُقاس في كل تشغيل وتختلف بين تشغيل وآخر (مثلاً 8.982/4.092 ms في جولة، و5.547/2.215 ms في جولة أخرى على نفس الجهاز)؛ ليست رقماً ثابتاً مثل 7.4 ms. وفحص الهجمات: **75 DENIED + 25 احتواءً كمدخلات حرفية + صفر اختراقات**، وليس «صدّاً بنسبة 100%».

## 5. البوابات والتحقق

```bash
python3 monitoring/test_watchdog.py -v          # Ran 44 tests … OK
python3 monitoring/test_phase2b.py -v           # Ran 9 tests … OK
python3 scripts/ci_guard.py imports             # PASS: stdlib only
python3 scripts/generate_launch_evidence.py --assert   # ALL GATES: PASS
```

`scripts/generate_launch_evidence.py` يضم الآن مجموعتَي `watchdog` و`phase2b` ضمن `TESTS` وبوابتَي `watchdog_44_tests` و`phase2b_9_tests` الإلزاميتين؛ أي تعطّل في المراقبة يُسقط `--assert` بدل أن يمرّ دليل الإطلاق أخضر. `.github/workflows/ci.yml` يشغّل ملف الاختبارات الجديد في خطوة مستقلة.

> **ملاحظة تدقيق:** `monitoring/test_watchdog.py:327` يختبر فجوة 300 ثانية في **حلقة الـ watchdog نفسها** (`run()` تسجّل `silence`)، ولا يستورد `phase2b` إطلاقاً. رفع عتبة التنبيه إلى 300 ث **لا يمسّه**؛ لذلك لم يُعدّل أي اختبار من اختبارات المرحلة 2أ، وبقيت الـ44 كما هي. «تكييف الاختبارات القائمة تلقائياً» كان سيُخفي انحداراً بدل كشفه.

## 6. مرجعية المسارات (تصحيحات مؤكدة)

| لا يوجد | الموجود فعلاً |
|---|---|
| `l1_memory.db` | `memory/out/mem.sqlite` صراحةً عبر `python3 memory/store.py init --db …`؛ و`l1_memory` في سكربت دليل الإطلاق مجرد **تسمية مجموعة اختبارات** |
| `KernelCore.inspect_state()` | `projects/agi-system/core/` (الصنف `Kernel` في `kernel.py`) |
| `python -m live_agent.server` | `python3 live-agent/server.py --port …` (شرطة في اسم المجلد، ملف لا حزمة) |
| `docs/PHASE-2B-ALERTING.md` قديماً | هذا الملف أُنشئ الآن؛ `docs/PHASE-2B.md` هو الملخص |
