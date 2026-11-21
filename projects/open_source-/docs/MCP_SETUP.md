# دليل إعداد وربط خوادم MCP — `os_mcp` (ADR-0017)

هذا دليل تشغيلي: كيف تربط خادم MCP خارجي (مثل خادم GitHub أو Gmail)، وكيف تشخّص
فشل الاتصال بدل أن يقف عند رسالة عامة.

المرجع المعماري: [`adr/0017-mcp-transport.md`](../adr/0017-mcp-transport.md).
التنفيذ: `packages/os_mcp/`.

> **قاعدة واحدة قبل كل شيء:** لا يكفي أن يمنح الرمز (PAT) الصلاحيات. كل خادم يُعطّل
> افتراضيًا، ويجب أن تفتحه أنت **عمدًا** بمتغيّرَي بيئة منفصلين.

---

## ١. المتغيّرات الثلاثة

| المتغيّر | القيمة | ما يفتحه بالضبط |
|---|---|---|
| `CELA_MCP_ENABLE` | `1` | قراءة ملف الإعداد والتحدث مع خوادم MCP |
| `CELA_MCP_ALLOW_HOST_EXEC` | `1` | **إضافةً**: خوادم `stdio` — أي تشغيل عملية على المضيف |
| `CELA_MCP_CONFIG` | مسار ملف | مكان ملف الإعداد إذا لم يكن الافتراضي |

الافتراضي: `~/.config/cela/mcp.json`

```bash
export CELA_MCP_ENABLE=1
export CELA_MCP_CONFIG="$HOME/.config/cela/mcp.json"
```

### لماذا opt-in منفصل لخوادم `stdio`؟

خادم `stdio` هو **عملية يبدأها النظام على المضيف**: خارج الصندوق المعزول، وخارج
`EgressPolicy` الذي يحرس الشبكة. من يستطيع تعديل `command` يستطيع تشغيل كود
بصالحياتك. لذلك `CELA_MCP_ENABLE=1` وحده لا يكفي؛ الخادم يبقى موقوفًا مع سبب
واضح:

> `stdio server would run as a process on the host, outside the sandbox and the egress policy; set CELA_MCP_ALLOW_HOST_EXEC=1 to allow host execution`

خادم `http` بعيد **لا** يحتاج هذا المتغيّر: هو محكوم بـ`EgressPolicy` وقائمة سماح
محدّدة، فيبقى داخل الحدود نفسها.

---

## ٢. قالب الإعداد

احفظه في `~/.config/cela/mcp.json` (أو ضع `CELA_MCP_CONFIG` مسارًا آخر):

```jsonc
{
  "mcpServers": {
    // (أ) خادم بعيد عبر HTTP — يحتاج رمزًا، ولا يحتاج.allow-host-exec
    "github": {
      "url": "https://api.githubcopilot.com/mcp/",
      "headers": {
        "Authorization": "Bearer ${GITHUB_TOKEN}"
      },
      "risk": "read_only",
      "tools": { "allow": ["search_code", "get_file_contents"] }
    },

    // (ب) خادم محلي عبر stdio — يحتاج CELA_MCP_ALLOW_HOST_EXEC=1
    "files": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/srv/docs"],
      "env": { "HOME": "${HOME}" },
      "risk": "read_only",
      "tools": { "allow": ["read_file", "list_directory"] }
    }
  }
}
```

### الحقول

| الحقل | المعنى |
|---|---|
| `url` | خادم HTTP بعيد. **أو** `command` + `args` لخادم `stdio` — واحد منهما بالضبط |
| `headers` | ترويسات الطلب. `${VAR}` يُستبدل من بيئة العملية **وقت الطلب** |
| `env` | بيئة خادم `stdio`، مع قائمة سماح فوقها |
| `risk` | `read_only` \| `write` \| `dangerous` — الافتراضي `dangerous` (يُوقف كل نداء حتى موافقة بشرية) |
| `tool_risk` | استثناء لأداة بعينها: `"write_file": "dangerous"` |
| `tools.allow` / `tools.deny` | تصفية الأدوات صراحةً |

### عن `${VAR}`: التوسعة عند الطلب، لا عند القراءة

هذا هو السلوك الذي يفاجئ الناس. **ملف الإعداد لا يحمل السرّ، ولا يُوسَّع عند قراءته:**

```
ملف الإعداد        →  {"Authorization": "Bearer ${GITHUB_TOKEN}"}   ← يبقى حرفيًا
عند الطلب         →  {"Authorization": "Bearer ghp_xxxx"}          ← يُوسَّع الآن
```

لو كان المتغيّر غير مضبوط **وقت الطلب**، فشلت العملية برسالة صريحة:

```
MCPConfigError: environment variable GITHUB_TOKEN is not set
```

وليس برأس فارغ. هذا مقصود (ADR-0011): السرّ يعيش في البيئة، لا في الملفات،
والغياب يجب أن يكون **خطأً ظاهرًا** لا قيمة فارغة صامتة.

**لهذا** رمزٌ صحيح الصلاحيات قد يفشل الاتصال: إن كان `GITHUB_TOKEN` غير موجود في
بيئة العملية التي تشغّل الخادم، فالملف يُقرأ بلا خطأ والاتصال يفشل عند أول طلب.

---

## ٣. التشخيص: `mcp doctor`

```bash
python -m os_server.cli mcp list      # الخوادم المُهيّأة وسياستها (بلا اتصال)
python -m os_server.cli mcp doctor    # اتصال، مصافحة، قائمة الأدوات، مسحها
python -m os_server.cli mcp doctor --json
```

`doctor` هو الأداة التي تجيب على «لماذا لا يعمل الاتصال؟» — فهي تفعل المصافحة
وتفحص الأدوات، لا مجرد قراءة الملف.

### كيف تقرأ المخرجات

في `health` لكل خادم:

| `status` | المعنى |
|---|---|
| `ok` | اتصل، ثم صافح، ثم عدّد الأدوات |
| `off` | موقوف عمدًا — **اقرأ `message`، فهو يذكر السبب بالضبط** |
| `error` | حاول الاتصال وفشل — `message` فيه السبب |

وكل خادم في `servers` يحمل `enabled` و`disabled_reason` — سبب الإيقاف مكتوب،
لا مُحذَّف.

---

## ٤. الأخطاء الشائعة — ما السبب، وأين

### (أ) «لماذا خادمي `stdio` موقوف؟»

```
status: off
message: stdio server would run as a process on the host, outside the sandbox
         and the egress policy; set CELA_MCP_ALLOW_HOST_EXEC=1 to allow host execution
```

**السبب:** لم تفتح opt-in الثاني. **الحل:** `export CELA_MCP_ALLOW_HOST_EXEC=1` —
واعلم أن هذا يسمح بتشغيل عملية على المضيف، فافعل ذلك فقط لخادم تثق به.

### (ب) «خادمي البعيد أُوقف بسبب الشبكة»

```
message: handshake failed: egress policy refused https://...: network boundary:
         DNS resolution failed: ...
         Local MCP servers must run over stdio; remote ones need an allow rule.
```

**السبب:** `EgressPolicy` لا تسمح بهذا المضيف بعد. **الحل:** أضف المضيف إلى قائمة
السماح صراحةً. القاعدة نفسها في `EgressPolicy`: **المضيف غير المسموح به يُرفض**،
ولا يُخفَّف تلقائيًا.

### (ج) «الرمز صحيح لكن الاتصال يفشل»

```
MCPConfigError: environment variable GITHUB_TOKEN is not set
```

**السبب:** الرمز غير موجود في بيئة العملية وقت الطلب — لا في الملف، ولا في
الصدفة التي شغّلت الأمر. **الحل:** `export GITHUB_TOKEN=...` في نفس البيئة التي
تشغّل الخادم، وتحقّق بـ`env | grep GITHUB_TOKEN` (لا تطبع القيمة).

### (د) «الاتصال يُفتح ثم يُغلق»

```
message: peer is not speaking MCP
message: mcp server '<name>' closed stdout
message: mcp client '<name>' is closed
```

**السبب:** ما على الطرف الآخر ليس خادم MCP (صفحة ويب، خطأ في التحقق (auth)،proxy). **الحل:**
تأكّد أن `url` يشير إلى نقطة `/mcp` حقيقية، وأن الرمز مُرسل في `Authorization`،
وأن الرابط لا يُعيد تحويلًا (redirect) إلى صفحة تسجيل دخول.

### (هـ) «الخادم يقبل الطلب لكن لا يرد»

```
message: <method>: server accepted the request but sent no response
```

**السبب:** طلب بلا استجابة — عادةً الخادم لا يدعم الطريقة المطلوبة، أو الجلسة
انتهت. **الحل:** جرّب `mcp list` على نفس الخادم، وأعد تشغيل الخادم بعد أن يبقى
مفتوحًا (بعض خوادم `stdio` تخرج إذا لم تقرأ stdout).

### (و) «كل الأدوات مُصفّاة — ظهرت صفر أدوات»

راجع `filtered` و`quarantined` في مخرجات `doctor`:
- `filtered` = أُزيلت بتصريحتك (`tools.allow` / `tools.deny`) — تأكّد أن الاسم صحيح.
- `quarantined` = رُفضت بعد المسح الأمني — **هذا مقصود**، ولا يُخفَّف.

---

## ٥. قائمة تحقّق سريعة

```bash
# ١. الملف موجود ويُقرأ
python -m os_server.cli mcp list

# ٢. الخوادم مفعّلة وسياستها ظاهرة (enabled / disabled_reason)
python -m os_server.cli mcp list --json | python3 -m json.tool

# ٣. الاتصال فعليًا: مصافحة + أدوات
python -m os_server.cli mcp doctor

# ٤. الرمز موجود (لا تطبع قيمته)
test -n "$GITHUB_TOKEN" && echo "token present" || echo "token MISSING -> will fail at request time"
```

---

## ٦. حدود — اقرأها قبل أن تفعّل شيئًا

- **الافتراضي مغلق.** بلا `CELA_MCP_ENABLE=1` لا يُقرأ أي ملف ولا يُتصل بأي خادم.
- **`CELA_MCP_ALLOW_HOST_EXEC=1` تشغيل على المضيف**، خارج الصندوق وخارج سياسة
  الخروج. افتحه لخادم تثق به فقط.
- **كل أداة خارجية تمرّ بالحواجز** (`Guardrails` ← تصنيف الخطر ← بوابة HITL) قبل
  أن تصل إلى النموذج أو تُنفَّذ. الأداة لا تُسجَّل قبل أن تمرّ.
- **الرمز لا يُطبع ولا يُسجَّل** في المخرجات ولا في سجل التدقيق — يُسجَّل
  الاسم وطوله وبصمة `sha256` فقط.
- **الخادم البعيد يحتاج إذنًا صريحًا** في `EgressPolicy`؛ الاتصال المحلي عبر
  `stdio` هو البديل المُقيَّد.

للتفاصيل المعمارية وإ Reasons القبول: [`adr/0017-mcp-transport.md`](../adr/0017-mcp-transport.md).
