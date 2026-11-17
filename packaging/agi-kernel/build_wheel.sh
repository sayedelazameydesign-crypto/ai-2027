#!/usr/bin/env bash
# Sync the untouched upstream kernel into the package tree, then build a wheel.
set -euo pipefail
cd "$(dirname "$0")"
SRC="../../projects/agi-system/core"
DST="src/agi_kernel"
rm -rf "$DST" && mkdir -p "$DST"
cp "$SRC"/*.py "$DST"/
echo "synced $(ls "$DST" | wc -l) modules from projects/agi-system/core"
python3 -m pip wheel . --no-deps --no-build-isolation -w dist -q
ls -la dist/
