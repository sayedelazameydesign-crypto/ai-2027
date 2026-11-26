// Cross-language interop CLI — used by tests/test_passkey_interop.py.
//
//   node interop.js canonical <approval_id> <action> <hash> <decision> <exp>
//   node interop.js sign <secretHex> <messageUtf8>
//   node interop.js verify <pubHex> <messageUtf8> <sigBase64>
//   node interop.js sign-envelope <secretHex> <approval_id> <action> <hash> <decision> <exp>
//
// sign/verify use node:crypto (an independent implementation from both the
// Python module and the Worker's WebCrypto path); sign-envelope exercises the
// Worker's actual signEnvelope() so Python verifies what production emits.
import { signEnvelope } from "../src/crypto.js";

const { createPrivateKey, createPublicKey, sign, verify } = await import(
  "node:crypto");

const PRIVATE_PREFIX = Buffer.from("302e020100300506032b657004220420", "hex");
const PUBLIC_PREFIX = Buffer.from("302a300506032b6570032100", "hex");

function privateKeyFromSeed(hex) {
  return createPrivateKey({
    key: Buffer.concat([PRIVATE_PREFIX, Buffer.from(hex, "hex")]),
    format: "der",
    type: "pkcs8",
  });
}

function publicKeyFromRaw(hex) {
  return createPublicKey({
    key: Buffer.concat([PUBLIC_PREFIX, Buffer.from(hex, "hex")]),
    format: "der",
    type: "spki",
  });
}

const [mode, ...args] = process.argv.slice(2);

if (mode === "canonical") {
  const [approval_id, action, payload_hash, decision, exp] = args;
  process.stdout.write(
    `${approval_id}|${action}|${payload_hash}|${decision}|${exp}`);
} else if (mode === "sign") {
  const [secretHex, message] = args;
  const sig = sign(null, Buffer.from(message, "utf8"),
                   privateKeyFromSeed(secretHex));
  process.stdout.write(sig.toString("base64"));
} else if (mode === "verify") {
  const [pubHex, message, sigB64] = args;
  const ok = verify(null, Buffer.from(message, "utf8"),
                    publicKeyFromRaw(pubHex), Buffer.from(sigB64, "base64"));
  process.stdout.write(ok ? "OK" : "BAD");
  process.exitCode = ok ? 0 : 1;
} else if (mode === "sign-envelope") {
  const [secretHex, approval_id, action, payload_hash, decision, exp] = args;
  const der = Buffer.concat([PRIVATE_PREFIX, Buffer.from(secretHex, "hex")]);
  const pem = `-----BEGIN PRIVATE KEY-----\n`
    + `${der.toString("base64")}\n-----END PRIVATE KEY-----\n`;
  const envelope = await signEnvelope(
    { approval_id, action, payload_hash, decision, exp }, pem);
  process.stdout.write(JSON.stringify(envelope));
} else {
  process.stderr.write("unknown mode\n");
  process.exitCode = 2;
}
