# 🌐 Running the live demo on a real URL (24/7, no laptop needed)

This turns the static GitHub Pages showcase into a **running Python server** that
anyone can open. It is a *deployment* layer only: it does not touch the kernel,
the memory store, or the Command HUD.

---

## ⚠️ Read this before you point a public URL at it

`live-agent/server.py` is a **local** demo and must not be exposed directly. The
deployment launcher now adds a bounded write gate; the remaining limits are explicit:

| Control / gap | Behavior |
|---|---|
| Writes without a secret | **Fail closed:** `POST /api/run` is disabled if `AI2027_WRITE_TOKEN` is absent, even when `AI2027_READ_ONLY=0` |
| Write authentication | Every enabled write requires `Authorization: Bearer …`; tokens must be at least 32 characters |
| Application rate limit | Default **10 write attempts per 60 seconds per peer IP**, in this process; spoofable `X-Forwarded-For` is ignored |
| Public reads | Run lists, evidence, memory summaries, and sandbox artifacts have **no user-level access control** |
| HTTPS | The Python server speaks HTTP; terminate TLS at a trusted host/proxy |
| Distributed / proxy protection | **Not provided:** no user accounts, shared-token identity, distributed quota, or DDoS protection |
| No disk cap (upstream) | **69.5 KB per run**, 4 files, never deleted → **~7,500 requests fill a 512 MB free tier in ~1.4 min**; ~75,000 fill a 5 GB Space in ~14 min |
| A global kernel lock | a handful of clients can make the demo unusable for everyone |

Behind a reverse proxy, Python may see the proxy's address rather than each visitor;
the process-local limiter can then throttle visitors together. Keep the host's edge
rate limit enabled and configure a trusted per-user auth proxy for public writes.

The launcher also caps disk use and runs per process. Normal startup preserves
existing runtime files; `--fresh` is the explicit destructive reset. This does not
make the service production-ready: the rate limit is process-local, and a shared
bearer token is not a user-login system.

**Read-only is the default when no write token is configured**, and the Render
blueprint explicitly sets `AI2027_READ_ONLY=1`. Read-only still serves the UI,
`/api/health`, and evidence already on disk; it refuses `POST /api/run` with HTTP
403. To enable a private API client, generate a random token locally with
`python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`, store it directly
in the hosting provider's secret manager as `AI2027_WRITE_TOKEN`, and set
`AI2027_READ_ONLY=0`. Never commit the secret, send it in chat, put it in a URL,
or embed it in public HTML/JavaScript. For interactive public writes, use a real
per-user auth proxy and proxy-level rate limiting; do not simply expose a shared
token or flip the flag.

---

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
| `AI2027_WRITE_TOKEN` | unset | writes stay disabled unless set to a secret of at least 32 characters |
| `AI2027_READ_ONLY` | `0` | `1` always disables writes; missing write token also disables writes |
| `AI2027_WRITE_RATE_LIMIT` | `10` | write attempts allowed per observed peer IP per window (1–10,000) |
| `AI2027_WRITE_RATE_WINDOW_SECONDS` | `60` | rate-limit window in seconds (1–86,400) |

## Verify it locally before deploying

```bash
python3 deployment/test_deployment.py -v      # config, HTTP auth/rate limit, and prune safety
AI2027_READ_ONLY=1 AI2027_MAX_DISK_MB=1 PORT=8090 python3 deployment/live_server.py
```

The disk cap is tested by sending 60 real requests against a 1 MB cap and then
measuring the directory size — not by reading the config. Pruning only considers
valid run IDs with matching records under the configured runtime's direct `runs/`,
`sandboxes/`, and `runs/ledgers/` directories. Ledger events must also match that run;
legacy kernel IDs are accepted only when the ledger's final record matches its sibling
record. Symlinked storage roots, mismatched records, and mismatched ledgers are left
untouched. The saved record is removed before the sandbox, and the ledger last; a
partial cleanup is logged, reported as incomplete, and never counted as a successful
prune.

## What is NOT in this repo

The earlier draft referenced a `facebook_connector.py` that **does not exist
anywhere in this repository** (checked). There is no Facebook integration here, and
no connector can "read your open browser session" — a server-side process has no
access to your logged-in browser, by design of every major browser's security
model. If a Facebook integration is wanted, it needs its own OAuth app, its own
tokens, and its own review — see `docs/` for the honest scope of what this project
currently does.
