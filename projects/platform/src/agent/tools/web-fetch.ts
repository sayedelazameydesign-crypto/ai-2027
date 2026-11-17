import { z } from 'zod';
import { register, Tool } from './registry';

/** web.fetch — §4: 10s timeout, 100KB body truncation. */

const inputSchema = z.object({
  url: z.string().url(),
  method: z.enum(['GET', 'HEAD']).default('GET'),
});

const outputSchema = z.object({
  status: z.number(),
  contentType: z.string(),
  body: z.string(),
  truncated: z.boolean(),
});

type I = z.infer<typeof inputSchema>;
type O = z.infer<typeof outputSchema>;

const CAP = 100 * 1024; // 100KB

const webFetch: Tool<I, O> = {
  name: 'web.fetch',
  risk: 'safe',
  description: 'HTTP GET/HEAD a URL. Returns status and body text truncated to 100KB. 10 second timeout.',
  inputSchema,
  outputSchema,
  async execute(input, ctx): Promise<O> {
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(new Error('web.fetch timed out after 10s')), 10_000);
    const onOuterAbort = () => ac.abort(ctx.signal.reason);
    ctx.signal.addEventListener('abort', onOuterAbort, { once: true });
    try {
      const res = await fetch(input.url, { method: input.method, signal: ac.signal, redirect: 'follow' });
      const text = input.method === 'HEAD' ? '' : await res.text();
      return {
        status: res.status,
        contentType: res.headers.get('content-type') ?? '',
        body: text.slice(0, CAP),
        truncated: text.length > CAP,
      };
    } finally {
      clearTimeout(timer);
      ctx.signal.removeEventListener('abort', onOuterAbort);
    }
  },
};

register(webFetch);
export { webFetch };
