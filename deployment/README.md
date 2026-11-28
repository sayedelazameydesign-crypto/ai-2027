# 🌐 Running the live demo on a real URL (24/7, no laptop needed)

This turns the static GitHub Pages showcase into a **running Python server** that
anyone can open. It is a *deployment* layer only: it does not touch the kernel,
the memory store, or the Command HUD.

---

## ⚠️ Read this before you point a public URL at it

`live-agent/server.py` is a **local** demo. The deployment launcher adds bounded,
fail-closed API controls; these do not make it a multi-user production service:

| Control / gap | Behavior |
|---|---|
| Public API | Only `GET /api/health` is public; it returns a minimal status object |
| Private reads | Every other `/api/` read (runs, run IDs, files, memory, search, metrics, agent card) requires a reader ID plus secret whose SHA-256 digest is configured server-side; an empty reader map denies all reads |
| Writes | `POST /api/run` requires `Authorization: Bearer <AI2027_WRITE_TOKEN>`; missing configuration fails closed, and `AI2027_READ_ONLY=1` overrides it |
| Application rate limit | Default **120 reads and 10 write attempts per 60 seconds per peer IP**, in this process; spoofable `X-Forwarded-For` is ignored |
| TLS | The Python server speaks HTTP; terminate TLS at a trusted host/proxy before sending credentials |
| Multi-user / edge protection | **Not provided:** bearer credentials are not user sessions or per-object authorization; quotas are process-local and there is no DDoS protection |
| No disk cap (upstream) | **69.5 KB per run**, 4 files, never deleted → **~7,500 requests fill a 512 MB free tier in ~1.4 min**; ~75,000 fill a 5 GB Space in ~14 min |
| A global kernel lock | a handful of clients can make the demo unusable for everyone |

Behind a reverse proxy, Python may see the proxy's address rather than each visitor;
the process-local limiter can then throttle visitors together. Keep a trusted edge
rate limit enabled. For public use, place a real authentication and authorization
gateway in front of the service and do not distribute a shared bearer token.

The launcher also caps disk use and runs per process. Normal startup preserves
existing runtime files; `--fresh` is the explicit destructive reset. The optional
reader IDs are interim API credentials, not user identity, and should not be used as
a final multi-user access model.

**Writes are disabled unless an operator token is configured**, and the Render
blueprint explicitly sets `AI2027_READ_ONLY=1`. The page and minimal `/api/health`
remain available; all private read routes fail closed until reader hashes are
configured. To enable a private *test* client, store an operator write token in the
host's secret manager as `AI2027_WRITE_TOKEN` and set `AI2027_READ_ONLY=0` only
when approved. Reader clients send `Authorization: Bearer <token-id>.<secret>`;
`AI2027_READ_TOKEN_HASHES` contains a JSON object mapping token IDs to SHA-256
hex digests of high-entropy random secrets. Generate credentials offline, keep the
plaintext only in the approved secret manager/client, and never commit, chat, log,
URL-encode, or embed them in public HTML/JavaScript. The static UI accepts them in
password fields for the current page session only; use it only behind the approved
HTTPS gateway. A shared bearer token is not a substitute for gateway user identity,
authorization, or per-user quotas.

---

> **Need it always on, with durable storage (e.g. the WhatsApp/YCloud webhook)?** Render's free
> tier sleeps after 15 idle minutes and has no persistent disk. Use the single-VM bundle in
> [`deployment/vm/`](vm/) and its runbook [`docs/DEPLOY-24-7-PLAN.md`](../docs/DEPLOY-24-7-PLAN.md).

## Option A — Render (free tier)

`render.yaml` is a ready blueprint. In Render: **New → Blueprint → pick this repo**.

```
services:
  - type: web
    name: ai-2027-live-agent
    runtime: python
    startCommand: "python3 deployment/live_server.py"
    healthCheckPath: /api/health
```

Two things that will bite if you copy them from elsewhere:

* **`python3 -m live_agent.server` does not work.** There is no `live_agent`
  package — the server is `live-agent/server.py`, and `live-agent` is not a valid
  module path (hyphen). The launcher loads the file explicitly.
* **The `services:` list must be one complete item.** A common typo is

  ```yaml
  services:
    - type: web
    - name: ai-2027-live-agent      # ← starts a SECOND service
      startCommand: ...
  ```

  which parses as *two* services: one with only a `type`, and one with everything
  else but **no `type`**. Render cannot deploy either. `test_deployment.py` asserts
  the file yields exactly one complete service.

You do not need a build step: there are **zero dependencies**, and
`requirements.txt` is deliberately empty of packages.

### Storage and redeploy durability

The blueprint has **no persistent disk** configured. Its free plan therefore does
not guarantee that runs, sandboxes, or memory survive an instance replacement,
redeploy, or migration; this launcher only promises recovery from a process restart
while the same runtime filesystem is retained. Verify the host's disk/mount lifecycle
before treating it as durable. On a host that provides persistent storage, set
`AI2027_LIVE_RUNTIME_DIR` to its mounted path; the Render blueprint does not set
one. To request a clean local runtime explicitly, start with
`python3 deployment/live_server.py --fresh`; never add that flag to a routine
production restart. The stdlib process-level regression test is
`python3 scripts/test_runtime_restart.py -v`: it runs both real HTTP services
against temporary runtime directories, terminates them, starts new OS processes,
and checks records, history, artifacts, hashes, and memory through the APIs.

The persistence proposal and operational backup/restore runbook are in
[`PERSISTENCE.md`](PERSISTENCE.md). No Render or hosting settings were changed.
For a stopped/quiesced runtime, the stdlib snapshot tool creates a checksummed,
no-overwrite archive and restores only into a new directory:

```bash
python3 deployment/runtime_snapshot.py backup /secure/runtime /secure/staging/live-agent.tar.gz
python3 deployment/runtime_snapshot.py restore /secure/staging/live-agent.tar.gz /secure/restore/live-agent
python3 deployment/test_runtime_snapshot.py -v
```

The archive is **not encrypted** and contains user data; protect/encrypt it using
an approved storage system before leaving the trusted host. Checksums detect
accidental corruption but do not authenticate an archive against a malicious
replacement.

## Option B — Hugging Face Spaces (Docker)

Create a Space → **Docker** → blank. Push `Dockerfile`,
`deployment/`, `live-agent/`, and `projects/agi-system/core/`.

HF Spaces expects port **7860**, which is the image default. Keep
`AI2027_READ_ONLY=1` for a public demo. The launcher also disables writes when
`AI2027_WRITE_TOKEN` is absent; if you configure a token, never expose it in the
browser bundle or public page.

## Option C — any Docker host

```bash
docker build -t ai-2027 .
docker run -p 7860:7860 -e AI2027_MAX_DISK_MB=256 ai-2027
```

The image runs as an unprivileged user, uses exec-form `CMD` so `SIGTERM` stops it
cleanly, and installs nothing.

---

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `8090` | listening port (injected by most PaaS) |
| `HOST` | `0.0.0.0` on PaaS, `127.0.0.1` locally | bind address |
| `AI2027_MAX_DISK_MB` | `256` | cap for generated evidence; oldest runs pruned first |
| `AI2027_MAX_RUNS` | `5000` | hard cap on runs per process |
| `AI2027_WRITE_TOKEN` | unset | writes stay disabled unless set to 32-512 visible ASCII characters (`0x21`-`0x7e`), with no whitespace; symbols such as `+`, `/`, and `=` are accepted |
| `AI2027_READ_ONLY` | `0` | `1` always disables writes; missing write token also disables writes |
| `AI2027_READ_TOKEN_HASHES` | unset | JSON map from reader token ID to SHA-256 hex digest; unset means private reads fail closed |
| `AI2027_READ_RATE_LIMIT` | `120` | private read attempts per observed peer IP per window (1–10,000) |
| `AI2027_READ_RATE_WINDOW_SECONDS` | `60` | read rate-limit window in seconds (1–86,400) |
| `AI2027_WRITE_RATE_LIMIT` | `10` | write attempts allowed per observed peer IP per window (1–10,000) |
| `AI2027_WRITE_RATE_WINDOW_SECONDS` | `60` | write rate-limit window in seconds (1–86,400) |

## Verify it locally before deploying

```bash
python3 deployment/test_deployment.py -v      # config, HTTP auth/rate limit, cleanup and prune safety
python3 deployment/test_runtime_snapshot.py -v # offline backup/restore + storage-safety tests
AI2027_READ_ONLY=1 AI2027_MAX_DISK_MB=1 PORT=8090 python3 deployment/live_server.py
```

The disk cap is tested by sending 60 real requests against a 1 MB cap and then
measuring the directory size — not by reading the config. Pruning only considers
valid run IDs with matching records under the configured runtime's direct `runs/`,
`sandboxes/`, and `runs/ledgers/` directories. Ledger events must also match that run;
legacy kernel IDs are accepted only when the ledger's final record matches its sibling
record. Symlinked storage roots, mismatched records, and mismatched ledgers are left
untouched. The saved record is removed before the sandbox, and the ledger last.
`POST /api/run` returns a path-free `cleanup` object (`complete`, `partial`, or
`failed`) with stable warning codes; a failed/partial cleanup is never represented
as complete. Structured logs contain only request IDs, canonical routes, methods,
status codes, durations, and sanitized cleanup warning codes.

## What is NOT in this repo

The earlier draft referenced a `facebook_connector.py` that **does not exist
anywhere in this repository** (checked). There is no Facebook integration here, and
no connector can "read your open browser session" — a server-side process has no
access to your logged-in browser, by design of every major browser's security
model. If a Facebook integration is wanted, it needs its own OAuth app, its own
tokens, and its own review — see `docs/` for the honest scope of what this project
currently does.
