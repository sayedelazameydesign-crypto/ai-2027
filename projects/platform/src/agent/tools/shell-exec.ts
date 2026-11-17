import { z } from 'zod';
import { execFile } from 'node:child_process';
import path from 'node:path';
import { register, Tool, ToolContext } from './registry';

/**
 * shell.exec — EXECUTION-ORDER-MVP.md §4.
 * child_process.execFile ONLY (no shell interpolation).
 * Allowlist, pinned cwd, 30s timeout, 1MB stdout cap, env scrubbed to PATH.
 */

const ALLOWLIST = new Set(['node', 'python3', 'ls', 'cat', 'grep']);

const inputSchema = z.object({
  bin: z.string(),
  args: z.array(z.string()).default([]),
});

const outputSchema = z.object({
  stdout: z.string(),
  stderr: z.string(),
  exitCode: z.number(),
});

type I = z.infer<typeof inputSchema>;
type O = z.infer<typeof outputSchema>;

const shellExec: Tool<I, O> = {
  name: 'shell.exec',
  risk: 'sensitive', // arbitrary code execution → human approval required (Phase 2)
  description:
    'Run an allowlisted binary (node, python3, ls, cat, grep) inside the run sandbox directory. No shell features (pipes, globs, redirects).',
  inputSchema,
  outputSchema,
  async execute(input, ctx: ToolContext): Promise<O> {
    if (!ALLOWLIST.has(input.bin)) {
      throw new Error(`Binary not allowlisted: ${input.bin}. Allowed: ${[...ALLOWLIST].join(', ')}`);
    }
    return new Promise<O>((resolve, reject) => {
      execFile(
        input.bin,
        input.args,
        {
          cwd: ctx.sandboxDir,
          timeout: 30_000,               // 30s execution timeout
          maxBuffer: 1024 * 1024,        // 1MB stdout cap
          env: { PATH: process.env.PATH ?? '/usr/bin:/bin' } as unknown as NodeJS.ProcessEnv, // env scrubbed
          signal: ctx.signal,
          encoding: 'utf8' as const,
        },
        (err: import('node:child_process').ExecFileException | null, stdout: string | Buffer, stderr: string | Buffer) => {
          if (ctx.signal.aborted) {
            return reject(ctx.signal.reason instanceof Error ? ctx.signal.reason : new Error('aborted'));
          }
          if (err && (err as NodeJS.ErrnoException).code === 'ENOENT') {
            return reject(new Error(`Binary not found: ${input.bin}`));
          }
          const exitCode = err ? ((err as { code?: number }).code as number | undefined) ?? 1 : 0;
          if (err && err.killed) return reject(new Error('shell.exec timed out after 30s'));
          resolve({ stdout: String(stdout), stderr: String(stderr), exitCode: typeof exitCode === 'number' ? exitCode : 1 });
        },
      );
    });
  },
};

register(shellExec);
export { shellExec };
