# تقرير مرحلة A و B — جرد المكوّنات ومقارنة الفجوات
**نوع التقرير:** فحص قراءة فقط (read-only). لا تعديل، لا دمج، لا تجاوز حماية.
**المستودع الداخلي:** `sayedelazameydesign-crypto/ai-2027` (branch `arena/de047a78-ai-2027`, HEAD `e706bb0`)
**المستودع الخارجي:** `Arindam200/awesome-ai-apps` (`main`, MIT, 144 تطبيقاً/وكيلاً)
**تاريخ التدقيق:** 2026-11-24

> **القاعدة الهندسية المطبقة:** نستفيد من الأفكار والتنفيذ القابل لإعادة الاستخدام، لكن لا نستبدل Core أو Kernel أو Router أو بوابات Evidence الموجودة. المستودع الخارجي مصدر تعلم ومكونات، وليس مشروعاً ندمجه كاملاً.

---

## الخلاصة التنفيذية (TL;DR)

| المكون المقترح من الخارج | وجوده في AI-2027 | القرار | الأهمية |
|---|---|---|---|
| **Coding Harness** | تنفيذ داخل الـ Sandbox كامل (إنشاء/تعديل/اختبار/تحقق/git-diff/إعادة تخطيط) — **الفجوة: نقل العمل إلى مشروع المستخدم الحقيقي بموافقة** | **نسحب انتقائياً** — لا نقل الحزمة كاملة | عالية |
| **Human-in-the-Loop (موافقة)** | جزئي (Approvals في connectors-hub، لا gate عام على أدوات تعديل المشروع) | **نستفيد من الفكرة، ندمجها في gate واحد** | عالية |
| **Browser Agent** | موجود بعمق (`browser_skill_adapter`، 3 مراحل) | **لا نسحب الكود** — نواصِل تفعيل Stages 2/3 | متوسطة |
| **Deep Researcher** | موجود جزئي (`research_*` + planner) | **نستفيد من نموذج searcher→analyst→writer فقط** | متوسطة |
| **MCP Agents** | موجود جزئي (`mcp_server.py` stdio، أداة واحدة) | **نستفيد من مفاهيم Universal Gateway، لا الكود** | عالية |
| **Memory Agents** | موجود بنزاهة (`memory/store.py` + `retrieval.py`) | **لا نسحب** — نطوّر الاسترجاع حول بوابة Evidence | منخفضة |

> **تصحيح مبنيّ على التحقق من الكود (2026-11-24):** العبارة السابقة «لا توجد دورة تنفيذ واختبار» **غير دقيقة**. تم التحقق من `tools.py` / `tools_advanced.py` / `agent.py`:
> - `sandbox.write_artifact` / `sandbox.edit_file` — إنشاء + تعديل داخل الـ sandbox.
> - `sandbox.run_python` / `sandbox.run_tests` — تشغيل Python وunittest مع **رفض الـ silent pass على 0 اختبار**.
> - `sandbox.verify_artifact` — SHA-256 مستقل + L0 ledger + tamper detection.
> - `workspace.git_diff` / `workspace.read_file` — عرض git status/diff.
> - `agent.py` يدور `planner.decide` **كل خطوة** مع `AgentStepTrace` و`format_step_history`.
>
> **الفجوة الحقيقية:** الوكيل يعمل داخل الـ sandbox، لكن لا يوجد مسار متكامل لنقل التغييرات إلى **مشروع المستخدم الحقيقي**: اقتراح Patch، عرض Diff كامل، طلب موافقة محددة، تطبيق متزامن، تحقق، ثم موافقة منفصلة على Push/PR. هنا بالضبط تستفيدنا مكونات Coding Harness (كتصميم، لا كحزمة).

**النتيجة المحورية:** لا يُنقل Coding Harness كحزمة كاملة (يعتمد على `openai-agents` + `pydantic`، لا يدعم Git automation أو ذاكرة دائمة أو حاويات، ولا أوامر اختبار متنوعة). يُستفاد منه **انتقائياً** لبناء **Coding Workspace Engine** داخل `python-2027`، مع الاحتفاظ بـ `FreeFirstRouter` و`AgentPlannerProtocol` كمسار النموذج.

---

## المرحلة A — جرد AI-2027 (ما هو موجود فعلاً)

المستودع أكبر بكثير من قائمة المشاريع؛ البنات الأساسية موجودة وعميقة.

### 1. النواة ومنطق التشغيل
| المسار | الملفات | الحالة |
|---|---|---|
| **Agent Core / Agent Studio** | `python-2027/src/ai2027_protocol/agent.py`, `studio.py`, `server.py` | موجود ومختبر |
| **Kernel** | `kernel-2027/` (19 ملفاً) | موجود |
| **Model Router** | `python-2027/.../router.py` (319 سطراً) | موجود، يحتاج إثبات حي |
| **Budget Guard** | `python-2027/.../budget.py` (343 سطراً) | موجود (MAX_SPEND=0 افتراضياً) |
| **Planner** | `python-2027/.../planner_engine.py` (498 سطراً) — `AgentPlannerProtocol`, `FreeFirstAgentPlanner`, نمط offline/online | موجود، يحتاج توصيل حي |
| **Tools** | `tools.py`, `tools_exec.py`, `tools_text.py`, `tools_advanced.py`, `tools_validate.py`, `tools_describe.py`, `tools_deliver.py` | موجود — نظام أدوات مقسم |
| **Verifier / Audit** | `verify.py`, `auditor.py`, `auditor_ast.py`, `compliance.py` | موجود — بوابة Evidence/تدقيق |

### 2. المكوّنات الخارجية-متصلة
| المسار | الحالة | الملاحظة |
|---|---|---|
| **MCP** | `mcp_server.py` (426 سطراً, stdio), `mcp_stdio.py`, `mcp_contracts.py`, `mcp_attestation.py`, `mcp_inspector.py` | تنفيذ أولي: نقل stdio، أداة قراءة واحدة، لا HTTP/SSE |
| **Connectors Hub** | `connectors-hub/src/connectors_hub/` (21 ملفاً): `approvals.py`, `catalog.py`, `credentials.py`, `crypto.py`, `http_api.py`, `reasoning.py` | أساس موجود، تكامل غير مكتمل |
| **Browser** | `browser_skill_adapter/adapter.py` (1622 سطراً) + `handoff_contract.py`, `probe.py`, `run_local_task.py`, `verified_handoff.py`, `windows_acl.py` | **بنية أعمق من المستودع الخارجي**: 3 مراحل، `DomainPolicy`, `Redactor`، أدوات تدقيق |
| **Memory** | `memory/store.py` (535 سطراً), `memory/retrieval.py` (288 سطراً), `memory/evaluate.py` | **بوابة Evidence صارمة**، lexical + موارد يدوية، نزيهة في الحدود |
| **Fleet / Task Orchestration** | `fleet-2027/` (37 ملفاً) | موجود |
| **Agent Substrate (K8s)** | `agent-substrate/` (32 ملفاً): `controller.py`, `gateway.py`, `manifests/*.yaml` | نموذج أولي، ليس منصة إنتاج متعددة المستخدمين |
| **Workspace UI** | `workspace-ui/` (React/TS): `StudioWorkspace.tsx`, `ConnectorsWorkspace.tsx`, `integrations.ts` | موجود، تشغيل محلي loopback |
| **Live Agent** | `live-agent/`: `server.py`, `llm_planner.py`, `bench.py` | مخطط حتمي، ليس نموذج LLM حياً |
| **Research (P4)** | `python-2027/.../research_*.py` (api, experiment, experiment_store, store, contracts, ledger, radar, sources) | PR #185 — مفتوح، محجوب (راجع قسم P4) |

### 3. وثائق الحالة الموجودة
- `docs/AI-2027-COMPETITIVE-ARCHITECTURE.md` — تقييم معماري
- `docs/PAP-2027-GAP-AUDIT.md` — جرد فجوات
- `docs/TIERED-ARCHITECTURE.md` — بنية متدرجة
- `docs/BROWSERSKILL-PLAN.md` — خطة BrowserSkill (مرجع مراحل المتصفح)

---

## المرحلة B — جرد awesome-ai-apps (المرشّحون لإعادة الاستخدام)

المستودع مقسّم إلى 8 فئات. المرشّحون الستة ذوو الأولوية ومحتواهم الفعلي:

### 1. `simple_ai_agents/browser_agent`
- **المحتوى:** `main.py` (747 بايت) فقط. يستخدم `browser_use` + `ChatOpenAI` (Nebius) لمهمة واحدة ثابتة (فلبكار، بحث حاسوب، استخراج سعر).
- **الاعتمادات:** `browser-use>=0.5.11`, `langchain-openai`, `NEBIUS_API_KEY`.
- **القيمة:** يمثل **فكرة library `browser_use`** فقط. لا إدارة جلسات، لا عزل، لا تدقيق.
- **لا يُسحب:** AI-2027 يمتلك `browser_skill_adapter` (1622 سطراً) ببنية أمنية أعمق (DomainPolicy، Redactor، 3 مراحل).

### 2. `advance_ai_agents/deep_researcher_agent`
- **المحتوى:** `agents.py` (6425 بايت) — `DeepResearcherAgent(Workflow)` من **agno**: searcher → analyst → writer، ثلاث agents من Nebius (`DeepSeek-V3`)، أدوات ScrapeGraph.
- **القيمة:** **نموذج مريح لتعاون متعدد المراحل** مع تعليمات مضادة للهلوسة (لا تخترع روابط). الفكرة ممتازة.
- **لا يُسحب ككود:** مرتبط بإطار `agno` + Nebius + ScrapeGraph. AI-2027 لديه `research_*` و`planner_engine`؛ يُستفاد فقط من **نمط البحث→التحليل→التوليف**.

### 3. `memory_agents/agno_memory_agent` (ومنها بقية memory_agents)
- **المحتوى:** `main.py` (2071 بايت) — agno `Memory` v2 + `SqliteMemoryDb` + `SqliteStorage`، `enable_agentic_memory`.
- **القيمة:** فكرة ذاكرة بواسطة النموذج (استخلاص ذاكرة تلقائي).
- **لا يُسحب:** AI-2027 لديه `memory/` **أكثر نزاهة** (بوابة Evidence: لا تُدخل إلا نتائج `VERIFIED` + hash). استبدالها بذاكرة agno سيُدمّر ضمان النزاهة. يُستفاد فقط من **آليات الاسترجاع إذا أثبتت تفوقها** — وحتى ذلك مشروط.

### 4. `mcp_ai_agents/*` (`mcp_starter`, `github_mcp_agent`, ...)
- **المحتوى:** `mcp_starter/main.py` (3335 بايت) — وكيل يستخدم `MCPServerStdio` لتشغيل `@modelcontextprotocol/server-github` عبر `npx`، بنموذج Llama عبر Nebius.
- **القيمة:** **فكرة Universal Tool Gateway**: وكيل يتصل بخوادم MCP خارجية.
- **لا يُسحب:** تنفيذ محدود (يعتمد على `npx` + server GitHub واحد). AI-2027 لديه `mcp_server.py` (stdio) و`connectors-hub`؛ يحتاج **توسيع النقل (HTTP/SSE)** و**كتالوج أدوات موحد**، وهذا بناء داخلي وليس سحب كود.

### 5. `starter_ai_agents/coding_harness_starter` ← **الأعلى قيمة**
- **المحتوى (بنية مقسّمة جيداً):**
  - `coding_harness/models.py` (3698 بايت) — `FileOperation`, `ImplementationPlan`, `PatchProposal`, `RunSummary`, `TestRunRecord`.
  - `coding_harness/approval.py` (1167 بايت) — `ApprovalGate`: يطلب موافقة صريحة على Patch، **لا يُجري تعديلاً**.
  - `coding_harness/workspace.py` (8906 بايت) — `Workspace`/`WorkspaceError`: فحص/تعديل مساحة العمل.
  - `coding_harness/patching.py` (9630 بايت) — `prepare_patch`, `apply_prepared_patch`, `render_unified_diff`.
  - `coding_harness/testing.py` (3986 بايت) — `run_tests`, `TestResult`.
  - `coding_harness/runner.py` (10564 بايت) — `HarnessRunner`: دورة plan→approve→patch→test→review.
  - `coding_harness/agent.py` (9757 بايت) — `CodingAgent`, `ModelConfig`.
  - `main.py` — CLI، مع حقن (`input_fn`, `test_executor`) لجعل الاختبارات offline.
- **القيمة:** **دورة هندسة برمجيات كاملة بموافقة إنسانية**: فهم → تخطيط → عرض diff → موافقة → كتابة → اختبار → مراجعة فشل. هذا يملأ فجوة حقيقية: AI-2027 لديه أدوات تنفيذ وedit، لكن ليس **دورة plan/approve/patch/test موحدة**.
- **القرار:** **نسحب** (ككود + بنية)، يُكيَّف مع Tool contract وبوابة Evidence.

### 6. `simple_ai_agents/human_in_the_loop_agent`
- **المحتوى:** `main.py` (3070 بايت) — agno agent بـ `pre_hook` على كل أداة يطلب y/n/retry، `StopAgentRun`/`RetryAgentRun`، MAX_RETRIES=3.
- **القيمة:** **فكرة gate الموافقة العام** على استدعاء الأداة.
- **لا يُسحب ككود:** مرتبط بـ agno. يُستفاد من **فكرة gate واحد يغطي كل الأدوات** — تُدمج في AI-2027's Approvals/Tool gate.

---

## المرحلة C — مقارنة الفجوات (Missing / Existing / Partial)

| المجال | الوضع في AI-2027 | المرشح من الخارج | القرار |
|---|---|---|---|
| **Core / Kernel** | أساس قوي | — | **لا نمسّ** |
| **Model Router** | موجود، يحتاج إثبات حي | — | **لا نسحب** — توصيل مزوّد حقيقي (Gemini/Ollama) هو المهمة |
| **Adaptive Planning** | `planner_engine` موجود | Deep Researcher workflow | **نستفيد من النمط** (search→analyze→write) |
| **Coding Agent** | أدوات تنفيذ/edit موجودة، **لا دورة موحدة** | Coding Harness | **نسحب** (الأكثر قيمة) |
| **Human-in-the-Loop** | Approvals في connectors-hub، **لا gate عام على الأدوات** | human_in_the_loop | **نستفيد من الفكرة** — gate واحد |
| **Browser** | `browser_skill_adapter` عميق (Stages 1–3) | browser_agent (بسيط) | **لا نسحب** — نواصِل Stages 2/3 |
| **MCP / Tool Gateway** | stdio + أداة واحدة | mcp_starter | **نستفيد من فكرة Gateway** — بناء داخلي |
| **Memory** | بوابة Evidence صارمة، lexical | agno_memory | **لا نسحب** — نُطوّر الاسترجاع حول البوابة |
| **Cloud Computer** | agent-substrate MVP | — | بناء داخلي (توثيق إجراءات) |
| **Multi-user SaaS** | غير مثبت | — | بناء داخلي (المرحلة 6) |

---

## المرحلة D — ترتيب المكونات (حسب القيمة / المخاطر / التكلفة)

1. **Coding Harness** — سحب كود + بنية. القيمة أعلى، التكلفة متوسطة، يُخضع لبوابة Evidence.
2. **Human-in-the-Loop gate** — فكرة فقط، تكلفة منخفضة، تُدمج في gate موجود.
3. **Deep Researcher نمط** — فكرة، تكلفة منخفضة، تُدمج في planner/research.
4. **MCP Universal Gateway** — فكرة، تكلفة مرتفعة، بناء داخلي (المرحلة 3).
5. **Memory استرجاع** — مشروط بإثبات تفوق، تكلفة متوسطة.
6. **Browser Stages 2/3** — تفعيل داخلي، لا سحب.

---

## المرحلة E — دمج انتقائي (كيف، وليس ماذا)

> **القرار المعتمد:** لا نقل الحزمة كاملة. يُستفاد من Coding Harness كتصميم لبناء **Coding Workspace Engine** داخل `python-2027`. لا ننقل `agent.py` ولا اعتماد `openai-agents` إلى النواة — `FreeFirstRouter` و`AgentPlannerProtocol` هما المسار الصحيح.

### دورة العمل المستهدفة (Coding Workspace Engine — تصميم مقترح، ليس منفذاً)
`User Task → FreeFirstRouter+Planner → Inspect Workspace → Structured Patch Proposal → Path/Policy Validation → Generate Full Diff → Human Approval → (No→zero writes / Yes→) Apply Atomic Patch → Run Allowed Tests → Independent Verification → All Gates Pass → Report → Git Branch/Commit Proposal (موافقة منفصلة للـ Push/PR)`

### استعارة انتقائية من Coding Harness
| الملف الخارجي | الفائدة | قرار الدمج |
|---|---|---|
| `coding_harness/models.py` | عقود Patch Proposal (FileOperation, PatchProposal, RunSummary) | **تكييف العقود** لتناسب Tool contract الحالي |
| `coding_harness/workspace.py` | قفل وفحص مساحة العمل | **تكييف الأمان** مع resolve_safe_path الحالي |
| `coding_harness/patching.py` | Diff والتحقق قبل الكتابة | **استعارة انتقائية** — render_unified_diff + apply_prepared_patch |
| `coding_harness/approval.py` | موافقة مستقلة على التعديل | **ربط ببوابة Studio** (Approvals الحالية) |
| `coding_harness/testing.py` | نمط تشغيل الاختبارات | **إعادة تصميم** — اختبارات حسب المشروع، ليس unittest فقط |
| `coding_harness/runner.py` | تسلسل Plan→Approve→Apply→Test | **الاستفادة من النمط** فقط |

**ما لا ننقله:** `agent.py`، اعتماد `openai-agents`، `fixture_repo`، `assets`، أي `.env`/أسرار/قواعد بيانات.

### للبقية (أفكار، لا كود)
- Human-in-the-Loop: تطبيق gate على `tools_*` عبر Policy Engine (موافقة على كل تعديل مشروع).
- Deep Researcher: تطبيق نمط search→analyze→write في `research_*`/`planner_engine` مع تعليمات مضادة للهلوسة.
- MCP: تصميم `Universal Tool Gateway` (discovery, contract check, policy, run, audit) فوق `mcp_server.py` + `connectors-hub`.

---

## نقاط الانتباه (تُطبّق بحزم)

- **لا نسخ شامل**، لا استبدال للنواة، لا اعتماد على خدمات مدفوعة (Nebius/OpenAI/ScrapeGraph) دون موافقة صريحة.
- **الحفاظ على MIT + إشعارات الترخيص** في أي ملف منقول.
- **لا نقل قواعد بيانات/أسرار/إعدادات تشغيل** من المشروع الأصلي.
- **Browser Agent** يحتاج عزلاً وصلاحيات محدودة (موجود جزئياً في `DomainPolicy`).
- **Coding Harness** يُخضع لبوابات Evidence — لا يُنتج تعديلاً دون موافقة + تحقق.
- **لا إعلان اكتمال دون إثبات التشغيل** (CI ≠ تشغيل إنتاجي حقيقي).
- **P0 أمني (Issue #21)**: مفتاح Gemini حقيقي تاريخياً — الإبطال/التدوير غير مثبت. يُغلق قبل أي نشر.

## P4 Scientific Intelligence (حالة عملية قائمة)
PR #185 مفتوح، `mergeStateStatus=BLOCKED` بسبب **required_status_checks** في ruleset `27819411`. كل الفحوصات الفعلية خضراء. **لا `--admin`، لا تجاوز.** الإصلاح: تصحيح الـ ruleset (إعادة تسمية `Evidence gate`، إزالة سياقات وهمية `a`/`git`/`docs/GOVERNANCE.md`/`Cloudflare Edge Gateway`، والبت في المشروطة `Kubernetes`/`Dependabot`). راجع تقرير سابق لتفاصيل السياق.

## الخطوة التالية المقترحة
الموافقة على **سحب Coding Harness فقط** (مرحلة E)، ثم تنفيذه كحزمة داخلية مع اختبارات offline مستقلة وربط ببوابة Evidence، قبل المضي إلى مكونات الأفكار الأخرى.
