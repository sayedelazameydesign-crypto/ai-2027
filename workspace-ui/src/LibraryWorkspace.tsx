import { useCallback, useEffect, useState } from 'react';
import {
  AlertCircle,
  ArrowDownToLine,
  BadgeCheck,
  CheckCircle2,
  Clock3,
  FileText,
  HardDrive,
  History,
  LoaderCircle,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
} from 'lucide-react';
import {
  ApiError,
  formatTimestamp,
  requestJson,
  type ConnectionState,
  type Language,
  type StudioSession,
  type StudioStatus,
} from './integrations';

const copyByLanguage = {
  ar: {
    title: 'المكتبة',
    subtitle: 'ذاكرة Celia المُدارة بالأدلة، وسجل الجلسات، والمخرجات الموثّقة',
    source: 'مصدر البيانات: Agent Studio · StudioStatus.memory',
    refresh: 'تحديث',
    loading: 'جارٍ تحميل المكتبة…',
    offline: 'تعذر قراءة المكتبة الحية.',
    memory: 'ذاكرة الأدلة (celia_memory)',
    memoryAvailable: 'متاحة',
    memoryUnavailable: 'غير متاحة',
    totalMemories: 'إجمالي الذكريات',
    verifiedArtifacts: 'مخرجات موثّقة',
    refusals: 'رفوض',
    noiseFloor: 'عتبة الضوضاء (p95)',
    sessions: 'سجل الجلسات',
    noSessions: 'لا توجد جلسات محفوظة بعد.',
    openSession: 'فتح في المهام',
    artifacts: 'المخرجات والأدلة',
    noArtifacts: 'لا توجد مخرجات مسجّلة.',
    verified: 'تحقق مستقل ناجح',
    notVerified: 'لم يثبت التحقق',
    download: 'تنزيل',
    evidenceBundle: 'تنزيل حزمة الأدلة',
    statusIdle: 'جاهزة',
    statusRunning: 'قيد التشغيل',
    statusCompleted: 'مكتملة',
    statusAborted: 'أُلغيت',
    statusFailed: 'فشلت',
    statusUnknown: 'غير معروفة',
    checkedAt: 'آخر فحص',
  },
  en: {
    title: 'Library',
    subtitle: 'Celia’s evidence-gated memory, the session log, and documented outputs',
    source: 'Data source: Agent Studio · StudioStatus.memory',
    refresh: 'Refresh',
    loading: 'Loading the library…',
    offline: 'Could not read the live library.',
    memory: 'Evidence memory (celia_memory)',
    memoryAvailable: 'Available',
    memoryUnavailable: 'Unavailable',
    totalMemories: 'Total memories',
    verifiedArtifacts: 'Verified artifacts',
    refusals: 'Refusals',
    noiseFloor: 'Noise floor (p95)',
    sessions: 'Session log',
    noSessions: 'No saved sessions yet.',
    openSession: 'Open in Tasks',
    artifacts: 'Outputs and evidence',
    noArtifacts: 'No outputs are recorded.',
    verified: 'Independently verified',
    notVerified: 'Verification not established',
    download: 'Download',
    evidenceBundle: 'Download evidence bundle',
    statusIdle: 'Ready',
    statusRunning: 'Running',
    statusCompleted: 'Completed',
    statusAborted: 'Aborted',
    statusFailed: 'Failed',
    statusUnknown: 'Unknown',
    checkedAt: 'Last checked',
  },
} as const;

type Copy = { [Key in keyof (typeof copyByLanguage)['en']]: string };

interface LibraryWorkspaceProps {
  lang: Language;
  onConnectionChange: (state: ConnectionState) => void;
  onOpenSession: (sessionId: string) => void;
}

function labelForStatus(status: string, copy: Copy): string {
  switch (status) {
    case 'idle': return copy.statusIdle;
    case 'running': return copy.statusRunning;
    case 'completed': return copy.statusCompleted;
    case 'aborted': return copy.statusAborted;
    case 'failed': return copy.statusFailed;
    default: return copy.statusUnknown;
  }
}

export function LibraryWorkspace({ lang, onConnectionChange, onOpenSession }: LibraryWorkspaceProps) {
  const copy = copyByLanguage[lang];
  const [status, setStatus] = useState<StudioStatus | null>(null);
  const [sessions, setSessions] = useState<StudioSession[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    onConnectionChange('checking');
    const [statusResult, sessionsResult] = await Promise.allSettled([
      requestJson<StudioStatus>('/studio-api/v1/studio/status'),
      requestJson<StudioSession[]>('/studio-api/v1/studio/sessions'),
    ]);
    if (statusResult.status === 'fulfilled') {
      setStatus(statusResult.value);
      setError(null);
      onConnectionChange('online');
    } else {
      setStatus(null);
      setError(copy.offline);
      onConnectionChange('offline');
    }
    setSessions(sessionsResult.status === 'fulfilled' ? sessionsResult.value : []);
    setLoading(false);
  }, [copy.offline, onConnectionChange]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const memory = status?.memory;
  const nf = typeof memory?.noise_floor_p95 === 'number' ? memory.noise_floor_p95.toFixed(3) : '—';

  return (
    <div className="integration-page library-page" dir={lang === 'ar' ? 'rtl' : 'ltr'}>
      <div className="integration-page-header">
        <div className="integration-heading">
          <div className="integration-heading-icon library-heading-icon"><History size={19} /></div>
          <div>
            <div className="integration-eyebrow">{copy.source}</div>
            <h1>{copy.title}</h1>
            <p>{copy.subtitle}</p>
          </div>
        </div>
        <div className="integration-header-actions">
          <button className="secondary-button" onClick={() => void refresh()} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'spin' : ''} />{copy.refresh}
          </button>
        </div>
      </div>

      {error && (
        <div className="integration-alert integration-alert-error" role="alert">
          <AlertCircle size={16} /><span>{error}</span>
        </div>
      )}

      <section className="library-grid">
        <article className="studio-surface library-memory-card">
          <div className="studio-section-heading">
            <div><span className="section-kicker"><BadgeCheck size={13} />{copy.memory}</span><h2>{copy.memory}</h2></div>
            {memory && <span className={`session-status-chip ${memory.available ? 'session-completed' : 'session-failed'}`}><i />{memory.available ? copy.memoryAvailable : copy.memoryUnavailable}</span>}
          </div>
          <div className="system-data-list">
            <div><span>{copy.totalMemories}</span><strong>{memory ? String(memory.total_memories) : '—'}</strong></div>
            <div><span>{copy.verifiedArtifacts}</span><strong>{memory ? String(memory.verified_artifacts) : '—'}</strong></div>
            <div><span>{copy.refusals}</span><strong>{memory ? String(memory.refusals) : '—'}</strong></div>
            <div><span>{copy.noiseFloor}</span><strong dir="ltr">{nf}</strong></div>
          </div>
        </article>

        <article className="studio-surface library-sessions-card">
          <div className="studio-section-heading">
            <div><span className="section-kicker"><Clock3 size={13} />{copy.sessions}</span><h2>{copy.sessions}</h2></div>
            <span className="section-count">{sessions.length}</span>
          </div>
          {loading && sessions.length === 0 ? (
            <div className="studio-empty-state"><LoaderCircle size={18} className="spin" />{copy.loading}</div>
          ) : sessions.length === 0 ? (
            <div className="studio-empty-state"><History size={18} /><p>{copy.noSessions}</p></div>
          ) : (
            <div className="library-session-list">
              {sessions.slice(0, 24).map((session) => (
                <button key={session.session_id} className="library-session-item" onClick={() => onOpenSession(session.session_id)}>
                  <span className={`session-list-dot session-dot-${session.status}`} />
                  <span className="library-session-copy">
                    <strong>{session.title || copy.title}</strong>
                    <small>{formatTimestamp(session.updated_at, lang)} · {session.session_id.slice(-8)}</small>
                  </span>
                  <span className={`session-mini-status mini-${session.status}`}>{labelForStatus(session.status, copy)}</span>
                  <ArrowDownToLine size={13} className="library-session-open" />
                </button>
              ))}
            </div>
          )}
        </article>
      </section>
    </div>
  );
}
