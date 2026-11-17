'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

/* ─────────── Types ─────────── */
interface Msg { id: number; role: string; content: string; runId: string | null }
interface Trace { type: string; [k: string]: unknown }
interface RunSummary { id: string; task: string; status: string; createdAt: number }
interface PlanStep { tool: string; input: Record<string, unknown> }

const EVENT_TYPES = ['run:start', 'plan:created', 'step:start', 'step:result', 'step:error',
  'run:done', 'run:failed', 'run:cancelled', 'approval:pending', 'approval:decided', 'run:needs_clarification'];

/* ─────────── Theme (Claude-warm × Manus-structure) ─────────── */
const T = {
  bg: '#f5f4ef', panel: '#fbfaf7', border: '#e4e1d7', borderSoft: '#edeae0',
  ink: '#1a1915', sub: '#77746a', accent: '#c9643f', accentSoft: '#f7e8e1',
  green: '#3d7a4f', greenSoft: '#e6f0e8', amber: '#b07d18', amberSoft: '#f8efdb',
  red: '#b3372f', redSoft: '#f7e5e3', blue: '#3563a8', blueSoft: '#e6edf7',
  dark: '#23211c', darkInk: '#efede6',
};

const statusMeta: Record<string, { label: string; fg: string; bg: string }> = {
  goal_submitted: { label: 'مُستلم', fg: T.sub, bg: T.borderSoft },
  planning: { label: 'يخطط…', fg: T.blue, bg: T.blueSoft },
  plan_validated: { label: 'خطة صالحة', fg: T.blue, bg: T.blueSoft },
  plan_rejected: { label: 'خطة مرفوضة', fg: T.red, bg: T.redSoft },
  needs_clarification: { label: 'يحتاج توضيحًا', fg: T.blue, bg: T.blueSoft },
  executing: { label: 'ينفذ…', fg: T.amber, bg: T.amberSoft },
  awaiting_approval: { label: 'بانتظار موافقتك', fg: T.accent, bg: T.accentSoft },
  completed: { label: 'مكتمل', fg: T.green, bg: T.greenSoft },
  failed: { label: 'فشل', fg: T.red, bg: T.redSoft },
  cancelled: { label: 'ملغى', fg: T.amber, bg: T.amberSoft },
};

const toolIcon: Record<string, string> = {
  'browser.run': '🌐', 'web.fetch': '📡', 'shell.exec': '⌨️', 'fs.readwrite': '📄',
};

/* ─────────── Page ─────────── */
export default function Home() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [trace, setTrace] = useState<Trace[]>([]);
  const [plan, setPlan] = useState<PlanStep[] | null>(null);
  const [runStatus, setRunStatus] = useState<string>('');
  const [input, setInput] = useState('');
  const [running, setRunning] = useState(false);
  const [currentRunId, setCurrentRunId] = useState<string | null>(null);
  const [pendingApproval, setPendingApproval] = useState<{ approvalId: string; tool: string; input: unknown } | null>(null);
  const [tab, setTab] = useState<'browser' | 'terminal' | 'files' | 'events'>('events');
  const [files, setFiles] = useState<{ name: string; size: number }[]>([]);
  const [openFile, setOpenFile] = useState<{ name: string; content: string; isImage: boolean } | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const chatEndRef = useRef<HTMLDivElement | null>(null);
  const panelEndRef = useRef<HTMLDivElement | null>(null);

  /** Serverless fallback: reconcile live state from the persisted run row
   *  (Turso) — covers hosts where SSE lands on a different instance. */
  function reconcileFromRow(r: { status: string; plan: { steps: PlanStep[] } | null; trace: Trace[] | null }) {
    const dbTrace = r.trace ?? [];
    setTrace((prev) => (dbTrace.length > prev.length ? dbTrace : prev));
    if (r.plan?.steps) setPlan(r.plan.steps);
    setRunStatus(r.status);
    // derive pending approval from persisted trace
    let pend: { approvalId: string; tool: string; input: unknown } | null = null;
    for (const e of dbTrace) {
      if (e.type === 'approval:pending') pend = { approvalId: e.approvalId as string, tool: e.tool as string, input: e.input };
      if (e.type === 'approval:decided') pend = null;
    }
    setPendingApproval(r.status === 'awaiting_approval' ? pend : null);
    if (['completed', 'failed', 'cancelled', 'needs_clarification', 'plan_rejected'].includes(r.status)) {
      stopLive();
      void loadMessages(currentRunIdRef.current);
      void loadRuns();
      if (currentRunIdRef.current) void loadFiles(currentRunIdRef.current);
    }
  }

  function stopLive() {
    esRef.current?.close();
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    setRunning(false);
  }

  const loadRuns = useCallback(async () => {
    const d = await (await fetch('/api/runs')).json();
    setRuns(d.runs ?? []);
  }, []);
  const loadFiles = useCallback(async (runId: string) => {
    const d = await (await fetch(`/api/runs/${runId}/files`)).json();
    setFiles(d.files ?? []);
  }, []);
  const viewFile = useCallback(async (runId: string, name: string) => {
    const isImage = /\.(png|jpe?g|gif)$/i.test(name);
    if (isImage) { setOpenFile({ name, content: `/api/runs/${runId}/file?name=${encodeURIComponent(name)}`, isImage: true }); return; }
    const res = await fetch(`/api/runs/${runId}/file?name=${encodeURIComponent(name)}`);
    setOpenFile({ name, content: (await res.text()).slice(0, 20000), isImage: false });
  }, []);
  const loadMessages = useCallback(async (runId?: string | null) => {
    const d = await (await fetch(runId ? `/api/messages?runId=${runId}` : '/api/messages')).json();
    setMessages(d.messages ?? []);
  }, []);

  useEffect(() => { void loadRuns(); }, [loadRuns]);
  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }, [messages, plan, pendingApproval]);
  useEffect(() => { panelEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }, [trace]);

  function applyEvent(t: string, data: Trace) {
    setTrace((prev) => [...prev, data]);
    if (t === 'step:start') {
      const tool = data.tool as string;
      if (tool === 'browser.run') setTab('browser');
      else if (tool === 'shell.exec') setTab('terminal');
      else if (tool === 'fs.readwrite') setTab('files');
    }
    if ((t === 'step:result' || ['run:done', 'run:failed', 'run:cancelled'].includes(t)) && currentRunIdRef.current) {
      void loadFiles(currentRunIdRef.current);
    }
    if (t === 'run:start') setRunStatus('planning');
    if (t === 'plan:created') { setPlan((data.plan as { steps: PlanStep[] }).steps); setRunStatus('executing'); }
    if (t === 'approval:pending') {
      setRunStatus('awaiting_approval');
      setPendingApproval({ approvalId: data.approvalId as string, tool: data.tool as string, input: data.input });
    }
    if (t === 'approval:decided') { setPendingApproval(null); setRunStatus('executing'); }
    if (t === 'run:done') setRunStatus('completed');
    if (t === 'run:failed') setRunStatus('failed');
    if (t === 'run:cancelled') setRunStatus('cancelled');
    if (t === 'run:needs_clarification') setRunStatus('needs_clarification');
    if (['run:done', 'run:failed', 'run:cancelled', 'run:needs_clarification'].includes(t)) {
      stopLive();
      setPendingApproval(null);
      void loadMessages(currentRunIdRef.current);
      void loadRuns();
    }
  }
  const currentRunIdRef = useRef<string | null>(null);

  async function send() {
    const prompt = input.trim();
    if (!prompt || running) return;
    setRunning(true); setTrace([]); setPlan(null); setRunStatus('goal_submitted'); setInput('');
    setFiles([]); setOpenFile(null); setTab('events');
    const res = await fetch('/api/tasks', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ prompt }),
    });
    const { runId } = await res.json();
    setCurrentRunId(runId); currentRunIdRef.current = runId;
    await loadMessages(runId); await loadRuns();
    esRef.current?.close();
    const es = new EventSource(`/api/runs/${runId}/events`);
    esRef.current = es;
    for (const t of EVENT_TYPES) {
      es.addEventListener(t, (ev) => applyEvent(t, JSON.parse((ev as MessageEvent).data) as Trace));
    }
    es.onerror = () => { es.close(); };
    // DB polling safety net (every 3s) — merges whichever channel is ahead.
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(() => {
      void (async () => {
        const res = await fetch(`/api/runs/${runId}`).catch(() => null);
        if (res?.ok) reconcileFromRow((await res.json()).run);
      })();
    }, 3000);
  }

  async function openRun(runId: string) {
    if (running) return;
    setCurrentRunId(runId); currentRunIdRef.current = runId;
    const d = await (await fetch(`/api/runs/${runId}`)).json();
    const r = d.run;
    setTrace((r.trace as Trace[]) ?? []);
    setPlan(r.plan?.steps ?? null);
    setRunStatus(r.status);
    setPendingApproval(null);
    setOpenFile(null); setTab('events');
    await loadFiles(runId);
    await loadMessages(runId);
  }

  async function newTask() {
    if (running) return;
    setCurrentRunId(null); currentRunIdRef.current = null;
    setTrace([]); setPlan(null); setRunStatus(''); setPendingApproval(null);
    setMessages([]); setFiles([]); setOpenFile(null); setTab('events');
  }

  async function cancel() {
    if (!currentRunId || !running) return;
    await fetch(`/api/runs/${currentRunId}/cancel`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: 'أُلغي من الواجهة' }),
    });
  }

  async function decide(decision: 'approve' | 'reject') {
    if (!pendingApproval) return;
    await fetch(`/api/approvals/${pendingApproval.approvalId}/decide`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision, reason: decision === 'approve' ? 'موافقة من الواجهة' : 'رفض من الواجهة' }),
    });
  }

  /* step live status from trace */
  function stepState(i: number): 'idle' | 'running' | 'done' | 'error' {
    let s: 'idle' | 'running' | 'done' | 'error' = 'idle';
    for (const e of trace) {
      if (e.index !== i) continue;
      if (e.type === 'step:start') s = 'running';
      if (e.type === 'step:result') s = 'done';
      if (e.type === 'step:error') s = 'error';
    }
    return s;
  }

  const meta = statusMeta[runStatus];

  return (
    <main style={{ display: 'grid', gridTemplateColumns: '250px 1fr 330px', height: '100vh', overflow: 'hidden' }}>

      {/* ═══ Sidebar (right in RTL) ═══ */}
      <aside style={{ background: T.dark, color: T.darkInk, display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
        <div style={{ padding: '18px 16px', display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ width: 34, height: 34, borderRadius: 10, background: `linear-gradient(135deg, ${T.accent}, #a34a2a)`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 17 }}>◈</div>
          <div>
            <div style={{ fontWeight: 700, fontSize: 15 }}>نواة</div>
            <div style={{ fontSize: 11, color: '#8e8b81' }}>منصة الوكيل الذكي</div>
          </div>
        </div>
        <button onClick={() => void newTask()} style={{
          margin: '4px 14px 12px', padding: '10px 12px', borderRadius: 10, border: '1px solid #3d3a33',
          background: '#2e2b25', color: T.darkInk, fontSize: 13, fontWeight: 600, cursor: 'pointer', textAlign: 'right',
        }}>＋ مهمة جديدة</button>
        <div style={{ padding: '0 16px 6px', fontSize: 11, color: '#8e8b81', fontWeight: 600 }}>المهام السابقة</div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '0 10px', minHeight: 0 }}>
          {runs.map((r) => {
            const m = statusMeta[r.status] ?? statusMeta.goal_submitted;
            const active = r.id === currentRunId;
            return (
              <button key={r.id} onClick={() => void openRun(r.id)} style={{
                display: 'block', width: '100%', textAlign: 'right', padding: '9px 10px', marginBottom: 4,
                borderRadius: 9, border: 'none', cursor: 'pointer', fontSize: 12.5,
                background: active ? '#37342c' : 'transparent', color: T.darkInk,
              }}>
                <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.task}</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 3 }}>
                  <span style={{ width: 7, height: 7, borderRadius: '50%', background: m.fg }} />
                  <span style={{ fontSize: 10.5, color: '#8e8b81' }}>{m.label}</span>
                </div>
              </button>
            );
          })}
        </div>
        <div style={{ padding: 14, borderTop: '1px solid #35322b', fontSize: 11, color: '#8e8b81' }}>
          Groq · gpt-oss-120b · حوكمة Module 2
        </div>
      </aside>

      {/* ═══ Chat center ═══ */}
      <section style={{ display: 'flex', flexDirection: 'column', background: T.bg, minWidth: 0, minHeight: 0, overflow: 'hidden' }}>
        <header style={{ padding: '13px 26px', borderBottom: `1px solid ${T.border}`, display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: T.panel }}>
          <div style={{ fontWeight: 700, fontSize: 14.5 }}>المحادثة</div>
          {meta && (
            <span style={{ padding: '4px 12px', borderRadius: 999, fontSize: 12, fontWeight: 700, color: meta.fg, background: meta.bg }}>
              {meta.label}
            </span>
          )}
        </header>

        <div style={{ flex: 1, overflowY: 'auto', padding: '26px 8%', minHeight: 0 }}>
          {messages.length === 0 && !running && (
            <div style={{ textAlign: 'center', marginTop: '14vh' }}>
              <div style={{ fontSize: 42 }}>◈</div>
              <h2 style={{ fontWeight: 700, fontSize: 21, margin: '10px 0 6px' }}>ماذا تريد أن يُنجز الوكيل اليوم؟</h2>
              <p style={{ color: T.sub, fontSize: 13.5, maxWidth: 440, margin: '0 auto' }}>
                اكتب مهمة بلغتك الطبيعية — يخطط الوكيل، ينفذ بالأدوات، ويطلب موافقتك قبل أي خطوة حساسة.
              </p>
              <div style={{ display: 'flex', gap: 8, justifyContent: 'center', marginTop: 18, flexWrap: 'wrap' }}>
                {['هات عنوان صفحة https://example.com واحفظه في ملف', 'افتح example.com وخد أول 100 حرف من النص'].map((s) => (
                  <button key={s} onClick={() => setInput(s)} style={{ padding: '8px 14px', borderRadius: 999, border: `1px solid ${T.border}`, background: T.panel, cursor: 'pointer', fontSize: 12, color: T.sub }}>
                    {s.length > 46 ? s.slice(0, 46) + '…' : s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m) => (
            <div key={m.id} style={{ display: 'flex', justifyContent: m.role === 'user' ? 'flex-start' : 'flex-start', marginBottom: 14 }}>
              {m.role === 'user' ? (
                <div style={{ marginRight: 'auto', maxWidth: '78%', background: T.accentSoft, border: `1px solid #eed6ca`, padding: '11px 15px', borderRadius: '14px 14px 3px 14px', fontSize: 14, whiteSpace: 'pre-wrap' }}>
                  {m.content}
                </div>
              ) : (
                <div style={{ maxWidth: '88%', display: 'flex', gap: 10 }}>
                  <div style={{ width: 28, height: 28, borderRadius: 8, background: T.dark, color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, flexShrink: 0, marginTop: 2 }}>◈</div>
                  <div style={{ fontSize: 14, lineHeight: 1.75, whiteSpace: 'pre-wrap' }}>{m.content}</div>
                </div>
              )}
            </div>
          ))}

          {/* Live plan card */}
          {plan && (
            <div style={{ margin: '18px 0', border: `1px solid ${T.border}`, borderRadius: 14, background: T.panel, overflow: 'hidden' }}>
              <div style={{ padding: '10px 16px', borderBottom: `1px solid ${T.borderSoft}`, fontSize: 12.5, fontWeight: 700, color: T.sub }}>
                📋 خطة التنفيذ — {plan.length} خطوات
              </div>
              {plan.map((s, i) => {
                const st = stepState(i);
                const mark = st === 'done' ? '✓' : st === 'running' ? '◌' : st === 'error' ? '✗' : i + 1;
                const color = st === 'done' ? T.green : st === 'running' ? T.amber : st === 'error' ? T.red : T.sub;
                return (
                  <div key={i} style={{ display: 'flex', gap: 12, padding: '10px 16px', borderBottom: i < plan.length - 1 ? `1px solid ${T.borderSoft}` : 'none', alignItems: 'flex-start' }}>
                    <span style={{ width: 22, height: 22, borderRadius: '50%', border: `2px solid ${color}`, color, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, fontWeight: 700, flexShrink: 0 }}>{mark}</span>
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontSize: 13, fontWeight: 600 }}>{toolIcon[s.tool] ?? '🔧'} {s.tool}</div>
                      <div style={{ fontSize: 11.5, color: T.sub, fontFamily: 'ui-monospace, monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', direction: 'ltr', textAlign: 'left' }}>
                        {JSON.stringify(s.input).slice(0, 90)}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* Approval card */}
          {pendingApproval && (
            <div style={{ margin: '16px 0', border: `1.5px solid ${T.accent}`, borderRadius: 14, background: '#fdf6f2', padding: 16 }}>
              <div style={{ fontWeight: 700, fontSize: 14, color: T.accent, marginBottom: 6 }}>
                ⚠️ الوكيل يطلب إذنك — أداة حساسة: <code style={{ direction: 'ltr' }}>{pendingApproval.tool}</code>
              </div>
              <pre style={{ margin: '0 0 12px', fontSize: 11.5, color: T.sub, whiteSpace: 'pre-wrap', wordBreak: 'break-all', direction: 'ltr', textAlign: 'left', background: T.panel, padding: 10, borderRadius: 8, border: `1px solid ${T.borderSoft}` }}>
                {JSON.stringify(pendingApproval.input, null, 1)}
              </pre>
              <div style={{ display: 'flex', gap: 10 }}>
                <button onClick={() => void decide('approve')} style={{ padding: '9px 22px', borderRadius: 10, border: 'none', background: T.green, color: '#fff', fontWeight: 700, fontSize: 13, cursor: 'pointer' }}>✓ موافقة ومتابعة</button>
                <button onClick={() => void decide('reject')} style={{ padding: '9px 22px', borderRadius: 10, border: `1.5px solid ${T.red}`, background: 'transparent', color: T.red, fontWeight: 700, fontSize: 13, cursor: 'pointer' }}>✗ رفض وإيقاف</button>
              </div>
            </div>
          )}

          {running && !plan && (
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', color: T.sub, fontSize: 13 }}>
              <span className="pulse" style={{ width: 9, height: 9, borderRadius: '50%', background: T.accent, display: 'inline-block' }} />
              الوكيل يفكر ويخطط…
            </div>
          )}
          <div ref={chatEndRef} />
        </div>

        {/* Composer */}
        <div style={{ padding: '14px 8% 20px', background: T.bg }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', border: `1.5px solid ${T.border}`, borderRadius: 16, background: T.panel, padding: '10px 12px', boxShadow: '0 2px 10px rgba(30,28,20,.05)' }}>
            <textarea
              value={input} onChange={(e) => setInput(e.target.value)} rows={2}
              placeholder="اكتب مهمتك… مثال: هات عنوان صفحة example.com واحفظه في ملف"
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send(); } }}
              style={{ flex: 1, resize: 'none', border: 'none', outline: 'none', background: 'transparent', fontSize: 14, fontFamily: 'inherit', color: T.ink }}
            />
            {running && (
              <button onClick={() => void cancel()} title="إلغاء التشغيل" style={{ padding: '9px 14px', borderRadius: 10, border: `1.5px solid ${T.red}`, background: 'transparent', color: T.red, fontWeight: 700, cursor: 'pointer', fontSize: 13 }}>⏹</button>
            )}
            <button onClick={() => void send()} disabled={running || !input.trim()} style={{
              padding: '9px 20px', borderRadius: 10, border: 'none', fontSize: 13.5, fontWeight: 700, cursor: running ? 'default' : 'pointer',
              background: running || !input.trim() ? T.borderSoft : T.accent, color: running || !input.trim() ? T.sub : '#fff',
            }}>
              {running ? '…' : 'إرسال ↵'}
            </button>
          </div>
          <div style={{ fontSize: 11, color: T.sub, marginTop: 8, textAlign: 'center' }}>
            الأدوات الحساسة تتوقف لموافقتك · كل تشغيلة محفوظة في <a href="/runs" style={{ color: T.accent }}>السجل الدائم</a>
          </div>
        </div>
      </section>

      {/* ═══ Agent computer (left in RTL) — EXECUTION view ═══ */}
      <aside style={{ background: T.panel, borderRight: `1px solid ${T.border}`, display: 'flex', flexDirection: 'column', minWidth: 0, minHeight: 0, overflow: 'hidden' }}>
        <header style={{ padding: '10px 14px', borderBottom: `1px solid ${T.border}`, display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 15 }}>🖥️</span>
          <span style={{ fontWeight: 700, fontSize: 13.5 }}>كمبيوتر الوكيل</span>
          {running && <span className="pulse" style={{ width: 8, height: 8, borderRadius: '50%', background: T.green, marginRight: 'auto' }} />}
        </header>
        {/* Tabs */}
        <div style={{ display: 'flex', borderBottom: `1px solid ${T.border}`, background: T.bg }}>
          {([['browser', '🌐 المتصفح'], ['terminal', '⌨️ الطرفية'], ['files', '📁 الملفات'], ['events', '⚡ الأحداث']] as const).map(([k, label]) => (
            <button key={k} onClick={() => { setTab(k); setOpenFile(null); }} style={{
              flex: 1, padding: '8px 4px', border: 'none', cursor: 'pointer', fontSize: 11.5, fontWeight: 700, fontFamily: 'inherit',
              background: tab === k ? T.panel : 'transparent', color: tab === k ? T.accent : T.sub,
              borderBottom: tab === k ? `2px solid ${T.accent}` : '2px solid transparent',
            }}>{label}</button>
          ))}
        </div>

        <div style={{ flex: 1, overflowY: 'auto', minHeight: 0, background: tab === 'terminal' ? '#191817' : undefined }}>

          {/* ── 🌐 المتصفح: ما يراه الوكيل فعليًا ── */}
          {tab === 'browser' && (() => {
            const shots = trace.filter((e) => e.type === 'step:result' && (e.output as Record<string, unknown>)?.screenshot)
              .map((e) => (e.output as Record<string, string>).screenshot);
            const last = shots[shots.length - 1];
            const browsing = trace.some((e) => e.type === 'step:start' && e.tool === 'browser.run')
              && !trace.some((e) => e.type === 'step:result' && e.tool === 'browser.run');
            return (
              <div style={{ padding: 10 }}>
                {last && currentRunId ? (
                  <>
                    <div style={{ border: `1px solid ${T.border}`, borderRadius: 10, overflow: 'hidden', background: '#fff' }}>
                      <div style={{ display: 'flex', gap: 5, padding: '7px 10px', background: T.bg, borderBottom: `1px solid ${T.borderSoft}`, alignItems: 'center' }}>
                        <span style={{ width: 9, height: 9, borderRadius: '50%', background: '#f56' }} />
                        <span style={{ width: 9, height: 9, borderRadius: '50%', background: '#fb3' }} />
                        <span style={{ width: 9, height: 9, borderRadius: '50%', background: '#4c4' }} />
                        <span style={{ fontSize: 10, color: T.sub, direction: 'ltr', marginRight: 'auto', fontFamily: 'ui-monospace, monospace' }}>agent-browser</span>
                      </div>
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={`/api/runs/${currentRunId}/file?name=${encodeURIComponent(last)}`} alt="شاشة متصفح الوكيل" style={{ width: '100%', display: 'block' }} />
                    </div>
                    {shots.length > 1 && <div style={{ fontSize: 10.5, color: T.sub, marginTop: 6, textAlign: 'center' }}>{shots.length} لقطات — الأحدث معروضة</div>}
                  </>
                ) : (
                  <div style={{ color: T.sub, fontSize: 12.5, textAlign: 'center', marginTop: 50 }}>
                    {browsing ? '⏳ الوكيل يفتح الصفحة الآن…' : 'لم يفتح الوكيل متصفحًا في هذه المهمة بعد'}
                  </div>
                )}
              </div>
            );
          })()}

          {/* ── ⌨️ الطرفية: أوامر حقيقية ومخرجاتها ── */}
          {tab === 'terminal' && (
            <div style={{ padding: 12, fontFamily: 'ui-monospace, SFMono-Regular, monospace', fontSize: 11.5, direction: 'ltr', textAlign: 'left', color: '#d5d2c8' }}>
              {trace.filter((e) => (e.type === 'step:start' || e.type === 'step:result' || e.type === 'step:error') && e.tool === 'shell.exec').length === 0 && (
                <div style={{ color: '#7a776d', textAlign: 'center', marginTop: 50, fontFamily: 'inherit' }}>$ (لا أوامر بعد)</div>
              )}
              {trace.map((e, i) => {
                if (e.tool !== 'shell.exec') return null;
                if (e.type === 'step:start') {
                  const inp = e.input as { bin?: string; args?: string[] };
                  return <div key={i} style={{ marginTop: 10 }}><span style={{ color: '#7ec97f' }}>agent@nawah</span><span style={{ color: '#7a776d' }}>:~$</span> <span style={{ color: '#e8e6de' }}>{inp.bin} {(inp.args ?? []).map((a) => a.includes(' ') ? `'${a.slice(0, 120)}'` : a).join(' ').slice(0, 300)}</span></div>;
                }
                if (e.type === 'step:result') {
                  const out = e.output as { stdout?: string; stderr?: string; exitCode?: number };
                  return <div key={i} style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{(out.stdout ?? '').slice(0, 2000)}{out.stderr ? <span style={{ color: '#e08880' }}>{out.stderr.slice(0, 500)}</span> : null}<span style={{ color: '#7a776d' }}>[exit {out.exitCode}]</span></div>;
                }
                if (e.type === 'step:error') return <div key={i} style={{ color: '#e08880', whiteSpace: 'pre-wrap' }}>{String(e.error).slice(0, 300)}</div>;
                return null;
              })}
            </div>
          )}

          {/* ── 📁 الملفات: منتجات المهمة ── */}
          {tab === 'files' && (
            <div style={{ padding: 10 }}>
              {openFile ? (
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                    <button onClick={() => setOpenFile(null)} style={{ border: `1px solid ${T.border}`, background: T.bg, borderRadius: 7, padding: '3px 10px', cursor: 'pointer', fontSize: 11 }}>→ رجوع</button>
                    <span style={{ fontSize: 12, fontWeight: 700, direction: 'ltr' }}>{openFile.name}</span>
                    {currentRunId && <a href={`/api/runs/${currentRunId}/file?name=${encodeURIComponent(openFile.name)}&download=1`} style={{ marginRight: 'auto', fontSize: 11, color: T.accent }}>⬇ تنزيل</a>}
                  </div>
                  {openFile.isImage
                    // eslint-disable-next-line @next/next/no-img-element
                    ? <img src={openFile.content} alt={openFile.name} style={{ width: '100%', borderRadius: 8, border: `1px solid ${T.border}` }} />
                    : <pre style={{ margin: 0, background: T.bg, border: `1px solid ${T.borderSoft}`, borderRadius: 8, padding: 10, fontSize: 11.5, whiteSpace: 'pre-wrap', wordBreak: 'break-all', direction: 'ltr', textAlign: 'left', maxHeight: '62vh', overflow: 'auto' }}>{openFile.content || '(ملف فارغ)'}</pre>}
                </div>
              ) : (
                <>
                  {files.length === 0 && <div style={{ color: T.sub, fontSize: 12.5, textAlign: 'center', marginTop: 50 }}>لا ملفات منتَجة بعد</div>}
                  {files.map((fl) => (
                    <button key={fl.name} onClick={() => currentRunId && void viewFile(currentRunId, fl.name)} style={{
                      display: 'flex', width: '100%', alignItems: 'center', gap: 9, padding: '9px 10px', marginBottom: 5,
                      border: `1px solid ${T.borderSoft}`, borderRadius: 9, background: T.bg, cursor: 'pointer', fontFamily: 'inherit',
                    }}>
                      <span style={{ fontSize: 15 }}>{/\.(png|jpe?g|gif)$/i.test(fl.name) ? '🖼️' : '📄'}</span>
                      <span style={{ fontSize: 12, fontWeight: 600, direction: 'ltr', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{fl.name}</span>
                      <span style={{ marginRight: 'auto', fontSize: 10.5, color: T.sub }}>{fl.size < 1024 ? `${fl.size} B` : `${(fl.size / 1024).toFixed(1)} KB`}</span>
                    </button>
                  ))}
                </>
              )}
            </div>
          )}

          {/* ── ⚡ الأحداث: الخط الزمني ── */}
          {tab === 'events' && (
            <div style={{ padding: 14 }}>
              {trace.length === 0 && (
                <div style={{ color: T.sub, fontSize: 12.5, textAlign: 'center', marginTop: 40 }}>
                  لا نشاط بعد — أرسل مهمة وشاهد التنفيذ الحي هنا
                </div>
              )}
          {trace.map((e, i) => {
            const kind = e.type as string;
            const isStep = kind.startsWith('step:');
            const icon = kind === 'run:start' ? '🚀' : kind === 'plan:created' ? '📋'
              : kind === 'step:start' ? (toolIcon[e.tool as string] ?? '🔧')
              : kind === 'step:result' ? '✅' : kind === 'step:error' ? '⚠️'
              : kind === 'approval:pending' ? '✋' : kind === 'approval:decided' ? '🗳️'
              : kind === 'run:done' ? '🏁' : kind === 'run:cancelled' ? '⏹' : kind === 'run:needs_clarification' ? '❓' : '❌';
            const title = kind === 'run:start' ? 'بدأ التشغيل' : kind === 'plan:created' ? 'الخطة جاهزة'
              : kind === 'step:start' ? `تشغيل ${e.tool}` : kind === 'step:result' ? `اكتمل ${e.tool}`
              : kind === 'step:error' ? `تعثّر ${e.tool}` : kind === 'approval:pending' ? 'بانتظار الموافقة'
              : kind === 'approval:decided' ? `القرار: ${e.decision === 'approved' ? 'موافقة' : 'رفض'}`
              : kind === 'run:done' ? 'اكتملت المهمة' : kind === 'run:cancelled' ? 'أُلغيت المهمة'
              : kind === 'run:needs_clarification' ? 'يحتاج توضيحًا' : 'فشلت المهمة';
            const detail = kind === 'step:start' ? JSON.stringify(e.input)
              : kind === 'step:result' ? JSON.stringify(e.output)
              : kind === 'step:error' ? String(e.error) : kind === 'run:done' ? String(e.answer)
              : kind === 'run:failed' ? String(e.error) : kind === 'run:needs_clarification' ? String(e.question) : '';
            return (
              <div key={i} style={{ display: 'flex', gap: 10, marginBottom: 2 }}>
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                  <div style={{ width: 26, height: 26, borderRadius: 8, background: T.bg, border: `1px solid ${T.border}`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, flexShrink: 0 }}>{icon}</div>
                  {i < trace.length - 1 && <div style={{ width: 2, flex: 1, background: T.borderSoft, minHeight: 8 }} />}
                </div>
                <div style={{ paddingBottom: 12, minWidth: 0, flex: 1 }}>
                  <div style={{ fontSize: 12.5, fontWeight: 600 }}>{title}
                    {isStep && typeof e.index === 'number' && <span style={{ color: T.sub, fontWeight: 400 }}> · خطوة {(e.index as number) + 1}</span>}
                  </div>
                  {detail && (
                    <div style={{ fontSize: 11, color: T.sub, fontFamily: kind === 'run:done' ? 'inherit' : 'ui-monospace, monospace', direction: kind === 'run:done' ? 'rtl' : 'ltr', textAlign: kind === 'run:done' ? 'right' : 'left', background: T.bg, border: `1px solid ${T.borderSoft}`, borderRadius: 7, padding: '5px 8px', marginTop: 4, overflow: 'hidden', wordBreak: 'break-all', maxHeight: 76 }}>
                      {detail.slice(0, 220)}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
          <div ref={panelEndRef} />
            </div>
          )}
        </div>
      </aside>

      <style>{`
        .pulse { animation: pulse 1.4s ease-in-out infinite; }
        @keyframes pulse { 0%,100% { opacity: 1 } 50% { opacity: .25 } }
        ::-webkit-scrollbar { width: 8px } ::-webkit-scrollbar-thumb { background: ${T.border}; border-radius: 4px }
      `}</style>
    </main>
  );
}
