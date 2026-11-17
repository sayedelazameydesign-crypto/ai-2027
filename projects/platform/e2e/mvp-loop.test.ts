/**
 * Acceptance Verification Suite — EXECUTION-ORDER-MVP.md §6.
 * Run with the dev server up on :3000:  npm run e2e
 */
import fs from 'node:fs/promises';

const BASE = process.env.BASE_URL ?? 'http://localhost:3000';

interface TraceEvent { type: string; [k: string]: unknown }

let passed = 0;
let failed = 0;

function assert(cond: unknown, label: string): void {
  if (cond) { console.log(`  ✅ ${label}`); passed++; }
  else { console.error(`  ❌ ${label}`); failed++; }
}

async function collectSse(runId: string, timeoutMs = 120_000): Promise<TraceEvent[]> {
  const res = await fetch(`${BASE}/api/runs/${runId}/events`);
  if (!res.ok || !res.body) throw new Error(`SSE HTTP ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const events: TraceEvent[] = [];
  let buf = '';
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf('\n\n')) !== -1) {
      const block = buf.slice(0, idx); buf = buf.slice(idx + 2);
      const dataLine = block.split('\n').find((l) => l.startsWith('data: '));
      if (dataLine) events.push(JSON.parse(dataLine.slice(6)) as TraceEvent);
    }
    const last = events[events.length - 1]?.type;
    if (last === 'run:done' || last === 'run:failed' || last === 'run:cancelled' || last === 'run:needs_clarification') break;
  }
  return events;
}

async function waitPendingApproval(runId: string, timeoutMs = 15_000): Promise<{ id: string; tool: string; stepIndex: number; planHash: string }> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const d = (await (await fetch(`${BASE}/api/approvals?runId=${runId}&status=pending`)).json()) as {
      approvals: { id: string; tool: string; stepIndex: number; planHash: string }[];
    };
    if (d.approvals?.length) return d.approvals[0];
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error('no pending approval appeared');
}

async function decide(approvalId: string, decision: 'approve' | 'reject', reason?: string) {
  return fetch(`${BASE}/api/approvals/${approvalId}/decide`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decision, reason }),
  });
}

async function main() {
  console.log('═══ MVP Loop Acceptance Suite ═══\n');

  const registry = ((await (await fetch(`${BASE}/api/tools`)).json()) as { tools: string[] }).tools;
  console.log(`Registry tools: ${registry.join(', ')}\n`);

  /* ---- Happy path (§6.1–6.5) ---- */
  console.log('▶ Happy path');
  const taskRes = await fetch(`${BASE}/api/tasks`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      prompt: 'Fetch https://example.com, extract the page title with the browser tool, and write it to result.txt in the sandbox.',
    }),
  });
  assert(taskRes.ok, '6.1 POST /api/tasks accepted');
  const { runId } = (await taskRes.json()) as { runId: string };

  const events = await collectSse(runId);
  const types = events.map((e) => e.type);
  console.log(`  trace: ${types.join(' → ')}`);

  assert(types[0] === 'run:start', '6.2 first event is run:start');
  assert(types[1] === 'plan:created', '6.2 second event is plan:created');
  const stepPairs = types.filter((t) => t === 'step:result').length;
  assert(stepPairs >= 2, `6.2 ≥2 step:start/step:result pairs (got ${stepPairs})`);
  assert(types[types.length - 1] === 'run:done', '6.2 final event is run:done');

  const plan = events.find((e) => e.type === 'plan:created')?.plan as { steps: { tool: string }[] } | undefined;
  assert(plan && plan.steps.length >= 2 && plan.steps.length <= 4, `6.3 plan has 2–4 steps (got ${plan?.steps.length})`);
  assert(plan?.steps.every((s) => registry.includes(s.tool)), '6.3 every planned tool exists in registry');

  const fileContent = await fs.readFile(`/tmp/agent-runs/${runId}/result.txt`, 'utf8').catch(() => '');
  assert(fileContent.includes('Example Domain'), `6.4 result.txt contains "Example Domain" (got: "${fileContent.slice(0, 60)}")`);

  const msgs = (await (await fetch(`${BASE}/api/messages?runId=${runId}`)).json()) as { messages: { role: string }[] };
  assert(msgs.messages.some((m) => m.role === 'assistant'), '6.5 assistant message persisted via Drizzle');

  /* ---- Negative path (§6.6) ---- */
  console.log('\n▶ Negative path (nonexistent tool)');
  const badRes = await fetch(`${BASE}/api/tasks`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt: 'Solve this task with the teleport tool immediately.' }),
  });
  const { runId: badRunId } = (await badRes.json()) as { runId: string };
  const badEvents = await collectSse(badRunId, 60_000);
  const badTypes = badEvents.map((e) => e.type);
  console.log(`  trace: ${badTypes.join(' → ')}`);

  const failedEvent = badEvents.find((e) => e.type === 'run:failed') as { error?: string } | undefined;
  assert(!!failedEvent, '6.6 run:failed emitted for invalid tool plan');
  assert(/registry|validation|invalid/i.test(failedEvent?.error ?? ''), '6.6 Zod/registry error surfaced in trace');

  const health = await fetch(`${BASE}/api/health`);
  assert(health.ok, '6.6 HTTP process still alive after failed run');

  /* ---- v2 Phase 1: persistent runs (Module 2 contract) ---- */
  console.log('\n▶ v2 Phase 1 — persistence');
  const persisted = (await (await fetch(`${BASE}/api/runs/${runId}`)).json()) as {
    run: { status: string; plan: { steps: unknown[] } | null; trace: { type: string }[] | null };
  };
  assert(persisted.run?.status === 'completed', `7.1 happy run persisted with status 'completed' (got ${persisted.run?.status})`);
  assert((persisted.run?.plan?.steps?.length ?? 0) >= 2, '7.1 plan persisted in runs table');
  const persistedTypes = (persisted.run?.trace ?? []).map((e) => e.type);
  assert(persistedTypes[0] === 'run:start' && persistedTypes[persistedTypes.length - 1] === 'run:done',
    '7.1 full trace persisted (run:start … run:done)');

  const rejected = (await (await fetch(`${BASE}/api/runs/${badRunId}`)).json()) as { run: { status: string } };
  assert(rejected.run?.status === 'plan_rejected', `7.1 invalid-plan run persisted as 'plan_rejected' (got ${rejected.run?.status})`);

  /* ---- v2 Phase 1: cancellation ---- */
  console.log('\n▶ v2 Phase 1 — cancellation');
  const slowRes = await fetch(`${BASE}/api/tasks`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt: 'Run a long sleep 40 seconds task with the shell tool.' }),
  });
  const { runId: slowRunId } = (await slowRes.json()) as { runId: string };
  // Phase 2: the sensitive shell step gates on approval first — approve, then cancel mid-sleep.
  const slowApproval = await waitPendingApproval(slowRunId);
  await decide(slowApproval.id, 'approve', 'e2e: approve before cancel');
  await new Promise((r) => setTimeout(r, 1500)); // let it reach the sleeping step
  const cancelRes = await fetch(`${BASE}/api/runs/${slowRunId}/cancel`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason: 'e2e cancellation test' }),
  });
  assert(cancelRes.ok, '7.2 cancel endpoint accepted the request');

  const slowEvents = await collectSse(slowRunId, 30_000);
  const slowTypes = slowEvents.map((e) => e.type);
  console.log(`  trace: ${slowTypes.join(' → ')}`);
  const cancelledEvent = slowEvents.find((e) => e.type === 'run:cancelled') as { reason?: string } | undefined;
  assert(!!cancelledEvent, '7.2 run:cancelled emitted over SSE');
  assert(cancelledEvent?.reason === 'e2e cancellation test', '7.2 cancel reason carried in the event');

  const cancelledRun = (await (await fetch(`${BASE}/api/runs/${slowRunId}`)).json()) as {
    run: { status: string; cancelReason: string | null };
  };
  assert(cancelledRun.run?.status === 'cancelled', `7.2 run persisted as 'cancelled' (got ${cancelledRun.run?.status})`);
  assert(cancelledRun.run?.cancelReason === 'e2e cancellation test', '7.2 cancel reason persisted in DB');

  const cancelAgain = await fetch(`${BASE}/api/runs/${slowRunId}/cancel`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason: 'double cancel' }),
  });
  assert(cancelAgain.status === 409, '7.2 cancelling a finished run returns 409');

  /* ---- v2 Phase 2: approval gate (Module 2 + celia binding) ---- */
  console.log('\n▶ v2 Phase 2 — approval gate');
  const apprRes = await fetch(`${BASE}/api/tasks`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt: 'Quick job: sleep 1 second with the shell tool.' }),
  });
  const { runId: apprRunId } = (await apprRes.json()) as { runId: string };
  const appr = await waitPendingApproval(apprRunId);
  assert(appr.tool === 'shell.exec' && appr.stepIndex === 0, '8.1 approval bound to sensitive step (shell.exec, step 0)');
  assert(typeof appr.planHash === 'string' && appr.planHash.length === 64, '8.1 approval carries the plan hash (sha256)');

  const midRun = (await (await fetch(`${BASE}/api/runs/${apprRunId}`)).json()) as { run: { status: string } };
  assert(midRun.run?.status === 'awaiting_approval', `8.1 run persisted as 'awaiting_approval' (got ${midRun.run?.status})`);

  const ok1 = await decide(appr.id, 'approve', 'e2e approval');
  assert(ok1.ok, '8.1 decide endpoint accepted approval');
  const apprEvents = await collectSse(apprRunId, 60_000);
  const apprTypes = apprEvents.map((e) => e.type);
  console.log(`  trace: ${apprTypes.join(' → ')}`);
  assert(apprTypes.includes('approval:pending') && apprTypes.includes('approval:decided'),
    '8.1 approval:pending + approval:decided in trace');
  assert(apprTypes[apprTypes.length - 1] === 'run:done', '8.1 approved run completes (run:done)');
  const dup = await decide(appr.id, 'approve');
  assert(dup.status === 409, '8.1 double-decide returns 409');

  /* rejection path */
  const rejRes = await fetch(`${BASE}/api/tasks`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt: 'Another job: sleep 1 second with the shell tool.' }),
  });
  const { runId: rejRunId } = (await rejRes.json()) as { runId: string };
  const rejAppr = await waitPendingApproval(rejRunId);
  await decide(rejAppr.id, 'reject', 'e2e rejection');
  const rejEvents = await collectSse(rejRunId, 30_000);
  const rejTypes = rejEvents.map((e) => e.type);
  console.log(`  trace: ${rejTypes.join(' → ')}`);
  assert(rejTypes[rejTypes.length - 1] === 'run:cancelled', '8.2 rejected approval → run:cancelled (Module 2)');
  const rejRun = (await (await fetch(`${BASE}/api/runs/${rejRunId}`)).json()) as {
    run: { status: string; cancelReason: string | null };
  };
  assert(rejRun.run?.status === 'cancelled' && /rejected/.test(rejRun.run?.cancelReason ?? ''),
    '8.2 rejection persisted as cancelled with reason');

  /* needs_clarification path */
  const vagueRes = await fetch(`${BASE}/api/tasks`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt: 'مرحبا' }),
  });
  const { runId: vagueRunId } = (await vagueRes.json()) as { runId: string };
  const vagueEvents = await collectSse(vagueRunId, 30_000);
  const vagueTypes = vagueEvents.map((e) => e.type);
  console.log(`  trace: ${vagueTypes.join(' → ')}`);
  assert(vagueTypes[vagueTypes.length - 1] === 'run:needs_clarification', '8.3 ambiguous goal → run:needs_clarification');
  const vagueRun = (await (await fetch(`${BASE}/api/runs/${vagueRunId}`)).json()) as { run: { status: string } };
  assert(vagueRun.run?.status === 'needs_clarification', `8.3 persisted as 'needs_clarification' (got ${vagueRun.run?.status})`);

  console.log(`\n═══ RESULT: ${passed} passed, ${failed} failed ═══`);
  process.exit(failed === 0 ? 0 : 1);
}

main().catch((e) => { console.error('Suite crashed:', e); process.exit(1); });
