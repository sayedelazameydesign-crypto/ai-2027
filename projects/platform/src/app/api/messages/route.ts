import { NextRequest, NextResponse } from 'next/server';
import { asc, eq } from 'drizzle-orm';
import { db, schema } from '@/db';

export const dynamic = 'force-dynamic';

/** GET /api/messages[?runId=] — chat history from the existing schema. */
export async function GET(req: NextRequest) {
  const runId = req.nextUrl.searchParams.get('runId');
  const rows = runId
    ? await db.select().from(schema.messages).where(eq(schema.messages.runId, runId)).orderBy(asc(schema.messages.id))
    : await db.select().from(schema.messages).orderBy(asc(schema.messages.id));
  return NextResponse.json({ messages: rows });
}
