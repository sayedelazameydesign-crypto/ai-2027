// Pure signing core for the approval worker (ADR-0006).
//
// Deliberately free of @simplewebauthn and of any Node-only import: this
// module runs in the Cloudflare Workers runtime and is unit-tested with
// `node --test` without installing anything. The canonical message MUST stay
// byte-identical to canonical_assertion_message() in
// src/executors/governed_executor.py — the interop test
// (tests/test_passkey_interop.py) proves it across languages.

const TEXT_ENCODER = new TextEncoder();

/** Canonical bytes signed for a decision — pipe-delimited, no escaping. */
export function canonicalMessage({ approval_id, action, payload_hash,
                                   decision, exp }) {
  return `${approval_id}|${action}|${payload_hash}|${decision}|${exp}`;
}

/** Decode a PKCS#8 PEM (the wrangler secret) to an ArrayBuffer of DER. */
export function pemToDer(pem) {
  const body = pem
    .replace(/-----BEGIN PRIVATE KEY-----/g, "")
    .replace(/-----END PRIVATE KEY-----/g, "")
    .replace(/\s+/g, "");
  const binary = atob(body);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

/** Import the Ed25519 signing key (PKCS#8 PEM) via WebCrypto. */
export async function importSigningKey(privatePem) {
  return crypto.subtle.importKey(
    "pkcs8",
    pemToDer(privatePem),
    { name: "Ed25519" },
    false,
    ["sign"],
  );
}

/**
 * Sign a decision envelope. Returns the exact JSON the executor consumes:
 * { approval_id, action, payload_hash, decision, exp, signature } with the
 * signature base64 over canonicalMessage().
 */
export async function signEnvelope({ approval_id, action, payload_hash,
                                     decision = "grant", ttl_sec = 600,
                                     exp = null },
                                   privateKeyPem) {
  const expiry = exp || new Date(Date.now() + ttl_sec * 1000).toISOString();
  const envelope = { approval_id, action, payload_hash, decision,
                     exp: expiry };
  const key = await importSigningKey(privateKeyPem);
  const signature = await crypto.subtle.sign(
    { name: "Ed25519" },
    key,
    TEXT_ENCODER.encode(canonicalMessage(envelope)),
  );
  envelope.signature = btoa(String.fromCharCode(...new Uint8Array(signature)));
  return envelope;
}

/** Verify an envelope against a raw 32-byte public key (tests / interop). */
export async function verifyEnvelope(envelope, publicKeyRaw) {
  try {
    const key = await crypto.subtle.importKey(
      "raw",
      publicKeyRaw,
      { name: "Ed25519" },
      false,
      ["verify"],
    );
    const sig = Uint8Array.from(atob(envelope.signature), (c) =>
      c.charCodeAt(0));
    return await crypto.subtle.verify(
      { name: "Ed25519" },
      key,
      sig,
      TEXT_ENCODER.encode(canonicalMessage(envelope)),
    );
  } catch {
    return false;
  }
}
