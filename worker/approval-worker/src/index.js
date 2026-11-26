// Passkey approval worker (ADR-0006).
//
// Routes:
//   GET  /                 voting page (register passkey / vote with passkey)
//   POST /register/options WebAuthn registration ceremony (challenge in KV)
//   POST /register/verify  store the verified credential in KV
//   POST /vote/options     authentication challenge bound to one approval
//   POST /vote/verify      verify assertion -> Ed25519-signed envelope
//
// The returned envelope is exactly what
// `scripts/live_smoke_runner.py --execute … --assertion '<json>'` feeds to
// the governed executor, which verifies it fail-closed against
// PA_APPROVAL_SIGNING_PUBKEY.

import {
  generateRegistrationOptions,
  verifyRegistrationResponse,
  generateAuthenticationOptions,
  verifyAuthenticationResponse,
} from "@simplewebauthn/server";
import { signEnvelope } from "./crypto.js";

const RP_NAME = "AI-2027 Approval Passkey";
const CHALLENGE_TTL_SEC = 300;

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function originOf(request) {
  return new URL(request.url).origin;
}

function rpIdOf(request) {
  return new URL(request.url).host;
}

function b64urlToBytes(s) {
  const b = s.replace(/-/g, "+").replace(/_/g, "/");
  const pad = "=".repeat((4 - (b.length % 4)) % 4);
  const binary = atob(b + pad);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function bytesToB64url(bytes) {
  let s = "";
  for (const byte of bytes) s += String.fromCharCode(byte);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function bytesToB64(bytes) {
  let s = "";
  for (const byte of bytes) s += String.fromCharCode(byte);
  return btoa(s);
}

async function getCredentials(env) {
  return (await env.APPROVAL_AUTH.get("creds", "json")) || [];
}

async function putChallenge(env, challenge, record) {
  await env.APPROVAL_AUTH.put(`chal:${challenge}`, JSON.stringify(record), {
    expirationTtl: CHALLENGE_TTL_SEC,
  });
}

async function getChallenge(env, challenge) {
  return env.APPROVAL_AUTH.get(`chal:${challenge}`, "json");
}

function clientChallenge(response) {
  const raw = b64urlToBytes(response.response.clientDataJSON);
  return JSON.parse(new TextDecoder().decode(raw)).challenge;
}

async function handleRegisterOptions(request, env) {
  const rpID = rpIdOf(request);
  const creds = await getCredentials(env);
  const options = await generateRegistrationOptions({
    rpName: RP_NAME,
    rpID,
    userName: "owner",
    attestationType: "none",
    excludeCredentials: creds.map((c) => ({
      id: b64urlToBytes(c.id),
      type: "public-key",
      transports: ["internal", "hybrid"],
    })),
  });
  await putChallenge(env, options.challenge, { type: "register" });
  return json(options);
}

async function handleRegisterVerify(request, env) {
  const rpID = rpIdOf(request);
  const origin = originOf(request);
  const { response } = await request.json();
  const expectedChallenge = clientChallenge(response);
  const record = await getChallenge(env, expectedChallenge);
  if (!record || record.type !== "register") {
    return json({ error: "challenge_not_found_or_wrong_type" }, 400);
  }
  const result = await verifyRegistrationResponse({
    response,
    expectedChallenge,
    expectedOrigin: origin,
    expectedRPID: rpID,
    requireUserVerification: false,
  });
  if (!result.verified || !result.registrationInfo) {
    return json({ error: "registration_not_verified" }, 400);
  }
  const { credentialID, credentialPublicKey } = result.registrationInfo;
  const id = bytesToB64url(credentialID);
  const creds = await getCredentials(env);
  const next = creds.filter((c) => c.id !== id);
  next.push({
    id,
    publicKey: bytesToB64(credentialPublicKey),
    counter: result.registrationInfo.counter || 0,
  });
  await env.APPROVAL_AUTH.put("creds", JSON.stringify(next));
  return json({ ok: true, credential_id: id, count: next.length });
}

async function handleVoteOptions(request, env) {
  const rpID = rpIdOf(request);
  const { approval_id, action, payload_hash } = await request.json();
  if (!approval_id || !action || !payload_hash) {
    return json({ error: "approval_id, action and payload_hash are required" },
                400);
  }
  const creds = await getCredentials(env);
  if (creds.length === 0) {
    return json({ error: "no_passkey_registered" }, 409);
  }
  const challenge = bytesToB64url(crypto.getRandomValues(new Uint8Array(32)));
  const options = await generateAuthenticationOptions({
    rpID,
    allowCredentials: creds.map((c) => ({
      id: b64urlToBytes(c.id),
      type: "public-key",
      transports: ["internal", "hybrid"],
    })),
    userVerification: "preferred",
  });
  // generateAuthenticationOptions made its own challenge — store OUR record
  // under the challenge we return to the client.
  options.challenge = challenge;
  await putChallenge(env, challenge, {
    type: "vote",
    approval_id,
    action,
    payload_hash,
  });
  return json(options);
}

async function handleVoteVerify(request, env) {
  const rpID = rpIdOf(request);
  const origin = originOf(request);
  const { approval_id, action, payload_hash, response } =
    await request.json();
  const expectedChallenge = clientChallenge(response);
  const record = await getChallenge(env, expectedChallenge);
  if (!record || record.type !== "vote") {
    return json({ error: "challenge_not_found_or_wrong_type" }, 400);
  }
  if (record.approval_id !== approval_id || record.action !== action
      || record.payload_hash !== payload_hash) {
    return json({ error: "vote_context_mismatch" }, 400);
  }
  const creds = await getCredentials(env);
  const cred = creds.find((c) => c.id === response.id);
  if (!cred) {
    return json({ error: "unknown_credential" }, 400);
  }
  const result = await verifyAuthenticationResponse({
    response,
    expectedChallenge,
    expectedOrigin: origin,
    expectedRPID: rpID,
    authenticator: {
      credentialID: b64urlToBytes(cred.id),
      credentialPublicKey: (() => {
        const b = atob(cred.publicKey);
        const bytes = new Uint8Array(b.length);
        for (let i = 0; i < b.length; i += 1) bytes[i] = b.charCodeAt(i);
        return bytes;
      })(),
      counter: cred.counter || 0,
    },
    requireUserVerification: false,
  });
  if (!result.verified) {
    return json({ error: "assertion_not_verified" }, 400);
  }
  cred.counter = result.authenticationInfo.newCounter;
  await env.APPROVAL_AUTH.put("creds", JSON.stringify(creds));

  if (!env.SIGNING_PRIVATE_KEY) {
    return json({ error: "signing_key_not_configured" }, 500);
  }
  const envelope = await signEnvelope(
    { approval_id, action, payload_hash, decision: "grant" },
    env.SIGNING_PRIVATE_KEY,
  );
  return json(envelope);
}

const PAGE = `<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI-2027 — موافقة بمفتاح المرور</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:40rem;margin:2rem auto;padding:0 1rem;line-height:1.6}
 button{padding:.6rem 1.2rem;font-size:1rem;cursor:pointer;border-radius:.5rem;border:1px solid #333;background:#1a1a2e;color:#eee}
 input{width:100%;padding:.5rem;box-sizing:border-box;margin:.25rem 0}
 textarea{width:100%;min-height:8rem;box-sizing:border-box;font-family:monospace}
 .ok{color:#2e7d32}.err{color:#c62828}section{border:1px solid #ddd;border-radius:.75rem;padding:1rem;margin:1rem 0}
</style>
</head>
<body>
<h1>🔑 موافقة بمفتاح المرور</h1>
<p>مسار ADR-0006: يُوثَّق القرار بـ WebAuthn ثم يوقّعه الـWorker بـ Ed25519 —
والمنفّذ يتحقق قبل التنفيذ (أزرار تلغرام تبقى بديلاً انتقالياً).</p>
<section>
 <h2>1) تسجيل مفتاح المرور</h2>
 <button id="reg">سجّل مفتاح المرور على هذا الجهاز</button>
 <p id="reg-msg"></p>
</section>
<section>
 <h2>2) التصويت على طلب</h2>
 <p>رابط البطاقة يملأ الحقول تلقائياً (?approval=&action=&hash=).</p>
 <label>approval_id<input id="approval_id"></label>
 <label>action<input id="action"></label>
 <label>payload_hash<input id="hash"></label>
 <button id="vote">وقّع الموافقة بمفتاح المرور</button>
 <p id="vote-msg"></p>
 <label>الحزمة الموقّعة (انسخها إلى --assertion):
 <textarea id="envelope" readonly></textarea></label>
</section>
<script>
const q = new URLSearchParams(location.search);
if (q.get("approval")) document.getElementById("approval_id").value = q.get("approval");
if (q.get("action")) document.getElementById("action").value = q.get("action");
if (q.get("hash")) document.getElementById("hash").value = q.get("hash");
function msg(id, text, ok) { const el = document.getElementById(id); el.textContent = text; el.className = ok ? "ok" : "err"; }
async function post(path, body) {
  const r = await fetch(path, { method: "POST", headers: {"content-type":"application/json"}, body: JSON.stringify(body) });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || r.status);
  return data;
}
document.getElementById("reg").onclick = async () => {
  try {
    const options = await post("/register/options", {});
    const response = await navigator.credentials.create({ publicKey: options });
    const out = await post("/register/verify", { response });
    msg("reg-msg", "تم التسجيل (" + out.credential_id.slice(0, 12) + "…)", true);
  } catch (e) { msg("reg-msg", "فشل: " + e.message, false); }
};
document.getElementById("vote").onclick = async () => {
  try {
    const ctx = {
      approval_id: document.getElementById("approval_id").value.trim(),
      action: document.getElementById("action").value.trim(),
      payload_hash: document.getElementById("hash").value.trim(),
    };
    const options = await post("/vote/options", ctx);
    const response = await navigator.credentials.get({ publicKey: options });
    const envelope = await post("/vote/verify", { ...ctx, response });
    document.getElementById("envelope").value = JSON.stringify(envelope);
    msg("vote-msg", "موقّع بنجاح — انسخ الحزمة أعلاه إلى --assertion", true);
  } catch (e) { msg("vote-msg", "فشل: " + e.message, false); }
};
</script>
</body>
</html>`;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    try {
      if (request.method === "GET" && url.pathname === "/") {
        return new Response(PAGE, {
          headers: { "content-type": "text/html; charset=utf-8" },
        });
      }
      if (request.method === "POST"
          && url.pathname === "/register/options") {
        return await handleRegisterOptions(request, env);
      }
      if (request.method === "POST"
          && url.pathname === "/register/verify") {
        return await handleRegisterVerify(request, env);
      }
      if (request.method === "POST" && url.pathname === "/vote/options") {
        return await handleVoteOptions(request, env);
      }
      if (request.method === "POST" && url.pathname === "/vote/verify") {
        return await handleVoteVerify(request, env);
      }
      return json({ error: "not_found" }, 404);
    } catch (err) {
      return json({ error: String(err && err.message || err) }, 500);
    }
  },
};
