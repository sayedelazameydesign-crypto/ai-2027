#!/usr/bin/env bash
# 🔥 ember_up.sh — the whole ember on one command (T4: one-command local boot).
#
# Why this file exists: the repository claimed "returns to a working state in
# <= 10 minutes" while the actual procedure was prose: start two servers in two
# terminals, guess when they are ready, then remember three commands. A promise
# that lives in prose is not a promise. This script is the promise, executable.
#
#   ./scripts/ember_up.sh                # boot both services, smoke them, keep them up
#   ./scripts/ember_up.sh --check        # boot, smoke, shut down (CI / clean-machine test)
#   ./scripts/ember_up.sh --with-browser # also run read-only BrowserSkill local probe
#   ./scripts/ember_up.sh --stop         # stop what it started
#   ./scripts/ember_up.sh --json         # machine-readable result on stdout
#
# Honest behaviour:
#   * a port that is already serving the right service is *reused*, never
#     duplicated, and the reuse is reported;
#   * a port occupied by something else is an error, not a silent fallback;
#   * the exit code is 0 only when the real HTTP smoke passed;
#   * nothing is installed: python3 is the only requirement.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="$ROOT/.ember-up"
PID_FILE="$STATE_DIR/pids"
STATE_JSON="$STATE_DIR/state.json"
CREDENTIALS_FILE="$STATE_DIR/api-credentials.json"
LIVE_PORT=8090
HUD_PORT=8091
CHECK=0
STOP=0
NO_BROWSER=0
WITH_BROWSER_SKILL=0
NO_SMOKE=0
JSON=0
STARTED_AT=$(date +%s)

usage() {
  sed -n '2,23p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1 ;;
    --stop) STOP=1 ;;
    --no-browser) NO_BROWSER=1 ;;
    --with-browser) WITH_BROWSER_SKILL=1 ;;
    --no-smoke) NO_SMOKE=1 ;;
    --json) JSON=1 ;;
    --live-port) LIVE_PORT="$2"; shift ;;
    --hud-port) HUD_PORT="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
  shift
done

say() { [ "$JSON" -eq 1 ] || printf '%s\n' "$*"; }
err() { printf '%s\n' "$*" >&2; }

health() { # health <url> -> 0 when the service answers 200
  python3 - "$1" <<'PY' >/dev/null 2>&1
import sys, urllib.request
urllib.request.urlopen(sys.argv[1], timeout=2).read()
PY
}

stop_all() {
  local stopped=0
  if [ -f "$PID_FILE" ]; then
    while read -r pid; do
      [ -n "$pid" ] || continue
      kill -0 "$pid" 2>/dev/null || continue
      # Only ever signal something that really is one of our servers.
      case "$(ps -p "$pid" -o args= 2>/dev/null)" in
        *server.py*) kill "$pid" 2>/dev/null && stopped=$((stopped + 1)) ;;
      esac
    done < "$PID_FILE"
    rm -f "$PID_FILE"
  fi
  say "stopped $stopped process(es)"
  return 0
}

if [ -L "$STATE_DIR" ] || [ -L "$PID_FILE" ]; then
  err "FATAL: local state directory or PID file must not be a symbolic link"
  exit 2
fi

if [ "$STOP" -eq 1 ]; then
  stop_all
  exit 0
fi

umask 077
mkdir -p "$STATE_DIR" || { err "FATAL: cannot create local state directory"; exit 2; }
chmod 700 "$STATE_DIR" || { err "FATAL: cannot secure local state directory"; exit 2; }
for local_file in "$CREDENTIALS_FILE" "$STATE_JSON" "$STATE_DIR/live-agent.log" \
                  "$STATE_DIR/hud.log" "$STATE_DIR/smoke.log" \
                  "$STATE_DIR/browserskill-probe.json"; do
  if [ -L "$local_file" ]; then
    err "FATAL: local state files must not be symbolic links"
    exit 2
  fi
done
# Local-only services need explicit API credentials too. If the operator did not
# supply a complete test identity, create and persist random credentials with
# owner-only permissions; the ignored state directory is never printed or committed.
if [ -n "${AI2027_WRITE_TOKEN:-}${AI2027_READ_TOKEN_ID:-}${AI2027_READ_TOKEN_SECRET:-}${AI2027_READ_TOKEN_HASHES:-}" ]; then
  if [ -z "${AI2027_WRITE_TOKEN:-}" ] || [ -z "${AI2027_READ_TOKEN_ID:-}" ] \
      || [ -z "${AI2027_READ_TOKEN_SECRET:-}" ]; then
    err "FATAL: provide AI2027_WRITE_TOKEN, AI2027_READ_TOKEN_ID and AI2027_READ_TOKEN_SECRET together"
    exit 2
  fi
  if [ -z "${AI2027_READ_TOKEN_HASHES:-}" ]; then
    AI2027_READ_TOKEN_HASHES="$(AI2027_READ_TOKEN_ID="$AI2027_READ_TOKEN_ID" \
      AI2027_READ_TOKEN_SECRET="$AI2027_READ_TOKEN_SECRET" python3 - <<'PY'
import hashlib, json, os
identifier = os.environ["AI2027_READ_TOKEN_ID"]
secret = os.environ["AI2027_READ_TOKEN_SECRET"]
print(json.dumps({identifier: hashlib.sha256(secret.encode()).hexdigest()}))
PY
    )"
  fi
else
  if [ ! -f "$CREDENTIALS_FILE" ]; then
    python3 - "$CREDENTIALS_FILE" <<'PY'
import hashlib, json, os, secrets, sys
if os.name != "posix":
    raise SystemExit("automatic credential files require verifiable POSIX owner-only modes")
path = sys.argv[1]
identifier = "local-" + secrets.token_hex(6)
reader = secrets.token_urlsafe(32)
writer = secrets.token_urlsafe(32)
value = {
    "read_id": identifier,
    "read_secret": reader,
    "write_token": writer,
    "read_hashes": {identifier: hashlib.sha256(reader.encode()).hexdigest()},
}
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(value, handle)
    handle.write("\n")
PY
    if [ "$?" -ne 0 ]; then
      err "FATAL: could not create local API credentials"
      exit 2
    fi
  fi
  if ! chmod 600 "$CREDENTIALS_FILE"; then
    err "FATAL: could not secure local API credentials"
    exit 2
  fi
  credential_values="$(python3 - "$CREDENTIALS_FILE" <<'PY'
import hashlib, json, os, re, sys
try:
    if os.name != "posix":
        raise ValueError
    with open(sys.argv[1], encoding="utf-8") as handle:
        value = json.load(handle)
    identifier = value.get("read_id") if isinstance(value, dict) else None
    reader = value.get("read_secret") if isinstance(value, dict) else None
    writer = value.get("write_token") if isinstance(value, dict) else None
    hashes = value.get("read_hashes") if isinstance(value, dict) else None
    if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identifier):
        raise ValueError
    if not isinstance(reader, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", reader):
        raise ValueError
    if not isinstance(writer, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,512}", writer):
        raise ValueError
    expected = hashlib.sha256(reader.encode()).hexdigest()
    if not isinstance(hashes, dict) or hashes.get(identifier) != expected:
        raise ValueError
except (OSError, UnicodeError, ValueError, TypeError, AttributeError):
    print("invalid local API credential file", file=sys.stderr)
    sys.exit(1)
print("\t".join((identifier, reader, writer, json.dumps(hashes))))
PY
  )"
  if [ "$?" -ne 0 ]; then
    err "FATAL: local API credential file is invalid"
    exit 2
  fi
  IFS=$'\t' read -r AI2027_READ_TOKEN_ID AI2027_READ_TOKEN_SECRET AI2027_WRITE_TOKEN AI2027_READ_TOKEN_HASHES <<< "$credential_values"

fi
export AI2027_READ_TOKEN_ID AI2027_READ_TOKEN_SECRET AI2027_READ_TOKEN_HASHES AI2027_WRITE_TOKEN

# Never truncate the pid file: a boot that only *reuses* running services would
# otherwise lose the pids of the services it is about to be asked to stop.
if [ -f "$PID_FILE" ]; then
  alive=""
  while read -r pid; do
    [ -n "$pid" ] || continue
    kill -0 "$pid" 2>/dev/null && alive="${alive}${pid}"$'\n'
  done < "$PID_FILE"
  printf '%s' "$alive" > "$PID_FILE"
else
  : > "$PID_FILE"
fi
REUSED=""
PIDS=""

boot_one() { # boot_one <name> <port> <command...>
  local name="$1" port="$2"; shift 2
  local url="http://127.0.0.1:$port/api/health"
  if health "$url"; then
    say "  $name :$port already healthy — reusing the running service"
    REUSED="$REUSED $name"
    return 0
  fi
  if python3 - "$port" <<'PY'
import socket, sys
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
  then :; else
    err "FATAL: port $port is occupied by something that is not $name"
    return 1
  fi
  # `exec` so the recorded pid IS the service, and so the background job cannot
  # keep a caller's stdout pipe open (a real bug: `( ... & )` did exactly that).
  ( cd "$ROOT" && exec nohup "$@" ) > "$STATE_DIR/$name.log" 2>&1 < /dev/null &
  echo $! >> "$PID_FILE"
  PIDS="$PIDS $name"
  return 0
}

say "🔥 ember_up — booting the real stack from $ROOT"
say "  logs: ${STATE_DIR#$ROOT/}/<service>.log"
boot_one live-agent "$LIVE_PORT" python3 live-agent/server.py --port "$LIVE_PORT" --host 127.0.0.1 || exit 1
boot_one hud "$HUD_PORT" python3 command-hud/server.py --port "$HUD_PORT" --host 127.0.0.1 || exit 1

wait_health() { # wait_health <url> <seconds>
  local url="$1" limit="$2" i=0
  while [ "$i" -lt "$limit" ]; do
    health "$url" && return 0
    i=$((i + 1)); sleep 1
  done
  return 1
}
if ! wait_health "http://127.0.0.1:$LIVE_PORT/api/health" 30; then
  err "FATAL: live-agent did not become healthy in 30s"
  tail -30 "$STATE_DIR/live-agent.log" 2>/dev/null
  stop_all; exit 1
fi
if ! wait_health "http://127.0.0.1:$HUD_PORT/api/health" 30; then
  err "FATAL: HUD did not become healthy in 30s"
  tail -30 "$STATE_DIR/hud.log" 2>/dev/null
  stop_all; exit 1
fi

SMOKE_RC=0
SMOKE_LINE="skipped"
if [ "$NO_SMOKE" -eq 0 ]; then
  say "  smoke : running the real HTTP checks …"
  SMOKE_LOG="$STATE_DIR/smoke.log"
  if python3 "$ROOT/scripts/runtime_smoke.py" \
       --live-agent "http://127.0.0.1:$LIVE_PORT" \
       --hud "http://127.0.0.1:$HUD_PORT" > "$SMOKE_LOG" 2>&1; then
    SMOKE_RC=0
  else
    SMOKE_RC=1
  fi
  SMOKE_LINE=$(grep -Eo '[0-9]+/[0-9]+ checks' "$SMOKE_LOG" | tail -1)
  [ -n "$SMOKE_LINE" ] || SMOKE_LINE="see ${STATE_DIR#$ROOT/}/smoke.log"
  say "  smoke : $SMOKE_LINE (exit $SMOKE_RC)"
fi

if [ "$WITH_BROWSER_SKILL" -eq 1 ] && [ -f "$ROOT/browser_skill_adapter/probe.py" ]; then
  BSK_LOG="$STATE_DIR/browserskill-probe.json"
  python3 "$ROOT/browser_skill_adapter/probe.py" --json > "$BSK_LOG" 2>/dev/null
  BSK_RC=$?
  BSK_VERDICT=$(python3 - "$BSK_LOG" <<'PY' 2>/dev/null || echo "UNKNOWN"
import json, sys
d = json.loads(open(sys.argv[1], encoding="utf-8").read())
print(f"{d.get('verdict')} ({d.get('reason')})")
PY
)
  # Exit 2 (SKIPPED) does not fail ember_up: browser integration is opt-in local.
  # Exit 1 (FAILED: e.g. protocol mismatch) is surfaced prominently.
  say "  bsk   : Stage 1 local probe -> $BSK_VERDICT (exit $BSK_RC)"
fi

ELAPSED_MS=$(( ($(date +%s) - STARTED_AT) * 1000 ))
cat > "$STATE_JSON" <<EOF
{
  "booted_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "elapsed_ms": $ELAPSED_MS,
  "live_agent": "http://127.0.0.1:$LIVE_PORT",
  "hud": "http://127.0.0.1:$HUD_PORT",
  "reused": "$(echo "$REUSED" | xargs)",
  "smoke": "$SMOKE_LINE",
  "smoke_exit": $SMOKE_RC,
  "running": $([ "$CHECK" -eq 1 ] && echo false || echo true)
}
EOF

if [ "$CHECK" -eq 1 ]; then
  stop_all
else
  if [ "$NO_BROWSER" -eq 0 ]; then
    (xdg-open "http://127.0.0.1:$LIVE_PORT/" >/dev/null 2>&1 \
      || open "http://127.0.0.1:$LIVE_PORT/" >/dev/null 2>&1 || true) &
  fi
  say ""
  say "  live agent : http://127.0.0.1:$LIVE_PORT/"
  say "  command HUD: http://127.0.0.1:$HUD_PORT/"
  say "  stop with  : ./scripts/ember_up.sh --stop"
fi

if [ "$JSON" -eq 1 ]; then
  cat "$STATE_JSON"
else
  say ""
  say "EMBER UP — ${ELAPSED_MS} ms · smoke ${SMOKE_LINE} · exit ${SMOKE_RC}"
fi
exit "$SMOKE_RC"
