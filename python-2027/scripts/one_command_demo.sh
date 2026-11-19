#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VERIFY_ALL=0
LIVE_KEYS="${AI2027_LIVE_KEYS:-0}"

for arg in "$@"; do
  case "$arg" in
    --verify-all) VERIFY_ALL=1 ;;
    --live) LIVE_KEYS=1 ;;
    *)
      echo "Unknown option: $arg (expected --verify-all or --live)" >&2
      exit 2
      ;;
  esac
done

if ! command -v uv >/dev/null 2>&1; then
  if [ ! -x ".venv/bin/uv" ]; then
    "$PYTHON_BIN" -m venv .venv
    .venv/bin/pip install -q --upgrade pip uv
  fi
  UV=".venv/bin/uv"
else
  UV="uv"
fi

if [ ! -d ".venv" ]; then
  "$UV" venv .venv --python "$PYTHON_BIN"
fi

"$UV" pip install --python .venv/bin/python -q -e . --group dev

if [ "$VERIFY_ALL" -eq 1 ]; then
  .venv/bin/ruff check src tests workers/python-worker/src
  .venv/bin/ruff format --check src tests workers/python-worker/src
  .venv/bin/mypy src tests
  .venv/bin/pytest --cov-fail-under=90 -q
  if command -v node >/dev/null 2>&1; then
    node --test workers/edge-gateway/tests/gateway.test.mjs
  fi
fi

.venv/bin/ai2027-py27 demo
echo
.venv/bin/ai2027-py27 providers

if [ "$LIVE_KEYS" = "1" ]; then
  echo
  .venv/bin/ai2027-py27 live --provider all
fi
