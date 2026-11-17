import { z } from 'zod';
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { eq } from 'drizzle-orm';
import { db, schema } from '@/db';
import { complete } from '@/lib/llm';
import { emit, openRun, runKv, TraceEvent } from '@/lib/bus';
import { manifest, toolNames, getTool, runTool, ToolContext } from '@/agent/tools';
import { closeBrowser } from '@/agent/tools/browser-run';
import { registerRunController, unregisterRunController, getCancelReason } from '@/agent/cancelRegistry';
import { waitForDecision } from '@/agent/approvalRegistry';
import { resolveTemplates } from '@/agent/templates';

/**
 * Agent State Machine — Module 2 contract, Phase 1 subset.
 *
 * goal_submitted → planning → plan_validated → executing → completed
 *                     │                            │
 *                     ├→ plan_rejected (final)     ├→ failed (final)
 *                     └──────────────────────────→ cancelled (final, via cancel API)
 *
 * Per-step: 1 retry, 60s timeout. Run: 4 steps max, 5min global timeout.
 * Every state transition + every SSE event is persisted to the `runs` table
 * (survives restart). Phase 2 adds awaiting_approval / needs_clarification;
 * Phase 3 adds retry_scheduled / idempotency / redaction.
 */

const MAX_STEPS = 4;
const STEP_TIMEOUT_MS = 60_000;
const RUN_TIMEOUT_MS = 5 * 60_000;
const SANDBOX_ROOT = '/tmp/agent-runs';

export type RunStatus =
  | 'goal_submitted' | 'planning' | 'plan_validated' | 'plan_rejected'
  | 'needs_clarification' | 'executing' | 'awaiting_approval'
  | 'completed' | 'failed' | 'cancelled';

const StepSchema = z.object({
  tool: z.string().refine((t) => toolNames().includes(t), {
    message: 'tool must exist in the registry',
  }),
  input: z.record(z.unknown()),
});

export const PlanSchema = z.object({
  steps: z.array(StepSchema).min(2).max(MAX_STEPS),
});

export type Plan = z.infer<typeof PlanSchema>;

export interface TaskInput {
  runId: string;
  task: string;
}

class PlanRejectedError extends Error {}
class NeedsClarificationError extends Error {}
class ApprovalRejectedError extends Error {}

/** celia principle: the approval is bound to an exact plan fingerprint. */
function planHashOf(plan: Plan): string {
  return crypto.createHash('sha256').update(JSON.stringify(plan)).digest('hex');
}

export async function runAgent({ runId, task }: TaskInput): Promise<void> {
  openRun(runId);
  const sandboxDir = path.join(SANDBOX_ROOT, runId);
  await fs.mkdir(sandboxDir, { recursive: true });

  const runAbort = new AbortController();
  registerRunController(runId, runAbort);
  const runTimer = setTimeout(() => runAbort.abort(new Error('run timed out after 5 minutes')), RUN_TIMEOUT_MS);

  // Persistent run row + incremental trace (Module 2: durable audit).
  // Write queue: preserves update ordering on both sync (better-sqlite3)
  // and async (libSQL/Turso) drivers.
  let dbQueue: Promise<unknown> = Promise.resolve();
  const enqueue = (fn: () => unknown) => { dbQueue = dbQueue.then(fn).catch(() => {}); };

  const trace: TraceEvent[] = [];
  await db.insert(schema.runs).values({ id: runId, task, status: 'goal_submitted' }).onConflictDoNothing();

  const record = (e: TraceEvent) => {
    trace.push(e);
    emit(runId, e);
    enqueue(() => db.update(schema.runs)
      .set({ trace: [...trace], updatedAt: new Date() })
      .where(eq(schema.runs.id, runId))
      .run());
  };
  const setStatus = (status: RunStatus, extra?: Partial<{ plan: Plan; cancelReason: string }>) => {
    enqueue(() => db.update(schema.runs)
      .set({ status, updatedAt: new Date(), ...(extra?.plan ? { plan: extra.plan } : {}), ...(extra?.cancelReason ? { cancelReason: extra.cancelReason } : {}) })
      .where(eq(schema.runs.id, runId))
      .run());
  };

  record({ type: 'run:start', runId, task, ts: Date.now() });

  try {
    /* ---------------- planning ---------------- */
    setStatus('planning');
    const plan = await planWithRepair(task);
    setStatus('plan_validated', { plan });
    record({ type: 'plan:created', runId, plan, ts: Date.now() });

    /* ---------------- executing (with Phase 2 approval gate) ---------------- */
    setStatus('executing');
    const planHash = planHashOf(plan);
    const outputs: unknown[] = [];
    for (let i = 0; i < plan.steps.length; i++) {
      if (runAbort.signal.aborted) throw runAbort.signal.reason ?? new Error('aborted');
      const step = plan.steps[i];
      const input = resolveTemplates(step.input, outputs);

      // Deny-by-default policy: sensitivity comes from the registry, never the model.
      const tool = getTool(step.tool);
      if (!tool) throw new Error(`Tool not found in registry: ${step.tool}`);
      if (tool.risk === 'sensitive') {
        const approvalId = crypto.randomUUID();
        await db.insert(schema.approvals).values({
          id: approvalId, runId, stepIndex: i, tool: step.tool,
          input: input as object, planHash,
        });
        setStatus('awaiting_approval');
        record({ type: 'approval:pending', runId, approvalId, index: i, tool: step.tool, input, ts: Date.now() });

        const decision = await waitForDecision(approvalId, runAbort.signal);

        // Exact-binding verification against the durable row (celia: mismatch → block).
        const row = (await db.select().from(schema.approvals).where(eq(schema.approvals.id, approvalId)).limit(1))[0];
        const bound = row && row.runId === runId && row.stepIndex === i
          && row.tool === step.tool && row.planHash === planHash && row.status === decision.decision;
        if (!bound) throw new Error(`Approval binding mismatch for step ${i} — blocked`);

        record({ type: 'approval:decided', runId, approvalId, decision: decision.decision, reason: decision.reason, ts: Date.now() });
        if (decision.decision === 'rejected') {
          throw new ApprovalRejectedError(decision.reason || 'approval rejected');
        }
        setStatus('executing');
      }

      const output = await executeStep(record, runId, i, step.tool, input, sandboxDir, runAbort.signal);
      outputs.push(output);
    }

    /* ---------------- summarizing → completed ---------------- */
    const answer = await summarize(task, plan, outputs);
    await db.insert(schema.messages).values({ role: 'assistant', content: answer, runId });
    setStatus('completed');
    record({ type: 'run:done', runId, answer, ts: Date.now() });
  } catch (err) {
    const cancelReason = getCancelReason(runId);
    if (cancelReason !== undefined) {
      /* ---------------- cancelled (final) ---------------- */
      setStatus('cancelled', { cancelReason });
      await db.insert(schema.messages)
        .values({ role: 'assistant', content: `Run cancelled: ${cancelReason}`, runId })
        .catch(() => {});
      record({ type: 'run:cancelled', runId, reason: cancelReason, ts: Date.now() });
    } else if (err instanceof ApprovalRejectedError) {
      /* ---------------- approval rejected → cancelled (Module 2) ---------------- */
      const reason = `approval rejected: ${err.message}`;
      setStatus('cancelled', { cancelReason: reason });
      await db.insert(schema.messages)
        .values({ role: 'assistant', content: `Run cancelled: ${reason}`, runId })
        .catch(() => {});
      record({ type: 'run:cancelled', runId, reason, ts: Date.now() });
    } else if (err instanceof NeedsClarificationError) {
      /* ---------------- needs_clarification (Module 2) ---------------- */
      setStatus('needs_clarification');
      await db.insert(schema.messages)
        .values({ role: 'assistant', content: `أحتاج توضيحًا: ${err.message}`, runId })
        .catch(() => {});
      record({ type: 'run:needs_clarification', runId, question: err.message, ts: Date.now() });
    } else {
      const msg = err instanceof Error ? err.message : String(err);
      setStatus(err instanceof PlanRejectedError ? 'plan_rejected' : 'failed');
      await db.insert(schema.messages)
        .values({ role: 'assistant', content: `Run failed: ${msg}`, runId })
        .catch(() => {});
      record({ type: 'run:failed', runId, error: msg, ts: Date.now() });
    }
  } finally {
    clearTimeout(runTimer);
    unregisterRunController(runId);
    await closeBrowser(runId); // browser closed in finally — §4
    await dbQueue;             // flush queued persistence before the run ends
  }
}

/* ---------------- Planner: 1 LLM call + exactly 1 repair retry ---------------- */

async function planWithRepair(task: string): Promise<Plan> {
  const system = [
    '[PLANNER] You are a task planner. Produce a JSON plan of 2 to 4 sequential steps.',
    'Each step names exactly one tool from this manifest and its input:',
    manifest(),
    'Respond with JSON only: {"steps":[{"tool":"...","input":{...}}]}',
    'You may reference a previous step output with "{{steps.N.KEY}}".',
    'If the goal is too ambiguous to plan safely, respond {"clarify":"<one question>"} instead.',
  ].join('\n');

  let lastError = '';
  for (let attempt = 0; attempt < 2; attempt++) {
    const msgs: Parameters<typeof complete>[0] = [
      { role: 'system', content: system },
      { role: 'user', content: task },
    ];
    if (attempt === 1) {
      msgs.push({
        role: 'user',
        content: `Your previous plan failed validation:\n${lastError}\nRepair it. JSON only.`,
      });
    }
    const raw = await complete(msgs, { json: true });
    try {
      const parsedRaw = JSON.parse(extractJson(raw)) as Record<string, unknown>;
      // Module 2: ambiguous goal → needs_clarification, not a guessed plan.
      if (typeof parsedRaw.clarify === 'string' && parsedRaw.clarify) {
        throw new NeedsClarificationError(parsedRaw.clarify);
      }
      const parsed = PlanSchema.parse(parsedRaw);
      for (const s of parsed.steps) {
        getTool(s.tool)!.inputSchema.parse(s.input);
      }
      return parsed;
    } catch (e) {
      if (e instanceof NeedsClarificationError) throw e;
      lastError = e instanceof Error ? e.message : String(e);
    }
  }
  // Module 2: invalid plan is a distinct final state, not a generic failure.
  throw new PlanRejectedError(`Plan validation failed after repair retry: ${lastError}`);
}

function extractJson(raw: string): string {
  const start = raw.indexOf('{');
  const end = raw.lastIndexOf('}');
  if (start === -1 || end === -1) return raw;
  return raw.slice(start, end + 1);
}

/* ---------------- Executor: sequential, 1 retry per step ---------------- */

async function executeStep(
  record: (e: TraceEvent) => void,
  runId: string,
  index: number,
  tool: string,
  input: unknown,
  sandboxDir: string,
  runSignal: AbortSignal,
): Promise<unknown> {
  for (let attempt = 0; attempt < 2; attempt++) {
    if (runSignal.aborted) throw runSignal.reason ?? new Error('aborted');
    const stepAbort = new AbortController();
    const timer = setTimeout(() => stepAbort.abort(new Error('step timed out after 60s')), STEP_TIMEOUT_MS);
    const onRunAbort = () => stepAbort.abort(runSignal.reason);
    runSignal.addEventListener('abort', onRunAbort, { once: true });

    record({ type: 'step:start', runId, index, tool, input, ts: Date.now() });
    try {
      const ctx: ToolContext = { runId, sandboxDir, signal: stepAbort.signal, kv: runKv(runId)! };
      const output = await runTool(tool, input, ctx);
      record({ type: 'step:result', runId, index, tool, output, ts: Date.now() });
      return output;
    } catch (err) {
      if (runSignal.aborted) throw runSignal.reason ?? err; // cancellation wins over retry
      const msg = err instanceof Error ? err.message : String(err);
      const willRetry = attempt === 0;
      record({ type: 'step:error', runId, index, tool, error: msg, retry: willRetry, ts: Date.now() });
      if (!willRetry) throw new Error(`Step ${index} (${tool}) failed after retry: ${msg}`);
    } finally {
      clearTimeout(timer);
      runSignal.removeEventListener('abort', onRunAbort);
    }
  }
  throw new Error('unreachable');
}


/* ---------------- Summarizer: single LLM call ---------------- */

async function summarize(task: string, plan: Plan, outputs: unknown[]): Promise<string> {
  const context = plan.steps
    .map((s, i) => `Step ${i} [${s.tool}] output: ${JSON.stringify(outputs[i]).slice(0, 2000)}`)
    .join('\n');
  return complete(
    [
      { role: 'system', content: '[SUMMARIZER] Synthesize the step results into a concise, human-readable answer to the task.' },
      { role: 'user', content: `Task: ${task}\n\n${context}` },
    ],
    { maxTokens: 512 },
  );
}
