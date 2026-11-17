import { NextResponse } from 'next/server';
import { toolNames } from '@/agent/tools';

export const dynamic = 'force-dynamic';

/** GET /api/tools — registry names (used by the E2E suite to validate plans). */
export async function GET() {
  return NextResponse.json({ tools: toolNames() });
}
