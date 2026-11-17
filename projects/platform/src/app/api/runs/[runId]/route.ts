import { NextRequest, NextResponse } from 'next/server';
import { eq } from 'drizzle-orm';
import { db, schema } from '@/db';

export const dynamic = 'force-dynamic';

/** GET /api/runs/[runId] — full persisted run: status, plan, trace, timestamps. */
export async function GET(_req: NextRequest, { params }: { params: { runId: string } }) {
  const rows = await db.select().from(schema.runs).where(eq(schema.runs.id, params.runId)).limit(1);
  if (rows.length === 0) return NextResponse.json({ error: 'run not found' }, { status: 404 });
  return NextResponse.json({ run: rows[0] });
}
