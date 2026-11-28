#!/usr/bin/env bash
# AI-2027 nightly verified backup (run by ai2027-backup.timer, as root).
#
#   * webhook SQLite  -> online copy via the SQLite backup API (safe with WAL), no downtime
#   * live-agent, hud -> deployment/runtime_snapshot.py on a STOPPED service (consistent
#                        generation of records + sandboxes + ledgers + memory), restarted at once
#   * studio          -> tar of its state dir while stopped
#   * every SQLite copy must pass `PRAGMA integrity_check` or the run fails
#   * keeps AI2027_BACKUP_KEEP_DAYS local generations (default 14)
#   * optional off-VM copy: set AI2027_BACKUP_RCLONE_REMOTE in /etc/ai2027/backup.env
#     (use an rclone `crypt` remote so the provider only ever sees ciphertext)
set -euo pipefail
APP=/opt/ai2027
STATE=/var/lib/ai2027
ROOT=/var/backups/ai2027
KEEP="${AI2027_BACKUP_KEEP_DAYS:-14}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$ROOT/$TS"
umask 077
mkdir -p "$OUT"
log() { logger -t ai2027-backup "$*"; echo "$*"; }

restart_on_exit=()
cleanup() { for u in "${restart_on_exit[@]:-}"; do [ -n "$u" ] && systemctl start "$u" || true; done; }
trap cleanup EXIT

# 1. webhook — online
if [ -f "$STATE/webhook/free_stack.db" ]; then
  python3 - "$STATE/webhook/free_stack.db" "$OUT/free_stack.db" <<'PY'
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
dst.close(); src.close()
sys.exit(0 if ok == "ok" else f"integrity_check: {ok}")
PY
  log "webhook db: ok"
fi

# 2. live-agent + hud — stopped, verified snapshot
for svc in live-agent hud; do
  dir="$STATE/$svc"
  [ -d "$dir" ] && [ -n "$(ls -A "$dir" 2>/dev/null)" ] || continue
  systemctl stop "ai2027-$svc"; restart_on_exit+=("ai2027-$svc")
  python3 "$APP/deployment/runtime_snapshot.py" backup "$dir" "$OUT/$svc.tar.gz" >/dev/null
  systemctl start "ai2027-$svc"; restart_on_exit=("${restart_on_exit[@]/ai2027-$svc}")
  log "$svc snapshot: ok"
done

# 3. studio — stopped tar
if [ -d "$STATE/studio" ] && [ -n "$(ls -A "$STATE/studio" 2>/dev/null)" ]; then
  systemctl stop ai2027-studio; restart_on_exit+=("ai2027-studio")
  tar -C "$STATE" -czf "$OUT/studio.tar.gz" studio
  systemctl start ai2027-studio; restart_on_exit=("${restart_on_exit[@]/ai2027-studio}")
  log "studio archive: ok"
fi

# 4. manifest with checksums (restore drills verify against it)
( cd "$OUT" && sha256sum -- * > SHA256SUMS ) 2>/dev/null || true
git -C "$APP" rev-parse HEAD > "$OUT/REVISION"

# 5. retention
find "$ROOT" -mindepth 1 -maxdepth 1 -type d -mtime +"$KEEP" -exec rm -rf -- {} +

# 6. optional off-VM copy
if [ -n "${AI2027_BACKUP_RCLONE_REMOTE:-}" ]; then
  command -v rclone >/dev/null || { log "rclone not installed — off-VM copy SKIPPED"; exit 1; }
  rclone copy --config /etc/ai2027/rclone.conf "$OUT" "$AI2027_BACKUP_RCLONE_REMOTE/$TS"
  log "off-VM copy: ok ($AI2027_BACKUP_RCLONE_REMOTE/$TS)"
else
  log "off-VM copy not configured (AI2027_BACKUP_RCLONE_REMOTE unset) — backups live on this VM only"
fi
log "backup $TS complete"
