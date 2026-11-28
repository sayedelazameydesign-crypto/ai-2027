// AI-2027 free-stack webhook — Supabase Edge Function (Deno), $0, no card.
//
// Faithful port of scripts/free_stack_runner.py (make_webhook_handler +
// ZeroCostOrchestrator.ingest_signed_webhook) and of the verification/parsing
// helpers in src/connectors/whatsapp_cloud.py. Postgres replaces SQLite; the
// atomic quota gate lives in public.ai2027_stage_inbound (advisory lock).
//
// Routes (base = https://<ref>.supabase.co/functions/v1/ycloud-webhook):
//   GET  /healthz  -> {"ok":bool,"service":"free-stack-webhook"} (200|503), proves
//                     Edge -> PostgREST -> Postgres; never a secret/phone/count.
//   GET  /webhook  -> Meta subscription challenge (constant-time token compare).
//   POST /webhook  -> YCloud v2 (YCloud-Signature: t=..,s=..) or Meta
//                     (X-Hub-Signature-256) HMAC verified, fail-closed 401.
//                     Replies are only STAGED as pending_approval: nothing is
//                     ever sent from here (MAX_SPEND=0).
//   GET  /status   -> PII-free watchdog status + hash-chain verification.
//   POST /import-secrets -> GitHub Actions OIDC-authenticated one-shot import of
//                     allowlisted secrets (4 webhook + 5 CELIA) into Supabase Vault (see
//                     .github/workflows/supabase-secret-sync.yml). Returns digests.
//
// Deploy with verify_jwt=false: YCloud cannot send a Supabase JWT; the HMAC
// signature is the authentication. Zero third-party imports (Web Crypto only).

export const SERVICE_WINDOW_SECONDS = 86_400;
export const CLOCK_SKEW_TOLERANCE_SECONDS = 300;
export const MAX_WEBHOOK_BYTES = 1024 * 1024;
export const MESSAGE_MAX_CHARS = 4096;
export const PBKDF2_ITERATIONS = 120_000;
// Python defaults to 50; the operator constraint for this deployment is 10/day,
// so a missing secret must never widen the cap.
export const DEFAULT_DAILY_CAP = "10";

const PHONE_ID_RE = /^[1-9]\d{5,24}$/;
const E164_DIGITS_RE = /^[1-9]\d{7,14}$/;
const WAMID_RE = /^(?:wamid\.[A-Za-z0-9_+/=-]{4,240}|yim_[A-Za-z0-9_-]{4,120})$/;
const CHALLENGE_RE = /^[A-Za-z0-9._-]{1,256}$/;
const HEX64_RE = /^[0-9a-f]{64}$/;
const ASCII_DIGITS_RE = /^[0-9]{1,12}$/;
const MAX_YCLOUD_SIGNATURES = 8;
const enc = new TextEncoder();

type Env = (name: string) => string | undefined;
const realEnv: Env = (name) => Deno.env.get(name);

// ------------------------------------------------------------------ helpers
export function constantTimeEqual(a: string, b: string): boolean {
  const ab = enc.encode(a);
  const bb = enc.encode(b);
  let diff = ab.length ^ bb.length;
  const n = Math.max(ab.length, bb.length);
  for (let i = 0; i < n; i++) diff |= (ab[i] ?? 0) ^ (bb[i] ?? 0);
  return diff === 0;
}

const toHex = (buf: ArrayBuffer | Uint8Array) =>
  Array.from(buf instanceof Uint8Array ? buf : new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");

type Bytes = Uint8Array<ArrayBuffer>;
const bytes = (u: Uint8Array): Bytes => new Uint8Array(u); // copy onto a plain ArrayBuffer

export async function hmacSha256Hex(key: Uint8Array, data: Uint8Array): Promise<string> {
  const k = await crypto.subtle.importKey("raw", bytes(key), { name: "HMAC", hash: "SHA-256" }, false, [
    "sign",
  ]);
  return toHex(await crypto.subtle.sign("HMAC", k, bytes(data)));
}

/** Byte-identical to Python json.dumps(data, sort_keys=True, ensure_ascii=False). */
export function pyJsonDumps(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("non-finite number");
    return Number.isInteger(value) ? String(value) : JSON.stringify(value);
  }
  if (typeof value === "string") return JSON.stringify(value);
  if (Array.isArray(value)) return "[" + value.map(pyJsonDumps).join(", ") + "]";
  const obj = value as Record<string, unknown>;
  const keys = Object.keys(obj).sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
  return "{" + keys.map((k) => JSON.stringify(k) + ": " + pyJsonDumps(obj[k])).join(", ") + "}";
}

export async function computeSha256(data: unknown): Promise<string> {
  return toHex(await crypto.subtle.digest("SHA-256", enc.encode(pyJsonDumps(data))));
}

const pyStrip = (s: string) => s.trim();
const sliceChars = (s: string, n: number) => Array.from(s).slice(0, n).join("");

export function normalizeRecipientDigits(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  let digits = raw.trim().replace(/[\s()-]/g, "");
  if (digits.startsWith("+")) digits = digits.slice(1);
  if (digits.startsWith("00")) digits = digits.slice(2);
  if (/^01[0125]\d{8}$/.test(digits)) digits = "2" + digits;
  return E164_DIGITS_RE.test(digits) ? digits : null;
}

export function isWithinFreeServiceWindow(inboundTs: number, nowS: number): boolean {
  const elapsed = nowS - Math.trunc(inboundTs);
  if (elapsed < -CLOCK_SKEW_TOLERANCE_SECONDS) return false;
  return elapsed <= SERVICE_WINDOW_SECONDS;
}

export function configuredDailyCap(env: Env): number {
  const raw = (env("AI2027_MAX_FREE_REPLIES_PER_DAY") || DEFAULT_DAILY_CAP).trim();
  if (!/^[+-]?\d+$/.test(raw)) return 0; // fail-closed on malformed config
  return Math.max(0, parseInt(raw, 10));
}

// --------------------------------------------------------- signature checks
export function resolveYcloudSigningBytes(env: Env): Uint8Array | null {
  const dedicated = (env("YCLOUD_WEBHOOK_SECRET") || "").trim();
  if (dedicated) return enc.encode(dedicated);
  const fallback = (env("AI2027_WHATSAPP_WEBHOOK") || "").trim();
  if (fallback && !/^https?:\/\//i.test(fallback)) return enc.encode(fallback);
  return null;
}

export async function verifyYcloudSignature(
  raw: Uint8Array,
  header: string | null,
  secret: Uint8Array | null,
  nowS: number,
  toleranceS = CLOCK_SKEW_TOLERANCE_SECONDS,
): Promise<[boolean, string]> {
  if (!secret || secret.length === 0) return [false, "missing_signing_config"];
  if (!header) return [false, "missing_signature"];
  // YCloud: exactly one `t`; one or more `s` (several during a secret
  // rotation) — accept when ANY `s` matches. A duplicate `t` is refused.
  const tsValues: string[] = [];
  const sigValues: string[] = [];
  for (const item of header.trim().split(",")) {
    const i = item.indexOf("=");
    if (i < 0) continue;
    const key = item.slice(0, i).trim().toLowerCase();
    if (key === "t") tsValues.push(item.slice(i + 1).trim());
    else if (key === "s") sigValues.push(item.slice(i + 1).trim().toLowerCase());
  }
  if (tsValues.length !== 1 || !ASCII_DIGITS_RE.test(tsValues[0])) return [false, "malformed_signature"];
  const candidates = sigValues.filter((sig) => HEX64_RE.test(sig));
  if (candidates.length === 0 || sigValues.length > MAX_YCLOUD_SIGNATURES) {
    return [false, "malformed_signature"];
  }
  const ts = parseInt(tsValues[0], 10);
  if (Math.abs(nowS - ts) > Math.max(0, toleranceS)) return [false, "signature_timestamp_expired"];
  const prefix = enc.encode(`${ts}.`);
  const signed = new Uint8Array(prefix.length + raw.length);
  signed.set(prefix);
  signed.set(raw, prefix.length);
  const expected = await hmacSha256Hex(secret, signed);
  let matched = false;
  for (const sig of candidates) matched = constantTimeEqual(sig, expected) || matched; // no early exit
  if (!matched) return [false, "invalid_signature"];
  return [true, "ok"];
}

export async function verifyHubSignature(
  raw: Uint8Array,
  header: string | null,
  secret: Uint8Array | null,
): Promise<[boolean, string]> {
  if (!secret || secret.length === 0) return [false, "missing_signing_config"];
  if (!header) return [false, "missing_signature"];
  const h = header.trim();
  if (!h.startsWith("sha256=")) return [false, "malformed_signature"];
  const provided = h.slice("sha256=".length).toLowerCase();
  if (!HEX64_RE.test(provided)) return [false, "malformed_signature"];
  const expected = await hmacSha256Hex(secret, raw);
  if (!constantTimeEqual(provided, expected)) return [false, "invalid_signature"];
  return [true, "ok"];
}

export function verifyWebhookChallenge(
  mode: string | null,
  provided: string | null,
  expected: string,
  challenge: string | null,
): [boolean, string, string] {
  if (!expected || !expected.trim()) return [false, "", "missing_verify_config"];
  if (mode !== "subscribe") return [false, "", "invalid_mode"];
  if (!provided) return [false, "", "challenge_mismatch"];
  if (!constantTimeEqual(provided, expected)) return [false, "", "challenge_mismatch"];
  if (!challenge || !CHALLENGE_RE.test(challenge)) return [false, "", "invalid_challenge"];
  return [true, challenge, "ok"];
}

// ------------------------------------------------------------ event parsing
export interface InboundEvent {
  wamid: string;
  sender_wa_id: string;
  phone_number_id: string;
  timestamp_s: number;
  message_type: string;
  text_body: string;
  within_24h_window: boolean;
  stage: string; // accepted_text | duplicate_event | non_text_ignored | outside_24h_service_window
}

export function parseIsoOrEpochS(msg: Record<string, unknown>): number | null {
  const rawTs = String(msg["timestamp"] ?? "").trim();
  if (/^\d+$/.test(rawTs)) return parseInt(rawTs, 10);
  let sendTime = String(msg["sendTime"] ?? "").trim();
  if (!sendTime) return null;
  if (!/(Z|[+-]\d{2}:?\d{2})$/i.test(sendTime)) sendTime += "Z"; // naive => UTC (Python parity)
  const ms = Date.parse(sendTime);
  return Number.isFinite(ms) ? Math.trunc(ms / 1000) : null;
}

function buildEvent(
  wamid: string,
  sender: string,
  phoneNumberId: string,
  ts: number,
  msg: Record<string, unknown>,
  nowS: number,
  seen: Set<string>,
): InboundEvent {
  const msgType = String(msg["type"] || "unknown").trim().toLowerCase();
  const key = `${phoneNumberId}\u0000${wamid}`;
  const inWindow = isWithinFreeServiceWindow(ts, nowS);
  let text = "";
  if (msgType === "text") {
    const t = msg["text"];
    if (t && typeof t === "object" && !Array.isArray(t)) {
      text = sliceChars(pyStrip(String((t as Record<string, unknown>)["body"] || "")), MESSAGE_MAX_CHARS);
    }
  }
  let stage: string;
  if (seen.has(key)) stage = "duplicate_event";
  else {
    seen.add(key);
    if (msgType !== "text" || !text) stage = "non_text_ignored";
    else if (!inWindow) stage = "outside_24h_service_window";
    else stage = "accepted_text";
  }
  return {
    wamid,
    sender_wa_id: sender,
    phone_number_id: phoneNumberId,
    timestamp_s: ts,
    message_type: msgType,
    text_body: text,
    within_24h_window: inWindow,
    stage,
  };
}

const isObj = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);

export function parseInboundEvents(payload: unknown, nowS: number, seen = new Set<string>()): InboundEvent[] {
  if (!isObj(payload)) return [];
  if (payload["type"] === "whatsapp.inbound_message.received") {
    const y = payload["whatsappInboundMessage"];
    if (!isObj(y)) return [];
    // YCloud: `id` is YCloud's own 24-hex record id; the WhatsApp message id
    // lives in `wamid`. Taking `id` first silently dropped every real event.
    const wamid = String(y["wamid"] || y["id"] || "").trim();
    const sender = normalizeRecipientDigits(String(y["from"] ?? ""));
    const to = normalizeRecipientDigits(String(y["to"] ?? ""));
    const ts = parseIsoOrEpochS(y);
    if (!WAMID_RE.test(wamid) || sender === null || to === null || ts === null) return [];
    return [buildEvent(wamid, sender, to, ts, y, nowS, seen)];
  }
  if (payload["object"] !== "whatsapp_business_account") return [];
  const entries = payload["entry"];
  if (!Array.isArray(entries)) return [];
  const out: InboundEvent[] = [];
  for (const entry of entries) {
    if (!isObj(entry) || !Array.isArray(entry["changes"])) continue;
    for (const change of entry["changes"] as unknown[]) {
      if (!isObj(change) || change["field"] !== "messages" || !isObj(change["value"])) continue;
      const value = change["value"] as Record<string, unknown>;
      const meta = isObj(value["metadata"]) ? value["metadata"] : {};
      const phoneId = String((meta as Record<string, unknown>)["phone_number_id"] ?? "").trim();
      if (!PHONE_ID_RE.test(phoneId) || !Array.isArray(value["messages"])) continue;
      for (const msg of value["messages"] as unknown[]) {
        if (!isObj(msg)) continue;
        const wamid = String(msg["id"] || "").trim();
        const sender = normalizeRecipientDigits(String(msg["from"] ?? ""));
        const ts = parseIsoOrEpochS(msg);
        if (!WAMID_RE.test(wamid) || sender === null || ts === null) continue;
        out.push(buildEvent(wamid, sender, phoneId, ts, msg, nowS, seen));
      }
    }
  }
  return out;
}

// -------------------------------------------------------------- the store
export interface Store {
  healthz(): Promise<boolean>;
  stageInbound(ev: InboundEvent, cap: number, nowS: number): Promise<string>;
  finalizeReply(wamid: string, replyText: string, payloadHash: string): Promise<boolean>;
  issueCoupon(code: string, customerId: string, pct: number, expiresAt: number): Promise<void>;
  putVerification(customerId: string, saltHex: string, derivedHex: string, expiresAt: number): Promise<void>;
  publicStatus(): Promise<unknown>;
  /** Allowlisted secrets imported from GitHub Actions into Supabase Vault ({} if none). */
  runtimeSecrets(): Promise<Record<string, string>>;
  /** One-shot import (jti burned atomically). null = replayed token. */
  importSecrets(jti: string, exp: number, items: Record<string, string>): Promise<ImportedDigest[] | null>;
}
export interface ImportedDigest {
  name: string;
  sha256: string;
}

/** PostgREST RPC store using the server-side service_role key (never sent to clients). */
export class PostgrestStore implements Store {
  constructor(private url: string, private key: string, private fetchFn: typeof fetch = fetch) {}

  private async rpc(fn: string, args: Record<string, unknown>): Promise<unknown> {
    const headers: Record<string, string> = { apikey: this.key, "Content-Type": "application/json" };
    if (this.key.startsWith("eyJ")) headers["Authorization"] = `Bearer ${this.key}`;
    const res = await this.fetchFn(`${this.url.replace(/\/+$/, "")}/rest/v1/rpc/${fn}`, {
      method: "POST",
      headers,
      body: JSON.stringify(args),
    });
    const text = await res.text();
    if (!res.ok) throw new Error(`rpc ${fn} failed: HTTP ${res.status}`); // body not echoed
    return text ? JSON.parse(text) : null;
  }

  async healthz() {
    return (await this.rpc("ai2027_healthz", {})) === true;
  }
  async stageInbound(ev: InboundEvent, cap: number, nowS: number) {
    return String(
      await this.rpc("ai2027_stage_inbound", {
        p_wamid: ev.wamid,
        p_sender: ev.sender_wa_id,
        p_phone_number_id: ev.phone_number_id,
        p_body: ev.text_body,
        p_msg_ts: ev.timestamp_s,
        p_stage: ev.stage,
        p_cap: cap,
        p_now: nowS,
      }),
    );
  }
  async finalizeReply(wamid: string, replyText: string, payloadHash: string) {
    return (await this.rpc("ai2027_finalize_reply", {
      p_wamid: wamid,
      p_reply_text: replyText,
      p_payload_hash: payloadHash,
    })) === true;
  }
  async issueCoupon(code: string, customerId: string, pct: number, expiresAt: number) {
    await this.rpc("ai2027_issue_coupon", {
      p_code: code,
      p_customer_id: customerId,
      p_discount_pct: pct,
      p_expires_at: expiresAt,
    });
  }
  async putVerification(customerId: string, saltHex: string, derivedHex: string, expiresAt: number) {
    await this.rpc("ai2027_put_verification", {
      p_customer_id: customerId,
      p_salt_hex: saltHex,
      p_derived_hex: derivedHex,
      p_expires_at: expiresAt,
    });
  }
  async publicStatus() {
    return await this.rpc("ai2027_public_status", {});
  }
  async runtimeSecrets() {
    const v = await this.rpc("ai2027_runtime_secrets", {});
    return (v && typeof v === "object" ? v : {}) as Record<string, string>;
  }
  async importSecrets(jti: string, exp: number, items: Record<string, string>) {
    return (await this.rpc("ai2027_import_secrets", { p_jti: jti, p_exp: exp, p_items: items })) as
      | ImportedDigest[]
      | null;
  }
}

// ------------------------------------------- GitHub Actions -> Vault import
// GitHub secrets are write-only: only a workflow run can read them. The run
// proves who it is with a GitHub-signed OIDC token (no shared credential exists
// anywhere), and the values travel once over TLS into Supabase Vault.
/** This function's own secrets: imported AND read back here (ai2027_runtime_secrets). */
export const WEBHOOK_SECRETS = [
  "YCLOUD_WEBHOOK_SECRET",
  "WHATSAPP_CLOUD_APP_SECRET",
  "WHATSAPP_CLOUD_VERIFY_TOKEN",
  "AI2027_WHATSAPP_WEBHOOK",
] as const;
/**
 * CELIA's edge secrets ride the same OIDC door (one import path, no second
 * credential) but are never read by this function: only celia-telegram reads
 * them, through celia_runtime_secrets().
 */
export const CELIA_SECRETS = [
  "CELIA_TELEGRAM_TOKEN",
  "GEMINI_API_KEY",
  "GROQ_API_KEY",
  "NVIDIA_API_KEY",
  "OPENROUTER_API_KEY",
] as const;
export const IMPORTABLE = [...WEBHOOK_SECRETS, ...CELIA_SECRETS] as const;
const IMPORTABLE_SET = new Set<string>(IMPORTABLE);
const RUNTIME_SET = new Set<string>(WEBHOOK_SECRETS);
export const OIDC_POLICY = {
  iss: "https://token.actions.githubusercontent.com",
  aud: "ai2027-supabase-import",
  repository: "sayedelazameydesign-crypto/ai-2027",
  repository_id: "1460619599", // pinned: a deleted+recreated repo of the same name gets a new id
  repository_owner_id: "233639223",
  ref: "refs/heads/main",
  workflow_ref: "sayedelazameydesign-crypto/ai-2027/.github/workflows/supabase-secret-sync.yml@refs/heads/main",
  event_name: "workflow_dispatch",
} as const;
const GITHUB_JWKS_URL = "https://token.actions.githubusercontent.com/.well-known/jwks";
export const MAX_IMPORT_BYTES = 64 * 1024;

export interface Jwk {
  kty: string;
  kid?: string;
  n?: string;
  e?: string;
  alg?: string;
}
export type JwksFetcher = () => Promise<{ keys: Jwk[] }>;

let jwksCache: { at: number; keys: Jwk[] } | null = null;
export const githubJwks: JwksFetcher = async () => {
  if (jwksCache && Date.now() - jwksCache.at < 600_000) return { keys: jwksCache.keys };
  const res = await fetch(GITHUB_JWKS_URL, { signal: AbortSignal.timeout(5000) });
  if (!res.ok) throw new Error(`jwks HTTP ${res.status}`);
  const body = await res.json();
  jwksCache = { at: Date.now(), keys: body.keys };
  return body;
};

function b64urlBytes(s: string): Bytes {
  if (!/^[A-Za-z0-9_-]*$/.test(s)) throw new Error("bad b64url");
  const b = atob(s.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (s.length % 4)) % 4));
  const out = new Uint8Array(b.length);
  for (let i = 0; i < b.length; i++) out[i] = b.charCodeAt(i);
  return out;
}

/** Verify a GitHub Actions OIDC JWT against OIDC_POLICY. Returns claims or a reason. */
export async function verifyGithubOidc(
  token: string,
  nowS: number,
  jwks: JwksFetcher,
  policy: Record<string, string> = OIDC_POLICY,
): Promise<{ ok: true; jti: string; exp: number } | { ok: false; reason: string }> {
  const parts = token.split(".");
  if (parts.length !== 3) return { ok: false, reason: "malformed_token" };
  let header: Record<string, unknown>, claims: Record<string, unknown>;
  try {
    header = JSON.parse(new TextDecoder().decode(b64urlBytes(parts[0])));
    claims = JSON.parse(new TextDecoder().decode(b64urlBytes(parts[1])));
  } catch {
    return { ok: false, reason: "malformed_token" };
  }
  if (header.alg !== "RS256" || typeof header.kid !== "string") return { ok: false, reason: "bad_alg" };
  const jwk = (await jwks()).keys.find((k) => k.kid === header.kid && k.kty === "RSA");
  if (!jwk || !jwk.n || !jwk.e) return { ok: false, reason: "unknown_kid" };
  const key = await crypto.subtle.importKey(
    "jwk",
    { kty: "RSA", n: jwk.n, e: jwk.e, alg: "RS256", ext: true },
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
    false,
    ["verify"],
  );
  const good = await crypto.subtle.verify(
    "RSASSA-PKCS1-v1_5",
    key,
    b64urlBytes(parts[2]),
    bytes(enc.encode(`${parts[0]}.${parts[1]}`)),
  );
  if (!good) return { ok: false, reason: "bad_signature" };
  const exp = Number(claims.exp), nbf = Number(claims.nbf ?? claims.iat);
  if (!Number.isFinite(exp) || exp < nowS - 30) return { ok: false, reason: "expired" };
  if (!Number.isFinite(nbf) || nbf > nowS + 60) return { ok: false, reason: "not_yet_valid" };
  const aud = claims.aud;
  if (!(aud === policy.aud || (Array.isArray(aud) && aud.length === 1 && aud[0] === policy.aud))) {
    return { ok: false, reason: "claim_mismatch:aud" };
  }
  for (const k of ["iss", "repository", "repository_id", "repository_owner_id", "ref", "workflow_ref", "event_name"]) {
    if (String(claims[k] ?? "") !== policy[k]) return { ok: false, reason: `claim_mismatch:${k}` };
  }
  const jti = String(claims.jti ?? "");
  if (jti.length < 8) return { ok: false, reason: "missing_jti" };
  return { ok: true, jti, exp: Math.trunc(exp) };
}

// ------------------------------------------------------ orchestrator port
function randomBelow(n: number): number {
  const limit = Math.floor(0x1_0000_0000 / n) * n;
  const buf = new Uint32Array(1);
  for (;;) {
    crypto.getRandomValues(buf);
    if (buf[0] < limit) return buf[0] % n;
  }
}

export async function deriveCodeDigest(customerId: string, code: string, saltHex: string): Promise<string> {
  const salt = new Uint8Array(saltHex.match(/../g)!.map((h) => parseInt(h, 16)));
  const key = await crypto.subtle.importKey("raw", enc.encode(`${customerId}:${code}`), "PBKDF2", false, [
    "deriveBits",
  ]);
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt, iterations: PBKDF2_ITERATIONS },
    key,
    256,
  );
  return toHex(bits);
}

export class Orchestrator {
  constructor(private store: Store, private env: Env = realEnv, private now: () => number = () =>
    Math.trunc(Date.now() / 1000)) {}

  async issueCoupon(customerId: string, discountPct = 10, ttlHours = 48): Promise<string> {
    const pct = Math.max(1, Math.min(100, Math.trunc(discountPct)));
    const code = "CELIA-" + toHex(crypto.getRandomValues(new Uint8Array(3))).toUpperCase();
    await this.store.issueCoupon(code, customerId, pct, this.now() + Math.max(1, ttlHours) * 3600);
    return code;
  }

  async issueVerificationCode(customerId: string, ttlSeconds = 300): Promise<string> {
    const code = String(randomBelow(900_000) + 100_000);
    const salt = toHex(crypto.getRandomValues(new Uint8Array(16)));
    const derived = await deriveCodeDigest(customerId, code, salt);
    await this.store.putVerification(customerId, salt, derived, this.now() + Math.max(30, ttlSeconds));
    return code;
  }

  async defaultReply(ev: InboundEvent): Promise<string> {
    const n = pyStrip(ev.text_body || "").toLowerCase();
    if (n.includes("خصم") || n.includes("coupon")) {
      const promo = await this.issueCoupon(ev.sender_wa_id, 15);
      return `تم إصدار كود الخصم المجاني الخاص بك: ${promo} (صالح لمدة 48 ساعة).`;
    }
    if (n.includes("تأكيد") || n.includes("رمز") || n.includes("verify")) {
      const code = await this.issueVerificationCode(ev.sender_wa_id);
      return `رمز التحقق المجاني الخاص بك هو: ${code} (صالح لمدة 5 دقائق).`;
    }
    return "أهلاً بك في Celia! تم تسجيل طلبك وسنخدمك فوراً ضمن نافذة الخدمة المجانية.";
  }

  async ingestEvent(ev: InboundEvent): Promise<string> {
    const nowS = this.now();
    const gate = await this.store.stageInbound(ev, configuredDailyCap(this.env), nowS);
    if (gate !== "reserved") return gate;
    const reply = pyStrip(await this.defaultReply(ev));
    const payloadHash = await computeSha256({
      recipient_phone: ev.sender_wa_id,
      message_text: reply,
      inbound_timestamp_s: Math.trunc(ev.timestamp_s),
      reply_to_wamid: ev.wamid,
    });
    await this.store.finalizeReply(ev.wamid, reply, payloadHash);
    return "pending_approval";
  }

  async ingestSignedWebhook(
    raw: Uint8Array,
    hubSig: string | null,
    ycloudSig: string | null,
  ): Promise<{ ok: boolean; stage: string; ingested?: number; stages: string[] }> {
    const nowS = this.now();
    const [ok, sigStage] = ycloudSig
      ? await verifyYcloudSignature(raw, ycloudSig, resolveYcloudSigningBytes(this.env), nowS)
      : await verifyHubSignature(
        raw,
        hubSig,
        (() => {
          const s = this.env("WHATSAPP_CLOUD_APP_SECRET") || "";
          return s ? enc.encode(s) : null;
        })(),
      );
    if (!ok) return { ok: false, stage: sigStage, stages: [] };
    let payload: unknown;
    try {
      payload = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(raw));
    } catch {
      return { ok: false, stage: "invalid_json", stages: [] };
    }
    const events = parseInboundEvents(payload, nowS);
    const stages: string[] = [];
    for (const ev of events) stages.push(await this.ingestEvent(ev));
    return { ok: true, stage: "ok", ingested: stages.length, stages };
  }
}

// ---------------------------------------------------------------- handler
function json(body: unknown, status = 200, extra: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store", ...extra },
  });
}
const empty = (status: number) => new Response(null, { status });
// Only the two public, PII-free read endpoints are readable cross-origin (status
// page on GitHub Pages). /webhook never gets CORS: browsers have no business there.
const PUBLIC_READ = { "Access-Control-Allow-Origin": "*" };

/** Strip everything up to and including the function slug: /ycloud-webhook/healthz -> /healthz */
export function routeOf(pathname: string): string {
  const m = pathname.match(/\/ycloud-webhook(\/.*)?$/);
  const p = m ? (m[1] || "/") : pathname;
  return p.replace(/\/+$/, "") || "/";
}

export function makeHandler(store: Store, env: Env = realEnv, now?: () => number, jwks: JwksFetcher = githubJwks) {
  // Vault (imported from GitHub Actions) wins over Edge env for the allowlisted
  // names; refreshed at most every 60 s. A Vault read failure is a 503 (YCloud
  // retries) — never a silent fallback to a possibly stale env secret.
  let vault: { at: number; v: Record<string, string> } = { at: -Infinity, v: {} };
  const clock = now ?? (() => Math.trunc(Date.now() / 1000));
  const effEnv: Env = (n) => (RUNTIME_SET.has(n) && vault.v[n]) || env(n);
  const loadVault = async () => {
    if (clock() - vault.at < 60) return;
    vault = { at: clock(), v: await store.runtimeSecrets() };
  };
  const orch = new Orchestrator(store, effEnv, now);
  return async (req: Request): Promise<Response> => {
    const url = new URL(req.url);
    const route = routeOf(url.pathname);

    if (route === "/import-secrets") {
      if (req.method !== "POST") return empty(405);
      const auth = req.headers.get("Authorization") || "";
      if (!/^Bearer [A-Za-z0-9._-]{20,8192}$/.test(auth)) return empty(401);
      let v;
      try {
        v = await verifyGithubOidc(auth.slice(7), clock(), jwks);
      } catch {
        return empty(503); // JWKS unreachable: retryable, never "accepted"
      }
      if (!v.ok) return json({ ok: false, reason: v.reason }, 401);
      const raw = new Uint8Array(await req.arrayBuffer());
      if (raw.length === 0 || raw.length > MAX_IMPORT_BYTES) return empty(413);
      let body: unknown;
      try {
        body = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(raw));
      } catch {
        return empty(400);
      }
      const items = isObj(body) && isObj(body["secrets"]) ? body["secrets"] as Record<string, unknown> : null;
      if (!items || Object.keys(items).length === 0) return json({ ok: false, reason: "no_secrets" }, 400);
      const clean: Record<string, string> = {};
      for (const [k, val] of Object.entries(items)) {
        if (!IMPORTABLE_SET.has(k)) return json({ ok: false, reason: `name_not_allowed:${k}` }, 400);
        const t = typeof val === "string" ? val.trim() : "";
        if (t.length < 8 || t.length > 4096) return json({ ok: false, reason: `bad_value_length:${k}` }, 400);
        clean[k] = t;
      }
      let stored;
      try {
        stored = await store.importSecrets(v.jti, v.exp, clean);
      } catch {
        return empty(503);
      }
      if (stored === null) return json({ ok: false, reason: "token_replayed" }, 401);
      vault.at = -Infinity; // next webhook re-reads Vault immediately
      return json({ ok: true, stored });
    }

    if (req.method === "GET" && route === "/healthz") {
      let ok = false;
      try {
        ok = await store.healthz();
      } catch {
        ok = false;
      }
      return json({ ok, service: "free-stack-webhook" }, ok ? 200 : 503, PUBLIC_READ);
    }
    if (req.method === "GET" && route === "/status") {
      try {
        return json(await store.publicStatus(), 200, PUBLIC_READ);
      } catch {
        return json({ ok: false, service: "free-stack-webhook" }, 503, PUBLIC_READ);
      }
    }
    if (route !== "/webhook") return empty(404);
    try {
      await loadVault();
    } catch {
      return empty(503);
    }

    if (req.method === "GET") {
      const expected = (effEnv("WHATSAPP_CLOUD_VERIFY_TOKEN") || effEnv("AI2027_WHATSAPP_WEBHOOK") || "").trim();
      const q = url.searchParams;
      const [ok, challenge] = verifyWebhookChallenge(
        q.get("hub.mode"),
        q.get("hub.verify_token"),
        expected,
        q.get("hub.challenge"),
      );
      if (!ok) return empty(403);
      return new Response(challenge, { status: 200, headers: { "Content-Type": "text/plain; charset=utf-8" } });
    }
    if (req.method !== "POST") return empty(405);

    const declared = req.headers.get("Content-Length");
    if (declared !== null) {
      if (!/^\d+$/.test(declared)) return empty(400);
      if (parseInt(declared, 10) > MAX_WEBHOOK_BYTES) return empty(413);
    }
    const raw = new Uint8Array(await req.arrayBuffer());
    if (raw.length > MAX_WEBHOOK_BYTES) return empty(413);
    if (raw.length === 0) return empty(400);

    let outcome;
    try {
      outcome = await orch.ingestSignedWebhook(
        raw,
        req.headers.get("X-Hub-Signature-256"),
        req.headers.get("YCloud-Signature"),
      );
    } catch {
      // Store failure after a VALID signature: 503 makes YCloud retry (10s..2h, 7x).
      return empty(503);
    }
    if (!outcome.ok) return empty(401);
    return json({ ok: true, ingested: outcome.ingested, stages: outcome.stages });
  };
}

if (!Deno.env.get("AI2027_EDGE_TEST")) {
  const url = Deno.env.get("SUPABASE_URL") || "";
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
  Deno.serve(makeHandler(new PostgrestStore(url, key)));
}
