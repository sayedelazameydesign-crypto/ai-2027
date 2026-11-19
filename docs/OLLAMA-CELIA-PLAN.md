<div dir="rtl">

# طبقة الذكاء المحلية Ollama لـ AI-2027 وCeliaOS

هذا المستند يربط خطة تشغيل **Ollama** كطبقة ذكاء محلية (`Local Intelligence Layer`) بالبنية القائمة في `ai-2027` **دون تعديل نواة `projects/agi-system/core/`** (تبقى خالية من الاعتماديات وفق ADR-0002).

## المعمارية: Ollama يقترح، والنواة تقرّر وتتحقق

```
CeliaOS / AI-2027 (الهدف + التخطيط + الصلاحيات)
  ├── FreeFirstRouter (BudgetGuard MAX_SPEND=0)
  │     ├── OllamaLocalProvider (loopback 127.0.0.1 — $0 LOCAL، ملف 4GB: qwen3.5:0.8b, num_ctx=2048)
  │     ├── Free-First Remote (:free / allowlisted — Gemini / NVIDIA / Groq / OpenRouter)
  │     ├── OllamaCloudProvider (OLLAMA_API_KEY — مصنف UNKNOWN_COST وممنوع تلقائياً في $0)
  │     └── DeterministicFallbackProvider (احتياطي حتمي)
  ├── OllamaRuntimeClient (/api/tags, /api/ps, /api/chat + JSON Schema Pydantic V2, tools, /api/embed)
  ├── CeliaMemoryStore (SQLite RAG: chunk_document + embeddinggemma أو lexical_fallback صادق)
  └── Policy Gate -> Executor -> SHA-256 Verifier -> L1 Evidence
```

- **لا يملك Ollama أي صلاحية تنفيذ مباشر** على PowerShell أو GitHub: أي استدعاء أداة يمر أولًا عبر `OllamaRuntimeClient.chat_with_tools` ثم عبر بوابة الصلاحيات (`SandboxToolRuntime` / `ConnectorsBridge` مع الموافقة البشرية).

## ضبط جهاز 4GB RAM (i3 M380)

| الدور | النموذج الموصى به | ملاحظة الذاكرة |
|---|---|---|
| عام ومحادثة | `qwen3.5:0.8b` | ابدأ بسياق `OLLAMA_NUM_CTX=2048` ولا تبدأ بـ 3B أو 7B |
| برمجة ومراجعة | `qwen2.5-coder:0.5b` | يُختبر منفردًا |
| ذاكرة RAG | `embeddinggemma` | يضبط `keep_alive=0` لإفراغ النموذج قبل العودة للمحادثة |

## أوامر التشغيل والتحقق المستقل

```powershell
# المرحلة 0 على Windows 11 (فحص الذاكرة والقرص وollama --version و/api/tags وollama ps)
.\python-2027\scripts\ollama_windows_check.ps1

# فحص البروتوكول المحلي (لا يطبع VERIFIED إلا بعد نجاح الفحص الفعلي الحي)
cd python-2027
.venv\Scripts\ai2027-py27 ollama-check
.venv\Scripts\ai2027-py27 celia-memory index --paths ../docs ../README.md
.venv\Scripts\ai2027-py27 celia-memory search --query "Policy Gate Ollama"
```

## ملاحظة Ollama Cloud (`OLLAMA_API_KEY`)

خدمة Ollama Cloud خدمة سحابية منفصلة عن التشغيل المحلي وتتطلب حسابًا ومفتاح API، ولذلك يصنّفها `BudgetGuard` صراحةً كـ `UNKNOWN_COST` وتُحظر عند `AI2027_MAX_SPEND_USD=0` حتى يأذن المستخدم بتكلفة غير صفرية.

</div>
