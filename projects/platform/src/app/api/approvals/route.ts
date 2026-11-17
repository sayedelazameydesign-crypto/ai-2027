import { NextRequest, NextResponse } from 'next/server';
import { and, desc, eq } from 'drizzle-orm';
import { db, schema } from '@/db';

export const dynamic = 'force-dynamic';

/** GET /api/approvals[?runId=&status=pending] — approval audit list (Phase 2). */
export async function GET(req: NextRequest) {
  const runId = req.nextUrl.searchParams.get('runId');
  const status = req.nextUrl.searchParams.get('status');
  const conds = [];
  if (runId) conds.push(eq(schema.approvals.runId, runId));
  if (status) conds.push(eq(schema.approvals.status, status));
  const rows = await db
    .select()
    .from(schema.approvals)
    .where(conds.length ? and(...conds) : undefined)
    .orderBy(desc(schema.approvals.createdAt))
    .limit(100);
  return NextResponse.json({ approvals: rows });
}
