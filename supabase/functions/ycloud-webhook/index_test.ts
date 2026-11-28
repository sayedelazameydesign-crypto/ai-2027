// Run: AI2027_EDGE_TEST=1 deno test --allow-env --allow-read supabase/functions/ycloud-webhook/
// Parity vectors come from the REFERENCE Python stack
// (scripts/supabase_edge_parity_vectors.py), so this proves the port, not itself.
import {
  computeSha256,
  configuredDailyCap,
  deriveCodeDigest,
  type InboundEvent,
  isWithinFreeServiceWindow,
  makeHandler,
  normalizeRecipientDigits,
  parseInboundEvents,
  PostgrestStore,
  routeOf,
  type Store,
  verifyHubSignature,
  verifyYcloudSignature,
} from "./index.ts";

const V = JSON.parse(await Deno.readTextFile(new URL("./parity_vectors.json", import.meta.url)));
const enc = new TextEncoder();

function eq(actual: unknown, expected: unknown, msg = "") {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) throw new Error(`${msg}\n  actual:   ${a}\n  expected: ${e}`);
}

// ---------------------------------------------------------------- parity
Deno.test("compute_sha256 is byte-identical to Python json.dumps(sort_keys, ensure_ascii=False)", async () => {
  eq(await computeSha256(V.sha256.payload), V.sha256.expected);
});

Deno.test("YCloud v2 signature: ok / expired / tampered / malformed match Python", async () => {
  const y = V.ycloud, s = enc.encode(y.secret), b = enc.encode(y.body);
  eq(await verifyYcloudSignature(b, `t=${y.ts},s=${y.sig}`, s, y.ts + 10), y.ok);
  eq(await verifyYcloudSignature(b, `t=${y.ts},s=${y.sig}`, s, y.ts + 301), y.expired);
  eq(await verifyYcloudSignature(enc.encode(y.body + " "), `t=${y.ts},s=${y.sig}`, s, y.ts), y.tampered);
  eq(await verifyYcloudSignature(b, "t=abc,s=00", s, y.ts), y.malformed);
  eq(await verifyYcloudSignature(b, `t=${y.ts},s=${y.sig}`, null, y.ts), [false, "missing_signing_config"]);
});

Deno.test("YCloud rotation headers (several s=, one t=) match Python case by case", async () => {
  const y = V.ycloud, s = enc.encode(y.secret), b = enc.encode(y.body);
  const cases = Object.entries(y.headers as Record<string, [string, [boolean, string]]>);
  if (cases.length < 11) throw new Error(`expected >= 11 header vectors, got ${cases.length}`);
  for (const [name, [header, expected]] of cases) {
    eq(await verifyYcloudSignature(b, header, s, y.ts), expected, name);
  }
});

Deno.test("Meta X-Hub-Signature-256 matches Python", async () => {
  eq(await verifyHubSignature(enc.encode(V.ycloud.body), `sha256=${V.hub.sig}`, enc.encode(V.ycloud.secret)), V.hub.ok);
});

Deno.test("PBKDF2-HMAC-SHA256 120k verification digest matches Python", async () => {
  const [c, code, salt] = V.pbkdf2.args;
  eq(await deriveCodeDigest(c, code, salt), V.pbkdf2.expected);
});

Deno.test("phone normalisation + 24h window match Python", () => {
  for (const [raw, want] of Object.entries(V.normalize)) eq(normalizeRecipientDigits(raw), want, raw);
  for (const [d, want] of Object.entries(V.window)) {
    eq(isWithinFreeServiceWindow(V.now - Number(d), V.now), want, `delta ${d}`);
  }
});

Deno.test("YCloud + Meta inbound parsing (incl. duplicate, stale, non-text, bad id) matches Python", () => {
  const yc = {
    type: "whatsapp.inbound_message.received",
    whatsappInboundMessage: {
      id: "wamid.HBgMYC01", from: "+201012345678", to: "+15550109999",
      sendTime: "2026-11-28T09:00:00.000Z", type: "text", text: { body: "  عايز خصم  " },
    },
  };
  const naive = structuredClone(yc);
  naive.whatsappInboundMessage.sendTime = "2026-11-28T09:00:00";
  const msg = (id: string, t: string, extra: Record<string, unknown>) => ({ id, from: "201012345678", timestamp: t, ...extra });
  const meta = {
    object: "whatsapp_business_account",
    entry: [{ changes: [{ field: "messages", value: {
      metadata: { phone_number_id: "1234567890" },
      messages: [
        msg("wamid.META0001", "1795856400", { type: "text", text: { body: "hi" } }),
        msg("wamid.META0001", "1795856400", { type: "text", text: { body: "hi" } }),
        msg("wamid.META0002", "1795700000", { type: "text", text: { body: "old" } }),
        msg("wamid.META0003", "1795856400", { type: "image" }),
        msg("bad-id", "1795856400", { type: "text", text: { body: "x" } }),
      ],
    } }] }],
  };
  const flat = (evs: InboundEvent[]) =>
    evs.map((e) => [e.wamid, e.sender_wa_id, e.phone_number_id, e.timestamp_s, e.stage, e.text_body]);
  eq(flat(parseInboundEvents(yc, V.now)), V.events.ycloud, "ycloud");
  eq(flat(parseInboundEvents(naive, V.now)), V.events.ycloud_naive, "ycloud naive ts");
  eq(flat(parseInboundEvents(meta, V.now)), V.events.meta, "meta");
});

// ----------------------------------------------------- in-memory SQL mirror
class MemStore implements Store {
  inbox = new Map<string, { status: string; reply: string; hash: string; sender: string }>();
  quota = new Map<string, number>();
  coupons: string[] = [];
  verifications = new Map<string, string>();
  failNext = false;
  healthy = true;
  async healthz() {
    if (!this.healthy) throw new Error("db down");
    return true;
  }
  async stageInbound(ev: InboundEvent, cap: number, nowS: number) {
    if (this.failNext) throw new Error("db down");
    const day = new Date(nowS * 1000).toISOString().slice(0, 10);
    if (ev.stage === "duplicate_event" || this.inbox.has(ev.wamid)) return "duplicate_event";
    const put = (status: string) => this.inbox.set(ev.wamid, { status, reply: "", hash: "", sender: ev.sender_wa_id });
    if (ev.stage !== "accepted_text") { put(`blocked:${ev.stage}`); return ev.stage; }
    const used = this.quota.get(day) ?? 0;
    if (used >= Math.max(cap, 0)) {
      const r = `daily_free_quota_exceeded:${used}>=${cap}`;
      put(`blocked:${r}`);
      return r;
    }
    put("reserved");
    this.quota.set(day, used + 1);
    return "reserved";
  }
  async finalizeReply(w: string, reply: string, hash: string) {
    const row = this.inbox.get(w);
    if (!row || row.status !== "reserved") return false;
    Object.assign(row, { status: "pending_approval", reply, hash });
    return true;
  }
  async issueCoupon(code: string) { this.coupons.push(code); }
  async putVerification(c: string, _s: string, d: string) { this.verifications.set(c, d); }
  async publicStatus() { return { service: "free-stack-webhook", chain: { ok: true } }; }
  vault: Record<string, string> = {};
  jtis = new Set<string>();
  vaultDown = false;
  async runtimeSecrets() {
    if (this.vaultDown) throw new Error("vault down");
    return { ...this.vault };
  }
  async importSecrets(jti: string, _exp: number, items: Record<string, string>) {
    if (this.jtis.has(jti)) return null;
    this.jtis.add(jti);
    const out = [];
    for (const [name, v] of Object.entries(items)) {
      this.vault[name] = v;
      out.push({ name, sha256: toHex(await crypto.subtle.digest("SHA-256", enc.encode(v))) });
    }
    return out;
  }
}
const toHex = (b: ArrayBuffer) => Array.from(new Uint8Array(b)).map((x) => x.toString(16).padStart(2, "0")).join("");

const SECRET = "whsec_test_only";
const env = (vals: Record<string, string>) => (k: string) => vals[k];
const NOW = 1795860000;
const BASE = "https://example.supabase.co/functions/v1/ycloud-webhook";

async function signedPost(handler: (r: Request) => Promise<Response>, body: unknown, opts: { ts?: number; secret?: string } = {}) {
  const raw = JSON.stringify(body);
  const ts = opts.ts ?? NOW;
  const key = await crypto.subtle.importKey("raw", enc.encode(opts.secret ?? SECRET), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = Array.from(new Uint8Array(await crypto.subtle.sign("HMAC", key, enc.encode(`${ts}.${raw}`))))
    .map((b) => b.toString(16).padStart(2, "0")).join("");
  return handler(new Request(`${BASE}/webhook`, { method: "POST", body: raw, headers: { "YCloud-Signature": `t=${ts},s=${sig}` } }));
}

const ycMsg = (id: string, text: string, sendTime = "2026-11-28T09:59:00Z") => ({
  type: "whatsapp.inbound_message.received",
  whatsappInboundMessage: { id, from: "+201012345678", to: "+15550109999", sendTime, type: "text", text: { body: text } },
});

Deno.test("routing strips the function slug", () => {
  eq(routeOf("/ycloud-webhook/healthz"), "/healthz");
  eq(routeOf("/functions/v1/ycloud-webhook/webhook/"), "/webhook");
  eq(routeOf("/ycloud-webhook"), "/");
});

Deno.test("GET /healthz: 200 ok:true when DB answers, 503 when it does not; no-store", async () => {
  const s = new MemStore();
  const h = makeHandler(s, env({}), () => NOW);
  let r = await h(new Request(`${BASE}/healthz`));
  eq([r.status, await r.json(), r.headers.get("cache-control")], [200, { ok: true, service: "free-stack-webhook" }, "no-store"]);
  s.healthy = false;
  r = await h(new Request(`${BASE}/healthz`));
  eq([r.status, (await r.json()).ok], [503, false]);
});

Deno.test("unsigned / wrong-secret / stale / no-secret POSTs are refused 401 and store nothing", async () => {
  const s = new MemStore();
  const h = makeHandler(s, env({ YCLOUD_WEBHOOK_SECRET: SECRET }), () => NOW);
  eq((await h(new Request(`${BASE}/webhook`, { method: "POST", body: "{}" }))).status, 401, "unsigned");
  eq((await signedPost(h, ycMsg("wamid.T0001", "hi"), { secret: "wrong" })).status, 401, "wrong secret");
  eq((await signedPost(h, ycMsg("wamid.T0002", "hi"), { ts: NOW - 301 })).status, 401, "replayed/stale");
  const noSecret = makeHandler(s, env({}), () => NOW);
  eq((await signedPost(noSecret, ycMsg("wamid.T0003", "hi"))).status, 401, "no secret configured");
  eq(s.inbox.size, 0);
});

Deno.test("signed YCloud message is staged pending_approval with Python-identical payload hash", async () => {
  const s = new MemStore();
  const h = makeHandler(s, env({ YCLOUD_WEBHOOK_SECRET: SECRET }), () => NOW);
  const r = await signedPost(h, ycMsg("wamid.T0010", "مرحبا"));
  eq([r.status, await r.json()], [200, { ok: true, ingested: 1, stages: ["pending_approval"] }]);
  const row = s.inbox.get("wamid.T0010")!;
  eq(row.status, "pending_approval");
  eq(row.reply, "أهلاً بك في Celia! تم تسجيل طلبك وسنخدمك فوراً ضمن نافذة الخدمة المجانية.");
  eq(row.hash, await computeSha256({
    recipient_phone: "201012345678", message_text: row.reply,
    inbound_timestamp_s: Date.parse("2026-11-28T09:59:00Z") / 1000, reply_to_wamid: "wamid.T0010",
  }));
  // replay of the same (validly signed) event -> duplicate, no second quota unit
  const again = await signedPost(h, ycMsg("wamid.T0010", "مرحبا"));
  eq((await again.json()).stages, ["duplicate_event"]);
});

Deno.test("coupon + verification replies; OTP never stored in clear", async () => {
  const s = new MemStore();
  const h = makeHandler(s, env({ YCLOUD_WEBHOOK_SECRET: SECRET }), () => NOW);
  await signedPost(h, ycMsg("wamid.T0020", "عايز خصم"));
  if (!/^CELIA-[0-9A-F]{6}$/.test(s.coupons[0])) throw new Error("coupon format");
  if (!s.inbox.get("wamid.T0020")!.reply.includes(s.coupons[0])) throw new Error("coupon not in reply");
  await signedPost(h, ycMsg("wamid.T0021", "رمز التأكيد"));
  const reply = s.inbox.get("wamid.T0021")!.reply;
  const code = reply.match(/(\d{6})/)![1];
  const stored = s.verifications.get("201012345678")!;
  if (stored.includes(code) || !/^[0-9a-f]{64}$/.test(stored)) throw new Error("OTP stored in clear");
});

Deno.test("daily cap=10 is enforced (11th blocked) and a missing cap never widens it", async () => {
  eq(configuredDailyCap(env({})), 10);
  eq(configuredDailyCap(env({ AI2027_MAX_FREE_REPLIES_PER_DAY: "abc" })), 0);
  const s = new MemStore();
  const h = makeHandler(s, env({ YCLOUD_WEBHOOK_SECRET: SECRET, AI2027_MAX_FREE_REPLIES_PER_DAY: "10" }), () => NOW);
  const stages: string[] = [];
  for (let i = 0; i < 11; i++) stages.push(...(await (await signedPost(h, ycMsg(`wamid.Q${String(i).padStart(4, "0")}`, "hi"))).json()).stages);
  eq(stages.filter((x) => x === "pending_approval").length, 10);
  eq(stages[10], "daily_free_quota_exceeded:10>=10");
});

Deno.test("message older than 24h is recorded blocked, not staged", async () => {
  const s = new MemStore();
  const h = makeHandler(s, env({ YCLOUD_WEBHOOK_SECRET: SECRET }), () => NOW);
  const r = await signedPost(h, ycMsg("wamid.T0030", "hi", "2026-11-27T09:00:00Z"));
  eq((await r.json()).stages, ["outside_24h_service_window"]);
  eq(s.inbox.get("wamid.T0030")!.status, "blocked:outside_24h_service_window");
});

Deno.test("store failure after a valid signature -> 503 (YCloud retries), never 200", async () => {
  const s = new MemStore();
  s.failNext = true;
  const h = makeHandler(s, env({ YCLOUD_WEBHOOK_SECRET: SECRET }), () => NOW);
  eq((await signedPost(h, ycMsg("wamid.T0040", "hi"))).status, 503);
});

Deno.test("CORS only on public reads (/healthz, /status), never on /webhook", async () => {
  const h = makeHandler(new MemStore(), env({ YCLOUD_WEBHOOK_SECRET: SECRET }), () => NOW);
  eq((await h(new Request(`${BASE}/healthz`))).headers.get("access-control-allow-origin"), "*");
  eq((await h(new Request(`${BASE}/status`))).headers.get("access-control-allow-origin"), "*");
  eq((await signedPost(h, ycMsg("wamid.C0001", "hi"))).headers.get("access-control-allow-origin"), null);
  eq((await h(new Request(`${BASE}/webhook`, { method: "POST", body: "{}" }))).headers.get("access-control-allow-origin"), null);
});

Deno.test("size limits, verify challenge, 404/405", async () => {
  const h = makeHandler(new MemStore(), env({ WHATSAPP_CLOUD_VERIFY_TOKEN: "vt" }), () => NOW);
  eq((await h(new Request(`${BASE}/webhook`, { method: "POST", body: "" }))).status, 400);
  eq((await h(new Request(`${BASE}/webhook`, { method: "POST", body: "x".repeat(1024 * 1024 + 1) }))).status, 413);
  const ok = await h(new Request(`${BASE}/webhook?hub.mode=subscribe&hub.verify_token=vt&hub.challenge=abc123`));
  eq([ok.status, await ok.text()], [200, "abc123"]);
  eq((await h(new Request(`${BASE}/webhook?hub.mode=subscribe&hub.verify_token=no&hub.challenge=abc`))).status, 403);
  eq((await h(new Request(`${BASE}/nope`))).status, 404);
  eq((await h(new Request(`${BASE}/webhook`, { method: "PUT", body: "x" }))).status, 405);
});

Deno.test("PostgrestStore: RPC URL/headers; new sb_secret keys are not sent as Bearer; errors never echo body", async () => {
  const calls: { url: string; headers: Record<string, string> }[] = [];
  const fake = ((url: string, init: RequestInit) => {
    calls.push({ url, headers: init.headers as Record<string, string> });
    return Promise.resolve(new Response("leaky-body", { status: 500 }));
  }) as unknown as typeof fetch;
  const st = new PostgrestStore("https://x.supabase.co/", "sb_secret_abc", fake);
  let msg = "";
  try { await st.healthz(); } catch (e) { msg = (e as Error).message; }
  eq(calls[0].url, "https://x.supabase.co/rest/v1/rpc/ai2027_healthz");
  eq([calls[0].headers.apikey, calls[0].headers.Authorization], ["sb_secret_abc", undefined]);
  if (msg.includes("leaky")) throw new Error("error echoed response body");
  const st2 = new PostgrestStore("https://x.supabase.co", "eyJhbGciOi.x.y", fake);
  try { await st2.healthz(); } catch { /* expected */ }
  eq(calls[1].headers.Authorization, "Bearer eyJhbGciOi.x.y");
});


// ------------------------------------------------ GitHub OIDC -> Vault import
const b64u = (u: Uint8Array | string) =>
  btoa(typeof u === "string" ? u : String.fromCharCode(...u)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const kp = await crypto.subtle.generateKey(
  { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
  true, ["sign", "verify"],
);
const evil = await crypto.subtle.generateKey(
  { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
  true, ["sign", "verify"],
);
const pub = await crypto.subtle.exportKey("jwk", kp.publicKey);
const JWKS = async () => ({ keys: [{ kty: "RSA", kid: "gh1", n: pub.n!, e: pub.e! }] });
const GOOD_CLAIMS = {
  iss: "https://token.actions.githubusercontent.com", aud: "ai2027-supabase-import",
  repository: "sayedelazameydesign-crypto/ai-2027", repository_id: "1460619599", repository_owner_id: "233639223",
  ref: "refs/heads/main", event_name: "workflow_dispatch",
  workflow_ref: "sayedelazameydesign-crypto/ai-2027/.github/workflows/supabase-secret-sync.yml@refs/heads/main",
  iat: NOW - 5, nbf: NOW - 5, exp: NOW + 300, jti: "jti-0001-abcdef",
};
async function jwt(over: Record<string, unknown> = {}, o: { key?: CryptoKey; kid?: string; alg?: string } = {}) {
  const h = b64u(JSON.stringify({ alg: o.alg ?? "RS256", kid: o.kid ?? "gh1", typ: "JWT" }));
  const p = b64u(JSON.stringify({ ...GOOD_CLAIMS, ...over }));
  const sig = new Uint8Array(await crypto.subtle.sign("RSASSA-PKCS1-v1_5", o.key ?? kp.privateKey, enc.encode(`${h}.${p}`)));
  return `${h}.${p}.${b64u(sig)}`;
}
const importReq = (token: string, secrets: Record<string, unknown>) =>
  new Request(`${BASE}/import-secrets`, {
    method: "POST", headers: { Authorization: `Bearer ${token}` }, body: JSON.stringify({ secrets }),
  });
const sha = async (v: string) => toHex(await crypto.subtle.digest("SHA-256", enc.encode(v)));

Deno.test("import: GitHub OIDC -> Vault, digests returned, Vault secret wins over env at once", async () => {
  const store = new MemStore();
  const h = makeHandler(store, env({ YCLOUD_WEBHOOK_SECRET: "whsec_old_env_value" }), () => NOW, JWKS);
  // warm the 60 s cache with the env-only view first
  eq((await signedPost(h, { object: "probe" }, { secret: "whsec_old_env_value" })).status, 200);
  const r = await h(importReq(await jwt(), { YCLOUD_WEBHOOK_SECRET: "  whsec_from_github_123  " }));
  eq(r.status, 200);
  const body = await r.json();
  eq(body.stored, [{ name: "YCLOUD_WEBHOOK_SECRET", sha256: await sha("whsec_from_github_123") }]);
  eq(store.vault.YCLOUD_WEBHOOK_SECRET, "whsec_from_github_123", "trimmed before storage");
  eq((await signedPost(h, { object: "probe" }, { secret: "whsec_from_github_123" })).status, 200);
  eq((await signedPost(h, { object: "probe" }, { secret: "whsec_old_env_value" })).status, 401, "old env secret is dead");
});

Deno.test("import: replayed token refused (jti burned)", async () => {
  const store = new MemStore();
  const h = makeHandler(store, env({}), () => NOW, JWKS);
  const tok = await jwt();
  eq((await h(importReq(tok, { YCLOUD_WEBHOOK_SECRET: "whsec_first_value" }))).status, 200);
  const r = await h(importReq(tok, { YCLOUD_WEBHOOK_SECRET: "whsec_attacker_value" }));
  eq([r.status, (await r.json()).reason], [401, "token_replayed"]);
  eq(store.vault.YCLOUD_WEBHOOK_SECRET, "whsec_first_value");
});

Deno.test("import: every forged / foreign / stale token is refused and nothing is stored", async () => {
  const cases: [string, Promise<string>][] = [
    ["claim_mismatch:workflow_ref", jwt({ workflow_ref: GOOD_CLAIMS.workflow_ref.replace("main", "evil") })],
    ["claim_mismatch:workflow_ref", jwt({ workflow_ref: "sayedelazameydesign-crypto/ai-2027/.github/workflows/other.yml@refs/heads/main" })],
    ["claim_mismatch:ref", jwt({ ref: "refs/heads/arena/x" })],
    ["claim_mismatch:event_name", jwt({ event_name: "pull_request" })],
    ["claim_mismatch:repository", jwt({ repository: "attacker/ai-2027" })],
    ["claim_mismatch:repository_id", jwt({ repository_id: "1" })],
    ["claim_mismatch:repository_owner_id", jwt({ repository_owner_id: "2" })],
    ["claim_mismatch:iss", jwt({ iss: "https://evil.example" })],
    ["claim_mismatch:aud", jwt({ aud: "sts.amazonaws.com" })],
    ["claim_mismatch:aud", jwt({ aud: ["ai2027-supabase-import", "other"] })],
    ["expired", jwt({ exp: NOW - 120 })],
    ["not_yet_valid", jwt({ nbf: NOW + 600, iat: NOW + 600 })],
    ["missing_jti", jwt({ jti: "" })],
    ["bad_signature", jwt({}, { key: evil.privateKey })],
    ["unknown_kid", jwt({}, { kid: "nope" })],
    ["bad_alg", jwt({}, { alg: "none" })],
  ];
  for (const [reason, tokP] of cases) {
    const store = new MemStore();
    const h = makeHandler(store, env({}), () => NOW, JWKS);
    const r = await h(importReq(await tokP, { YCLOUD_WEBHOOK_SECRET: "whsec_should_not_land" }));
    eq([r.status, (await r.json()).reason], [401, reason], reason);
    eq(Object.keys(store.vault).length, 0, `nothing stored for ${reason}`);
  }
});

Deno.test("import: names allowlisted, values bounded, auth required, method enforced, no CORS", async () => {
  const store = new MemStore();
  const h = makeHandler(store, env({}), () => NOW, JWKS);
  let r = await h(importReq(await jwt({ jti: "jti-name-01" }), { GEMINI_API_KEY: "aaaaaaaaaaaa" }));
  eq([r.status, (await r.json()).reason], [400, "name_not_allowed:GEMINI_API_KEY"]);
  r = await h(importReq(await jwt({ jti: "jti-short-01" }), { YCLOUD_WEBHOOK_SECRET: "short" }));
  eq(r.status, 400);
  r = await h(importReq(await jwt({ jti: "jti-empty-01" }), {}));
  eq(r.status, 400);
  r = await h(new Request(`${BASE}/import-secrets`, { method: "POST", body: "{}" }));
  eq([r.status, r.headers.get("access-control-allow-origin")], [401, null]);
  eq((await h(new Request(`${BASE}/import-secrets`))).status, 405);
  eq(Object.keys(store.vault).length, 0);
});

Deno.test("import: JWKS unreachable or Vault down -> 503 (retryable), never a silent fallback", async () => {
  const down = makeHandler(new MemStore(), env({}), () => NOW, () => Promise.reject(new Error("offline")));
  eq((await down(importReq(await jwt(), { YCLOUD_WEBHOOK_SECRET: "whsec_value_123" }))).status, 503);
  const store = new MemStore();
  store.vaultDown = true;
  const h = makeHandler(store, env({ YCLOUD_WEBHOOK_SECRET: SECRET }), () => NOW, JWKS);
  eq((await signedPost(h, ycMsg("wamid.V0001", "hi"))).status, 503);
  eq((await h(new Request(`${BASE}/healthz`))).status, 200, "liveness unaffected");
});

Deno.test("Meta verify token can come from Vault", async () => {
  const store = new MemStore();
  store.vault.WHATSAPP_CLOUD_VERIFY_TOKEN = "vt_from_vault_123";
  const h = makeHandler(store, env({}), () => NOW, JWKS);
  const r = await h(new Request(`${BASE}/webhook?hub.mode=subscribe&hub.verify_token=vt_from_vault_123&hub.challenge=abc123`));
  eq([r.status, await r.text()], [200, "abc123"]);
});
