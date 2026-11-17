import { NextRequest, NextResponse } from 'next/server';
import crypto from 'node:crypto';
import { db, schema } from '@/db';
import { runAgent } from '@/agent/loop';
import { openRun } from '@/lib/bus';

export const dynamic = 'force-dynamic';

/** POST /api/tasks — chat message triggers a run (§6 wiring). */
export async function POST(req: NextRequest) {
  const body = (await req.json().catch(() => null)) as { prompt?: string } | null;
  const prompt = body?.prompt?.trim();
  if (!prompt) return NextResponse.json({ error: 'prompt required' }, { status: 400 });

  const runId = crypto.randomUUID();
  openRun(runId);
  await db.insert(schema.messages).values({ role: 'user', content: prompt, runId });

  // In-process, single worker (§2). On serverless (Vercel) the platform would
  // freeze background work after the response — waitUntil keeps the run alive
  // for the function's full duration. Local/VM: falls back to fire-and-forget.
  const runPromise = runAgent({ runId, task: prompt });
  try {
    const { waitUntil } = await import('@vercel/functions');
    waitUntil(runPromise);
  } catch {
    void runPromise;
  }

  return NextResponse.json({ runId });
}
