#!/usr/bin/env bash
# ============================================================
# Production smoke tests + rollback evidence — steps 6–9 of the
# approved deployment sequence (AUDIT-001 aftermath).
#
# Run ON the Oracle VM after oracle-setup.sh and after placing
# the NEW Groq key in /opt/platform/.env:
#     bash deploy/smoke-test.sh                 # local checks (step 6)
#     bash deploy/smoke-test.sh https://your.domain   # + external (steps 7–8)
# ============================================================
set -uo pipefail

BASE_LOCAL="http://localhost:3000"
EXTERNAL="${1:-}"
APP_DIR="${APP_DIR:-/opt/platform}"
PASS=0; FAIL=0
ok()   { echo "  ✅ $1"; PASS=$((PASS+1)); }
bad()  { echo "  ❌ $1"; FAIL=$((FAIL+1)); }

echo "═══ Step 0 — Deployment-source integrity (FAIL-CLOSED) ═══"
LOCK=$(cat "$APP_DIR/.deploy-lock" 2>/dev/null || true)
HEAD_SHA=$(git -C "$APP_DIR" rev-parse HEAD 2>/dev/null || true)
[ -n "$LOCK" ] && ok "deploy-lock present: $LOCK" || bad "no .deploy-lock — setup did not lock a SHA"
[ -n "$HEAD_SHA" ] && [ "$HEAD_SHA" = "$LOCK" ] \
  && ok "HEAD matches locked SHA" || bad "HEAD ($HEAD_SHA) != locked SHA ($LOCK) — env mutated after lock"
git -C "$APP_DIR" symbolic-ref -q HEAD >/dev/null 2>&1 \
  && bad "HEAD is on a branch (moving ref) — pinned detached checkout violated" \
  || ok "HEAD is detached (pinned)"

echo "═══ Step 6 — App health ON the VM ═══"
H=$(curl -fsS --max-time 5 "$BASE_LOCAL/api/health" 2>/dev/null)
[[ "$H" == *'"ok":true'* ]] && ok "health: $H" || bad "health endpoint unreachable"

T=$(curl -fsS --max-time 5 "$BASE_LOCAL/api/tools" 2>/dev/null)
[[ "$T" == *"shell.exec"* && "$T" == *"browser.run"* ]] && ok "tool registry: 4 tools" || bad "tools endpoint wrong: $T"

# real end-to-end run (browser + fs — no approval gate needed)
RID=$(curl -fsS -X POST "$BASE_LOCAL/api/tasks" -H 'Content-Type: application/json' \
  -d '{"prompt":"Get the title of https://example.com with the browser tool and write it to smoke.txt"}' \
  | sed -n 's/.*"runId":"\([^"]*\)".*/\1/p')
[ -n "$RID" ] && ok "task accepted: run $RID" || bad "task POST failed"
for _ in $(seq 1 30); do
  ST=$(curl -fsS "$BASE_LOCAL/api/runs/$RID" 2>/dev/null | sed -n 's/.*"status":"\([^"]*\)".*/\1/p')
  [[ "$ST" == "completed" || "$ST" == "failed" || "$ST" == "plan_rejected" ]] && break
  sleep 2
done
[[ "$ST" == "completed" ]] && ok "live run completed (status=$ST)" || bad "live run status=$ST"
grep -q "Example Domain" "/tmp/agent-runs/$RID/smoke.txt" 2>/dev/null \
  && ok "artifact smoke.txt contains 'Example Domain'" || bad "artifact missing/wrong"

echo
echo "═══ Step 9 — Backup / restore evidence (SQLite) ═══"
DB="$APP_DIR/sqlite.db"
if [ -f "$DB" ]; then
  BK="/tmp/rollback-evidence-$(date +%s).db"
  sqlite3 "$DB" ".backup $BK" 2>/dev/null || cp "$DB" "$BK"
  ROWS_ORIG=$(sqlite3 "$DB" "SELECT count(*) FROM runs;" 2>/dev/null)
  ROWS_BK=$(sqlite3 "$BK" "SELECT count(*) FROM runs;" 2>/dev/null)
  [[ -n "$ROWS_ORIG" && "$ROWS_ORIG" == "$ROWS_BK" ]] \
    && ok "backup restorable: runs=$ROWS_BK matches live=$ROWS_ORIG ($BK)" \
    || bad "backup verification failed (orig=$ROWS_ORIG bk=$ROWS_BK)"
  echo "  ↩ rollback drill: sudo systemctl stop platform && cp $BK $DB && sudo systemctl start platform"
else
  bad "sqlite.db not found at $DB"
fi

if [ -n "$EXTERNAL" ]; then
  echo
  echo "═══ Steps 7–8 — External reachability (run from OUTSIDE too) ═══"
  EH=$(curl -fsS --max-time 10 "$EXTERNAL/api/health" 2>/dev/null)
  [[ "$EH" == *'"ok":true'* ]] && ok "external health: $EXTERNAL" || bad "external unreachable: $EXTERNAL"
  CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$EXTERNAL/")
  [[ "$CODE" == "200" ]] && ok "external UI: 200" || bad "external UI: $CODE"
else
  echo
  echo "↷ external check skipped — pass your public URL as \$1 (and ALSO run curl from your own machine)"
fi

echo
echo "════════════════════════════════════════"
echo " SMOKE RESULT: $PASS passed · $FAIL failed"
# FAIL-CLOSED gate: eligible requires zero failures AND external check ran
# AND rollback evidence produced. Anything missing → BLOCKED.
GATE="BLOCKED"
if [ "$FAIL" -eq 0 ] && [ -n "$EXTERNAL" ] && [ -n "${BK:-}" ]; then GATE="eligible"; fi
echo " → PRODUCTION-READY gate: $GATE"
[ "$GATE" = "BLOCKED" ] && [ "$FAIL" -eq 0 ] && [ -z "$EXTERNAL" ] \
  && echo "   (reason: external reachability not verified — required evidence)"
[ "$GATE" = "BLOCKED" ] && [ "$FAIL" -eq 0 ] && [ -z "${BK:-}" ] \
  && echo "   (reason: rollback evidence missing — required evidence)"
echo "════════════════════════════════════════"

# ── PRODUCTION-READY evidence record: gate result bound to an exact version ──
EV="/tmp/PRODUCTION-READY-EVIDENCE-$(date +%Y%m%d-%H%M%S).md"
{
  echo "# PRODUCTION-READY Evidence Record"
  echo "| Field | Value |"
  echo "|---|---|"
  echo "| deployed SHA | $(git -C "$APP_DIR" rev-parse HEAD 2>/dev/null || echo unknown) |"
  echo "| detached (pinned)? | $(git -C "$APP_DIR" symbolic-ref -q HEAD >/dev/null 2>&1 && echo 'NO — VIOLATION (branch checkout)' || echo 'yes — detached') |"
  echo "| runtime | node $(node -v 2>/dev/null) · npm $(npm -v 2>/dev/null) |"
  echo "| environment fingerprint | $(uname -srm) · $(lsb_release -ds 2>/dev/null || echo n/a) · host $(hostname) |"
  echo "| smoke-test result | $PASS passed / $FAIL failed |"
  echo "| gate verdict | $GATE |"
  echo "| external check | ${EXTERNAL:-'not run — REQUIRED before gate'} |"
  echo "| rollback evidence | ${BK:-'none — REQUIRED before gate'} (runs=$ROWS_BK verified vs live=$ROWS_ORIG) |"
  echo "| timestamp (UTC) | $(date -u +%FT%TZ) |"
} > "$EV"
echo " 📄 evidence record: $EV"
cat "$EV"
exit $([ "$GATE" = "eligible" ] && echo 0 || echo 1)
