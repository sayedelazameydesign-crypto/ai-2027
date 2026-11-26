// Unit tests for the worker's pure signing core (no dependencies, no KV).
import test from "node:test";
import assert from "node:assert/strict";
import { createPrivateKey, createPublicKey } from "node:crypto";
import {
  canonicalMessage,
  signEnvelope,
  verifyEnvelope,
} from "../src/crypto.js";

const PARTS = {
  approval_id: "01SMOKEM1489KN08P02TBPEBTK",
  action: "agent.plan_day",
  payload_hash: "5f7b98b83faa21b645fc6a093d84450aad40eeec2578bb49daa4b9e08482ca94",
  decision: "grant",
  exp: "2026-11-26T20:00:00.000Z",
};

// RFC 8032 vector-3 secret (public key fc51cd8e…8025).
const SEED = "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7";
const PRIVATE_PREFIX = Buffer.from("302e020100300506032b657004220420", "hex");
const PUBLIC_PREFIX = Buffer.from("302a300506032b6570032100", "hex");

function pkcs8PemFromSeed(seedHex) {
  const der = Buffer.concat([PRIVATE_PREFIX, Buffer.from(seedHex, "hex")]);
  return `-----BEGIN PRIVATE KEY-----\n${der.toString("base64")}\n`
    + `-----END PRIVATE KEY-----\n`;
}

function rawPublicFromSeed(seedHex) {
  const priv = createPrivateKey({
    key: Buffer.concat([PRIVATE_PREFIX, Buffer.from(seedHex, "hex")]),
    format: "der",
    type: "pkcs8",
  });
  return createPublicKey(priv).export({ type: "spki", format: "der" })
    .subarray(-32);
}

test("canonicalMessage matches the Python contract byte-for-byte", () => {
  assert.equal(
    canonicalMessage(PARTS),
    `${PARTS.approval_id}|${PARTS.action}|${PARTS.payload_hash}|`
    + `${PARTS.decision}|${PARTS.exp}`,
  );
});

test("signEnvelope -> verifyEnvelope roundtrip (WebCrypto Ed25519)", async () => {
  const envelope = await signEnvelope(
    { approval_id: PARTS.approval_id, action: PARTS.action,
      payload_hash: PARTS.payload_hash, exp: PARTS.exp },
    pkcs8PemFromSeed(SEED),
  );
  assert.equal(envelope.decision, "grant");
  assert.equal(envelope.approval_id, PARTS.approval_id);
  assert.equal(envelope.exp, PARTS.exp, "pinned exp must be preserved");
  assert.equal(typeof envelope.signature, "string");
  assert.equal(Buffer.from(envelope.signature, "base64").length, 64);

  const rawPub = rawPublicFromSeed(SEED);
  assert.equal(await verifyEnvelope(envelope, rawPub), true);

  // Tampering with any signed field or the signature must fail.
  const tampered = { ...envelope, payload_hash: "0".repeat(64) };
  assert.equal(await verifyEnvelope(tampered, rawPub), false);
  const broken = { ...envelope, signature: Buffer.alloc(64).toString("base64") };
  assert.equal(await verifyEnvelope(broken, rawPub), false);
});

test("default exp is in the future when not pinned", async () => {
  const envelope = await signEnvelope(
    { approval_id: "A", action: "a.b", payload_hash: "00" },
    pkcs8PemFromSeed(SEED),
  );
  assert.ok(Date.parse(envelope.exp) > Date.now());
});
