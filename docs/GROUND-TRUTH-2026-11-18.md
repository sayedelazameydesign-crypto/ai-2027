<div dir="rtl">

# 🧾 إعادة التحقق — ai-2027 · 2026-11-18

**تاريخ التنفيذ:** 2026-11-18 · **الفرع:** `arena/9787903e-ai-2027` (من `4b89684` = رأس `main`)
**الطريقة:** تشغيل فعلي لكل أمر في [`GROUND-TRUTH-2026-11-17.md`](GROUND-TRUTH-2026-11-17.md) القسم 7 + استدعاء GitHub API. **لا رقم هنا منسوخ من README.**
**الغرض:** فحص إن كانت ادعاءات الأمس **ما زالت صحيحة اليوم** — لا إعادة كتابة لها.

---

## 0️⃣ الخلاصة في سطرين

**البنية التحتية المُختبَرة: ما زالت خضراء بالكامل** — **216 اختبارًا** محليًا (0 فشل · 1 مُتجاوز)، **15/15** فحص HTTP حقيقي، **100/100** هجوم في red-team بـ **0 اختراق**، وفهرس **48 مشروعًا / 3,967 ملفًا** مطابق للأعداد على القرص بالضبط.

**ثلاثة أشياء تغيّرت عن تقرير الأمس، وأربعة ادعاءات لم تعد قابلة للتحقق من هذه الجلسة.** أهمها: **P0 الأمني ما زال مفتوحًا** — مفتاح Gemini الحقيقي **موجود على HEAD في المستودع الأصلي** `ai-store2` الآن.

---

## 1️⃣ ما أُعيد تشغيله الآن (نتائج، لا أوصاف)

| ما شُغّل | النتيجة المقيسة الآن | مقابل الأمس |
|---|---|---|
| `projects/agi-system` (نواة الوكيل) | **86/86** في 1.74 ثانية | مطابق |
| `memory/test_memory.py` | **19/19** | مطابق |
| `live-agent/test_server.py` | **6/6** | مطابق |
| `command-hud/test_hud.py` | **15/15** | كان 14 · **+1** |
| `website/kernel-demo.test.js` (Node 22.22) | **12/12** عقد | مطابق |
| `scripts/test_ci_guard.py` | **9/9** | مطابق |
| `launch/test_launch_claims.py` | **10/10** | مطابق |
| `scripts/test_ember_beat.py` | **7/7** | مطابق |
| `scripts/test_health_gate.py` | **24/24** | مطابق |
| `scripts/test_policy_hardening.py` | **6/6** | مطابق |
| `scripts/test_run_task.py` | **6/6** | مطابق |
| `deployment/test_deployment.py` | **16 (1 مُتجاوز)** | مطابق |
| **الإجمالي** | **216 اختبارًا · 0 فشل · 1 مُتجاوز** | ✅ |
| `scripts/runtime_smoke.py` (سيرفران حقيقيان على 8090/8091) | **15/15** PASS | مطابق |
| `command-hud/redteam.py --markdown` | **100 هجوم · 100/100 متوقّع · 91 DENIED · 9 CONTAINED · 0 اختراق · 10/10 ضوابط** · p50 5.05 ms | مطابق |
| `command-hud/audit.py` | 48 مشروعًا · **3,967** ملفًا · 21 فيها اختبارات · 19 CI · 23 بلا ترخيص · 5 ملاحظات HIGH · **1 PROVEN · 40 DECLARED · 6 SIMULATED · 1 FAILED** | مطابق |
| `memory/evaluate.py` | EASY **16/16 = 1.0** · HARD **11/16 = 0.688** · سلبيات 0/5 · جوار-خطأ 0/7 · أرضية ضجيج p95 = 0.20 | مطابق |
| `live-agent/bench.py` | p50 = **12.0 ms** · p95 = 27.0 ms · (أمس 7.30 ms) | **جهاز مختلف** — لا تعارض |

**أعداد الفهرس مطابقة للقرص بالضبط:** `48` مجلدًا · `3,967` ملفًا على القرص (بعد استثناء `__pycache__`) · `catalog.json` يعلن `3,967`. الأرقام الثلاثة متساوية — هذا ما يجعل الفهرس قابلًا للثقة.

### نبضة الجمرة (Ember) — آخر تشغيل آلي قبل هذه الجلسة
`heartbeat` branch · `2026-11-18T00:56:45Z` · `smoke.ok = true` · **15/15** · المدة 7.27 ثانية.
آخر تشغيلات GitHub Actions: `Ember beat` ✅ · `Evidence-First CI` ✅ · `CodeQL` ✅ · `Release` ✅ (v1.0.1).

---

## 2️⃣ ما تغيّر عن تقرير 2026-11-17 (ثلاثة بنود)

| البند | أمس | اليوم | الحكم |
|---|---|---|---|
| **GitHub Pages** (`P2 #4`) | `status = errored` · `build_type = legacy` | **`status = built` · `build_type = workflow`** | ✅ **أُغلق** |
| **انحراف `easy-ai/data/manifest.json`** | أُعيدت مزامنته و«صار مطابقًا» | **انحرف من جديد**: المصدر أعاد توليده `2026-11-17T22:20:22Z` بعد مزامنتنا (`13:21:22Z`) | ⚠️ **ملف مولَّد آليًا — مطابقته لحظية بطبيعتها** (انظر §4) |
| **Issues مفتوحة** | 3 (كلها ذاتية) | **5** (#21 أمني · #23 إصلاحات محجوبة · +3 good-first-issue) | ⚠️ تغيّر شكلي |

---

## 3️⃣ ما **لم** يعد قابلًا للتحقق من هذه الجلسة (حدّ حقيقي، لا تُخفيه)

بيئة التشغيل هذه **لا تملك منفذًا للشبكة العامة** عدا `github.com` و`pypi.org` و`registry.npmjs.org`. كل محاولة `curl` إلى المواقع العامة رجعت `000` (لا اتصال). إذًا:

- ❌ **4 مواقع حيّة · 3 روابط ميتة** — لم تُعَد فحصًا اليوم. حكم الأمس **يبقى قائمًا بلا تأكيد جديد**.
- ❌ **مفتاح Google** — لا يمكن اختباره أو إبطاله من هنا (خارج الأدوات، كما أمس).
- ⚠️ **`projects/WEB`** — بيانات المستودع تُقرأ (`pushed_at = 2026-01-31`) لكن **contents API رجّع `502 Bad Gateway`** في كل محاولات الصباح ⇒ سُجّل **محجوبًا** لا «منحرفًا». *(وفي إعادة التشغيل مساءً عاد الـAPI سليمًا: 45 مطابقًا — العطل كان مؤقتًا في GitHub، وهذا بالضبط سبب عدم احتسابه انحرافًا.)*
- ⚠️ **Vercel connector** — القراءة فقط، `0` مشروع على الحساب المرتبط (كما أمس).

**الأمانة تقتضي قولها:** أي ادعاء عن «حي على الإنترنت» في هذا المستودع **لا يمكن إعادة إثباته من داخل هذه الجلسة**، ويجب إعادة فحصه من بيئة تملك شبكة عامة.

---

## 4️⃣ تصحيحان صغيران على تقرير الأمس

| ادعاء الأمس | الواقع اليوم |
|---|---|
| «**46 من 48** مطابقة بايت-ببايت + انحرافان موثّقان» | `verify_archive_identity.py` الآن: **44 مطابق · 2 انحراف موثّق · 1 انحرف · 1 محجوب (502)**. الفارق **ليس تلاعبًا**: `easy-ai/data/manifest.json` ملف **يُولَّد آليًا** في المصدر، وقد أُعيد توليده مساء الأمس. الأمانة: التطابق البايتي **ينطبق على الملفات الثابتة**، لا على المخرجات المولَّدة |
| «p50 = 7.30 ms» | على هذا الجهاز **12.0 ms** (p95 = 27.0 ms). الرقم ليس ادعاءً ثابتًا — إنه **قياس عتاد**، ويجب قراءته كذلك (وهذا سبب وجود `.github/workflows/ember.yml` و`bench.py` أصلاً) |

---

## 5️⃣ الحالة الأمنية — **P0 ما زال مفتوحًا**

قرأت `env.local` من المستودع الأصلي عبر GitHub API (المحتوى مُقنَّع هنا):

```
NEXT_PUBLIC_GEMINI_API_KEY=AIzaSyBa…(باقي المفتاح موجود على HEAD)
```

- النسخة داخل هذا المستودع **مُعمّاة** (`REDACTED-rotate-this-key-see-AUDIT.md`) — انحراف `D-2` موثّق.
- **التعمية ليست إبطالًا.** المفتاح ما زال صالحًا في المستودع الأصلي **وفي تاريخه**.
- **الإجراء الوحيد الناجع:** إبطاله من AI Studio ثم تدوير مفتاح جديد. Issue #21 ما زال مفتوحًا لأن هذا **لم يحدث**.
- لم أجد أي مفتاح خام آخر (`AIza…`) في نسخة `projects/` المحلية — الفحص الآن: **0 مطابقة**.

---

## 6️⃣ ما يقوله هذا التحقق ولا يقوله

**يقوله:** الطبقة الهندسية **حقيقية ومُختبَرة الآن**: 216 اختبارًا، 15 فحص HTTP على سيرفرات شغّالة، 100 هجوم بـ0 اختراق، وفهرس لا يكذب على نفسه (48/3,967 مطابقة، وتصنيف صادق 1 PROVEN مقابل 40 DECLARED).

**لا يقوله:** لا نموذج LLM في أي مسار مُثبت (المخطِّط حتمي — معلن في `/api/health`) · 41 مشروعًا لم يُشغَّل منها سطر · 308 نقطة API كود فقط · 0 نجمة · 0 تفريع · 0 متابع · لا جمهور.

---

## 7️⃣ أوامر إعادة هذا التحقق (تعمل من جذر المستودع)

```bash
python3 -m unittest discover -s projects/agi-system/tests -t projects/agi-system   # 86
python3 memory/test_memory.py                                                      # 19
python3 live-agent/test_server.py                                                  # 6
python3 command-hud/test_hud.py                                                    # 15
python3 command-hud/redteam.py --markdown                                          # 100 هجوم
node    website/kernel-demo.test.js                                                # 12
python3 scripts/test_ci_guard.py                                                   # 9
python3 launch/test_launch_claims.py                                               # 10
python3 scripts/test_ember_beat.py                                                 # 7
python3 scripts/test_health_gate.py                                                # 24
python3 scripts/test_policy_hardening.py                                           # 6
python3 scripts/test_run_task.py                                                   # 6
python3 -m unittest discover -s deployment -t deployment                           # 16 (1 skip)

python3 live-agent/server.py --port 8090 &
python3 command-hud/server.py --port 8091 &
python3 scripts/runtime_smoke.py                                                   # 15/15

python3 command-hud/audit.py --report /tmp/audit.md                                # 48 · 3967
python3 memory/evaluate.py                                                         # EASY 1.0 · HARD 0.688
python3 live-agent/bench.py                                                        # p50 على جهازك
python3 scripts/verify_archive_identity.py                                         # 45 + 2 انحراف + 1 مولَّد
python3 scripts/verify_archive_identity.py --strict-all                            # يفشل على drift/blocked
python3 scripts/test_network_preflight.py                                          # 10
python3 scripts/test_verify_identity.py                                            # 17
python3 scripts/network_preflight.py --json                                        # هل أستطيع الحكم على رابط عام؟
gh api repos/sayedelazameydesign-crypto/ai-2027/pages --jq .status,.build_type      # built · workflow
gh api repos/sayedelazameydesign-crypto/ai-store2/contents/env.local                # P0: المفتاح ما زال هناك
```

**القاعدة نفسها:** لا ادعاء بلا دليل — ولا دليل يُنقل من تقرير الأمس بلا إعادة تشغيل.

---

## 8️⃣ ما أُضيف بعد التحقق: حارسان يمنعان نوعين من الأكاذيب

التحقق أعلاه كشف نوعين من «الادعاء الوهمي»؛ كلاهما صار **مانعًا في الكود**، لا نصيحة في وثيقة.

### أ) `scripts/network_preflight.py` — العمى ليس موتًا

**المشكلة المقيسة:** `urllib` يرفع نفس `URLError` لـ«الموقع ميت» ولـ«هذه البيئة لا ترى الإنترنت».
فكانت `ember_beat.public_paths()` تُسطِّح الحالتين إلى `status: 0 · live: false` — أي **تنشر «المواقع متوقفة» من بيئة عمياء**. حدث هذا فعلًا في تحقق اليوم.

**الحل:** قياس القدرة قبل الحكم على أي رابط — 3 مضيفات مرجعية + مضيف من قائمة السماح:

| التصنيف | الشرط المقيس | ماذا يجوز أن تقول |
|---|---|---|
| `OPEN` | مضيفان مرجعيان على الأقل أجابا | يجوز الحكم: `live` / `dead` |
| `ALLOWLISTED_ONLY` | شيء أجاب لكن لا خروج عام (بيئة اليوم) | يجوز الحكم على GitHub/PyPI/npm فقط |
| `CLOSED` | لا شيء أجاب | **لا حكم**: كل رابط `blocked` |

**ملاحظة دقيقة:** رمز `404` يُحتسب **وصولًا** لا فشلًا — خادمٌ أجاب، والشبكة سليمة، والموقع هو الميت. أما `URLError/timeout` فليس حكمًا أصلًا.

**الأثر المقيس الآن** (`ember_beat` مع نفس رابطَي الإنتاج):
```
embers : 0 live · 2 BLOCKED
egress : ALLOWLISTED_ONLY  (public sites cannot be judged here)
```
وفي `STATUS.md` صار السطر `Public paths live: 0/0 (2 BLOCKED — not counted, see egress)`،
ووزن المسار العام **يُسقَط ويُعاد تطبيع الباقي** بدل أن يُحسب صفرًا — عمى البيئة لا يُبرّد المستودع.

### ب) `docs/deviations.json` — «مطابق» رقم له معنى

`scripts/verify_archive_identity.py` صار يقرأ سجلًا آليًا ويُصنّف **خمس حالات لا تُخلط**:
`identical` · `deviation` (D-1/D-2) · `generated` (يعيد المصدر توليده) · `drifted` (**العيب الوحيد**) · `blocked` (عمى مثبت).
`--strict` يفشل على `drifted` فقط، و`--strict-all` يفشل أيضًا على `blocked/failed`، و`--json` يخدم الـCI.

```
register            : docs/deviations.json
byte-identical      : 45
documented deviation: 2  (agi-system, ai-store2)
generated (volatile): 1  (easy-ai: data/manifest.json)
drifted             : 0
blocked (no egress) : 0
=> 48/48 accounted for · no unexpected drift
```

كذلك `command-hud/audit.py` صار يطبع **`Public egress (measured)`** في أول جدول، ويُحذّر صراحةً:
«أي ادعاء عن موقع عام من هنا = BLOCKED، وليس dead».

### ج) أين تُشغَّل؟ (ومُختبَرة بلا شبكة)

| الاختبار | العدد | ما يثبته |
|---|---:|---|
| `scripts/test_network_preflight.py` | **10** | العمى لا يُنشر موتًا · الحالة الواحدة لا تكفي لـOPEN · `404` وصول · `502` عطل مصدر لا عمى |
| `scripts/test_verify_identity.py` | **17** | `generated` ≠ `drifted` · ملف مولَّد ناقص = انحراف · `--strict`/`--strict-all`/`--json` |
| `scripts/test_ember_beat.py` | **12** (كان 7) | مسار محجوب لا يظهر ميتًا · وزن غير مقيس يُسقَط لا يُصفَّر |

**كل الاختبارات السبعة والعشرين تعمل بلا شبكة** (كل نداء يُحقن)، لذلك تصلح داخل بيئة مغلقة وداخل CI على السواء. أُضيفت إلى `ci.yml`.

**الإجمالي بعد الإضافة: 236 اختبار بيثون + 12 عقد JS = 248 اختبارًا · 0 فشل · 1 مُتجاوز.**
</div>
