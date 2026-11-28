import { useCallback, useEffect, useState } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  LoaderCircle,
  Lock,
  RefreshCw,
  Server,
  ShieldCheck,
  Wallet,
} from 'lucide-react';
import {
  ApiError,
  formatTimestamp,
  requestJson,
  type ConnectionState,
  type Language,
  type StudioStatus,
} from './integrations';
import { capabilityMatrix, nameOf, notesOf, type Language as CapLang } from './capabilities';

const copyByLanguage = {
  ar: {
    title: 'الإعدادات والحوكمة',
    subtitle: 'الميزانية والنواة والبوابة المحلية — قراءة فقط من واجهات حقيقية',
    source: 'مصدر البيانات: Agent Studio · kernel-2027 · vite.config.ts',
    refresh: 'تحديث',
    loading: 'جارٍ تحميل حالة الحوكمة…',
    offline: 'تعذر قراءة حالة الحوكمة الحية.',
    budgetGuard: 'حارس الميزانية (BudgetGuard)',
    budgetNote: 'الإنفاق الافتراضي صفر. أي مزوّد بعيد محجوب ما لم تُثبت تكلفته ضمن الحد المسموح.',
    spent: 'الإنفاق المسجل',
    maxSpend: 'الحد الأعلى',
    zeroLocal: 'مكالمات محلية صفرية التكلفة',
    freeTier: 'مكالمات ضمن الفئة المجانية',
    unknownBlocked: 'محجوبة بتكلفة غير معروفة',
    paidBlocked: 'محجوبة مدفوعة',
    keyNeverFree: 'وجود مفتاح لا يثبت مجانية',
    kernel: 'حالة النواة',
    kernelReady: 'النواة متاحة',
    kernelUnavailable: 'النواة غير متاحة',
    readOnly: 'وضع القراءة فقط',
    writable: 'التغييرات متاحة وفق حواجز الأمان',
    sessions: 'الجلسات',
    pendingApprovals: 'موافقات معلّقة',
    localGateway: 'البوابة المحلية المقيّدة',
    gatewayNote: 'خادم التطوير محصور على 127.0.0.1 فقط. لا تُحقن أسرار في حزمة المتصفح، ولا تُفتح بوابة الاعتمادات على 0.0.0.0.',
    gatewayHost: 'المضيف المسموح',
    gatewayAllowed: 'النطاقات المسموحة',
    gatewayCreds: 'حقن الاعتمادات',
    honesty: 'الصدق الهندسي',
    honestyNote: 'كل قدرة ومزوّد وموصل يُوسم بشارة حالة مشتقّة من الواقع فقط: منفّذ / جزئي / مخطّط / غير متاح.',
    viewCapabilityMap: 'عرض خريطة القدرات',
    checkedAt: 'آخر فحص',
    goProjects: 'خريطة القدرات والمشاريع',
  },
  en: {
    title: 'Settings & Governance',
    subtitle: 'Budget, kernel and the local gateway — read-only from real interfaces',
    source: 'Data source: Agent Studio · kernel-2027 · vite.config.ts',
    refresh: 'Refresh',
    loading: 'Loading governance status…',
    offline: 'Could not read live governance status.',
    budgetGuard: 'Budget guard',
    budgetNote: 'Default spend is zero. Any remote provider is blocked unless its cost is proven within the allowed cap.',
    spent: 'Recorded spend',
    maxSpend: 'Max spend',
    zeroLocal: 'Zero-cost local calls',
    freeTier: 'Declared free-tier calls',
    unknownBlocked: 'Unknown-cost blocked',
    paidBlocked: 'Paid blocked',
    keyNeverFree: 'A key never implies free',
    kernel: 'Kernel status',
    kernelReady: 'Kernel available',
    kernelUnavailable: 'Kernel unavailable',
    readOnly: 'Read-only mode',
    writable: 'Changes allowed behind safety gates',
    sessions: 'Sessions',
    pendingApprovals: 'Pending approvals',
    localGateway: 'Loopback-only local gateway',
    gatewayNote: 'The dev server is bound to 127.0.0.1 only. No secrets are injected into the browser bundle, and the credential gateway never opens on 0.0.0.0.',
    gatewayHost: 'Allowed host',
    gatewayAllowed: 'Allowed hosts',
    gatewayCreds: 'Credential injection',
    honesty: 'Engineering honesty',
    honestyNote: 'Every capability, provider and connector carries a status badge derived from reality only: implemented / partial / planned / unavailable.',
    viewCapabilityMap: 'View capability map',
    checkedAt: 'Last checked',
    goProjects: 'Capability & project map',
  },
} as const;

type Copy = { [Key in keyof (typeof copyByLanguage)['en']]: string };

interface SettingsWorkspaceProps {
  lang: Language;
  onConnectionChange: (state: ConnectionState) => void;
  onOpenProjects: () => void;
}

export function SettingsWorkspace({ lang, onConnectionChange, onOpenProjects }: SettingsWorkspaceProps) {
  const copy = copyByLanguage[lang];
  const [status, setStatus] = useState<StudioStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    onConnectionChange('checking');
    try {
      const payload = await requestJson<StudioStatus>('/studio-api/v1/studio/status');
      setStatus(payload);
      setError(null);
      onConnectionChange('online');
    } catch (caught) {
      setStatus(null);
      setError(caught instanceof ApiError ? copy.offline : copy.offline);
      onConnectionChange('offline');
    } finally {
      setLoading(false);
    }
  }, [copy.offline, onConnectionChange]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const budget = status?.budget;
  const gatewayCapability = capabilityMatrix.capabilities.find((c) => c.id === 'local-gateway');

  return (
    <div className="integration-page settings-page" dir={lang === 'ar' ? 'rtl' : 'ltr'}>
      <div className="integration-page-header">
        <div className="integration-heading">
          <div className="integration-heading-icon settings-heading-icon"><ShieldCheck size={19} /></div>
          <div>
            <div className="integration-eyebrow">{copy.source}</div>
            <h1>{copy.title}</h1>
            <p>{copy.subtitle}</p>
          </div>
        </div>
        <div className="integration-header-actions">
          {status && <span className="service-status service-online"><i />{copy.kernelReady}</span>}
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

      <section className="settings-grid">
        <article className="studio-surface governance-card">
          <div className="studio-section-heading">
            <div><span className="section-kicker">{copy.budgetGuard}</span><h2><Wallet size={16} />{copy.budgetGuard}</h2></div>
          </div>
          <p className="governance-note"><Lock size={13} />{copy.budgetNote}</p>
          <div className="system-data-list">
            <div><span>{copy.spent}</span><strong>{budget ? `$${budget.spent_usd.toFixed(4)}` : '—'}</strong></div>
            <div><span>{copy.maxSpend}</span><strong>{budget ? `$${budget.max_spend_usd.toFixed(2)}` : '—'}</strong></div>
            <div><span>{copy.zeroLocal}</span><strong>{budget ? String(budget.known_zero_local_calls) : '—'}</strong></div>
            <div><span>{copy.freeTier}</span><strong>{budget ? String(budget.declared_free_tier_calls) : '—'}</strong></div>
            <div><span>{copy.unknownBlocked}</span><strong>{budget ? String(budget.unknown_cost_blocked) : '—'}</strong></div>
            <div><span>{copy.paidBlocked}</span><strong>{budget ? String(budget.paid_blocked) : '—'}</strong></div>
          </div>
          <div className="system-mode-strip mode-writable"><ShieldCheck size={14} /><span>{budget?.api_key_never_implies_free ? copy.keyNeverFree : copy.budgetNote}</span></div>
        </article>

        <article className="studio-surface governance-card">
          <div className="studio-section-heading">
            <div><span className="section-kicker">{copy.kernel}</span><h2><Server size={16} />{copy.kernel}</h2></div>
            {status && <span className={`session-status-chip ${status.kernel_available ? 'session-completed' : 'session-failed'}`}><i />{status.kernel_available ? 'LIVE' : 'OFFLINE'}</span>}
          </div>
          <div className="system-data-list">
            <div><span>{copy.kernel}</span><strong className={status?.kernel_available ? 'metric-good-text' : 'metric-muted-text'}>{status ? (status.kernel_available ? copy.kernelReady : copy.kernelUnavailable) : '—'}</strong></div>
            <div><span>{copy.readOnly}</span><strong>{status ? (status.read_only ? copy.readOnly : copy.writable) : '—'}</strong></div>
            <div><span>{copy.sessions}</span><strong>{status ? String(status.sessions_count) : '—'}</strong></div>
            <div><span>{copy.pendingApprovals}</span><strong>{status ? String(status.pending_approvals) : '—'}</strong></div>
          </div>
        </article>

        <article className="studio-surface governance-card">
          <div className="studio-section-heading">
            <div><span className="section-kicker">{copy.localGateway}</span><h2><Lock size={16} />{copy.localGateway}</h2></div>
          </div>
          <p className="governance-note"><ShieldCheck size={13} />{copy.gatewayNote}</p>
          <div className="system-data-list">
            <div><span>{copy.gatewayHost}</span><strong dir="ltr">127.0.0.1</strong></div>
            <div><span>{copy.gatewayAllowed}</span><strong dir="ltr">127.0.0.1 · localhost</strong></div>
            <div><span>{copy.gatewayCreds}</span><strong>{lang === 'ar' ? 'محصورة خادميًا فقط' : 'Server-side only'}</strong></div>
          </div>
          {gatewayCapability && (
            <div className="governance-source">
              <code>{gatewayCapability.source}</code>
              <span>{notesOf(gatewayCapability, lang as CapLang)}</span>
            </div>
          )}
        </article>

        <article className="studio-surface governance-card governance-honesty">
          <div className="studio-section-heading">
            <div><span className="section-kicker">{copy.honesty}</span><h2><CheckCircle2 size={16} />{copy.honesty}</h2></div>
          </div>
          <p className="governance-note"><ShieldCheck size={13} />{copy.honestyNote}</p>
          <button className="secondary-button" onClick={onOpenProjects}>
            <RefreshCw size={14} />{copy.viewCapabilityMap}
          </button>
        </article>
      </section>

      {loading && !status && (
        <div className="integration-loading"><LoaderCircle size={18} className="spin" />{copy.loading}</div>
      )}
    </div>
  );
}
