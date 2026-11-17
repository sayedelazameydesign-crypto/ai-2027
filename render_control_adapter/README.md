# Render Control Center — Phase 1 adapter

Stdlib-only client for the **Render API v1** with the approval policy, secret
handling, rate limits and deploy *verification* implemented in code rather than
described in a document.

**Read this first:** the adapter was written **without live API access**. Every
response shape below comes from Render's published docs and is exercised only
against offline fixtures. It has **never** been run against `api.render.com`.
That is exactly what `probe.py` is for — the account owner runs it once, and the
recorded shapes either confirm the fixtures or break them.

## Why an adapter and not Core code

Render must not become part of the kernel. This directory imports **nothing**
outside the Python standard library; a CI step parses every file with `ast` and
fails the build on any third-party import. The same four contracts —
`POLICY`, `ApprovalEngine`, `redact`, terminal-state verification — are what a
Vercel or Supabase adapter would implement.

## Approval policy (as enforced by the code)

| Operation | Risk | Approval |
|---|---|---|
| list/get services, deploys, events, logs, owners | read | automatic |
| `diagnose`, `audit_infrastructure` (fault analysis) | read | automatic |
| `restart_service` | write | approval |
| `deploy` | write | approval |
| `set_env_var` | secret-write | **explicit** |
| `get_env_var_value` | secret-read | **explicit**, audited by key name only |
| `delete_service` | destructive | **explicit + the exact service name** as confirmation |

Deleting requires the real name, not a boolean. A typo cannot approve a delete.

## Three invariants worth checking yourself

1. **A deploy is not a success.** `deploy()` always returns `verified: false`.
   Only `wait_for_deploy()` may set `verified: true`, and only after a terminal
   state in `{live}`. `build_failed`, `update_failed`, `canceled`,
   `pre_deploy_failed` are terminal **failures**; a timeout returns
   `UNKNOWN, not success`.
2. **Secrets never leave the client.** `list_env_vars_masked()` returns
   `{key, value_present, value_len, generated}` and never the value. Every value
   the client has *ever* seen is kept in `_secrets` and scrubbed out of
   subsequent log lines, including values sitting under innocent key names.
3. **Nothing is assumed about the API.** Rate limits are enforced locally
   *before* a request is sent (Render returns 429 cheaply; a retry storm does
   not). Single-resource responses are accepted as a bare object **or** as the
   one-item list envelope Render actually returns.

## Run it

```bash
python3 render_control_adapter/test_adapter.py -v   # 43 offline tests, no network
python3 render_control_adapter/probe.py             # Phase 0 audit; exits 2 without RENDER_API_KEY
```

`probe.py` performs **reads only**, asserts `mutations_performed == 0`, reads
env-var **keys and lengths only**, and prints a redacted key fingerprint. If it
ever exits 0 without a key, the audit is lying — that is a CI test.

## Bugs the first execution of the suite found

The suite was written before it was ever run. Running it found four real
defects, each now a named regression test (`test_80`–`test_83`):

- **Approval tokens collided.** `HMAC(op|target|minute)` with no nonce made ten
  deploys in one minute produce one string, so the single-use set rejected
  deployments 2–10. The guard blocked legitimate work — a policy bug disguised
  as security.
- **`GET /services/{id}` returned `unexpected_shape`.** The real body is a
  one-item list envelope, not a bare object.
- **OOM kills were invisible.** `diagnose()` only classified lines containing
  `error|fail|exception`, so `Killed process 1 (python3) out of memory` — a
  leading cause of dead services — was skipped entirely.
- **The test transport returned `None`** because its request-body parameter
  shadowed the canned response. A fake that cannot tell a request from a
  response is not a fake of anything.

Each gate was then *removed on purpose* to confirm the tests fail: approval off
→ an unapproved restart is sent; redaction off → the secret appears in log
output; limiter off → 12 requests where the limit is 10.

## Phase 0 hand-off (the one thing that cannot be done from here)

The sandbox that wrote this code **cannot reach `api.render.com`** (TCP opens,
TLS is reset; `api.github.com` returns 200 as a control). So no live call has
been made, and nothing here claims otherwise.

The owner runs exactly one command:

```bash
RENDER_API_KEY=rnd_... python3 render_control_adapter/probe.py \
    --report render_control_adapter/phase0-report.json
```

It reads only, performs zero mutations (asserted, and exiting non-zero if not),
and writes one file. That file is gitignored on purpose: it contains real
service IDs and owner IDs. It answers the two questions code cannot answer from
here:

1. **Do the fixture shapes match reality?** Every endpoint is recorded as
   ok/status/latency. A shape mismatch shows up as a failure, not a silent pass.
2. **What is actually deployed?** Service count, types, regions, plans, health
   check paths, env-var *key names and lengths* only — never values.

That report becomes the fixture set for Phase 2. Until it exists, Phase 2 code
would be built on guessed shapes, which is the failure mode this whole adapter
was written to avoid.
