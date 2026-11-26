import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import {
  AlertCircle,
  ArrowDownToLine,
  ArrowUpLeft,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  Circle,
  Clock3,
  Copy,
  FilePlus2,
  FileText,
  FolderOpen,
  FolderPlus,
  HardDrive,
  History,
  LoaderCircle,
  RefreshCw,
  Search,
  Shield,
  ShieldAlert,
  ShieldCheck,
  X,
} from 'lucide-react';
import type { ConnectionState, Language } from './integrations';

type Copy = { [Key: string]: string };

const dictionaries = {
  ar: {
    workspace: 'مساحة النواة',
    newTask: 'مهمة جديدة',
    recentRuns: 'عمليات حديثة',
    emptyHistory: 'ستظهر نتائج تشغيلك هنا',
    loading: 'جارٍ تحميل النظام…',
    connected: 'النواة متصلة',
    offline: 'النواة غير متصلة',
    checkingConnection: 'جارٍ التحقق من الاتصال…',
    refresh: 'تحديث',
    eyebrow: 'CELIA.SH 2027 · التنفيذ المحلي',
    heroTitle: 'تنفيذ موثّق داخل صندوق معزول.',
    heroText: 'ابدأ بعملية ملفات محدودة داخل صندوق مستقل. كل نتيجة هنا قادمة من النواة الحقيقية، وليست محاكاة.',
    honestMode: 'المخطط الحالي حتمي — لا يوجد نموذج لغوي متصل بعد.',
    chooseCapability: 'اختر قدرة النواة',
    createFile: 'إنشاء ملف',
    createFileDesc: 'اكتب ملفًا واحصل على سجل تحقق وبصمة.',
    ensureDir: 'إنشاء مجلد',
    ensureDirDesc: 'أنشئ مجلدًا داخل صندوق هذه الجولة.',
    listDir: 'عرض مجلد',
    listDirDesc: 'اعرض محتوى مجلد الجولة الحالية.',
    pathLabel: 'المسار النسبي داخل الصندوق',
    pathPlaceholder: 'reports/first-proof.md',
    pathHint: 'كل جولة تبدأ في صندوق مستقل؛ الملفات لا تنتقل تلقائيًا بين الجولات.',
    contentLabel: 'محتوى الملف',
    contentPlaceholder: 'اكتب المحتوى الذي تريد حفظه…',
    contentHint: 'سيُحفظ النص كما هو، ثم تتحقق النواة منه بشكل مستقل.',
    submit: 'نفّذ عبر النواة',
    submitting: 'جارٍ تنفيذ الطلب…',
    requestNote: 'لا نعرض تقدّمًا مصطنعًا. ستظهر النتيجة عند عودة سجل التنفيذ الحقيقي.',
    quickActions: 'ابدأ بسرعة',
    quickReport: 'إنشاء ملف توثيق',
    quickFolder: 'إنشاء مجلد تقارير',
    quickList: 'عرض جذر الصندوق',
    securityTest: 'اختبار حاجز الأمان',
    securityHint: 'يرسل محاولة خروج آمنة؛ المتوقع أن ترفضها السياسة قبل التنفيذ.',
    resultTitle: 'نتيجة التنفيذ',
    resultReady: 'اكتملت الجولة',
    runId: 'معرّف الجولة',
    copyId: 'نسخ المعرّف',
    copied: 'نُسخ',
    downloadRecord: 'تنزيل سجل القرار',
    statusVerified: 'تم التحقق',
    statusDenied: 'مرفوض بالسياسة',
    statusFailed: 'فشل بوضوح',
    statusAborted: 'أُلغيت الجولة',
    statusInconclusive: 'غير حاسم',
    statusUnknown: 'حالة غير معروفة',
    cleanupStatus: 'حالة التنظيف',
    cleanupComplete: 'اكتمل التنظيف',
    cleanupNotApplicable: 'التنظيف غير منطبق في نمط Live Agent المباشر',
    cleanupNotConfirmed: 'التنظيف جزئي أو فاشل أو غير مُبلّغ عنه؛ النجاح الكامل غير مؤكد',
    duration: 'زمن التنفيذ',
    evidenceChecks: 'فحوص الأدلة',
    memoryAdmission: 'الذاكرة المحكومة',
    memoryAccepted: 'قُبلت النتيجة',
    memoryRefused: 'لم تُقبل',
    policyWall: 'حاجز السياسة',
    planStage: 'الخطة',
    executeStage: 'التنفيذ',
    verifyStage: 'التحقق',
    evidenceStage: 'الأدلة',
    stageDone: 'مكتمل',
    stageBlocked: 'مرفوض',
    stageSkipped: 'لم يُنفّذ',
    stageFailed: 'لم ينجح',
    actionsLabel: 'خطوات الخطة',
    policyLabel: 'قرارات السياسة',
    executionOutput: 'ما أعادته النواة',
    evidenceLabel: 'نتائج التحقق المستقل',
    artifacts: 'مخرجات الجولة',
    noArtifacts: 'لا توجد ملفات مخرجة في هذه الجولة.',
    preview: 'معاينة آمنة',
    previewEmpty: 'اختر ملفًا نصيًا لمعاينته هنا. لا يُعرض HTML أو يُنفّذ.',
    previewLoad: 'جارٍ جلب الملف…',
    previewError: 'تعذرت معاينة الملف.',
    previewTruncated: 'تم اقتطاع المعاينة؛ نزّل الملف لقراءة المحتوى كاملًا.',
    download: 'تنزيل',
    previewAction: 'معاينة',
    runtime: 'حالة النظام',
    runtimeName: 'نواة agi-system',
    runtimeMode: 'تنفيذ حقيقي · بلا LLM',
    scopeTitle: 'النطاق الحالي',
    scopeText: 'تنفّذ النواة الآن عمليات ملفات محدودة داخل صندوق مستقل لكل جولة. ليست بعدُ وكيلًا عامًا أو متصفحًا للويب.',
    capabilities: 'أنواع أهداف النواة',
    memoryCount: 'ذكريات مقبولة',
    proofTitle: 'سجل الدليل',
    proofEmpty: 'نفّذ مهمة لعرض حالتها وفحوصها وبصمتها هنا.',
    hashLabel: 'SHA-256',
    verifiedChecks: 'فحوص ناجحة',
    failureDetails: 'تفاصيل الرفض أو الخطأ',
    recordDetails: 'تفاصيل سجل القرار (JSON)',
    planUnavailable: 'لم تُعتمد خطة؛ راجع سبب الرفض في السجل.',
    noPolicyData: 'لا توجد قرارات سياسة مسجلة.',
    noEvidence: 'لم يصدر دليل تحقق لهذه الجولة.',
    readyTitle: 'مساحة النواة جاهزة للعمل.',
    readyText: 'اختر قدرة من الأعلى. سنعرض ما نفّذته النواة فعلًا، وما تحققت منه، وما رفضته — دون ادعاء قدرات غير متصلة.',
    noRunsYet: 'لا توجد جولات محفوظة بعد.',
    errorTitle: 'تعذر الوصول إلى النواة',
    errorText: 'تأكد أن خدمة Live Agent تعمل، ثم أعد المحاولة.',
    retry: 'أعد المحاولة',
    errorRequiredPath: 'أدخل مسارًا نسبيًا داخل الصندوق.',
    errorGeneral: 'تعذر تنفيذ الطلب.',
    safetySuccess: 'اختبار حاجز الأمان',
    sandboxNotice: 'الصندوق مستقل لكل جولة',
    consoleTitle: 'لوحة التشغيل',
    consoleHint: 'مراقبة النواة والهجمات والتدقيق',
    privacy: 'مخرجاتك تبقى ضمن بيئة التشغيل هذه.',
    footer: 'CELIA.SH 2027 · الدليل قبل الادعاء',
    emptyValue: '—',
  },
  en: {
    workspace: 'Kernel workspace',
    newTask: 'New task',
    recentRuns: 'Recent runs',
    emptyHistory: 'Your run results will appear here',
    loading: 'Loading the system…',
    connected: 'Kernel connected',
    offline: 'Kernel offline',
    checkingConnection: 'Checking connection…',
    refresh: 'Refresh',
    eyebrow: 'CELIA.SH 2027 · local execution',
    heroTitle: 'Documented execution inside an isolated sandbox.',
    heroText: 'Start with a bounded file operation in an isolated sandbox. Results come from the real kernel—not a simulation.',
    honestMode: 'The current planner is deterministic — no language model is connected yet.',
    chooseCapability: 'Choose a kernel capability',
    createFile: 'Create a file',
    createFileDesc: 'Write a file and receive a verification record and hash.',
    ensureDir: 'Create a folder',
    ensureDirDesc: 'Create a folder inside this run’s sandbox.',
    listDir: 'List a folder',
    listDirDesc: 'List entries in the current run’s sandbox.',
    pathLabel: 'Relative sandbox path',
    pathPlaceholder: 'reports/first-proof.md',
    pathHint: 'Each run starts in its own sandbox; files do not carry between runs.',
    contentLabel: 'File content',
    contentPlaceholder: 'Write the content you want to save…',
    contentHint: 'The text is saved as-is, then independently checked by the kernel.',
    submit: 'Run with the kernel',
    submitting: 'Running request…',
    requestNote: 'No simulated progress. The result appears when the real execution record returns.',
    quickActions: 'Start quickly',
    quickReport: 'Create a proof note',
    quickFolder: 'Create reports folder',
    quickList: 'List sandbox root',
    securityTest: 'Test the policy wall',
    securityHint: 'Sends a contained path-escape attempt; policy should deny it before execution.',
    resultTitle: 'Execution result',
    resultReady: 'Run complete',
    runId: 'Run ID',
    copyId: 'Copy ID',
    copied: 'Copied',
    downloadRecord: 'Download decision record',
    statusVerified: 'Verified',
    statusDenied: 'Denied by policy',
    statusFailed: 'Failed honestly',
    statusAborted: 'Run aborted',
    statusInconclusive: 'Inconclusive',
    statusUnknown: 'Unknown status',
    cleanupStatus: 'Cleanup status',
    cleanupComplete: 'Cleanup complete',
    cleanupNotApplicable: 'Cleanup not applicable in direct Live Agent mode',
    cleanupNotConfirmed: 'Cleanup is partial, failed, or unreported; full success is not confirmed',
    duration: 'Execution time',
    evidenceChecks: 'Evidence checks',
    memoryAdmission: 'Evidence-gated memory',
    memoryAccepted: 'Admitted',
    memoryRefused: 'Not admitted',
    policyWall: 'Policy wall',
    planStage: 'Plan',
    executeStage: 'Execute',
    verifyStage: 'Verify',
    evidenceStage: 'Evidence',
    stageDone: 'Complete',
    stageBlocked: 'Denied',
    stageSkipped: 'Not run',
    stageFailed: 'Did not pass',
    actionsLabel: 'Plan steps',
    policyLabel: 'Policy decisions',
    executionOutput: 'Kernel response',
    evidenceLabel: 'Independent verification',
    artifacts: 'Run outputs',
    noArtifacts: 'This run produced no user files.',
    preview: 'Safe preview',
    previewEmpty: 'Choose a text file to preview here. HTML is never rendered or executed.',
    previewLoad: 'Loading file…',
    previewError: 'Could not preview this file.',
    previewTruncated: 'Preview truncated; download the file to read it all.',
    download: 'Download',
    previewAction: 'Preview',
    runtime: 'System status',
    runtimeName: 'agi-system kernel',
    runtimeMode: 'Real execution · no LLM',
    scopeTitle: 'Current scope',
    scopeText: 'The kernel currently runs bounded file operations in a fresh sandbox per run. It is not yet a general-purpose agent or web browser.',
    capabilities: 'Kernel goal types',
    memoryCount: 'Admitted memories',
    proofTitle: 'Evidence record',
    proofEmpty: 'Run a task to see its status, checks and hash here.',
    hashLabel: 'SHA-256',
    verifiedChecks: 'Checks passed',
    failureDetails: 'Denial or failure details',
    recordDetails: 'Decision record details (JSON)',
    planUnavailable: 'No plan was accepted; review the denial reason in the record.',
    noPolicyData: 'No policy decisions were recorded.',
    noEvidence: 'No verification evidence was produced for this run.',
    readyTitle: 'Your kernel workspace is ready.',
    readyText: 'Choose a capability above. We will show what the kernel actually ran, verified and denied—without implying capabilities that are not connected.',
    noRunsYet: 'No saved runs yet.',
    errorTitle: 'Could not reach the kernel',
    errorText: 'Make sure the Live Agent service is running, then try again.',
    retry: 'Retry connection',
    errorRequiredPath: 'Enter a relative path inside the sandbox.',
    errorGeneral: 'The request could not be completed.',
    safetySuccess: 'Policy wall test',
    sandboxNotice: 'Isolated sandbox per run',
    consoleTitle: 'Operations console',
    consoleHint: 'Kernel, red-team and audit controls',
    privacy: 'Your outputs stay inside this runtime environment.',
    footer: 'CELIA.SH 2027 · Evidence before claims',
    emptyValue: '—',
  },
} as const;

type Operation = 'create_file' | 'ensure_dir' | 'list_dir';

interface HealthPayload { ok?: boolean; kernel?: string; mode?: string; supported_goals?: string[]; memory?: number; }
interface CleanupPayload { status?: string; reason?: string; warnings?: string[]; }
interface RunSummary { run_id: string; ts: number; status: string; goal_type: string; cleanup?: CleanupPayload; }
interface RunAction { capability?: string; rationale?: string; predicted_risk?: number; }
interface PolicyDecision { capability?: string; allowed?: boolean; reason?: string; }
interface EvidenceItem { checks?: Record<string, boolean>; details?: Record<string, unknown>; }
interface Observation { capability?: string; ok?: boolean; data?: Record<string, unknown>; error?: string | null; }
interface RunRecord {
  run_id: string; status: string;
  goal?: { goal_type?: string; params?: Record<string, unknown> };
  plan?: { actions?: RunAction[]; rationale?: string } | null;
  policy?: PolicyDecision[];
  observations?: Observation[];
  evidence?: EvidenceItem[];
  denial_reasons?: string[];
  error?: string | null;
  started_at?: number; finished_at?: number; duration_ms?: number;
  sandbox?: string; sandbox_files?: string[]; memory?: { admitted?: boolean; reason?: string };
  cleanup?: CleanupPayload;
}
interface FilePreview { name: string; runId: string; text: string; truncated: boolean; }

const api = async <T,>(path: string, init?: RequestInit): Promise<T> => {
  let response: Response;
  try {
    response = await fetch(path, { cache: 'no-store', ...init, headers: { Accept: 'application/json', ...(init?.headers ?? {}) } });
  } catch {
    throw new Error('network');
  }
  let body: unknown;
  try { body = await response.json(); } catch { body = null; }
  if (!response.ok) {
    const message = body && typeof body === 'object' && 'error' in body && typeof body.error === 'string' ? body.error : `HTTP ${response.status}`;
    throw new Error(message);
  }
  return body as T;
};

const OPERATION_ICON = { create_file: FilePlus2, ensure_dir: FolderPlus, list_dir: FolderOpen } as const;

function getStatusCopy(status: string, t: Copy): string {
  switch (status) {
    case 'VERIFIED': return t.statusVerified;
    case 'DENIED': return t.statusDenied;
    case 'FAILED': return t.statusFailed;
    case 'ABORTED': return t.statusAborted;
    case 'INCONCLUSIVE': return t.statusInconclusive;
    default: return t.statusUnknown;
  }
}
function statusClass(status: string): string { return status.toLowerCase().replace(/[^a-z0-9-]/g, '-'); }
function cleanupAccepted(cleanup?: CleanupPayload): boolean {
  return cleanup?.status === 'complete' || (cleanup?.status === 'not_applicable' && cleanup?.reason === 'direct_live_agent_mode');
}
function displayRunStatus(status: string, cleanup?: CleanupPayload): string {
  if (status !== 'VERIFIED') return status;
  return cleanupAccepted(cleanup) ? status : 'INCONCLUSIVE';
}
function cleanupMessage(cleanup: CleanupPayload | undefined, t: Copy): string {
  if (cleanup?.status === 'complete') return t.cleanupComplete;
  if (cleanupAccepted(cleanup) && cleanup?.status === 'not_applicable') return t.cleanupNotApplicable;
  const status = cleanup?.status || 'unreported';
  const warnings = Array.isArray(cleanup?.warnings) ? cleanup.warnings.join(', ') : '';
  return `${t.cleanupNotConfirmed} (${status}${warnings ? `: ${warnings}` : ''})`;
}
function operationCopy(goal: string | undefined, t: Copy): string {
  switch (goal) {
    case 'create_file': return t.createFile;
    case 'ensure_dir': return t.ensureDir;
    case 'list_dir': return t.listDir;
    default: return goal || t.emptyValue;
  }
}
function runPath(record: RunRecord): string {
  const value = record.goal?.params?.path;
  return typeof value === 'string' ? value : '.';
}
function durationMs(record: RunRecord): number | null {
  if (typeof record.duration_ms === 'number' && Number.isFinite(record.duration_ms)) return record.duration_ms;
  if (typeof record.started_at === 'number' && typeof record.finished_at === 'number') return Math.max(0, (record.finished_at - record.started_at) * 1000);
  return null;
}
function evidenceCounts(record: RunRecord): { passed: number; total: number } {
  let passed = 0; let total = 0;
  for (const item of record.evidence ?? []) for (const value of Object.values(item.checks ?? {})) { total += 1; if (value) passed += 1; }
  return { passed, total };
}
function recordHash(record: RunRecord): string | null {
  for (const observation of record.observations ?? []) {
    const value = observation.data?.sha256;
    if (typeof value === 'string') return value;
  }
  return null;
}
function formatTime(timestamp: number, lang: Language): string {
  const safe = timestamp > 10_000_000_000 ? timestamp : timestamp * 1000;
  if (!Number.isFinite(safe)) return '—';
  return new Intl.DateTimeFormat(lang === 'ar' ? 'ar-EG' : 'en-US', { hour: '2-digit', minute: '2-digit' }).format(new Date(safe));
}
function formatDuration(value: number | null, lang: Language): string {
  if (value === null) return '—';
  if (value < 1000) return `${value.toFixed(value < 10 ? 1 : 0)} ms`;
  return `${(value / 1000).toFixed(2)} s`;
}
function isTextFile(path: string): boolean {
  return /\.(txt|md|json|csv|log|yaml|yml|xml|html|css|js|ts|py)$/i.test(path);
}

interface KernelWorkspaceProps {
  lang: Language;
  onConnectionChange: (state: ConnectionState) => void;
}

export function KernelWorkspace({ lang, onConnectionChange }: KernelWorkspaceProps) {
  const t = dictionaries[lang] as Copy;
  const [health, setHealth] = useState<HealthPayload | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [overviewLoading, setOverviewLoading] = useState(true);
  const [overviewError, setOverviewError] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [record, setRecord] = useState<RunRecord | null>(null);
  const [recordLoading, setRecordLoading] = useState(false);
  const [operation, setOperation] = useState<Operation>('create_file');
  const [path, setPath] = useState('reports/first-proof.md');
  const [content, setContent] = useState(() => lang === 'ar' ? 'نتيجة موثّقة من CELIA.SH 2027.\n' : 'A verified artifact from CELIA.SH 2027.\n');
  const [submitting, setSubmitting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [preview, setPreview] = useState<FilePreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState(false);
  const [copied, setCopied] = useState(false);
  const recordRequestRef = useRef(0);
  const previewRequestRef = useRef(0);

  const refreshOverview = useCallback(async () => {
    setOverviewLoading(true);
    try {
      const [healthPayload, capabilitiesPayload, runPayload] = await Promise.all([
        api<HealthPayload>('/api/health'),
        api<Pick<HealthPayload, 'supported_goals' | 'memory'>>('/api/capabilities'),
        api<{ runs?: RunSummary[] }>('/api/runs'),
      ]);
      setHealth({ ...healthPayload, ...capabilitiesPayload });
      setRuns(Array.isArray(runPayload.runs) ? runPayload.runs : []);
      setOverviewError(false);
    } catch {
      setOverviewError(true); setHealth(null); setRuns([]);
    } finally {
      setOverviewLoading(false);
    }
  }, []);

  useEffect(() => { void refreshOverview(); }, [refreshOverview]);

  useEffect(() => {
    onConnectionChange(overviewLoading ? 'checking' : health?.ok ? 'online' : 'offline');
  }, [health, overviewLoading, onConnectionChange]);

  const openRun = useCallback(async (runId: string) => {
    const requestId = ++recordRequestRef.current;
    previewRequestRef.current += 1;
    setPreviewLoading(null); setPreviewError(false);
    setSelectedRunId(runId); setRecord(null); setPreview(null); setActionError(null);
    setRecordLoading(true);
    try {
      const payload = await api<RunRecord>(`/api/run/${encodeURIComponent(runId)}`);
      if (requestId === recordRequestRef.current) setRecord(payload);
    } catch (error) {
      if (requestId === recordRequestRef.current) setActionError(error instanceof Error ? error.message : 'request failed');
    } finally {
      if (requestId === recordRequestRef.current) setRecordLoading(false);
    }
  }, []);

  const executeGoal = useCallback(async (goalType: Operation, params: Record<string, string>) => {
    const requestId = ++recordRequestRef.current;
    previewRequestRef.current += 1;
    setSubmitting(true); setActionError(null); setPreviewLoading(null); setPreviewError(false);
    setPreview(null); setSelectedRunId(null); setRecord(null); setRecordLoading(false);
    try {
      const payload = await api<RunRecord>('/api/run', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ goal_type: goalType, params }),
      });
      if (requestId === recordRequestRef.current) { setRecord(payload); setSelectedRunId(payload.run_id); }
      void refreshOverview();
    } catch (error) {
      if (requestId === recordRequestRef.current) setActionError(error instanceof Error ? error.message : t.errorGeneral);
    } finally { setSubmitting(false); }
  }, [refreshOverview, t.errorGeneral]);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const cleanPath = path.trim();
    if (!cleanPath) { setActionError(t.errorRequiredPath); return; }
    const params: Record<string, string> = { path: cleanPath };
    if (operation === 'create_file') params.content = content;
    void executeGoal(operation, params);
  };

  const pickOperation = (next: Operation) => {
    const defaults: Record<Operation, string> = { create_file: 'reports/first-proof.md', ensure_dir: 'reports', list_dir: '.' };
    if (path === defaults[operation]) setPath(defaults[next]);
    setOperation(next); setActionError(null);
  };

  const runSecurityTest = () => {
    setOperation('create_file'); setPath('../../escape.txt'); setContent('this must be denied by the policy wall');
    void executeGoal('create_file', { path: '../../escape.txt', content: 'this must be denied by the policy wall' });
  };

  const openArtifact = async (runId: string, fileName: string) => {
    const requestId = ++previewRequestRef.current;
    setPreviewLoading(fileName); setPreviewError(false); setPreview(null);
    const query = new URLSearchParams({ run: runId, path: fileName });
    try {
      const response = await fetch(`/api/file?${query.toString()}`, { cache: 'no-store' });
      if (!response.ok) throw new Error('file unavailable');
      const text = await response.text();
      if (requestId !== previewRequestRef.current) return;
      const maxPreviewLength = 24_000;
      setPreview({ name: fileName, runId, text: text.slice(0, maxPreviewLength), truncated: text.length > maxPreviewLength });
    } catch {
      if (requestId === previewRequestRef.current) setPreviewError(true);
    } finally {
      if (requestId === previewRequestRef.current) setPreviewLoading(null);
    }
  };

  const copyRunId = async (runId: string) => {
    try {
      await navigator.clipboard.writeText(runId);
      setCopied(true); window.setTimeout(() => setCopied(false), 1400);
    } catch {
      setActionError(lang === 'ar' ? 'تعذر النسخ من المتصفح.' : 'Clipboard access was denied.');
    }
  };

  const displayedFiles = useMemo(() => (record?.sandbox_files ?? []).filter((file) => file !== 'metrics.json' && file !== 'ledger.jsonl'), [record]);
  const supportedGoalCount = health?.supported_goals?.length ?? 0;
  const connectionOnline = Boolean(health?.ok);
  const direction = lang === 'ar' ? 'rtl' : 'ltr';

  return (
    <div className="kernel-workspace" dir={direction}>
      <div className="integration-page-header">
        <div className="integration-heading">
          <div className="integration-heading-icon kernel-heading-icon"><HardDrive size={19} /></div>
          <div>
            <div className="integration-eyebrow">{t.eyebrow}</div>
            <h1>{t.workspace}</h1>
            <p>{t.heroText}</p>
          </div>
        </div>
      </div>

      <div className="content-grid">
        <section className="conversation-column">
          {!record && !recordLoading && !submitting && !overviewError && (
            <section className="welcome-panel">
              <div className="eyebrow"><span className="eyebrow-star"><ShieldCheck size={13} /></span>{t.eyebrow}</div>
              <h1>{t.heroTitle}</h1>
              <p>{t.heroText}</p>
              <div className="honesty-pill"><ShieldCheck size={14} /><span>{t.honestMode}</span></div>
            </section>
          )}

          {overviewError && (
            <section className="connection-error" role="alert">
              <div className="error-icon"><AlertCircle size={20} /></div>
              <div className="error-copy"><h2>{t.errorTitle}</h2><p>{t.errorText}</p></div>
              <button className="secondary-button" onClick={() => void refreshOverview()}><RefreshCw size={15} />{t.retry}</button>
            </section>
          )}

          {(recordLoading || submitting) && (
            <section className="loading-record" role="status"><LoaderCircle size={22} className="spin" /><span>{recordLoading ? t.loading : t.submitting}</span></section>
          )}

          {!record && !recordLoading && !submitting && !overviewError && (
            <section className="starter-panel">
              <div className="starter-icon"><ShieldAlert size={18} /></div>
              <div><strong>{t.readyTitle}</strong><p>{t.readyText}</p></div>
            </section>
          )}

          {record && <RunResult record={record} t={t} lang={lang} preview={preview} previewLoading={previewLoading} onOpenArtifact={(name) => void openArtifact(record.run_id, name)} onCopy={() => void copyRunId(record.run_id)} copied={copied} displayedFiles={displayedFiles} />}

          {actionError && (
            <div className="action-error" role="alert"><AlertCircle size={15} /><span>{actionError === 'network' ? t.errorText : actionError}</span><button aria-label="Dismiss" onClick={() => setActionError(null)}><X size={14} /></button></div>
          )}

          <form className="composer-card" onSubmit={handleSubmit}>
            <div className="composer-heading">
              <div><span className="composer-overline">{t.chooseCapability}</span><span className="sandbox-chip"><Shield size={12} />{t.sandboxNotice}</span></div>
              <span className="composer-phase">KERNEL</span>
            </div>
            <div className="operation-grid" role="group" aria-label={t.chooseCapability}>
              {(['create_file', 'ensure_dir', 'list_dir'] as const).map((item) => {
                const Icon = OPERATION_ICON[item];
                const title = item === 'create_file' ? t.createFile : item === 'ensure_dir' ? t.ensureDir : t.listDir;
                const description = item === 'create_file' ? t.createFileDesc : item === 'ensure_dir' ? t.ensureDirDesc : t.listDirDesc;
                return (
                  <button type="button" key={item} className={`operation-card ${operation === item ? 'operation-selected' : ''}`} onClick={() => pickOperation(item)} aria-pressed={operation === item}>
                    <span className="operation-icon"><Icon size={17} /></span>
                    <span className="operation-copy"><strong>{title}</strong><small>{description}</small></span>
                    {operation === item && <span className="operation-check"><Check size={12} /></span>}
                  </button>
                );
              })}
            </div>
            <div className="input-grid">
              <label className="field path-field">
                <span className="field-label">{t.pathLabel}</span>
                <span className="input-wrap"><FolderOpen size={15} /><input value={path} onChange={(e) => setPath(e.target.value)} placeholder={t.pathPlaceholder} dir="auto" autoComplete="off" spellCheck={false} /></span>
                <small>{t.pathHint}</small>
              </label>
              {operation === 'create_file' && (
                <label className="field content-field">
                  <span className="field-label">{t.contentLabel}</span>
                  <textarea value={content} onChange={(e) => setContent(e.target.value)} placeholder={t.contentPlaceholder} rows={4} dir="auto" spellCheck={false} />
                  <small>{t.contentHint}</small>
                </label>
              )}
            </div>
            <div className="composer-footer">
              <span className="composer-note"><ShieldCheck size={13} />{t.requestNote}</span>
              <button className="primary-button" type="submit" disabled={submitting || !connectionOnline || overviewLoading}>
                {submitting ? <LoaderCircle size={16} className="spin" /> : <ArrowUpLeft size={16} />}
                <span>{submitting ? t.submitting : t.submit}</span>
                {!submitting && <span className="button-arrow"><ArrowUpLeft size={13} /></span>}
              </button>
            </div>
          </form>

          <section className="quick-section">
            <div className="quick-heading"><span>{t.quickActions}</span><span className="quick-rule" /></div>
            <div className="quick-actions">
              <button className="quick-action" onClick={() => { pickOperation('create_file'); setPath('reports/first-proof.md'); }}><FileText size={15} /><span>{t.quickReport}</span><ArrowUpLeft size={12} /></button>
              <button className="quick-action" onClick={() => { pickOperation('ensure_dir'); setPath('reports'); }}><FolderPlus size={15} /><span>{t.quickFolder}</span><ArrowUpLeft size={12} /></button>
              <button className="quick-action" onClick={() => { pickOperation('list_dir'); setPath('.'); }}><Search size={15} /><span>{t.quickList}</span><ArrowUpLeft size={12} /></button>
            </div>
          </section>

          <button className="security-test-card" onClick={runSecurityTest} disabled={submitting || !connectionOnline}>
            <span className="security-shield"><ShieldAlert size={17} /></span>
            <span className="security-copy"><strong>{t.securityTest}</strong><small>{t.securityHint}</small></span>
            {submitting ? <LoaderCircle size={15} className="spin" /> : <ChevronLeft size={16} />}
          </button>
        </section>

        <aside className="inspector-column">
          <section className="inspector-card runtime-card">
            <div className="inspector-title-row">
              <div className="inspector-title"><span className="inspector-icon runtime-icon"><HardDrive size={15} /></span><h2>{t.runtime}</h2></div>
              <span className={`live-badge ${connectionOnline ? 'live' : 'not-live'}`}><span />{connectionOnline ? 'LIVE' : 'OFFLINE'}</span>
            </div>
            <div className="runtime-name-row"><div className="kernel-symbol"><HardDrive size={17} /></div><div><strong>{t.runtimeName}</strong><span>{connectionOnline ? 'agi-system · local runtime' : 'Waiting for API'}</span></div></div>
            <div className="runtime-facts">
              <div><span>{t.capabilities}</span><b>{supportedGoalCount || '—'}</b></div>
              <div><span>{t.memoryCount}</span><b>{typeof health?.memory === 'number' ? health.memory : '—'}</b></div>
            </div>
            <div className="runtime-mode"><span className="mode-dot" /><span>{t.runtimeMode}</span></div>
          </section>

          <section className="inspector-card proof-card">
            <div className="inspector-title-row">
              <div className="inspector-title"><span className="inspector-icon proof-icon"><ShieldCheck size={15} /></span><h2>{t.proofTitle}</h2></div>
              {record && <span className={`status-chip ${statusClass(displayRunStatus(record.status, record.cleanup))}`}>{getStatusCopy(displayRunStatus(record.status, record.cleanup), t)}</span>}
            </div>
            {!record ? (
              <div className="proof-empty"><div className="proof-empty-mark"><ShieldCheck size={18} /></div><p>{t.proofEmpty}</p></div>
            ) : <ProofSummary record={record} t={t} />}
          </section>

          <section className="inspector-card preview-card">
            <div className="inspector-title-row"><div className="inspector-title"><span className="inspector-icon file-icon"><FileText size={15} /></span><h2>{t.preview}</h2></div>{preview && <span className="file-extension">{preview.name.split('.').pop()?.toUpperCase() || 'FILE'}</span>}</div>
            {previewLoading ? (
              <div className="preview-state"><LoaderCircle size={17} className="spin" /><span>{t.previewLoad}</span></div>
            ) : previewError ? (
              <div className="preview-state preview-error"><AlertCircle size={16} /><span>{t.previewError}</span></div>
            ) : preview ? (
              <div className="preview-content"><div className="preview-file-name"><FileText size={13} /><span>{preview.name}</span></div><pre dir="auto">{preview.text || (lang === 'ar' ? 'الملف فارغ.' : 'File is empty.')}</pre>{preview.truncated && <small>{t.previewTruncated}</small>}</div>
            ) : (
              <div className="preview-placeholder"><FileText size={19} /><p>{t.previewEmpty}</p></div>
            )}
          </section>

          <section className="scope-card">
            <div className="scope-heading"><span className="scope-icon"><Shield size={14} /></span><strong>{t.scopeTitle}</strong></div>
            <p>{t.scopeText}</p>
            <div className="scope-tags"><span>Sandbox</span><span>DecisionRecord</span><span>SHA-256</span></div>
          </section>
          <p className="privacy-note"><ShieldCheck size={12} />{t.privacy}</p>
        </aside>
      </div>

      {selectedRunId === null && !record && !overviewLoading && (
        <div className="history-panel">
          <div className="history-heading"><span className="side-label">{t.recentRuns}</span><button className="tiny-icon-button" aria-label={t.refresh} title={t.refresh} onClick={() => void refreshOverview()} disabled={overviewLoading}><RefreshCw size={13} className={overviewLoading ? 'spin' : ''} /></button></div>
          <div className="run-history" aria-label={t.recentRuns}>
            {runs.length === 0 ? (
              <div className="history-empty"><History size={15} /><span>{overviewLoading ? t.loading : t.emptyHistory}</span></div>
            ) : runs.slice(0, 16).map((run) => {
              const resultStatus = displayRunStatus(run.status, run.cleanup);
              const runStatus = statusClass(resultStatus);
              return (
                <button key={run.run_id} className="history-item" onClick={() => void openRun(run.run_id)}>
                  <span className={`history-status ${runStatus}`}>{resultStatus === 'VERIFIED' ? <Check size={12} /> : resultStatus === 'DENIED' ? <ShieldAlert size={12} /> : <Circle size={9} />}</span>
                  <span className="history-copy">
                    <span className="history-name">{operationCopy(run.goal_type, t)}</span>
                    <span className="history-meta">{formatTime(run.ts, lang)} <i>·</i> {run.run_id.slice(-8)}</span>
                  </span>
                  <ChevronLeft size={13} className="history-chevron" />
                </button>
              );
            })}
          </div>
        </div>
      )}

    </div>
  );
}

function RunResult({ record, t, lang, preview, previewLoading, onOpenArtifact, onCopy, copied, displayedFiles }: { record: RunRecord; t: Copy; lang: Language; preview: FilePreview | null; previewLoading: string | null; onOpenArtifact: (name: string) => void; onCopy: () => void; copied: boolean; displayedFiles: string[]; }) {
  const displayStatus = displayRunStatus(record.status, record.cleanup);
  const status = statusClass(displayStatus);
  const counts = evidenceCounts(record);
  const policy = record.policy ?? [];
  const planActions = record.plan?.actions ?? [];
  const hash = recordHash(record);
  const duration = formatDuration(durationMs(record), lang);
  const denial = (record.denial_reasons ?? []).filter(Boolean);
  const details = record.error ? [record.error, ...denial] : denial;
  const allChecksPassed = counts.total > 0 && counts.passed === counts.total;
  const stages = [
    { label: t.planStage, state: record.plan ? 'done' : record.status === 'DENIED' ? 'skipped' : 'failed' },
    { label: t.policyWall, state: policy.some((item) => item.allowed === false) ? 'blocked' : policy.length ? 'done' : 'skipped' },
    { label: t.executeStage, state: (record.observations?.length ?? 0) > 0 ? ((record.observations ?? []).every((item) => item.ok) ? 'done' : 'failed') : 'skipped' },
    { label: t.verifyStage, state: allChecksPassed ? 'done' : counts.total ? 'failed' : 'skipped' },
    { label: t.evidenceStage, state: counts.total ? (allChecksPassed ? 'done' : 'failed') : 'skipped' },
  ] as const;

  return (
    <section className={`run-result-card run-${status}`} aria-live="polite">
      <div className="run-result-header">
        <div className="run-status-mark">{displayStatus === 'VERIFIED' ? <CheckCircle2 size={21} /> : displayStatus === 'DENIED' ? <ShieldAlert size={21} /> : <AlertCircle size={21} />}</div>
        <div className="run-title-group"><span className="result-kicker">{t.resultTitle} <i>·</i> {t.resultReady}</span><h2>{getStatusCopy(displayStatus, t)}</h2><p>{operationCopy(record.goal?.goal_type, t)} <b>·</b> <code dir="auto">{runPath(record)}</code></p></div>
        <div className="run-header-actions">
          <button className="run-action" onClick={onCopy} title={t.copyId}><Copy size={14} /><span>{copied ? t.copied : t.copyId}</span></button>
          <a className="run-action" href={`/api/run/${encodeURIComponent(record.run_id)}`} download={`${record.run_id}.json`}><ArrowDownToLine size={14} /><span>{t.downloadRecord}</span></a>
        </div>
      </div>
      <div className="run-summary-grid">
        <div className="summary-metric"><span><Clock3 size={13} />{t.duration}</span><strong>{duration}</strong></div>
        <div className="summary-metric"><span><ShieldCheck size={13} />{t.evidenceChecks}</span><strong>{counts.passed}<small> / {counts.total}</small></strong></div>
        <div className="summary-metric"><span><HardDrive size={13} />{t.memoryAdmission}</span><strong className={record.memory?.admitted ? 'metric-good' : 'metric-neutral'}>{record.memory?.admitted ? t.memoryAccepted : t.memoryRefused}</strong></div>
      </div>
      <div className={`cleanup-notice cleanup-${statusClass(record.cleanup?.status || 'unreported')}`} role="status"><ShieldCheck size={14} /><span><strong>{t.cleanupStatus}:</strong> {cleanupMessage(record.cleanup, t)}</span></div>
      <div className="run-stages" aria-label={t.resultTitle}>
        {stages.map((stage, index) => (
          <div className={`run-stage stage-${stage.state}`} key={stage.label}>
            <span className="stage-index">{stage.state === 'done' ? <Check size={12} /> : stage.state === 'blocked' ? <X size={12} /> : <span>{String(index + 1).padStart(2, '0')}</span>}</span>
            <span className="stage-label">{stage.label}</span>
            <small>{stage.state === 'done' ? t.stageDone : stage.state === 'blocked' ? t.stageBlocked : stage.state === 'failed' ? t.stageFailed : t.stageSkipped}</small>
          </div>
        ))}
      </div>
      {details.length > 0 && <div className={`denial-detail ${record.status === 'DENIED' ? 'denial-policy' : ''}`}><ShieldAlert size={15} /><div><strong>{t.failureDetails}</strong><p>{details.join(' · ')}</p></div></div>}
      <div className="result-lower-grid">
        <div className="result-section">
          <div className="result-section-heading"><span>{t.actionsLabel}</span><span className="section-count">{planActions.length}</span></div>
          {planActions.length ? (
            <div className="plan-list">{planActions.map((action, index) => <div className="plan-item" key={`${action.capability}-${index}`}><span className="plan-index">{String(index + 1).padStart(2, '0')}</span><div><strong>{action.capability || t.emptyValue}</strong><small>{action.rationale || ''}</small></div><Check size={14} className="plan-check" /></div>)}</div>
          ) : <p className="subtle-empty">{t.planUnavailable}</p>}
        </div>
        <div className="result-section">
          <div className="result-section-heading"><span>{t.evidenceLabel}</span><span className={`section-count ${allChecksPassed ? 'count-good' : ''}`}>{counts.passed}/{counts.total}</span></div>
          {(record.evidence ?? []).length ? (
            <div className="check-list">{(record.evidence ?? []).flatMap((item, itemIndex) => Object.entries(item.checks ?? {}).map(([name, passed]) => <div className="check-item" key={`${itemIndex}-${name}`}><span className={`check-mark ${passed ? 'check-pass' : 'check-fail'}`}>{passed ? <Check size={11} /> : <X size={11} />}</span><span>{name.replaceAll('_', ' ')}</span></div>))}</div>
          ) : <p className="subtle-empty">{t.noEvidence}</p>}
        </div>
      </div>
      <div className="policy-summary">
        <div className="result-section-heading"><span>{t.policyLabel}</span><span className="section-count">{policy.length}</span></div>
        {policy.length ? <div className="policy-list">{policy.map((decision, index) => <div className={`policy-item ${decision.allowed ? 'policy-allow' : 'policy-deny'}`} key={`${decision.capability}-${index}`}><span>{decision.allowed ? <CheckCircle2 size={14} /> : <ShieldAlert size={14} />}</span><strong>{decision.capability || t.emptyValue}</strong><p>{decision.reason || ''}</p></div>)}</div> : <p className="subtle-empty">{t.noPolicyData}</p>}
      </div>
      {(record.observations ?? []).length > 0 && (
        <div className="policy-summary output-summary">
          <div className="result-section-heading"><span>{t.executionOutput}</span><span className="section-count">{record.observations?.length ?? 0}</span></div>
          <div className="observation-list">{(record.observations ?? []).map((observation, index) => {
            const safeData = Object.fromEntries(Object.entries(observation.data ?? {}).filter(([key]) => key !== 'content'));
            return <div className="observation-item" key={`${observation.capability}-${index}`}><div className="observation-head"><code>{observation.capability || t.emptyValue}</code><span className={observation.ok ? 'observation-ok' : 'observation-failed'}>{observation.ok ? t.stageDone : t.stageFailed}</span></div><pre dir="ltr">{JSON.stringify(safeData, null, 2)}</pre></div>;
          })}</div>
        </div>
      )}
      <div className="artifacts-section">
        <div className="result-section-heading"><span>{t.artifacts}</span><span className="section-count">{displayedFiles.length}</span></div>
        {displayedFiles.length ? (
          <div className="artifact-list">{displayedFiles.map((file) => {
            const query = new URLSearchParams({ run: record.run_id, path: file });
            const isSelectedPreview = preview?.runId === record.run_id && preview.name === file;
            const isLoading = previewLoading === file;
            return <div className={`artifact-row ${isSelectedPreview ? 'artifact-selected' : ''}`} key={file}><span className="artifact-file-icon"><FileText size={15} /></span><span className="artifact-name" dir="auto">{file}</span>{isTextFile(file) && <button className="artifact-button" onClick={() => onOpenArtifact(file)} disabled={isLoading} title={t.previewAction}>{isLoading ? <LoaderCircle size={14} className="spin" /> : <Search size={14} />}<span>{t.previewAction}</span></button>}<a className="artifact-button download-button" href={`/api/file?${query.toString()}`} download={file.split('/').pop()} title={t.download}><ArrowDownToLine size={14} /><span>{t.download}</span></a></div>;
          })}</div>
        ) : <p className="subtle-empty">{t.noArtifacts}</p>}
      </div>
      {recordHash(record) && <div className="hash-panel"><span className="hash-icon"><ShieldCheck size={14} /></span><div><span>{t.hashLabel}</span><code dir="ltr">{hash}</code></div></div>}
      <details className="raw-record"><summary><span>{t.recordDetails}</span><ChevronDown size={15} /></summary><pre dir="ltr">{JSON.stringify(record, null, 2)}</pre></details>
      <div className="run-id-line"><span>{t.runId}</span><code dir="ltr">{record.run_id}</code></div>
    </section>
  );
}

function ProofSummary({ record, t }: { record: RunRecord; t: Copy }) {
  const counts = evidenceCounts(record);
  const hash = recordHash(record);
  const displayStatus = displayRunStatus(record.status, record.cleanup);
  const statusText = getStatusCopy(displayStatus, t);
  return (
    <div className="proof-summary">
      <div className="proof-status-line"><span className={`proof-status-icon ${statusClass(displayStatus)}`}>{displayStatus === 'VERIFIED' ? <Check size={13} /> : displayStatus === 'DENIED' ? <ShieldAlert size={13} /> : <AlertCircle size={13} />}</span><strong>{statusText}</strong><span className="proof-run-short" dir="ltr">{record.run_id.slice(-8)}</span></div>
      <div className="proof-stat-row"><span>{t.verifiedChecks}</span><strong>{counts.passed} / {counts.total}</strong></div>
      {hash && <div className="proof-hash"><span>{t.hashLabel}</span><code dir="ltr" title={hash}>{hash.slice(0, 12)}…{hash.slice(-8)}</code></div>}
      {record.memory && <div className="memory-state"><span className={`memory-dot ${record.memory.admitted ? 'memory-good' : 'memory-muted'}`} />{record.memory.admitted ? t.memoryAccepted : t.memoryRefused}</div>}
    </div>
  );
}
