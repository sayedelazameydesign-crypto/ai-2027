/**
 * In-memory run event bus + flat KV — EXECUTION-ORDER-MVP.md §2.
 * State may vanish on restart; acceptable for MVP.
 */

export type TraceEvent =
  | { type: 'run:start'; runId: string; task: string; ts: number }
  | { type: 'plan:created'; runId: string; plan: unknown; ts: number }
  | { type: 'step:start'; runId: string; index: number; tool: string; input: unknown; ts: number }
  | { type: 'step:result'; runId: string; index: number; tool: string; output: unknown; ts: number }
  | { type: 'step:error'; runId: string; index: number; tool: string; error: string; retry: boolean; ts: number }
  | { type: 'approval:pending'; runId: string; approvalId: string; index: number; tool: string; input: unknown; ts: number }
  | { type: 'approval:decided'; runId: string; approvalId: string; decision: 'approved' | 'rejected'; reason?: string; ts: number }
  | { type: 'run:needs_clarification'; runId: string; question: string; ts: number }
  | { type: 'run:done'; runId: string; answer: string; ts: number }
  | { type: 'run:cancelled'; runId: string; reason: string; ts: number }
  | { type: 'run:failed'; runId: string; error: string; ts: number };

type Subscriber = (e: TraceEvent) => void;

interface RunChannel {
  events: TraceEvent[];
  subscribers: Set<Subscriber>;
  finished: boolean;
  kv: Map<string, unknown>; // flat KV scratch state (§2)
}

const g = globalThis as unknown as { __bus?: Map<string, RunChannel> };
const channels = g.__bus ?? (g.__bus = new Map<string, RunChannel>());

export function openRun(runId: string): void {
  if (!channels.has(runId)) {
    channels.set(runId, { events: [], subscribers: new Set(), finished: false, kv: new Map() });
  }
}

export function emit(runId: string, e: TraceEvent): void {
  const ch = channels.get(runId);
  if (!ch) return;
  ch.events.push(e);
  if (e.type === 'run:done' || e.type === 'run:failed' || e.type === 'run:cancelled' || e.type === 'run:needs_clarification') ch.finished = true;
  for (const s of ch.subscribers) s(e);
}

export function subscribe(runId: string, fn: Subscriber): () => void {
  const ch = channels.get(runId);
  if (!ch) return () => {};
  for (const e of ch.events) fn(e); // replay buffer
  if (ch.finished) return () => {};
  ch.subscribers.add(fn);
  return () => ch.subscribers.delete(fn);
}

export function isFinished(runId: string): boolean {
  return channels.get(runId)?.finished ?? true;
}

export function runKv(runId: string): Map<string, unknown> | undefined {
  return channels.get(runId)?.kv;
}
