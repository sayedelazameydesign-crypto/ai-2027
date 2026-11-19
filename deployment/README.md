# 🌐 Running the live demo on a real URL (24/7, no laptop needed)

This turns the static GitHub Pages showcase into a **running Python server** that
anyone can open. It is a *deployment* layer only: it does not touch the kernel,
the memory store, or the Command HUD.

---

## ⚠️ Read this before you point a public URL at it

`live-agent/server.py` was built as a **local** demo. Exposed to the internet it
is **not safe**, and these are measured facts, not caution:

| What is missing | Measured consequence |
|---|---|
| No authentication on `POST /api/run` | anyone on the internet can run goals |
| No rate limiting | **~90 runs/second** from a single client |
| No disk cap (upstream) | **69.5 KB per run**, 4 files, never deleted → **~7,500 requests fill a 512 MB free tier in ~1.4 min**; ~75,000 fill a 5 GB Space in ~14 min |
| A global kernel lock | a handful of clients makes the demo unusable for everyone |

`deployment/live_server.py` fixes the part that silently takes the service down
(**disk cap + run budget**). Normal startup now preserves existing runtime files;
`--fresh` is the explicit destructive reset. This protects process restarts that
reuse the same filesystem only: it does **not** add authentication, and it says so
in its startup banner. For a public URL, run it **read-only**:

```bash
AI2027_READ_ONLY=1 python3 deployment/live_server.py
```

Read-only still serves the UI, `/api/health`, and any evidence already on disk; it
refuses `POST /api/run` with an explicit reason. If you want visitors to be able to
*run* goals, put it behind a real auth proxy and a rate limit — do not just flip the
flag.

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

HF Spaces expects port **7860**, which is the image default. Set
`AI2027_READ_ONLY=1` in the Space's variables unless you want an open write
endpoint on a public page.

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
| `AI2027_READ_ONLY` | `0` | `1` disables `POST /api/run` entirely |

## Verify it locally before deploying

```bash
python3 deployment/test_deployment.py -v      # 15 tests incl. live HTTP
AI2027_MAX_DISK_MB=1 PORT=8090 python3 deployment/live_server.py
```

The disk cap is tested by sending 60 real requests against a 1 MB cap and then
measuring the directory size — not by reading the config.

## What is NOT in this repo

The earlier draft referenced a `facebook_connector.py` that **does not exist
anywhere in this repository** (checked). There is no Facebook integration here, and
no connector can "read your open browser session" — a server-side process has no
access to your logged-in browser, by design of every major browser's security
model. If a Facebook integration is wanted, it needs its own OAuth app, its own
tokens, and its own review — see `docs/` for the honest scope of what this project
currently does.
