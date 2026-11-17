'use client';

import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';

interface RunDetail {
  id: string; task: string; status: string; cancelReason: string | null;
  plan: { steps: { tool: string; input: unknown }[] } | null;
  trace: { type: string; ts: number; [k: string]: unknown }[] | null;
  createdAt: number; updatedAt: number;
}

const badgeColor: Record<string, string> = {
  'run:start': '#58a6ff', 'plan:created': '#d2a8ff', 'step:start': '#e3b341',
  'step:result': '#3fb950', 'step:error': '#f85149', 'run:done': '#3fb950',
  'run:failed': '#f85149', 'run:cancelled': '#e3b341',
};

export default function RunDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    fetch(`/api/runs/${runId}`).then(async (r) => {
      if (!r.ok) { setErr('التشغيل غير موجود'); return; }
      setRun((await r.json()).run);
    });
  }, [runId]);

  if (err) return <main style={{ padding: 24 }}><p style={{ color: '#f85149' }}>{err}</p></main>;
  if (!run) return <main style={{ padding: 24 }}><p style={{ color: '#8b949e' }}>جارٍ التحميل…</p></main>;

  return (
    <main style={{ maxWidth: 900, margin: '0 auto', padding: 24 }}>
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h1 style={{ fontSize: 18, margin: 0 }}>تفاصيل التشغيل <code style={{ color: '#8b949e' }}>{run.id.slice(0, 8)}</code></h1>
        <a href="/runs" style={{ color: '#58a6ff', fontSize: 14, textDecoration: 'none' }}>← كل التشغيلات</a>
      </header>

      <section style={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 10, padding: 16, marginBottom: 16 }}>
        <div style={{ fontSize: 14, marginBottom: 8 }}><b>المهمة:</b> {run.task}</div>
        <div style={{ fontSize: 13, color: '#8b949e' }}>
          <b>الحالة:</b> <code>{run.status}</code>
          {run.cancelReason ? <> · <b>سبب الإلغاء:</b> {run.cancelReason}</> : null}
          {' '}· أنشئ: {new Date(run.createdAt).toLocaleString()} · آخر تحديث: {new Date(run.updatedAt).toLocaleString()}
        </div>
      </section>

      {run.plan && (
        <section style={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 10, padding: 16, marginBottom: 16 }}>
          <h2 style={{ fontSize: 15, marginTop: 0 }}>الخطة ({run.plan.steps.length} خطوات)</h2>
          {run.plan.steps.map((s, i) => (
            <div key={i} style={{ fontFamily: 'ui-monospace, monospace', fontSize: 12, padding: '6px 0', borderBottom: '1px solid #21262d' }}>
              <span style={{ color: '#d2a8ff' }}>{i}. {s.tool}</span>
              <pre style={{ margin: '4px 0 0', color: '#8b949e', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{JSON.stringify(s.input)}</pre>
            </div>
          ))}
        </section>
      )}

      <section style={{ background: '#010409', border: '1px solid #30363d', borderRadius: 10, padding: 16 }}>
        <h2 style={{ fontSize: 15, marginTop: 0 }}>الأثر الكامل ({run.trace?.length ?? 0} حدثًا) — من قاعدة البيانات</h2>
        {(run.trace ?? []).map((e, i) => (
          <div key={i} style={{ marginBottom: 10, borderLeft: `3px solid ${badgeColor[e.type] ?? '#8b949e'}`, paddingLeft: 10 }}>
            <div style={{ fontFamily: 'ui-monospace, monospace', fontSize: 12 }}>
              <span style={{ color: badgeColor[e.type] ?? '#8b949e', fontWeight: 700 }}>{e.type}</span>
              <span style={{ color: '#484f58' }}> · {new Date(e.ts).toLocaleTimeString()}</span>
            </div>
            <pre style={{ margin: '4px 0 0', whiteSpace: 'pre-wrap', wordBreak: 'break-all', color: '#8b949e', fontSize: 11 }}>
              {JSON.stringify({ ...e, type: undefined, ts: undefined, runId: undefined }, null, 1)?.slice(0, 500)}
            </pre>
          </div>
        ))}
      </section>
    </main>
  );
}
