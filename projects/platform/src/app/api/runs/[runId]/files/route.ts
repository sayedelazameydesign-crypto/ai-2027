import { NextRequest, NextResponse } from 'next/server';
import fs from 'node:fs/promises';
import path from 'node:path';

export const dynamic = 'force-dynamic';

const ROOT = '/tmp/agent-runs';

function safeDir(runId: string): string {
  if (!/^[\w-]+$/.test(runId)) throw new Error('bad runId');
  return path.join(ROOT, runId);
}

/** GET /api/runs/[runId]/files — list the run's sandbox artifacts. */
export async function GET(_req: NextRequest, { params }: { params: { runId: string } }) {
  try {
    const dir = safeDir(params.runId);
    const names = await fs.readdir(dir).catch(() => [] as string[]);
    const files = [];
    for (const name of names) {
      const st = await fs.stat(path.join(dir, name)).catch(() => null);
      if (st?.isFile()) files.push({ name, size: st.size, mtime: st.mtimeMs });
    }
    files.sort((a, b) => b.mtime - a.mtime);
    return NextResponse.json({ files });
  } catch {
    return NextResponse.json({ files: [] });
  }
}
