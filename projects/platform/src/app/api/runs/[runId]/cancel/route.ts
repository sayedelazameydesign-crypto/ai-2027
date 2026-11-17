import { NextRequest, NextResponse } from 'next/server';
import { eq } from 'drizzle-orm';
import { db, schema } from '@/db';
import { cancelRun } from '@/agent/cancelRegistry';

export const dynamic = 'force-dynamic';

/** POST /api/runs/[runId]/cancel — Module 2 `cancelled` transition (v2 Phase 1). */
export async function POST(req: NextRequest, { params }: { params: { runId: string } }) {
  const body = (await req.json().catch(() => ({}))) as { reason?: string };
  const reason = body.reason?.trim() || 'cancelled by user';

  const rows = await db
    .select({ id: schema.runs.id, status: schema.runs.status })
    .from(schema.runs)
    .where(eq(schema.runs.id, params.runId))
    .limit(1);
  if (rows.length === 0) return NextResponse.json({ error: 'run not found' }, { status: 404 });

  const ok = cancelRun(params.runId, reason);
  if (!ok) {
    return NextResponse.json(
      { error: `run is not active (status: ${rows[0].status})` },
      { status: 409 },
    );
  }
  return NextResponse.json({ cancelled: true, runId: params.runId, reason });
}
