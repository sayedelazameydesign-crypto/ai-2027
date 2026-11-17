/**
 * Template resolution for step-output references — extracted to a
 * dependency-free module so behavior is unit-testable (AUDIT-001 F2 follow-up).
 *
 * Semantics (documented behavioral contract):
 *  - "{{steps.N.KEY}}" with a known key path → String(value)
 *  - unknown key / "output" on an object     → most salient scalar
 *    (title → text → stdout → content → body), else compact JSON
 *  - missing step index / null output        → '' (empty string)
 */
export function resolveTemplates(value: unknown, outputs: unknown[]): unknown {
  if (typeof value === 'string') {
    return value.replace(/\{\{steps\.(\d+)\.([\w.]+)\}\}/g, (_: string, n: string, key: string) => {
      const out = outputs[Number(n)] as Record<string, unknown> | undefined;
      const v = key.split('.').reduce<unknown>((acc: unknown, k: string) => (acc as Record<string, unknown>)?.[k], out);
      if (v !== undefined && v !== null) return String(v);
      if (out && typeof out === 'object') {
        for (const k of ['title', 'text', 'stdout', 'content', 'body']) {
          const s = (out as Record<string, unknown>)[k];
          if (typeof s === 'string' && s) return s;
        }
        return JSON.stringify(out);
      }
      return '';
    });
  }
  if (Array.isArray(value)) return value.map((v) => resolveTemplates(v, outputs));
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, resolveTemplates(v, outputs)]));
  }
  return value;
}
