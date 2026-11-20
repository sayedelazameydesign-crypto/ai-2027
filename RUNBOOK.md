# AI-2027 تشغيل آمن — Runbook P0

**الغرض:** تشغيل أولي آمن وقابل للتكرار قبل تفعيل أي مزوّد حي أو نشر 24/7. هذا الدليل لا يثبت أن كل مشاريع المستودع الـ48 تعمل معًا؛ النجاح يُقاس على مسار محدد، بأوامر وأدلة قابلة لإعادة التشغيل.

> **قاعدة الدليل:** لا تُسجّل «ناجح» من ملخص وكيل. احتفظ بـcommit SHA ورابط تشغيل CI ومخرجات البوابات. لا تضع مفاتيح أو قيم أسرار في هذا الملف أو في issue/PR أو سجل shell.

## الحالة الحالية — الفحوص المحلية ناجحة، CI ما زال مطلوبًا

- شجرة العمل ما زالت غير committed؛ لا يوجد PR لهذا التغيير بعد، وبالتالي **لا يوجد CI أخضر عليه**. هذه الأرقام من تشغيل محلي فعلي وليست بديلًا عن CI.
- أُعيد تشغيل مدقق المشروع نفسه: `average_score_pct=100.0` و`max_file_loc=590` عبر 113 ملف Python. فحص القائمة التي يستخدمها المدقق أكد أن الأكبر `tests/test_ollama_celia.py` (590 LOC)، أي هامش 60 من سقف 650؛ شرط الهامش ≥50 (كل ملف ≤600) **متحقق محليًا** من دون خفض السقف. نُقلت العقود/الأجزاء المتماسكة إلى `ir_contracts.py` و`provider_transport.py` و`bridge_contracts.py` و`tool_specs.py` و`worker_providers.py`، مع إبقاء واجهات الاستيراد السابقة.
- اختبارات B1 المستهدفة: **119 passed**، ومنها 100 مسار هجومي متميز، ورفض symlink أثناء تبديل الملف/المجلد، نمو الملف أثناء snapshot، استبدال المصدر بعد snapshot، إخفاء النص الحر والمعرّفات، وHMAC tamper/freshness/replay. لم تُكشف markers في النتائج. شغّلها بعد أي تعديل، ولا تسمِّها CI evidence قبل أن تعمل على PR.
- غلاف HMAC يوقّع `tool` و`request_id` وSHA-256 لمسار الطلب ووقت الإصدار والـnonce والنتيجة كاملة وبصمتها. التحقق الافتراضي يرفض ما تجاوز 5 دقائق؛ ويمكن للعميل تقييده بـtool/id/path وذاكرة nonces. لا يضمن HMAC وحده منع replay إذا تجاهل العميل هذه الخيارات.
- اختبارات MCP تولّد مفتاح HMAC مؤقتًا عبر `secrets.token_bytes(32)`؛ لا توجد قيمة مفتاح إنتاج ثابتة في الاختبارات. فحص SDK الخارجي 1.32.2 نجح في اكتشاف الأداة والتوقيع والتقدم والإلغاء، وبُني wheel يحوي الوحدات الجديدة.
- فحوص محلية على هذه الشجرة: المجموعة الكاملة **748 passed، coverage 92.02%**؛ Ruff/format ناجحة (113 ملفًا)، وmypy ناجح في 109 ملفات مصدر. secret scanner **18 اختبارًا و0 findings حاجبة في code scope**؛ أبلغ أيضًا عن 11 finding داخل byte-mirror تحت `projects/` خارج blocking code scope، لذلك لا يعني هذا أن الأرشيف أو Git history خالٍ من أسرار. smoke لـ`ember_up` **15/15**. تحذير Starlette/httpx واحد مسجّل مفتوحًا في `docs/DEFECT-LEDGER.md` §18.

المتبقي لقبول B1 هو مراجعة CI خضراء على PR فقط؛ لا تبدأ B2 ولا تعلن القبول النهائي حتى ظهور تشغيل PR الأخضر.

## 0. قبل أي اختبار حي: تدوير المفاتيح

إذا ظهر مفتاح فعلي في README أو Git history:

1. ألغِ المفتاح القديم من لوحة المزوّد وأنشئ بديلًا جديدًا هناك. لا ترسله إلى المحادثة ولا تضعه في الأمر أو الملف.
2. راجع استخداماته، ثم خزّنه في مدير أسرار محلي/CI. إزالة النص من آخر نسخة **لا** تلغي مفتاحًا مسرّبًا ولا تمسح التاريخ.
3. لا تُعِد كتابة Git history إلا بقرار مستقل؛ افترض أن القيمة القديمة مكشوفة حتى بعد إعادة الكتابة.
4. سجّل فقط اسم السر وتاريخ تدويره وحالته (تم/غير مؤكد)، لا قيمته ولا بادئته.

المستودع يقرأ OpenRouter باسم البيئة `OPENROUTER_API_KEY`. `MYAPP_LOCAL_DEV` ليس اسمًا يقرأه الكود؛ قد يكون label في لوحة المفاتيح فقط. لا تضف أيًا منهما إلى HTML أو JavaScript يعمل في المتصفح. تكامل OpenRouter موجود أصلًا في Python-2027؛ هذه المرحلة لا تحتاج تعديل provider جديدًا.

## 1. بيئة Windows 11

السكربتات `ember_up.sh` و`studio_up.sh` و`install_hooks.sh` مكتوبة بـBash؛ شغّلها من **WSL (موصى به)** أو Git Bash، وليس مباشرة من PowerShell/CMD. في WSL يفضّل وضع clone داخل نظام ملفات Linux لتفادي بطء `/mnt/c` ومشاكل صلاحيات الملفات.

من PowerShell تحقّق من WSL وافتح Ubuntu تفاعليًا:

```powershell
wsl --status
wsl --list --verbose
wsl -d Ubuntu
```

أو نفّذ بوابة Bash واحدة عبر PowerShell من دون فتح shell تفاعلي:

```powershell
wsl -d Ubuntu -- bash -lc 'cd ~/src/ai-2027 && ./scripts/ember_up.sh --check --no-browser --json'
```

داخل WSL (أو Git Bash)، انتقل إلى نسخة المستودع ثم تحقق من الفرع والشجرة:

```bash
cd ~/src/ai-2027   # عدّل المسار إلى مكان الـclone الفعلي
pwd
git branch --show-current
git status --short
git diff --check
```

يجب أن يكون الفرع `arena/557d33a3-ai-2027` عند العمل على هذه الجلسة. لا تبدّل الفرع.

### متغيرات الأسرار عند الحاجة فقط

لا يحتاج فحص P0 المحلي إلى مفتاح OpenRouter. عند اختبار MCP محليًا، أنشئ مفتاح توقيع مؤقتًا في shell ولا تحفظه في ملف:

```bash
export AI2027_MCP_SIGNING_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
# بعد انتهاء الاختبار:
unset AI2027_MCP_SIGNING_KEY
```

لا تعِد استخدام مفتاح MCP هذا كمفتاح مزوّد. لا تضع `AI2027_MCP_SIGNING_KEY` في JSON عميل ملتزم بالمستودع؛ استخدم مخزن أسرار عميل MCP أو مدير أسرار النظام.

إذا احتاج فحص P1 اتصال OpenRouter حيًا، أدخل السر تفاعليًا في WSL بدل كتابته في command history، وشغّل الفحص من backend فقط:

```bash
read -rsp 'OpenRouter API key: ' OPENROUTER_API_KEY
export OPENROUTER_API_KEY
printf '\n'
# شغّل فحصًا محدودًا ومحدد النموذج من backend/CLI، لا من المتصفح.
unset OPENROUTER_API_KEY
```

في GitHub Actions استخدم Repository/Environment Secret باسم `OPENROUTER_API_KEY`، لا قيمة داخل workflow أو `agent-card.json`. أبقِ الفحوص الحية خلف شروطها الحالية؛ لا تمرّر الأسرار إلى `pull_request` من fork.

## 2. حواجز الأسرار المحلية

من جذر المستودع:

```bash
python3 scripts/test_secret_scan.py -v
python3 scripts/secret_scan.py --all --scope code
./scripts/install_hooks.sh
./scripts/install_hooks.sh --check
python3 scripts/secret_scan.py --staged

git check-ignore -v .env .env.local env.local
```

النتيجة المقبولة: الاختبارات ناجحة، فحص الكود لا يجد أي credential حقيقي أو ملف ممنوع، والخطاف مفعّل. `secret_scan.py` يفحص الملفات المتتبعة أو staged حسب الخيار؛ لا يثبت أن Git history نظيف ولا يدوّر المفاتيح. لا تطبع محتوى أي finding؛ عالج الملف ودوّر المفتاح أولًا.

## 3. بوابات Python-2027 المحلية

لا تستبدل هذه الأوامر بـ`ruff check .` أو `mypy .` من جذر monorepo؛ إعدادات المشروع ومسارات CI موجودة داخل `python-2027`.

```bash
cd python-2027
.venv/bin/ruff check --target-version py313 src tests workers/python-worker/src
.venv/bin/ruff format --check src tests workers/python-worker/src
.venv/bin/mypy src tests
.venv/bin/pytest --cov-fail-under=90
cd ..
```

تتطلب بوابة المشروع تغطية كلية ≥90%. سجّل العدد والتغطية من خرج التشغيل نفسه، مع مسار العمل وإصدار Python. اختبارات فرعية فقط لا تثبت نجاح المجموعة الكاملة.

## 4. فحص تشغيل محلي آمن

من الجذر، شغّل فحص الإقلاع الذي ينظف عملياته بعد الاختبار، ومن دون BrowserSkill أو مفاتيح سحابية:

```bash
./scripts/ember_up.sh --check --no-browser --json
```

النجاح يعني exit code صفرًا، واجتياز فحوص HTTP المسجلة في JSON، وإيقاف العمليات التي بدأها السكربت. README الحالي يذكر smoke من 15 فحصًا؛ اعتمد العدد الفعلي في JSON لهذا التشغيل، لا رقم README وحده. عند تعارض منفذ، لا تقتل عمليات عشوائية؛ افحص الحالة، واستخدم `./scripts/ember_up.sh --stop` فقط للعمليات التي يديرها السكربت.

لتشغيل Studio تفاعليًا بعد اجتياز فحص الإقلاع:

```bash
./scripts/studio_up.sh --host 127.0.0.1 --port 8080
```

أبقِه محليًا؛ لا تنشر منفذًا عامًا ولا تضع مفتاح مزوّد في واجهة المتصفح.

## 5. قبول B1 الأمني قبل commit

خادم MCP المقصود محدود إلى `stdio` وأداة قراءة واحدة `ai2027.inspect_file`؛ لا resources/prompts ولا HTTP ولا كتالوج متعدد الأدوات. الأداة تعيد أعدادًا بنيوية وفحوصًا وبصمات، ولا تعيد النص الحر أو أسماء الأعمدة/الجداول أو رسائل parser من الملف. تستخدم snapshot مؤقتًا محدودًا، ولا تعدّل ملفات workspace. مع ذلك، النتيجة تغادر عملية الخادم إلى العميل، لذلك يلزم اختبارها كحدّ بيانات.

شغّل مجموعة red-team المستقلة ذات 100 حالة:

```bash
cd python-2027
.venv/bin/pytest -o addopts='' tests/test_mcp_server.py tests/test_mcp_file_races.py tests/test_mcp_attestation.py tests/test_mcp_redteam.py -q
cd ..
```

معيار التقرير: ≥100 حالة هجومية متميزة، **0 تسريب/تجاوز**، وكل رفض لا يتضمن محتوى الملف. يجب أن تشمل الحالات على الأقل:

- `../` ومرات traversal متكررة، absolute/drive paths، backslash وUNC، `%2f` و`%2e` وحالة الأحرف والـdouble encoding.
- `.env` وكل أشكاله، `.git` وملفات الاعتماد، مفاتيح خاصة، ومسارات alias داخل مجلدات مخفية.
- symlink مباشر وغير مباشر إلى ملف خارج workspace، وsymlink إلى `.env` أو `.git`.
- ملف يتجاوز 8 MiB، binary يحوي marker سرّيًا، ZIP/OOXML بحمولات لا ينبغي فكها، ونمو/استبدال الملف أثناء الفحص؛ النتيجة يجب أن تطابق snapshot محدودًا واحدًا أو يُرفض الطلب، لا أن تخلط نسخًا.
- العبث في `request_id` أو `path` أو النتيجة الموقعة (حتى مع إعادة حساب `result_sha256`)، وإعادة إرسال envelope قديم بعد انتهاء الصلاحية. يجب التفريق بوضوح بين **سلامة HMAC** و**منع replay/freshness**؛ لا تعتبر إعادة استخدام نتيجة موقعة حديثًا مرفوضة ما لم يتحقق العميل من سياق الطلب والوقت/nonce.

ثم شغّل مدقق المشروع نفسه وسجّل أكبر ملف واسمه:

```bash
python-2027/.venv/bin/ai2027-py27 --root "$PWD" audit python-2027 --json
```

شرط الهامش ≥50 مع سقف 650 هو `max_file_loc <= 600`. يجب أن يعرض التقرير اسم الملف الأكبر؛ إذا لم يعرضه، احسبه من الملفات التي يمسحها `auditor_ast.iter_python_files` ولا تخمّن أنه MCP. لا تخفّض حد 650 ولا تستثنِ ملفًا لتجاوز الشرط.

بعد اجتياز اختبارات الأسرار، حدّث `agent-card.json` بحدود صادقة فقط إذا كان ذلك الـcard يصف هذا النظام فعلًا: أداة MCP واحدة، stdio فقط، وHMAC بسر مشترك. لا تضف ادعاءً عن API أو موارد MCP.

## 6. فتح PR وإثبات CI

لا تعمل commit/push قبل نجاح جميع بوابات P0 وB1 المحلية أعلاه. بعدها، على الفرع الحالي فقط:

```bash
git diff --check
git status --short
git diff --stat
git add <الملفات-المراجعة-فقط>
git commit -m "Harden MCP B1 and document P0 runbook"
git push origin arena/557d33a3-ai-2027
gh pr create --base main --head arena/557d33a3-ai-2027 --title "Harden MCP B1 and document P0 runbook" --body-file /path/to/reviewed-pr-body.md
```

بعد إنشاء PR، استخدم رقمه الفعلي (لا placeholder):

```bash
gh pr checks <PR_NUMBER> --watch
```

لا تكتب `ci_green_on_pr=true` قبل أن تُظهر checks الخضراء على SHA الموجود في PR. سجّل في PR: commit SHA، رابط CI، أمر الاختبارات الكامل، التغطية، تقرير red-team، وملخص المدقق. لا ترفق مفاتيح أو مخرجات تحتوي أسرارًا.

## 7. المراحل التالية — لا تشغّلها دفعة واحدة

| المرحلة | العمل | بوابة قبول قابلة للقياس |
|---|---|---|
| **P0** | تدوير الأسرار، hooks، فحوص محلية، red-team، أساس أمان | CI أخضر على PR، أسرار 0، smoke ناجح، red-team 0، وهامش ملفات ≥50 |
| **P1 / L4** | Ollama ثم Gemini وNVIDIA وGroq وOpenRouter؛ اختبار كل مزوّد منفردًا وسلسلة fallback | هدف الخطة 5 مزوّدين يردّون في البيئة المُهيّأة، مع fallback مُجرّب؛ ميزانية `0` تمنع أي نموذج غير مصنف مجانيًا. OpenRouter يستخدم `OPENROUTER_API_KEY` و`openrouter/free` أو slug `:free` فقط. Offline fallback موسوم «محاكاة» |
| **P2 / 24×7** | نشر gated وجدولة heartbeat وفحص health | Soak لمدة 7 أيام على الأقل، health probe كل 5 دقائق، توافر مستهدف ≥99.5% مع تقرير تعطل/استرداد؛ لا تفترض أن free tier يبقى مستيقظًا، ولا تستخدم ping للتحايل على شروط الاستضافة؛ 24×7 قد يحتاج خطة مدفوعة |
| **P3 / L5** | موصل واحد حقيقي فقط بموافقة صريحة | Approval Gate موثق، receipt قابل للتحقق، واختبار رفض بلا موافقة. اختبار المتصفح المعزول محليًا يبقى بانتظار مالك النظام |
| **P4** | ترقية مشاريع مرشحة من DECLARED إلى PROVEN | معيار الهدف: 4 مشاريع PROVEN، ولكل منها نتيجة `command-hud/audit.py` قبل/بعد ودليل قابل لإعادة التشغيل؛ لا تنقل أرقام README إلى evidence بلا تشغيل |
| **P5** | حسم الترخيص وA27P | قرار ترخيص `agi-system` قبل نشر PyPI، وpush لـA27P مع conformance tests خضراء في CI |

**تعريف «كامل الطاقة» هنا:** طبقات L0–L5 تعمل فعليًا وباستمرارية 24×7 ضمن حدود تكلفة واستضافة مثبتة، مع أكثر من مشروع واحد في حالة `PROVEN` (والهدف التشغيلي في P4 هو أربعة). هذا لا يعني تشغيل 48 مشروعًا معًا ولا ادعاء 24×7 على خدمة تنام. يظل أي جزء غير مقاس في حالة `DECLARED` أو `SIMULATED` حتى ينتج دليلًا قابلًا لإعادة التشغيل.
