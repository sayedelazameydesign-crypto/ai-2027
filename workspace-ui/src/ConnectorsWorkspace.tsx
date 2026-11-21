import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  CalendarDays,
  Check,
  ChevronDown,
  Clock3,
  Cloud,
  Database,
  FileText,
  Github,
  HardDrive,
  Inbox,
  KeyRound,
  Layers3,
  LoaderCircle,
  Mail,
  Network,
  RefreshCw,
  Search,
  Shield,
  ShieldAlert,
  Table2,
  Trello,
  X,
} from 'lucide-react';
import {
  ApiError,
  formatTimestamp,
  requestJson,
  type ConnectionState,
  type HubCatalog,
  type HubConnector,
  type Language,
} from './integrations';

const copyByLanguage = {
  ar: {
    title: 'Connectors Hub',
    subtitle: 'كتالوج حي من خدمة الموصلات — لا بطاقات تجريبية',
    source: 'مصدر البيانات: Connectors Hub API',
    refresh: 'تحديث الكتالوج',
    checking: 'جارٍ الاتصال بـ Hub…',
    connected: 'Connectors Hub متصل',
    offline: 'Connectors Hub غير متاح',
    missingToken: 'يلزم تهيئة توكن الخدمة على خادم Agent Studio.',
    unauthorized: 'رفض Connectors Hub بيانات اعتماد الخدمة.',
    serviceUnavailable: 'الخدمة غير متاحة أو تحتاج تهيئة محلية.',
    upstreamError: 'أعاد Connectors Hub استجابة غير ناجحة.',
    invalidEndpoint: 'عنوان Connectors Hub غير صالح أو غير آمن.',
    invalidPayload: 'تعذر التحقق من شكل الكتالوج الوارد.',
    networkError: 'تعذر الوصول إلى واجهة Agent Studio.',
    requestError: 'تعذر تحميل كتالوج الموصلات.',
    retry: 'إعادة المحاولة',
    searchPlaceholder: 'ابحث عن موصل أو نوع اعتماد…',
    all: 'الكل',
    configured: 'مهيّأ',
    unconfigured: 'غير مهيّأ',
    needsResource: 'يحتاج إعداد مورد',
    noConnectors: 'لا توجد موصلات في الكتالوج الحي.',
    noMatches: 'لا توجد نتائج تطابق البحث.',
    count: 'موصلات',
    authGroup: 'مجموعة الاعتماد',
    searchCapability: 'البحث مدعوم',
    readCapability: 'القراءة مدعومة',
    noSearchCapability: 'البحث غير مدعوم',
    noReadCapability: 'القراءة غير مدعومة',
    configuredLabel: 'بيانات اعتماد موجودة · لم يُتحقق من اتصال حي',
    unconfiguredLabel: 'يلزم إعداد بيانات الاعتماد أو التفويض',
    resourceLabel: 'الاعتماد موجود · يلزم تحديد المورد أو قائمة السماح',
    unknownLabel: 'حالة غير معروفة من الخدمة',
    liveVerification: 'الكتالوج يبيّن حالة التهيئة فقط ولا يختبر اتصالًا حيًا؛ لذلك لا نَصِف أي موصل بأنه جاهز دون قراءة موثّقة.',
    writeGate: 'لا تعرض هذه الصفحة بيانات اعتماد ولا تنفّذ كتابات خارجية. إجراءات الكتابة تبقى خلف الموافقة المحلية.',
    checkedAt: 'آخر فحص',
    dataUnavailable: 'لا تعرض الواجهة بيانات تجريبية عند تعذّر المصدر.',
    details: 'تفاصيل ومتطلبات',
    availableCount: 'اعتماد مهيأ · غير متحقق',
    setupCount: 'تحتاج إعدادًا',
    resourceCount: 'تحتاج إعداد مورد',
    filterCount: 'عرض',
  },
  en: {
    title: 'Connectors Hub',
    subtitle: 'Live connector catalog from the service—not placeholder cards',
    source: 'Data source: Connectors Hub API',
    refresh: 'Refresh catalog',
    checking: 'Connecting to Hub…',
    connected: 'Connectors Hub connected',
    offline: 'Connectors Hub unavailable',
    missingToken: 'The Agent Studio server needs its Hub API token configured.',
    unauthorized: 'Connectors Hub rejected the server credentials.',
    serviceUnavailable: 'The Hub is unavailable or needs local initialization.',
    upstreamError: 'Connectors Hub returned an unsuccessful response.',
    invalidEndpoint: 'The Connectors Hub endpoint is invalid or unsafe.',
    invalidPayload: 'The returned catalog could not be validated.',
    networkError: 'Could not reach the Agent Studio API.',
    requestError: 'Could not load the connector catalog.',
    retry: 'Retry',
    searchPlaceholder: 'Search connectors or authorization groups…',
    all: 'All',
    configured: 'Configured',
    unconfigured: 'Not configured',
    needsResource: 'Needs resource setup',
    noConnectors: 'The live catalog contains no connectors.',
    noMatches: 'No connectors match this search.',
    count: 'connectors',
    authGroup: 'Authorization group',
    searchCapability: 'Search supported',
    readCapability: 'Read supported',
    noSearchCapability: 'Search not supported',
    noReadCapability: 'Read not supported',
    configuredLabel: 'Credentials present · live connection not verified',
    unconfiguredLabel: 'Credentials or authorization setup required',
    resourceLabel: 'Credentials present · resource allowlist/config is still required',
    unknownLabel: 'Unknown state returned by the service',
    liveVerification: 'The catalog reports configuration only, not live access tests; no connector is labeled ready without a verified read.',
    writeGate: 'This page never shows credential values or executes external writes. Writes remain behind local approval.',
    checkedAt: 'Last checked',
    dataUnavailable: 'No sample data is shown when the live source is unavailable.',
    details: 'Details and requirements',
    availableCount: 'Configured · unverified',
    setupCount: 'Need setup',
    resourceCount: 'Need resource setup',
    filterCount: 'Showing',
  },
} as const;

type Copy = { [Key in keyof (typeof copyByLanguage)['en']]: string };
type FilterState = 'all' | 'configured' | 'unconfigured' | 'needs_resource_config';

interface ConnectorsWorkspaceProps {
  lang: Language;
  onConnectionChange: (state: ConnectionState) => void;
}

function errorMessage(error: unknown, catalog: HubCatalog | null, copy: Copy): string {
  if (catalog) {
    switch (catalog.reason) {
      case 'token_missing': return copy.missingToken;
      case 'unauthorized': return copy.unauthorized;
      case 'service_unavailable': return copy.serviceUnavailable;
      case 'invalid_endpoint': return copy.invalidEndpoint;
      case 'invalid_payload': return copy.invalidPayload;
      case 'upstream_error': return copy.upstreamError;
      default: break;
    }
  }
  if (error instanceof ApiError && error.status === 0) return copy.networkError;
  return copy.requestError;
}

function connectorIcon(id: string) {
  switch (id) {
    case 'github': return Github;
    case 'gmail': return Mail;
    case 'google_calendar': return CalendarDays;
    case 'google_drive': return HardDrive;
    case 'google_docs': return FileText;
    case 'google_sheets': return Table2;
    case 'google_slides': return Layers3;
    case 'dropbox': return Inbox;
    case 'linear': return Trello;
    case 'notion': return Network;
    case 'supabase': return Database;
    case 'vercel': return Cloud;
    default: return Shield;
  }
}

function stateLabel(state: string, copy: Copy): string {
  if (state === 'configured') return copy.configured;
  if (state === 'unconfigured') return copy.unconfigured;
  if (state === 'needs_resource_config') return copy.needsResource;
  return copy.unknownLabel;
}

function stateDescription(state: string, copy: Copy): string {
  if (state === 'configured') return copy.configuredLabel;
  if (state === 'unconfigured') return copy.unconfiguredLabel;
  if (state === 'needs_resource_config') return copy.resourceLabel;
  return copy.unknownLabel;
}

function stateClass(state: string): string {
  if (state === 'configured') return 'configured';
  if (state === 'needs_resource_config') return 'resource';
  return 'unconfigured';
}

export function ConnectorsWorkspace({ lang, onConnectionChange }: ConnectorsWorkspaceProps) {
  const copy = copyByLanguage[lang];
  const [catalog, setCatalog] = useState<HubCatalog | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<FilterState>('all');

  const refreshCatalog = useCallback(async () => {
    setLoading(true);
    onConnectionChange('checking');
    try {
      const payload = await requestJson<HubCatalog>('/studio-api/v1/studio/connectors/catalog');
      setCatalog(payload);
      if (payload.available && payload.reason === 'connected') {
        setError(null);
        onConnectionChange('online');
      } else {
        setError(null);
        onConnectionChange('offline');
      }
    } catch (caught) {
      setCatalog(null);
      setError(caught);
      onConnectionChange('offline');
    } finally {
      setLoading(false);
    }
  }, [onConnectionChange]);

  useEffect(() => {
    void refreshCatalog();
    const timer = window.setInterval(() => void refreshCatalog(), 60_000);
    return () => window.clearInterval(timer);
  }, [refreshCatalog]);

  const connectors = catalog?.available ? catalog.connectors : [];
  const counts = useMemo(() => ({
    configured: connectors.filter((item) => item.state === 'configured').length,
    unconfigured: connectors.filter((item) => item.state === 'unconfigured').length,
    resource: connectors.filter((item) => item.state === 'needs_resource_config').length,
  }), [connectors]);
  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase(lang === 'ar' ? 'ar-EG' : 'en-US');
    return connectors.filter((item) => {
      const matchesState = filter === 'all' || item.state === filter;
      const haystack = [item.id, item.name, item.auth_group, ...item.notes].join(' ').toLocaleLowerCase();
      return matchesState && (!needle || haystack.includes(needle));
    });
  }, [connectors, filter, lang, query]);

  const filters: Array<{ id: FilterState; label: string }> = [
    { id: 'all', label: copy.all },
    { id: 'configured', label: copy.configured },
    { id: 'unconfigured', label: copy.unconfigured },
    { id: 'needs_resource_config', label: copy.needsResource },
  ];

  return (
    <div className="integration-page connectors-page" dir={lang === 'ar' ? 'rtl' : 'ltr'}>
      <div className="integration-page-header">
        <div className="integration-heading">
          <div className="integration-heading-icon connectors-heading-icon"><Network size={19} /></div>
          <div>
            <div className="integration-eyebrow">{copy.source}</div>
            <h1>{copy.title}</h1>
            <p>{copy.subtitle}</p>
          </div>
        </div>
        <div className="integration-header-actions">
          <span className={`service-status ${catalog?.available ? 'service-online' : loading ? 'service-checking' : 'service-offline'}`}>
            <i />{catalog?.available ? copy.connected : loading ? copy.checking : copy.offline}
          </span>
          <button className="secondary-button" onClick={() => void refreshCatalog()} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'spin' : ''} />{copy.refresh}
          </button>
        </div>
      </div>

      <div className="connector-truth-banner"><ShieldCheckIcon /><span>{copy.liveVerification}</span></div>

      {Boolean(error) && <div className="integration-alert integration-alert-error" role="alert"><AlertCircle size={16} /><span>{errorMessage(error, catalog, copy)}</span><button className="secondary-button" onClick={() => void refreshCatalog()}>{copy.retry}</button></div>}
      {!error && catalog && !catalog.available && <div className="integration-alert integration-alert-error" role="status"><AlertCircle size={16} /><span>{errorMessage(null, catalog, copy)} {copy.dataUnavailable}</span><button className="secondary-button" onClick={() => void refreshCatalog()}>{copy.retry}</button></div>}

      {catalog?.available && (
        <>
          <section className="connector-summary-grid">
            <article className="connector-summary-card summary-all"><span><Network size={16} /></span><div><strong>{formatCount(connectors.length, lang)}</strong><small>{copy.count}</small></div></article>
            <article className="connector-summary-card summary-configured"><span><KeyRound size={16} /></span><div><strong>{formatCount(counts.configured, lang)}</strong><small>{copy.availableCount}</small></div></article>
            <article className="connector-summary-card summary-setup"><span><ShieldAlert size={16} /></span><div><strong>{formatCount(counts.unconfigured, lang)}</strong><small>{copy.setupCount}</small></div></article>
            <article className="connector-summary-card summary-resource"><span><HardDrive size={16} /></span><div><strong>{formatCount(counts.resource, lang)}</strong><small>{copy.resourceCount}</small></div></article>
          </section>

          <section className="connector-browser studio-surface">
            <div className="connector-browser-toolbar">
              <label className="connector-search"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={copy.searchPlaceholder} /></label>
              <span className="connector-count-note">{copy.filterCount} {formatCount(filtered.length, lang)} / {formatCount(connectors.length, lang)}</span>
            </div>
            <div className="connector-filter-row" role="group" aria-label={copy.title}>
              {filters.map((item) => <button key={item.id} className={`connector-filter ${filter === item.id ? 'filter-active' : ''}`} onClick={() => setFilter(item.id)} aria-pressed={filter === item.id}>{item.label}<small>{item.id === 'all' ? connectors.length : item.id === 'needs_resource_config' ? counts.resource : counts[item.id]}</small></button>)}
            </div>
            {filtered.length === 0 ? <div className="connector-empty"><Network size={21} /><p>{connectors.length ? copy.noMatches : copy.noConnectors}</p></div> : (
              <div className="connector-card-grid">
                {filtered.map((connector) => <ConnectorCard key={connector.id} connector={connector} copy={copy} />)}
              </div>
            )}
          </section>

          <div className="connector-footer-notes">
            <span><Shield size={14} />{copy.writeGate}</span>
            {catalog.checked_at && <small><Clock3 size={13} />{copy.checkedAt}: {formatTimestamp(catalog.checked_at, lang)}</small>}
          </div>
        </>
      )}

      {loading && !catalog?.available && <div className="integration-loading"><LoaderCircle size={18} className="spin" />{copy.checking}</div>}
    </div>
  );
}

function ConnectorCard({ connector, copy }: { connector: HubConnector; copy: Copy }) {
  const Icon = connectorIcon(connector.id);
  const className = stateClass(connector.state);
  return (
    <article className={`connector-card connector-state-${className}`}>
      <div className="connector-card-heading">
        <span className="connector-product-icon"><Icon size={18} /></span>
        <div className="connector-name-block"><h2>{connector.name}</h2><code>{connector.id}</code></div>
        <span className={`connector-state-badge badge-${className}`}><i />{stateLabel(connector.state, copy)}</span>
      </div>
      <p className="connector-state-description">{stateDescription(connector.state, copy)}</p>
      <div className="connector-capabilities">
        <span className={connector.search_supported ? 'capability-supported' : 'capability-unavailable'}>{connector.search_supported ? <Check size={12} /> : <X size={12} />}{connector.search_supported ? copy.searchCapability : copy.noSearchCapability}</span>
        <span className={connector.read_supported ? 'capability-supported' : 'capability-unavailable'}>{connector.read_supported ? <Check size={12} /> : <X size={12} />}{connector.read_supported ? copy.readCapability : copy.noReadCapability}</span>
      </div>
      <div className="connector-auth-group"><span>{copy.authGroup}</span><code>{connector.auth_group}</code></div>
      {connector.notes.length > 0 && (
        <details className="connector-requirements">
          <summary>{copy.details}<ChevronDown size={13} /></summary>
          <ul>{connector.notes.map((note, index) => <li key={`${connector.id}-${index}`}>{note}</li>)}</ul>
        </details>
      )}
    </article>
  );
}

function formatCount(value: number, lang: Language): string {
  return new Intl.NumberFormat(lang === 'ar' ? 'ar-EG' : 'en-US').format(value);
}

function ShieldCheckIcon() {
  return <Shield size={15} />;
}
