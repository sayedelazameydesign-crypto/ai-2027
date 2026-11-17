import { z } from 'zod';
import path from 'node:path';
import fs from 'node:fs/promises';
import { register, Tool } from './registry';

/** fs.readwrite — §4: strictly confined to /tmp/agent-runs/<runId>/. */

const inputSchema = z.object({
  mode: z.enum(['read', 'write']),
  path: z.string(),
  content: z.string().optional(),
});

const outputSchema = z.object({
  path: z.string(),
  content: z.string().optional(),
  bytesWritten: z.number().optional(),
});

type I = z.infer<typeof inputSchema>;
type O = z.infer<typeof outputSchema>;

function resolveSafe(sandboxDir: string, rel: string): string {
  const abs = path.resolve(sandboxDir, rel);
  if (!abs.startsWith(path.resolve(sandboxDir) + path.sep) && abs !== path.resolve(sandboxDir)) {
    throw new Error(`Path escapes sandbox: ${rel}`); // path traversal rejected
  }
  return abs;
}

const fsReadWrite: Tool<I, O> = {
  name: 'fs.readwrite',
  risk: 'safe', // confined to the run sandbox dir
  description: 'Read or write a text file inside the run sandbox directory only. Relative paths only.',
  inputSchema,
  outputSchema,
  async execute(input, ctx): Promise<O> {
    const abs = resolveSafe(ctx.sandboxDir, input.path);
    if (input.mode === 'read') {
      const content = await fs.readFile(abs, 'utf8');
      return { path: input.path, content: content.slice(0, 100 * 1024) };
    }
    const data = input.content ?? '';
    await fs.mkdir(path.dirname(abs), { recursive: true });
    await fs.writeFile(abs, data, 'utf8');
    return { path: input.path, bytesWritten: Buffer.byteLength(data) };
  },
};

register(fsReadWrite);
export { fsReadWrite };
