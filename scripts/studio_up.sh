#!/usr/bin/env bash
# Launch the unified AI-2027 Agent Studio (Manus+Claude Workspace + L0/L1/L4/L5).
#
#   ./scripts/studio_up.sh            # install/verify python-2027 venv and start Studio on :8080
#   ./scripts/studio_up.sh --check    # boot, run HTTP + autonomous task smoke, and shut down
#   ./scripts/studio_up.sh --port 8790
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY27="$ROOT/python-2027"
HOST="0.0.0.0"
PORT="8080"
CHECK=0

while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1 ;;
    --host) HOST="$2"; shift ;;
    --port) PORT="$2"; shift ;;
    -h|--help)
      sed -n '2,6p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

cd "$PY27"
if ! command -v uv >/dev/null 2>&1; then
  if [ ! -x ".venv/bin/uv" ]; then
    python3 -m venv .venv
    .venv/bin/pip install -q --upgrade pip uv
  fi
  UV=".venv/bin/uv"
else
  UV="uv"
fi

if [ ! -d ".venv" ]; then
  "$UV" venv .venv --python python3
fi
"$UV" pip install --python .venv/bin/python -q -e .

if [ "$CHECK" -eq 0 ]; then
  echo "Starting AI-2027 Agent Studio at http://${HOST}:${PORT}/studio"
  exec .venv/bin/ai2027-py27 --root "$ROOT" studio --host "$HOST" --port "$PORT"
fi

LOG_FILE="$(mktemp)"
.venv/bin/ai2027-py27 --root "$ROOT" studio --host 127.0.0.1 --port "$PORT" >"$LOG_FILE" 2>&1 &
PID=$!
cleanup() {
  local rc=$?
  kill "$PID" >/dev/null 2>&1 || true
  wait "$PID" >/dev/null 2>&1 || true
  if [ "$rc" -ne 0 ] && [ -f "$LOG_FILE" ]; then
    cat "$LOG_FILE" >&2
  fi
  rm -f "$LOG_FILE"
}
trap cleanup EXIT

python3 - "$PORT" <<'PY'
import json, sys, time, urllib.request

port = sys.argv[1]
base = f"http://127.0.0.1:{port}"
deadline = time.time() + 15
while time.time() < deadline:
    try:
        with urllib.request.urlopen(f"{base}/v1/studio/status", timeout=2) as r:
            st = json.loads(r.read())
            assert st["kernel_available"] is True
            assert st["cost_usd"] == 0.0
            break
    except Exception:
        time.sleep(0.25)
else:
    raise SystemExit("Studio did not respond in time")

req = urllib.request.Request(
    f"{base}/v1/studio/sessions",
    data=json.dumps({"title": "smoke"}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=5) as r:
    sess = json.loads(r.read())
sid = sess["session_id"]

run_req = urllib.request.Request(
    f"{base}/v1/studio/sessions/{sid}/run",
    data=json.dumps({"task": "Inspect workspace and produce verified HTML artifact", "max_steps": 4}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(run_req, timeout=15) as r:
    state = json.loads(r.read())
assert state["status"] == "completed", state
assert len(state["artifacts"]) >= 1
print(f"STUDIO CHECK PASS: session={sid} steps={len(state['steps'])} artifacts={len(state['artifacts'])}")
PY
