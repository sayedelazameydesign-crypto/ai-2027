'use client';

import { useEffect, useState } from 'react';

interface RunSummary {
  id: string; task: string; status: string; cancelReason: string | null;
  createdAt: number; updatedAt: number;
}

const statusStyle: Record<string, { bg: string; fg: string; label: string }> = {
  goal_submitted: { bg: '#1f2937', fg: '#8b949e', label: 'مُستلم' },
  planning: { bg: '#1e3a5f', fg: '#58a6ff', label: 'تخطيط' },
  plan_validated: { bg: '#2d1e5f', fg: '#d2a8ff', label: 'خطة صالحة' },
  plan_rejected: { bg: '#4c1d1d', fg: '#f85149', label: 'خطة مرفوضة' },
  executing: { bg: '#4a3800', fg: '#e3b341', label: 'قيد التنفيذ' },
  completed: { bg: '#12351f', fg: '#3fb950', label: 'مكتمل' },
  failed: { bg: '#4c1d1d', fg: '#f85149', label: 'فاشل' },
  cancelled: { bg: '#4a3800', fg: '#e3b341', label: 'ملغى' },
};

export default function RunsPage() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    fetch('/api/runs').then((r) => r.json()).then((d) => { setRuns(d.runs ?? []); setLoaded(true); });
  }, []);

  return (
    <main style={{ maxWidth: 900, margin: '0 auto', padding: 24 }}>
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <h1 style={{ fontSize: 20, margin: 0 }}>📜 سجل التشغيلات الدائم</h1>
        <a href="/" style={{ color: '#58a6ff', fontSize: 14, textDecoration: 'none' }}>← العودة للمحادثة</a>
      </header>
      {!loaded && <p style={{ color: '#8b949e' }}>جارٍ التحميل…</p>}
      {loaded && runs.length === 0 && <p style={{ color: '#8b949e' }}>لا تشغيلات محفوظة بعد.</p>}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {runs.map((r) => {
          const s = statusStyle[r.status] ?? statusStyle.goal_submitted;
          return (
            <a key={r.id} href={`/runs/${r.id}`} style={{
              display: 'block', padding: '14px 16px', borderRadius: 10, textDecoration: 'none',
              background: '#161b22', border: '1px solid #30363d', color: '#e6edf3',
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
                <span style={{ fontSize: 14, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.task}</span>
                <span style={{ padding: '3px 10px', borderRadius: 999, fontSize: 12, fontWeight: 700, background: s.bg, color: s.fg, flexShrink: 0 }}>{s.label}</span>
              </div>
              <div style={{ marginTop: 6, fontSize: 12, color: '#8b949e', fontFamily: 'ui-monospace, monospace' }}>
                {r.id.slice(0, 8)} · {new Date(r.createdAt).toLocaleString()}
                {r.cancelReason ? ` · سبب الإلغاء: ${r.cancelReason}` : ''}
              </div>
            </a>
          );
        })}
      </div>
    </main>
  );
}
