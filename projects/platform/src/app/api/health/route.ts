import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

/** GET /api/health — liveness probe (§6.6: process must survive failed runs). */
export async function GET() {
  return NextResponse.json({ ok: true, ts: Date.now() });
}
