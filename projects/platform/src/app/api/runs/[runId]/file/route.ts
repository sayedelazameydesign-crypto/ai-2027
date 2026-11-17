import { NextRequest, NextResponse } from 'next/server';
import fs from 'node:fs/promises';
import path from 'node:path';

export const dynamic = 'force-dynamic';

const ROOT = '/tmp/agent-runs';
const MIME: Record<string, string> = {
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif',
  '.txt': 'text/plain; charset=utf-8', '.md': 'text/plain; charset=utf-8',
  '.json': 'application/json; charset=utf-8', '.csv': 'text/csv; charset=utf-8',
  '.html': 'text/plain; charset=utf-8',
};

/** GET /api/runs/[runId]/file?name=…[&download=1] — serve one sandbox artifact. */
export async function GET(req: NextRequest, { params }: { params: { runId: string } }) {
  const name = req.nextUrl.searchParams.get('name') ?? '';
  if (!/^[\w-]+$/.test(params.runId)) return NextResponse.json({ error: 'bad runId' }, { status: 400 });
  const dir = path.join(ROOT, params.runId);
  const abs = path.resolve(dir, name);
  if (!abs.startsWith(path.resolve(dir) + path.sep)) {
    return NextResponse.json({ error: 'path escapes sandbox' }, { status: 400 });
  }
  try {
    const buf = await fs.readFile(abs);
    const ext = path.extname(abs).toLowerCase();
    const headers: Record<string, string> = {
      'Content-Type': MIME[ext] ?? 'application/octet-stream',
      'Cache-Control': 'no-cache',
    };
    if (req.nextUrl.searchParams.get('download')) {
      headers['Content-Disposition'] = `attachment; filename="${path.basename(abs)}"`;
    }
    return new NextResponse(new Uint8Array(buf.subarray(0, 5 * 1024 * 1024)), { headers });
  } catch {
    return NextResponse.json({ error: 'not found' }, { status: 404 });
  }
}
