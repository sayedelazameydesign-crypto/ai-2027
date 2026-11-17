/**
 * v2 Phase 1 — cancellation registry (Module 2: `cancelled` state).
 * Maps active runIds to their AbortControllers so an HTTP request can
 * abort an in-process run and record the reason.
 */

interface CancelEntry {
  ctrl: AbortController;
  reason?: string;
}

const g = globalThis as unknown as { __cancels?: Map<string, CancelEntry> };
const registry = g.__cancels ?? (g.__cancels = new Map<string, CancelEntry>());

export function registerRunController(runId: string, ctrl: AbortController): void {
  registry.set(runId, { ctrl });
}

export function unregisterRunController(runId: string): void {
  registry.delete(runId);
}

/** Returns false if the run is not active (already finished or unknown). */
export function cancelRun(runId: string, reason: string): boolean {
  const entry = registry.get(runId);
  if (!entry || entry.ctrl.signal.aborted) return false;
  entry.reason = reason;
  entry.ctrl.abort(new Error(`cancelled: ${reason}`));
  return true;
}

export function getCancelReason(runId: string): string | undefined {
  return registry.get(runId)?.reason;
}
