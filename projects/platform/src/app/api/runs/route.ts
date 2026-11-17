import { NextResponse } from 'next/server';
import { desc } from 'drizzle-orm';
import { db, schema } from '@/db';

export const dynamic = 'force-dynamic';

/** GET /api/runs — persistent run registry (v2 Phase 1). */
export async function GET() {
  const rows = await db
    .select({
      id: schema.runs.id,
      task: schema.runs.task,
      status: schema.runs.status,
      cancelReason: schema.runs.cancelReason,
      createdAt: schema.runs.createdAt,
      updatedAt: schema.runs.updatedAt,
    })
    .from(schema.runs)
    .orderBy(desc(schema.runs.createdAt))
    .limit(100);
  return NextResponse.json({ runs: rows });
}
