#!/usr/bin/env bash
# AI-2027 health watchdog (run by ai2027-healthcheck.timer every 2 minutes, as root).
# A unit is restarted only after TWO consecutive failed probes, so one slow answer
# never causes a restart loop. Results go to the journal:
#   journalctl -t ai2027-health --since today
set -uo pipefail
STATE_DIR=/run/ai2027-health
install -d -m 0700 "$STATE_DIR"

probe() {  # probe <unit> <url>
  local unit="$1" url="$2" code fails_file="$STATE_DIR/$1.fails" fails
  code="$(curl -s -o /dev/null -m 8 -w '%{http_code}' "$url" || true)"
  if [ "$code" = "200" ]; then
    rm -f "$fails_file"; return 0
  fi
  fails=$(( $(cat "$fails_file" 2>/dev/null || echo 0) + 1 ))
  echo "$fails" > "$fails_file"
  if [ "$fails" -ge 2 ]; then
    logger -t ai2027-health -p daemon.warning "$unit unhealthy ($url -> ${code:-no answer}) x$fails: restarting"
    systemctl restart "$unit"
    rm -f "$fails_file"
  else
    logger -t ai2027-health -p daemon.notice "$unit probe failed once ($url -> ${code:-no answer})"
  fi
}

probe ai2027-webhook    http://127.0.0.1:8080/healthz
probe ai2027-studio     http://127.0.0.1:8790/v1/studio/status
probe ai2027-live-agent http://127.0.0.1:8090/api/health
probe ai2027-hud        http://127.0.0.1:8091/api/health

# Disk guard: warn early, long before SQLite starts failing writes.
used="$(df --output=pcent /var/lib/ai2027 | tail -1 | tr -dc '0-9')"
if [ -n "$used" ] && [ "$used" -ge 85 ]; then
  logger -t ai2027-health -p daemon.err "disk ${used}% full on /var/lib/ai2027"
fi
exit 0
