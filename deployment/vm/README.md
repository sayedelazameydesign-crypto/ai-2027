# deployment/vm — 24/7 on one free VM

The executable half of [`docs/DEPLOY-24-7-PLAN.md`](../../docs/DEPLOY-24-7-PLAN.md) (Arabic runbook with
owner steps, acceptance gates, RPO/RTO and risks). Target: Ubuntu 24.04 on Oracle Cloud Always Free, $0.

```bash
sudo bash bootstrap.sh --public-host <name>.duckdns.org --acme-email <you@example.com> --open-firewall
sudo bash bootstrap.sh --check      # 4 × PASS
sudo bash bootstrap.sh --ref <sha>  # update / roll back (offline gate first, auto-rollback on failure)
```

| File | Role |
|---|---|
| `bootstrap.sh` | Idempotent install **and** update: packages, `ai2027` user, pinned checkout in `/opt/ai2027`, locked venv, offline gate, env files (once, `0600`), units, Caddy, health verification, rollback |
| `systemd/ai2027-{webhook,studio,live-agent,hud}.service` | Loopback-only, unprivileged, `ProtectSystem=strict`, one `StateDirectory` each, `Restart=always` (`systemd-analyze security`: 3.2 OK) |
| `systemd/ai2027-healthcheck.{service,timer}` + `healthcheck.sh` | Every 2 min; restarts a unit after two consecutive failed probes; disk ≥ 85% warning |
| `systemd/ai2027-backup.{service,timer}` + `backup.sh` | 03:30 UTC; online SQLite copy + verified `runtime_snapshot.py` generations, `SHA256SUMS`, 14-day retention, optional rclone (crypt) off-VM copy |
| `Caddyfile` | Public edge: **only** `/webhook` and `/healthz` → `127.0.0.1:8080`; everything else 404; automatic HTTPS |
| `env/*.env.example` | Variable names only; filled on the VM in `/etc/ai2027/` |

Exposure model: Studio, Live Agent and Command HUD have no multi-user authentication and are never
proxied — use `ssh -L 8790:127.0.0.1:8790 -L 8090:127.0.0.1:8090 -L 8091:127.0.0.1:8091 <vm>`.

Contract tests (run in CI with every `scripts/test_*.py`): `python3 -m unittest scripts.test_vm_bundle`.
