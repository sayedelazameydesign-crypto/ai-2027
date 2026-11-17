import { NextRequest } from 'next/server';
import { subscribe, isFinished } from '@/lib/bus';

export const dynamic = 'force-dynamic';

/** GET /api/runs/[runId]/events — the SSE trace stream (§2, §6). */
export async function GET(req: NextRequest, { params }: { params: { runId: string } }) {
  const { runId } = params;
  const encoder = new TextEncoder();

  const stream = new ReadableStream({
    start(controller) {
      let closed = false;
      const close = () => {
        if (closed) return;
        closed = true;
        unsub();
        try { controller.close(); } catch {}
      };

      const send = (e: unknown) => {
        if (closed) return;
        const t = (e as { type: string }).type;
        try {
          controller.enqueue(encoder.encode(`event: ${t}\ndata: ${JSON.stringify(e)}\n\n`));
        } catch { closed = true; }
        if (t === 'run:done' || t === 'run:failed') setTimeout(close, 50);
      };

      const unsub = subscribe(runId, send);
      if (isFinished(runId)) setTimeout(close, 100);
      req.signal.addEventListener('abort', close);
    },
  });

  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
    },
  });
}
