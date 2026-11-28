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
  ALLOWED_UPDATES,
  parseMemoryCommand,
  type Provider,
  renderOwnerPrompt,
  relaySecret,
  VAULT_SECRETS,
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
  relay = new Map<number, unknown>();
  ownersSaved: string[] = [];
  relayFails = false;
  relayPush(id: number, payload: unknown) {
    if (this.relayFails) return Promise.reject(new Error("db down"));
    if (!this.relay.has(id)) this.relay.set(id, payload);
    return Promise.resolve();
  }
  relayFetch(offset: number | null, limit: number) {
    if (this.relayFails) return Promise.reject(new Error("db down"));
    if (offset !== null) for (const id of [...this.relay.keys()]) if (id < offset) this.relay.delete(id);
    return Promise.resolve([...this.relay.entries()].sort((a, b) => a[0] - b[0]).slice(0, limit).map((e) => e[1]));
  }
  loadOwners() {
    return Promise.resolve([...this.ownersSaved]);
  }
  saveOwners(o: string[]) {
    this.ownersSaved = o;
    return Promise.resolve();
  }
  memoryReplace(rows: MemoryRecord[]) {
    this.rows = rows.map((r) => ({ ...r }));
    return Promise.resolve(this.rows.length);
  }
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
  vault: Record<string, string> = {};
  vaultFails = false;
  vaultReads = 0;
  runtimeSecrets() {
    this.vaultReads++;
    if (this.vaultFails) return Promise.reject(new Error("vault down"));
    return Promise.resolve({ ...this.vault });
  }
}

function setup(envExtra: Record<string, string> = {}, providerReply: string | null = "رد سيليا") {
  const tgCalls: { method: string; body: Record<string, unknown> }[] = [];
  let nextId = 500;
  // A stateful fake Telegram: where the webhook points, and failure switches.
  const tg = { url: "", rejectSet: false, misroute: false, rejectDelete: false };
  const fetchFn = ((url: string | URL | Request, init?: RequestInit) => {
    const method = String(url).split("/").pop()!;
    const body = JSON.parse(String(init?.body ?? "{}"));
    tgCalls.push({ method, body });
    if (method === "setWebhook") {
      if (tg.rejectSet) return Promise.resolve(new Response(JSON.stringify({ ok: false, description: "Bad Request" })));
      tg.url = tg.misroute ? "https://elsewhere.example/hook" : String(body.url);
    }
    if (method === "deleteWebhook" && !tg.rejectDelete) tg.url = "";
    const result = method === "sendMessage"
      ? { message_id: nextId++ }
      : method === "getMe"
      ? { username: "CeliaBot" }
      : method === "getWebhookInfo"
      ? { url: tg.url, pending_update_count: 0 }
      : true;
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
    CELIA_TELEGRAM_TOKEN: TOKEN, CELIA_OWNER_IDS: String(OWNER), CELIA_CHAT: "instant",
    SUPABASE_URL: "https://ref.supabase.co", ...envExtra,
  };
  const store = new FakeStore();
  const logs: string[] = [];
  const handler = makeHandler({
    env: (n) => env[n], store, fetchFn, providers: [provider],
    sleep: () => Promise.resolve(), clock: () => V.now, log: (l) => logs.push(l),
  });
  return { handler, tgCalls, store, systems, logs, tg };
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

Deno.test("strangers and groups get nothing from CELIA — their updates go to the runner", async () => {
  const t = setup();
  await post(t.handler, msg(1, "hi", 999));
  await post(t.handler, msg(2, "hi", OWNER, "group"));
  eq(t.tgCalls.length, 0);
  eq([...t.store.relay.keys()], [1, 2]);
  eq(t.systems.length, 0);
});

Deno.test("before owners are configured a chat only learns its own id", async () => {
  const t = setup({ CELIA_OWNER_IDS: "" });
  await post(t.handler, msg(1, "/start", 4242));
  eq(t.tgCalls.length, 1);
  ok(String(t.tgCalls[0].body.text).includes("4242"));
  eq(t.systems.length, 0);
});

Deno.test("owners pushed by the authenticated runner are honoured (no extra Supabase secret)", async () => {
  const t = setup({ CELIA_OWNER_IDS: "" });
  await t.store.saveOwners([String(OWNER)]);
  await post(t.handler, msg(1, "تذكّري: معلومة"));
  eq(t.store.rows.length, 1);
});

Deno.test("no free model => CELIA's honest fallback, no history written", async () => {
  const t = setup({}, null);
  await post(t.handler, msg(1, "ازيك"));
  eq(t.tgCalls.filter((c) => c.method === "editMessageText").at(-1)!.body.text, SPEC.persona.owner_fallback);
  eq(t.store.turns.length, 0);
});

const SETUP = "https://x/functions/v1/celia-telegram/setup";
const HOOK = "https://ref.supabase.co/functions/v1/celia-telegram/telegram";

Deno.test("/setup points the webhook at this function with the derived secret", async () => {
  const t = setup({ CELIA_WEBHOOK: "on" });
  const res = await t.handler(new Request(SETUP));
  eq(res.status, 200);
  const hook = t.tgCalls.find((c) => c.method === "setWebhook")!;
  eq(hook.body.url, "https://ref.supabase.co/functions/v1/celia-telegram/telegram");
  eq(hook.body.secret_token, await webhookSecret(TOKEN));
  ok(ALLOWED_UPDATES.includes("callback_query"), "approval buttons must reach the bot");
  eq(hook.body.allowed_updates, ALLOWED_UPDATES);
  eq(hook.body.drop_pending_updates, false);
  const body = await res.json();
  ok(!JSON.stringify(body).includes(TOKEN));
  eq([body.ok, body.webhook_actual, body.chat], [true, "celia", "instant"]);
  eq(t.tg.url, HOOK);
});

Deno.test("/setup without CELIA_WEBHOOK changes nothing (strangers, link previews)", async () => {
  const t = setup();
  const body = await (await t.handler(new Request(SETUP))).json();
  eq([body.changed, body.webhook, body.webhook_actual], [false, "unset", "none"]);
  eq(t.tgCalls.map((c) => c.method), ["getWebhookInfo"]);
});

Deno.test("/setup is idempotent: twice = same single webhook", async () => {
  const t = setup({ CELIA_WEBHOOK: "on" });
  eq((await t.handler(new Request(SETUP))).status, 200);
  eq((await t.handler(new Request(SETUP))).status, 200);
  eq(t.tg.url, HOOK);
});

Deno.test("/setup refuses BEFORE touching Telegram when the queue cannot serve", async () => {
  const t = setup({ CELIA_WEBHOOK: "on" });
  t.store.relayFails = true;
  const res = await t.handler(new Request(SETUP));
  eq(res.status, 503);
  eq((await res.json()).checks, { store: true, relay: false });
  ok(!t.tgCalls.some((c) => c.method === "setWebhook"));
  eq(t.tg.url, "");
});

Deno.test("/setup: Telegram rejects setWebhook => nothing set, 502, safe to re-run", async () => {
  const t = setup({ CELIA_WEBHOOK: "on" });
  t.tg.rejectSet = true;
  const res = await t.handler(new Request(SETUP));
  eq(res.status, 502);
  const body = await res.json();
  eq([body.ok, body.webhook_actual, body.rolled_back], [false, "none", true]);
  t.tg.rejectSet = false;
  eq((await t.handler(new Request(SETUP))).status, 200);
  eq(t.tg.url, HOOK);
});

Deno.test("/setup: webhook not verifiably ours => rolled back, never left half-configured", async () => {
  const t = setup({ CELIA_WEBHOOK: "on" });
  t.tg.misroute = true;
  const res = await t.handler(new Request(SETUP));
  eq(res.status, 502);
  eq((await res.json()).rolled_back, true);
  eq(t.tg.url, "");
  ok(!t.tgCalls.some((c) => c.method === "setMyCommands"), "no success side-effects after a failed verify");
});

Deno.test("healthz reports booleans only", async () => {
  const t = setup();
  const body = await (await t.handler(new Request("https://x/functions/v1/celia-telegram/healthz"))).json();
  eq(body, {
    ok: true, service: "celia-telegram", telegram: true, token_source: "env", chat: "instant", webhook: "unset",
    webhook_actual: "none", owners: true, providers: 1, store: true,
  });
});

// ------------------------------------- secrets from GitHub Actions via Vault
const VAULT_TOKEN = "456:FROM-GITHUB";
const healthz = async (t: ReturnType<typeof setup>) =>
  (await t.handler(new Request("https://x/functions/v1/celia-telegram/healthz"))).json();

Deno.test("vault: the token imported from GitHub Actions is enough on its own", async () => {
  const t = setup({ CELIA_TELEGRAM_TOKEN: "" });
  t.store.vault = { CELIA_TELEGRAM_TOKEN: VAULT_TOKEN };
  const body = await healthz(t);
  eq([body.telegram, body.token_source], [true, "vault"]);
  const r = await relayCall(t.handler, { op: "updates", limit: 1 }, await relaySecret(VAULT_TOKEN));
  eq(r.status, 200, "the runner's secret, derived from the same GitHub token, is accepted");
});

Deno.test("vault: wins over Edge env, so a stale hand-copied token stops working", async () => {
  const t = setup(); // env holds TOKEN
  t.store.vault = { CELIA_TELEGRAM_TOKEN: VAULT_TOKEN };
  eq((await relayCall(t.handler, { op: "updates", limit: 1 })).status, 401);
  eq((await relayCall(t.handler, { op: "updates", limit: 1 }, await relaySecret(VAULT_TOKEN))).status, 200);
  eq((await post(t.handler, msg(1, "hi"))).status, 401, "webhook secret follows the effective token");
});

Deno.test("vault: the gates never come from Vault", async () => {
  const t = setup({ CELIA_CHAT: "", CELIA_WEBHOOK: "" });
  t.store.vault = { CELIA_TELEGRAM_TOKEN: TOKEN, CELIA_CHAT: "instant", CELIA_WEBHOOK: "on" };
  const body = await healthz(t);
  eq([body.chat, body.webhook], ["shadow", "unset"]);
  eq(VAULT_SECRETS.includes("CELIA_CHAT" as never) || VAULT_SECRETS.includes("CELIA_WEBHOOK" as never), false);
});

Deno.test("vault: a failed read falls back to Edge env and is retried, cached otherwise", async () => {
  const t = setup();
  t.store.vaultFails = true;
  eq((await healthz(t)).token_source, "env");
  eq((await relayCall(t.handler, { op: "updates", limit: 1 })).status, 200);
  const reads = t.store.vaultReads;
  t.store.vaultFails = false;
  t.store.vault = { CELIA_TELEGRAM_TOKEN: VAULT_TOKEN };
  eq((await healthz(t)).token_source, "vault", "retried on the next request");
  await healthz(t);
  eq(t.store.vaultReads, reads + 1, "then cached (<= one read per 60 s)");
});

Deno.test("long replies are split under Telegram's limit", () => {
  const parts = splitForTelegram("كلمة ".repeat(2000), 4000);
  ok(parts.length === 3 && parts.every((p) => Array.from(p).length <= 4000));
  eq(routeOf("/functions/v1/celia-telegram/telegram/"), "/telegram");
});

// ------------------------------------------------- one integrated bot (relay)
const press = (id: number, data: string, from = OWNER) => ({
  update_id: id,
  callback_query: { id: `q${id}`, data, from: { id: from }, message: { message_id: 9, chat: { id: from } } },
});

async function relayCall(handler: (r: Request) => Promise<Response>, body: unknown, secret?: string) {
  return handler(new Request("https://x/functions/v1/celia-telegram/relay", {
    method: "POST",
    headers: { "X-Celia-Relay-Secret": secret ?? await relaySecret(TOKEN) },
    body: JSON.stringify(body),
  }));
}

Deno.test("shadow mode (default): every update is queued, CELIA stays silent", async () => {
  const t = setup({ CELIA_CHAT: "" });
  eq((await post(t.handler, msg(1, "ازيك"))).status, 200);
  await post(t.handler, press(2, "appr:A1:grant"));
  eq(t.tgCalls.map((c) => c.method), ["answerCallbackQuery"], "only the neutral ack, never a reply");
  eq(t.tgCalls[0].body.callback_query_id, "q2");
  ok(!/موافق|granted/.test(String(t.tgCalls[0].body.text)), "the ack must not claim a decision");
  eq(t.systems.length, 0);
  eq([...t.store.relay.keys()], [1, 2]);
});

Deno.test("instant mode: approval buttons still go to the runner untouched", async () => {
  const t = setup();
  await post(t.handler, press(3, "appr:A1:grant"));
  eq(t.tgCalls.map((c) => c.method), ["answerCallbackQuery"], "ack only — the vote is decided by the runner");
  eq(t.store.relay.get(3), press(3, "appr:A1:grant"));
});

Deno.test("instant mode: owner chat is answered here and NOT queued (no double reply)", async () => {
  const t = setup();
  await post(t.handler, msg(4, "ازيك"));
  eq(t.store.relay.size, 0);
  ok(t.logs.includes("celia stage=replied"));
});

Deno.test("unknown commands are left to the runner", async () => {
  const t = setup();
  await post(t.handler, msg(5, "/status"));
  eq(t.tgCalls.length, 0);
  eq([...t.store.relay.keys()], [5]);
});

Deno.test("a failed queue write returns 503 so Telegram retries (no lost vote)", async () => {
  const t = setup();
  t.store.relayFails = true;
  eq((await post(t.handler, press(6, "appr:A1:deny"))).status, 503);
  eq(t.tgCalls.length, 0, "no ack for a press that is not safely queued");
});

Deno.test("relay secret is byte-identical to the python runner's (tests/test_celia_relay.py)", async () => {
  eq(await relaySecret(TOKEN), "bb383f437d96b3aafee1e51a64091a3132549abadb7dbb1d62a40941540399e1");
  eq(await webhookSecret(TOKEN), "05ba56bb43983b5af25375879b542d44c1aaa29278b8700b2386fb73c10e8686");
});

Deno.test("relay endpoint: wrong secret is rejected; the webhook secret is not accepted", async () => {
  const t = setup();
  eq((await relayCall(t.handler, { op: "updates" }, "nope")).status, 401);
  eq((await relayCall(t.handler, { op: "updates" }, await webhookSecret(TOKEN))).status, 401);
});

Deno.test("relay endpoint behaves like getUpdates (offset confirms) and stores the owner allowlist", async () => {
  const t = setup({ CELIA_CHAT: "" });
  for (const id of [10, 11, 12]) await post(t.handler, msg(id, "x"));
  let r = await (await relayCall(t.handler, { op: "updates", owners: ["777", "bad id", "778"] })).json();
  eq(r.result.map((u: { update_id: number }) => u.update_id), [10, 11, 12], "no offset = peek");
  eq(r.chat, "shadow");
  eq(t.store.ownersSaved, ["777", "778"]);
  r = await (await relayCall(t.handler, { op: "updates", offset: 12 })).json();
  eq(r.result.map((u: { update_id: number }) => u.update_id), [12]);
  eq([...t.store.relay.keys()], [12]);
});

Deno.test("one memory: the runner reads and replaces the same Supabase rows CELIA uses", async () => {
  const t = setup();
  await post(t.handler, msg(20, "تذكّري: أفضّل الردود القصيرة"));
  const loaded = await (await relayCall(t.handler, { op: "memory_load" })).json();
  eq(loaded.rows.map((r: MemoryRecord) => r.text), ["أفضّل الردود القصيرة"]);
  const rows = [...loaded.rows, { id: 2, kind: "project", text: "قرار من الدورة", source: "telegram:1", created_at: V.now, confidence: 1 }];
  eq((await (await relayCall(t.handler, { op: "memory_replace", rows })).json()).count, 2);
  await post(t.handler, msg(21, "ماذا تتذكرين؟"));
  ok(String(t.tgCalls.filter((c) => c.method === "sendMessage").at(-1)!.body.text).includes("قرار من الدورة"));
});

Deno.test("rollback: CELIA_WEBHOOK=off makes /setup delete the webhook (and never set it)", async () => {
  const t = setup({ CELIA_WEBHOOK: "off" });
  t.tg.url = HOOK;
  const res = await t.handler(new Request(SETUP));
  eq(res.status, 200);
  eq(t.tgCalls.map((c) => c.method), ["deleteWebhook", "getWebhookInfo"]);
  eq(t.tgCalls[0].body.drop_pending_updates, false);
  eq((await res.json()).webhook_actual, "none");
  t.tg.rejectDelete = true;
  t.tg.url = HOOK;
  eq((await t.handler(new Request(SETUP))).status, 502, "an unconfirmed rollback is reported, not assumed");
});

Deno.test("the bot token and both derived secrets never appear in logs or response bodies", async () => {
  const t = setup({ CELIA_WEBHOOK: "on", CELIA_CHAT: "" });
  const bodies: string[] = [];
  const keep = async (r: Response) => bodies.push(await r.text());
  await keep(await t.handler(new Request(SETUP)));
  await keep(await t.handler(new Request("https://x/functions/v1/celia-telegram/healthz")));
  await keep(await post(t.handler, press(1, "appr:A1:grant")));
  await keep(await post(t.handler, msg(2, "x"), "forged"));
  await keep(await relayCall(t.handler, { op: "updates", owners: [String(OWNER)] }));
  await keep(await relayCall(t.handler, { op: "updates" }, "forged"));
  t.store.relayFails = true;
  await keep(await relayCall(t.handler, { op: "updates" }));
  const all = [...bodies, ...t.logs].join("\n");
  for (const secret of [TOKEN, await webhookSecret(TOKEN), await relaySecret(TOKEN)]) {
    ok(!all.includes(secret), "secret material leaked");
  }
});
