# approval-worker — Passkey voting for the governed approval loop (ADR-0006)

A Cloudflare Worker that (1) runs a WebAuthn passkey ceremony and (2) signs
the approval decision with Ed25519. The Python side
(`src/executors/governed_executor.py`) verifies that signature fail-closed
before executing a pending approval — see `docs/adr/0006-passkey-approval-signing.md`.

```
Telegram card link  →  https://<worker>/…?approval=<id>&action=<a>&hash=<h>
      →  register / vote with passkey (WebAuthn, challenge bound in KV)
      →  Worker returns a signed envelope (approval_id|action|hash|decision|exp)
      →  scripts/live_smoke_runner.py --execute <a> --assertion '<json>'
      →  governed executor: four gates → consumed_at + chained audit row
```

## Test (no install needed)

```bash
node --test worker/approval-worker/test/*.test.js   # pure signing core
python3 -m unittest tests.test_passkey_interop      # Python ↔ JS interop
```

The interop suite proves: identical canonical messages in both languages,
Node `node:crypto` ↔ Python RFC 8032 signatures both directions, and a
Worker-signed envelope executing through the real `GovernedExecutor`.

## Deploy (owner's environment — nothing here can deploy itself)

```bash
cd worker/approval-worker
npm install

# 1. KV namespace for challenges + the stored credential
npx wrangler kv:namespace create APPROVAL_AUTH
#    → paste the returned id into wrangler.toml

# 2. Signing key: generate once, keep the private key ONLY as a Wrangler secret
openssl genpkey -algorithm Ed25519 -out key.pem
npx wrangler secret put SIGNING_PRIVATE_KEY < key.pem

# 3. Export the public key hex for the Python executor (never a secret)
openssl pkey -in key.pem -pubout -outform DER | tail -c 32 | xxd -p -c 64
#    → PA_APPROVAL_SIGNING_PUBKEY=<64 hex chars>   (.env.example section 9)

# 4. Deploy and wire the card link
npx wrangler deploy
#    → PA_APPROVAL_VOTE_BASE_URL=https://approval-worker.<account>.workers.dev
```

Then restart the approval bot (the card gains the 🔑 link) and execute with:

```bash
python3 scripts/live_smoke_runner.py --execute <action> \
  --assertion '{"approval_id":"…","action":"…","payload_hash":"…","decision":"grant","exp":"…","signature":"…"}'
```

## Fail-closed properties

- No `SIGNING_PRIVATE_KEY` → `/vote/verify` returns `signing_key_not_configured`.
- No `PA_APPROVAL_SIGNING_PUBKEY` on the Python side → the assertion path
  denies (`assertion_verification_key_missing`); only bot-granted rows work.
- Challenge records expire in 300 s and are bound to the exact
  `(approval_id, action, payload_hash)` triple — a captured WebAuthn
  assertion cannot be re-pointed at another approval.
- The signature binds the same triple plus `decision` and `exp`; the
  executor's single-use consume makes replay of a *signed envelope* fail
  with `approval_already_consumed`.
- The inline Telegram buttons remain as the transitional fallback until the
  passkey path proves itself in production (removal is an ADR decision).
