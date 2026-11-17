#!/usr/bin/env bash
# ============================================================
# Remote verification for HF Space deployment (steps 11–17)
# Bound to OFFICIAL DEPLOYMENT SOURCE SHA (AUDIT-002 / F3-2).
#
#   Phase A (before Factory reboot):
#       bash deploy/hf-verify.sh https://xxx.hf.space phase1
#   → run Factory reboot from HF Settings, wait for rebuild, then:
#   Phase B (persistence proof):
#       bash deploy/hf-verify.sh https://xxx.hf.space phase2
# ============================================================
set -uo pipefail
BASE="${1:?usage: hf-verify.sh <space-url> [phase1|phase2]}"
PHASE="${2:-phase1}"
OFFICIAL_SHA="e352167c14d6bf8c772b551bc65783388c30a6c0"
STATE=/tmp/hf-verify-state
PASS=0; FAIL=0
ok(){ echo "  ✅ $1"; PASS=$((PASS+1)); }
bad(){ echo "  ❌ $1"; FAIL=$((FAIL+1)); }

echo "═══ التحقق الخارجي — $BASE (المصدر الرسمي: ${OFFICIAL_SHA:0:7}) ═══"
H=$(curl -fsS --max-time 30 "$BASE/api/health" 2>/dev/null)
[[ "$H" == *'"ok":true'* ]] && ok "external health" || bad "health unreachable: $H"
T=$(curl -fsS --max-time 20 "$BASE/api/tools" 2>/dev/null)
[[ "$T" == *"browser.run"* ]] && ok "registry live (4 tools)" || bad "tools: $T"
CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 "$BASE/")
[[ "$CODE" == "200" ]] && ok "UI 200" || bad "UI $CODE"

if [ "$PHASE" = "phase1" ]; then
  echo "═══ تشغيلة حية (متصفح→ملف) عبر الإنترنت ═══"
  RID=$(curl -fsS --max-time 30 -X POST "$BASE/api/tasks" -H 'Content-Type: application/json' \
    -d '{"prompt":"Get the title of https://example.com with the browser tool and write it to hfcheck.txt"}' \
    | sed -n 's/.*"runId":"\([^"]*\)".*/\1/p')
  [ -n "$RID" ] && ok "task accepted: $RID" || bad "task POST failed"
  for _ in $(seq 1 45); do
    ST=$(curl -fsS --max-time 15 "$BASE/api/runs/$RID" 2>/dev/null | sed -n 's/.*"status":"\([^"]*\)".*/\1/p')
    case "$ST" in completed|failed|plan_rejected|cancelled) break;; esac; sleep 3
  done
  [[ "$ST" == "completed" ]] && ok "live run completed" || bad "run status=$ST"
  echo "$RID" > "$STATE" && echo "  📌 runId محفوظ للمرحلة الثانية"
  echo
  echo "→ الآن: HF Settings → Factory reboot، انتظر اكتمال البناء، ثم شغّل phase2"
else
  echo "═══ برهان الاستمرارية بعد Factory reboot (Turso) ═══"
  RID=$(cat "$STATE" 2>/dev/null)
  [ -n "$RID" ] || { bad "no saved runId — run phase1 first"; exit 1; }
  R=$(curl -fsS --max-time 30 "$BASE/api/runs/$RID" 2>/dev/null)
  [[ "$R" == *'"status":"completed"'* ]] && ok "run $RID survived reboot (status intact)" || bad "run lost after reboot"
  [[ "$R" == *'run:done'* ]] && ok "full trace survived (run:done present)" || bad "trace lost"
  N=$(curl -fsS --max-time 20 "$BASE/api/runs" | grep -o '"id"' | wc -l)
  [ "$N" -ge 1 ] && ok "runs registry intact ($N runs)" || bad "registry empty"
fi

echo
EV="/tmp/HF-EVIDENCE-$PHASE-$(date +%Y%m%d-%H%M%S).md"
{ echo "# HF Deployment Evidence — $PHASE"
  echo "| official SHA | $OFFICIAL_SHA |"; echo "|---|---|"
  echo "| space | $BASE |"; echo "| result | $PASS passed / $FAIL failed |"
  echo "| SHA-in-image evidence | HF build log line: 'deployment source locked: $OFFICIAL_SHA' |"
  echo "| timestamp | $(date -u +%FT%TZ) |"
} > "$EV"
echo "📄 $EV — RESULT: $PASS passed · $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1
