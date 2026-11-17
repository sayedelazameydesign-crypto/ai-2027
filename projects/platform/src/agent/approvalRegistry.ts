/**
 * v2 Phase 2 — approval waiters (dual-channel).
 * The loop parks on a promise; a decision arrives via either:
 *  1. in-memory resolver — same-instance fast path (local / VM)
 *  2. DB polling every 3s — cross-instance path (serverless hosts where the
 *     decide endpoint may land on a different instance than the runner)
 * The approval row stays the durable source of truth either way.
 */

export type Decision = { decision: 'approved' | 'rejected'; reason?: string };

const g = globalThis as unknown as { __approvalWaiters?: Map<string, (d: Decision) => void> };
const waiters = g.__approvalWaiters ?? (g.__approvalWaiters = new Map());

export function waitForDecision(approvalId: string, signal: AbortSignal): Promise<Decision> {
  return new Promise<Decision>((resolve, reject) => {
    let settled = false;
    let pollTimer: ReturnType<typeof setInterval> | undefined;
    const settle = (fn: () => void) => {
      if (settled) return;
      settled = true;
      if (pollTimer) clearInterval(pollTimer);
      waiters.delete(approvalId);
      signal.removeEventListener('abort', onAbort);
      fn();
    };
    const onAbort = () => settle(() => reject(signal.reason ?? new Error('aborted while awaiting approval')));
    if (signal.aborted) return onAbort();
    signal.addEventListener('abort', onAbort, { once: true });
    waiters.set(approvalId, (d: Decision) => settle(() => resolve(d)));

    pollTimer = setInterval(() => {
      void (async () => {
        try {
          const { db, schema } = await import('@/db');
          const { eq } = await import('drizzle-orm');
          const row = (await db.select().from(schema.approvals)
            .where(eq(schema.approvals.id, approvalId)).limit(1))[0];
          if (row && row.status !== 'pending') {
            settle(() => resolve({
              decision: row.status as 'approved' | 'rejected',
              reason: row.decisionReason ?? undefined,
            }));
          }
        } catch { /* transient DB error — next tick retries */ }
      })();
    }, 3000);
  });
}

/** Returns false if no run is parked on this approval in this instance. */
export function resolveDecision(approvalId: string, d: Decision): boolean {
  const fn = waiters.get(approvalId);
  if (!fn) return false;
  fn(d);
  return true;
}
