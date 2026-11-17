import { NextRequest, NextResponse } from 'next/server';
import { eq } from 'drizzle-orm';
import { db, schema } from '@/db';
import { resolveDecision } from '@/agent/approvalRegistry';

export const dynamic = 'force-dynamic';

/**
 * POST /api/approvals/[approvalId]/decide — Phase 2 human gate.
 * Body: { decision: 'approve' | 'reject', reason?: string }
 * The durable row is updated first; then the parked run (if any) is resumed.
 */
export async function POST(req: NextRequest, { params }: { params: { approvalId: string } }) {
  const body = (await req.json().catch(() => null)) as { decision?: string; reason?: string } | null;
  if (body?.decision !== 'approve' && body?.decision !== 'reject') {
    return NextResponse.json({ error: "decision must be 'approve' or 'reject'" }, { status: 400 });
  }

  const row = (await db.select().from(schema.approvals).where(eq(schema.approvals.id, params.approvalId)).limit(1))[0];
  if (!row) return NextResponse.json({ error: 'approval not found' }, { status: 404 });
  if (row.status !== 'pending') {
    return NextResponse.json({ error: `approval already ${row.status}` }, { status: 409 });
  }

  const status = body.decision === 'approve' ? 'approved' : 'rejected';
  await db.update(schema.approvals)
    .set({ status, decisionReason: body.reason ?? null, decidedAt: new Date() })
    .where(eq(schema.approvals.id, params.approvalId));

  const resumed = resolveDecision(params.approvalId, { decision: status, reason: body.reason });
  return NextResponse.json({ approvalId: params.approvalId, status, resumed });
}
