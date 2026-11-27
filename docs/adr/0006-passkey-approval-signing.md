# ADR 0006 — Passkey-signed approval decisions (Ed25519) with Telegram buttons as a transitional fallback

- **Status**: Accepted
- **Date**: 2026-11-26
- **Context**: The P1 approval loop authenticates votes with Telegram inline
  buttons. A button click proves only "someone holding this Telegram session
  clicked" — an account-level,phishing-relevant factor. The governed executor
  already binds each approval to `payload_hash` and consumes it single-use, but
  the *human decision* itself carries no cryptographic proof: anyone who
  compromises the chat can grant any pending approval. The owner asked for a
  newer approval mechanism (passkey / content-bound signature) evaluated
  against the current buttons.

## Decision

- **Passkey (WebAuthn) authenticates the human; Ed25519 signs the decision.**
  A Cloudflare Worker (`worker/approval-worker/`, HTTPS on `workers.dev`) runs
  the WebAuthn ceremony and signs the canonical assertion
  `approval_id|action|payload_hash|decision|exp` (pipe-delimited, UTF-8 —
  no JSON canonicalisation needed across the JS/Python boundary) with an
  Ed25519 private key held only as a Worker secret.
- **The executor verifies, never trusts.** `GovernedExecutor.execute(...,
  assertion=...)` accepts a pending row only when: a verification public key
  is configured (`PA_APPROVAL_SIGNING_PUBKEY`, hex), the envelope's fields
  equal the row's `approval_id`/`action`/`payload_hash` **and** the live
  `compute_sha256(payload)`, `decision == "grant"`, `exp` is in the future,
  and the Ed25519 signature over the canonical message verifies. Any gap
  denies with a structured `assertion_*` reason. After execution the normal
  single-use consume (`status → consumed`, `version + 1`, checksum) applies,
  so a captured assertion cannot be replayed.
- **Verification is pure-stdlib Python** (`src/executors/ed25519.py`,
  RFC 8032): ADR-0002 forbids third-party dependencies in the gated code, and
  CI runs the P1 suites bare. Correctness is anchored by the official RFC
  8032 §7.1 vectors (exact sign+verify match), tamper tests, and a
  cross-check against OpenSSL in both directions
  (`tests/test_ed25519.py`).
- **Telegram buttons remain as a transitional fallback, deliberately.**
  Availability during the rollout matters, and each path is individually
  fail-closed. Removal criteria: the passkey path has completed N production
  round-trips, then drop the inline buttons from `build_approval_card` in a
  dedicated change (recorded here so the removal is a decision, not drift).
- **The card links to the Worker** when `PA_APPROVAL_VOTE_BASE_URL` is set;
  the buttons are unchanged otherwise (zero behaviour change without config).

## Consequences

- The Worker owns ceremony state (challenge, stored credential) in Workers KV
  and the signing key as a secret; deployment (`wrangler`) and key generation
  happen in the owner's environment — this repository ships code and tests,
  not infrastructure.
- Key rotation = new Worker secret + update `PA_APPROVAL_SIGNING_PUBKEY`;
  the executor fails closed (`assertion_verification_key_missing`) while the
  key is absent, so rotation windows deny rather than widen.
- The signature binds `payload_hash`, so an assertion captured for one
  payload cannot authorise a different payload for the same approval id —
  the same property the bot path gets from `payload_hash_mismatch`.
- `scripts/live_smoke_runner.py --execute` gained `--assertion` and reads
  `PA_APPROVAL_SIGNING_PUBKEY`, so the whole loop (seed → link/vote →
  execute → `consumed_at`) is exercisable from one CLI.
