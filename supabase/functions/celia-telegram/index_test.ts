// Run: AI2027_EDGE_TEST=1 deno test --allow-env --allow-read supabase/functions/celia-telegram/
// Parity vectors come from the REFERENCE python-2027 CELIA
// (scripts/celia_edge_parity_vectors.py), so this proves the port, not itself.
import {
  type Change,
  classifyKind,
  completeFreeFirst,
  type CeliaStore,
  detectMode,
  geminiProvider,
  looksSensitive,
  makeHandler,
  MemoryBook,
  type MemoryRecord,
  openAiProvider,
  parseMemoryCommand,
  type Provider,
  renderOwnerPrompt,
  routeOf,
  SPEC,
  splitForTelegram,
  type Turn,
  webhookSecret,
} from "./index.ts";

const V = JSON.parse(await Deno.readTextFile(new URL("./parity_vectors.json", import.meta.url)));

function eq(actual: unknown, expected: unknown, msg = "") {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) throw new Error(`${msg}\n  actual:   ${a}\n  expected: ${e}`);
}
function ok(cond: unknown, msg = "") {
  if (!cond) throw new Error(msg || "assertion failed");
}

// ------------------------------------------------------------------ parity
Deno.test("mode detection matches python-2027 detect_mode", () => {
  for (const [text, mode] of V.modes) eq(detectMode(text), mode, text);
});

Deno.test("memory command parsing matches python-2027 parse_memory_command", () => {
  for (const [text, expected] of V.commands) {
    const c = parseMemoryCommand(text);
    eq(c ? [c.kind, c.argument] : null, expected, text);
  }
});

Deno.test("secret detection matches python-2027 looks_sensitive", () => {
  for (const [text, expected] of V.sensitive) eq(looksSensitive(text), expected, text);
});

Deno.test("memory layers match python-2027 classify_kind", () => {
  for (const [text, expected] of V.kinds) eq(classifyKind(text), expected, text);
});

Deno.test("owner prompts are byte-identical to python-2027 for every mode", () => {
  for (const [mode, prompt] of Object.entries(V.prompts)) eq(renderOwnerPrompt(mode as never), prompt, mode);
  eq(renderOwnerPrompt("executive", V.memories_input), V.prompt_with_memories, "with memories");
});

class MemStore {
  rows: MemoryRecord[] = [];
  fail = false;
  loadMemory() {
    return Promise.resolve(this.rows.map((r) => ({ ...r })));
  }
  applyChange(change: Change) {
    if (this.fail) return Promise.reject(new Error("db down"));
    if (change && "insert" in change) this.rows.push({ ...change.insert });
    if (change && "delete" in change) this.rows = this.rows.filter((r) => r.id !== change.delete);
    return Promise.resolve();
  }
}

Deno.test("a scripted memory session gives python-2027's exact replies and ids", async () => {
  const store = new MemStore();
  const book = new MemoryBook([], store, () => V.now, V.session_max_records);
  for (const [i, [text, stage, reply, ids]] of (V.session as [string, string, string, number[]][]).entries()) {
    const cmd = parseMemoryCommand(text);
    ok(cmd, text);
    const out = await book.executeDurably(cmd!, `telegram:${i}`);
    eq([out.stage, out.reply], [stage, reply], text);
    eq(book.records.map((r) => r.id), ids, `ids after: ${text}`);
  }
  eq(store.rows.map((r) => r.id), book.records.map((r) => r.id), "store == book");
});

Deno.test("a failed write is rolled back and admitted", async () => {
  const store = new MemStore();
  store.fail = true;
  const book = new MemoryBook([], store, () => V.now);
  const out = await book.executeDurably({ kind: "remember", argument: "معلومة" }, "t:1");
  eq(out.stage, "memory_store_failed");
  eq(book.records.length, 0);
});

Deno.test("memory survives a restart (rows reloaded from the store)", async () => {
  const store = new MemStore();
  await new MemoryBook([], store, () => V.now).executeDurably(
    { kind: "remember", argument: "قررنا عدم تعديل Kernel في مشروع CELIA" }, "t:9");
  const restarted = new MemoryBook(await store.loadMemory(), store);
  ok(restarted.recall("هل نعدل الـ Kernel؟", 1)[0].startsWith("#1 (مشروع"));
  eq(restarted.recall("كمّلنا فين؟", 1).length, 1, "recent fallback");
});

Deno.test("SPEC is the generated python persona (one identity)", () => {
  ok(SPEC.persona.identity_text.includes("سيليا"));
  eq(SPEC.persona.autonomy, "L0");
});

// --------------------------------------------------------------- providers
function recordingFetch(reply: unknown, status = 200) {
  const calls: { url: string; init: RequestInit }[] = [];
  const fn = (url: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(url), init: init ?? {} });
    return Promise.resolve(new Response(JSON.stringify(reply), { status }));
  };
  return { calls, fn: fn as typeof fetch };
}

Deno.test("gemini sends the persona as systemInstruction and history as turns", async () => {
  const f = recordingFetch({ candidates: [{ content: { parts: [{ text: "أهلاً" }] } }] });
  const p = geminiProvider((n) => ({ GEMINI_API_KEY: "k" } as Record<string, string>)[n], f.fn);
  ok(p.configured());
  const hist: Turn[] = [{ role: "user", content: "a" }, { role: "assistant", content: "b" }];
  eq(await p.complete("SYS", hist, "c", AbortSignal.timeout(1000)), "أهلاً");
  const body = JSON.parse(String(f.calls[0].init.body));
  eq(body.systemInstruction.parts[0].text, "SYS");
  eq(body.contents.map((c: { role: string }) => c.role), ["user", "model", "user"]);
  ok(!f.calls[0].url.includes("key="), "key travels in a header, not the URL");
});

Deno.test("openrouter refuses non-free models (unknown cost => refuse)", () => {
  const env = (m: string) => (n: string) => ({ OPENROUTER_API_KEY: "k", OPENROUTER_MODEL: m } as Record<string, string>)[n];
  ok(!openAiProvider("openrouter", env("openai/gpt-5")).configured());
  ok(openAiProvider("openrouter", env("openrouter/free")).configured());
  ok(openAiProvider("openrouter", env("qwen/qwen3:free")).configured());
});

Deno.test("free-first skips failing and unconfigured providers", async () => {
  const mk = (name: string, conf: boolean, out: string | Error): Provider => ({
    name,
    configured: () => conf,
    complete: () => (out instanceof Error ? Promise.reject(out) : Promise.resolve(out)),
  });
  eq(await completeFreeFirst([mk("a", false, "x"), mk("b", true, new Error("429")), mk("c", true, "ok")], "s", [], "u"),
    { text: "ok", provider: "c" });
  eq(await completeFreeFirst([mk("a", true, "")], "s", [], "u"), null);
});

// ----------------------------------------------------------------- webhook
const TOKEN = "123:TEST";
const OWNER = 777;

class FakeStore extends MemStore implements CeliaStore {
  claimed = new Set<number>();
  turns: Turn[] = [];
  claimUpdate(id: number) {
    const first = !this.claimed.has(id);
    this.claimed.add(id);
    return Promise.resolve(first);
  }
  loadTurns() {
    return Promise.resolve([...this.turns]);
  }
  appendTurns(_c: string, u: string, a: string) {
    this.turns.push({ role: "user", content: u }, { role: "assistant", content: a });
    return Promise.resolve();
  }
  healthz() {
    return Promise.resolve(true);
  }
}

function setup(envExtra: Record<string, string> = {}, providerReply: string | null = "رد سيليا") {
  const tgCalls: { method: string; body: Record<string, unknown> }[] = [];
  let nextId = 500;
  const fetchFn = ((url: string | URL | Request, init?: RequestInit) => {
    const method = String(url).split("/").pop()!;
    const body = JSON.parse(String(init?.body ?? "{}"));
    tgCalls.push({ method, body });
    const result = method === "sendMessage" ? { message_id: nextId++ } : method === "getMe" ? { username: "CeliaBot" } : true;
    return Promise.resolve(new Response(JSON.stringify({ ok: true, result })));
  }) as typeof fetch;
  const systems: string[] = [];
  const provider: Provider = {
    name: "groq",
    configured: () => providerReply !== null,
    complete: (system) => {
      systems.push(system);
      return Promise.resolve(providerReply ?? "");
    },
  };
  const env: Record<string, string> = {
    CELIA_TELEGRAM_TOKEN: TOKEN, CELIA_OWNER_IDS: String(OWNER),
    SUPABASE_URL: "https://ref.supabase.co", ...envExtra,
  };
  const store = new FakeStore();
  const logs: string[] = [];
  const handler = makeHandler({
    env: (n) => env[n], store, fetchFn, providers: [provider],
    sleep: () => Promise.resolve(), clock: () => V.now, log: (l) => logs.push(l),
  });
  return { handler, tgCalls, store, systems, logs };
}

async function post(handler: (r: Request) => Promise<Response>, update: unknown, secret?: string) {
  return handler(new Request("https://x/functions/v1/celia-telegram/telegram", {
    method: "POST",
    headers: { "X-Telegram-Bot-Api-Secret-Token": secret ?? await webhookSecret(TOKEN) },
    body: JSON.stringify(update),
  }));
}
const msg = (id: number, text: string, from = OWNER, type = "private") => ({
  update_id: id,
  message: { message_id: id * 10, text, chat: { id: from, type }, from: { id: from } },
});

Deno.test("wrong webhook secret is rejected and nothing is sent", async () => {
  const t = setup();
  eq((await post(t.handler, msg(1, "hi"), "nope")).status, 401);
  eq(t.tgCalls.length, 0);
});

Deno.test("chat reply: 👀, typing, animated placeholder, reveal, then history", async () => {
  const long = "خطة اليوم: " + "خطوة مهمة. ".repeat(20);
  const t = setup({}, long);
  await t.store.applyChange({ insert: { id: 1, kind: "project", text: "المرحلة التالية في مشروع AI-2027 هي الذاكرة", source: "s", created_at: V.now, confidence: 1 } });
  eq((await post(t.handler, msg(1, "رتّبي لي خطة مشروع AI-2027"))).status, 200);
  const methods = t.tgCalls.map((c) => c.method);
  eq(methods.slice(0, 3), ["setMessageReaction", "sendChatAction", "sendMessage"]);
  ok(String(t.tgCalls[2].body.text).startsWith("🧭 سيليا · مديرة المهام"), "mode badge placeholder");
  const edits = t.tgCalls.filter((c) => c.method === "editMessageText");
  ok(edits.some((e) => String(e.body.text).endsWith(" ▍")), "typewriter reveal");
  eq(edits.at(-1)!.body.text, long, "final text replaces the placeholder");
  ok(t.systems[0].includes("مديرة المهام") && t.systems[0].includes("هي الذاكرة"), "mode + memory in prompt");
  eq(t.store.turns.length, 2);
  ok(t.logs.some((l) => l === "celia stage=replied"));
  ok(!t.logs.join("\n").includes("خطة"), "message text is never logged");
});

Deno.test("animation can be switched off", async () => {
  const t = setup({ CELIA_ANIMATION: "0" });
  await post(t.handler, msg(1, "ازيك"));
  eq(t.tgCalls.filter((c) => c.method === "editMessageText").length, 0);
  eq(t.tgCalls.filter((c) => c.method === "sendMessage").at(-1)!.body.text, "رد سيليا");
});

Deno.test("memory command runs in code, no model call, durable", async () => {
  const t = setup();
  await post(t.handler, msg(1, "تذكّري: أفضّل الردود القصيرة"));
  eq(t.systems.length, 0);
  ok(String(t.tgCalls.find((c) => c.method === "sendMessage")!.body.text).startsWith("حفظتُ ✅ رقم 1"));
  eq(t.store.rows[0].text, "أفضّل الردود القصيرة");
  eq(t.store.rows[0].source, "telegram:10");
  ok(t.tgCalls.some((c) => c.method === "setMessageReaction" && JSON.stringify(c.body).includes("✍")));
});

Deno.test("telegram retries of the same update are processed once", async () => {
  const t = setup();
  await post(t.handler, msg(5, "تذكّري: شيء"));
  await post(t.handler, msg(5, "تذكّري: شيء"));
  eq(t.store.rows.length, 1);
  ok(t.logs.includes("celia stage=duplicate_update"));
});

Deno.test("strangers and groups get nothing", async () => {
  const t = setup();
  await post(t.handler, msg(1, "hi", 999));
  await post(t.handler, msg(2, "hi", OWNER, "group"));
  eq(t.tgCalls.length, 0);
  ok(t.logs.includes("celia stage=ignored_sender") && t.logs.includes("celia stage=ignored_group"));
});

Deno.test("before owners are configured a chat only learns its own id", async () => {
  const t = setup({ CELIA_OWNER_IDS: "" });
  await post(t.handler, msg(1, "/start", 4242));
  eq(t.tgCalls.length, 1);
  ok(String(t.tgCalls[0].body.text).includes("4242"));
  eq(t.systems.length, 0);
});

Deno.test("no free model => CELIA's honest fallback, no history written", async () => {
  const t = setup({}, null);
  await post(t.handler, msg(1, "ازيك"));
  eq(t.tgCalls.filter((c) => c.method === "editMessageText").at(-1)!.body.text, SPEC.persona.owner_fallback);
  eq(t.store.turns.length, 0);
});

Deno.test("/setup points the webhook at this function with the derived secret", async () => {
  const t = setup();
  const res = await t.handler(new Request("https://x/functions/v1/celia-telegram/setup"));
  eq(res.status, 200);
  const hook = t.tgCalls.find((c) => c.method === "setWebhook")!;
  eq(hook.body.url, "https://ref.supabase.co/functions/v1/celia-telegram/telegram");
  eq(hook.body.secret_token, await webhookSecret(TOKEN));
  ok(!JSON.stringify(await res.json()).includes(TOKEN));
});

Deno.test("healthz reports booleans only", async () => {
  const t = setup();
  const body = await (await t.handler(new Request("https://x/functions/v1/celia-telegram/healthz"))).json();
  eq(body, { ok: true, service: "celia-telegram", telegram: true, owners: true, providers: 1, store: true });
});

Deno.test("long replies are split under Telegram's limit", () => {
  const parts = splitForTelegram("كلمة ".repeat(2000), 4000);
  ok(parts.length === 3 && parts.every((p) => Array.from(p).length <= 4000));
  eq(routeOf("/functions/v1/celia-telegram/telegram/"), "/telegram");
});
