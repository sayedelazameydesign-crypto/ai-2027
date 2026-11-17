#!/usr/bin/env bash
# Gap Audit — new-new branch vs docs/EXECUTION-ORDER-MVP.md
# Usage: bash scripts/gap-audit.sh  (run from repo root, on branch new-new)
set -uo pipefail

PASS=0; WARN=0; FAIL=0
ok()   { echo "  ✅ $1"; PASS=$((PASS+1)); }
warn() { echo "  🟡 $1"; WARN=$((WARN+1)); }
miss() { echo "  ❌ $1"; FAIL=$((FAIL+1)); }

hit() { grep -rn --include='*.ts' --include='*.tsx' -l -E "$1" src app server lib 2>/dev/null | head -5; }

echo "═══ 1. SSE Trace Stream (expected: Built) ═══"
SSE_FILES=$(hit 'text/event-stream|EventSource|createEventStream')
[ -n "$SSE_FILES" ] && ok "SSE endpoint found: $(echo $SSE_FILES | tr '\n' ' ')" || miss "No SSE endpoint (text/event-stream) found"
for EV in 'run:start' 'plan:created' 'step:start' 'step:result' 'step:error' 'run:done' 'run:failed'; do
  [ -n "$(hit "$EV")" ] && ok "event type '$EV' present" || warn "event type '$EV' missing → extend SSE union"
done

echo ""
echo "═══ 2. Tool Registry (expected: Partially built) ═══"
[ -n "$(hit 'interface Tool<')" ]        && ok "Tool<I, O> interface found"      || miss "Tool interface missing"
[ -n "$(hit 'inputSchema.*ZodType|z\.ZodType')" ] && ok "Zod schemas wired"      || miss "No Zod input/output schemas"
[ -n "$(hit 'manifest\(\)')" ]           && ok "manifest() exists"               || warn "manifest() missing → must add for planner prompt"
for T in 'shell\.exec' 'web\.fetch' 'browser\.run' 'fs\.readwrite'; do
  [ -n "$(hit "$T")" ] && ok "tool '$T' registered" || miss "tool '$T' not implemented"
done

echo ""
echo "═══ 3. Agent Loop (Module 2 contract states) ═══"
[ -f src/agent/loop.ts ] && ok "src/agent/loop.ts exists" || miss "src/agent/loop.ts missing → build state machine"
for ST in "'planning'" "'executing'" "'awaiting_approval'" "'needs_clarification'" "'plan_rejected'" "'cancelled'" 'PlanSchema' 'AbortController'; do
  [ -n "$(hit "$ST")" ] && ok "$ST referenced" || miss "$ST absent"
done
[ -f src/lib/llm.ts ] && ok "src/lib/llm.ts adapter exists" || miss "LLM adapter src/lib/llm.ts missing"

echo ""
echo "═══ 4. Sandbox Execution (expected: Spec'd only) ═══"
[ -n "$(hit 'execFile')" ]  && ok "execFile used" || miss "execFile not used yet"
BAD=$(grep -rn --include='*.ts' -E "child_process.*[^F]exec\(|exec\(.*sh -c" src server 2>/dev/null | head -3)
[ -z "$BAD" ] && ok "no raw exec()/shell interpolation" || miss "DANGER raw exec() found: $BAD"
[ -n "$(hit 'agent-runs')" ]  && ok "run-scoped sandbox dir referenced" || miss "/tmp/agent-runs/<runId>/ not wired"
[ -n "$(hit 'playwright')" ]  && ok "playwright referenced" || miss "playwright not integrated"

echo ""
echo "═══ 5. Hard-boundary violations (must be ZERO) ═══"
VIOL=$(grep -rn --include='*.ts' -i -E 'bullmq|ioredis|circuitbreaker|reranker|embedding' src server lib 2>/dev/null | grep -v node_modules | head -5)
[ -z "$VIOL" ] && ok "no excluded-scope code (Redis/BullMQ/CircuitBreaker/embeddings)" || miss "Excluded-scope code found: $VIOL"
MIG=$(find drizzle migrations -name '*.sql' -newer package.json 2>/dev/null | head -3)
[ -z "$MIG" ] && ok "no fresh migrations" || warn "recent migrations detected — verify none are new tables: $MIG"

echo ""
echo "════════════════════════════════════════"
echo " RESULT: $PASS built ✅ · $WARN partial 🟡 · $FAIL missing ❌"
echo "════════════════════════════════════════"
