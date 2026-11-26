import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import {
  Activity,
  AlertCircle,
  ArrowDownToLine,
  ArrowUpLeft,
  BadgeCheck,
  Check,
  CheckCircle2,
  ChevronDown,
  Circle,
  Clock3,
  FileCheck2,
  FileText,
  HardDrive,
  History,
  LoaderCircle,
  Play,
  Plus,
  RefreshCw,
  Server,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Square,
  Wallet,
  X,
} from 'lucide-react';
import {
  ApiError,
  artifactEndpoint,
  formatTimestamp,
  isPreviewableArtifact,
  readStudioEventStream,
  requestJson,
  type ConnectionState,
  type Language,
  type StudioApproval,
  type StudioArtifact,
  type StudioEvent,
  type StudioSession,
  type StudioStatus,
} from './integrations';

const copyByLanguage = {
  ar: {
    title: 'Agent Studio',
    subtitle: 'Celia تشغّل المهام عبر Agent Studio: حالة وأدلة حقيقية',
    source: 'مصدر البيانات: واجهات Agent Studio',
    refresh: 'تحديث البيانات',
    retry: 'إعادة المحاولة',
    loading: 'جارٍ جلب الحالة…',
    online: 'Agent Studio متصل',
    offline: 'تعذر الوصول إلى Agent Studio',
    offlineHint: 'ابدأ خدمة Agent Studio على المنفذ 8080 وتحقق من وكيل Vite.',
    errorRequest: 'تعذر إكمال الطلب.',
    errorOffline: 'تعذر الوصول إلى خدمة Agent Studio. تحقق من أنها تعمل ثم أعد المحاولة.',
    errorReadOnly: 'الخدمة في وضع القراءة فقط؛ لا يمكن إنشاء جلسات أو تشغيل مهام.',
    errorUnauthorized: 'رفضت الخدمة الطلب. تحقق من صلاحيات التشغيل.',
    errorConflict: 'الجلسة مشغولة حاليًا؛ انتظر حتى ينتهي التشغيل الحالي.',
    errorNotFound: 'لم تعد الجلسة متاحة. حدّث القائمة وحاول مجددًا.',
    errorStream: 'تعذر فتح بث أحداث المهمة.',
    errorTaskRequired: 'اكتب وصف المهمة أولًا.',
    errorSessionCreate: 'تعذر إنشاء جلسة جديدة.',
    newSession: 'جلسة جديدة',
    sessions: 'الجلسات الحديثة',
    noSessions: 'لا توجد جلسات محفوظة بعد.',
    sessionTitle: 'جلسة',
    selectSession: 'اختر جلسة لعرض المهام ومخرجاتها.',
    selectedSession: 'الجلسة النشطة',
    taskLabel: 'ما الذي تريد إنجازه؟',
    taskPlaceholder: 'صف النتيجة المطلوبة، والملفات أو الأدلة التي تتوقعها…',
    taskHint: 'تُرسل المهمة إلى الوكيل الحقيقي، وتُعرض أحداثه عند صدورها؛ لا يوجد تقدّم مصطنع.',
    maxSteps: 'الحد الأعلى للخطوات',
    allowRemote: 'السماح بمعالجة المهمة لدى مزوّد بعيد',
    allowRemoteHint: 'عند التفعيل قد يُرسل نص المهمة إلى مزوّد خارجي مهيأ. افتراضيًا يبقى هذا الخيار مغلقًا.',
    runTask: 'تشغيل عبر Agent Studio',
    running: 'المهمة قيد التنفيذ…',
    stop: 'طلب إيقاف',
    stopSent: 'أُرسل طلب الإيقاف؛ انتظر حدث الإلغاء من الخدمة.',
    taskFailed: 'تعذر تشغيل المهمة.',
    liveEvents: 'أحداث التشغيل الحية',
    noEvents: 'ستظهر هنا أحداث الخطة والأدوات والنتيجة عند وصولها من الخادم.',
    eventStarted: 'بدأت الجلسة',
    eventResumed: 'استئناف الجلسة',
    eventPlan: 'الخطة',
    eventStep: 'بدء خطوة',
    eventTool: 'نتيجة أداة',
    eventArtifact: 'مخرج جديد',
    eventApproval: 'موافقة مطلوبة',
    eventFinal: 'النتيجة النهائية',
    eventError: 'خطأ',
    eventAborted: 'أُلغيت المهمة',
    eventMessage: 'حدث',
    step: 'خطوة',
    sessionStatus: 'حالة الجلسة',
    statusIdle: 'جاهزة',
    statusRunning: 'قيد التشغيل',
    statusCompleted: 'مكتملة',
    statusAborted: 'أُلغيت',
    statusFailed: 'فشلت',
    statusUnknown: 'غير معروفة',
    plan: 'خطة التنفيذ',
    noPlan: 'لم تُسجّل خطة لهذه الجلسة.',
    artifacts: 'المخرجات والأدلة',
    noArtifacts: 'لا توجد مخرجات مسجلة لهذه الجلسة.',
    verified: 'تحقق مستقل ناجح',
    notVerified: 'لم يثبت التحقق',
    preview: 'معاينة نصية آمنة',
    previewLoading: 'جارٍ تحميل الملف…',
    previewError: 'تعذرت قراءة هذا الملف.',
    previewClose: 'إغلاق المعاينة',
    previewOnlyText: 'المعاينة تعرض النص فقط؛ لا يتم تشغيل HTML أو SVG.',
    download: 'تنزيل',
    evidenceBundle: 'تنزيل حزمة الأدلة',
    model: 'المزوّد / النموذج',
    deterministic: 'تنفيذ حتمي دون LLM مباشر',
    liveModel: 'تنفيذ بمزوّد LLM',
    trace: 'معرّف التتبّع',
    finalAnswer: 'خلاصة الوكيل',
    noFinalAnswer: 'لم تُسجّل خلاصة نهائية بعد.',
    systemStatus: 'حالة النظام',
    kernel: 'نواة L0',
    kernelReady: 'متاحة',
    kernelUnavailable: 'غير متاحة',
    memory: 'ذاكرة الأدلة L1',
    memoryCount: 'ذكريات',
    available: 'متاحة',
    unavailable: 'غير متاحة',
    localProvider: 'محلي',
    remoteProvider: 'بعيد',
    observability: 'المراقبة والتتبّع',
    persistedSpans: 'امتدادات محفوظة',
    inMemorySpans: 'امتدادات في الذاكرة',
    otlpExporter: 'مصدّر OTLP',
    yes: 'نعم',
    no: 'لا',
    budget: 'حارس الميزانية',
    spent: 'الإنفاق المسجل',
    pendingApprovals: 'موافقات معلّقة',
    approvalReadOnlyNote: 'تسجيل قرار المراجعة هنا لا ينفّذ التغيير الخارجي؛ لا تزال إجراءات Hub بحاجة إلى مسارها المحلي المصرح.',
    approvals: 'مراجعة مقترحات الإجراءات',
    noApprovals: 'لا توجد مقترحات إجراءات مسجلة.',
    approve: 'تسجيل موافقة',
    reject: 'رفض',
    confirmApprove: 'سيسجّل هذا القرار في Agent Studio فقط، ولن ينفّذ إجراءً خارجيًا. هل تريد المتابعة؟',
    confirmReject: 'هل تريد تسجيل رفض هذا المقترح؟',
    approvalUpdated: 'تم تحديث قرار المراجعة.',
    readOnly: 'وضع القراءة فقط',
    writable: 'التغييرات متاحة وفق حواجز الأمان',
    providers: 'جاهزية المزوّدين',
    configured: 'بيانات اعتماد مهيأة',
    absent: 'غير مهيأ',
    zeroBudgetAllowed: 'مسموح ضمن حد التكلفة',
    zeroBudgetBlocked: 'محجوب أو غير مسموح',
    providerCaution: 'السماح ضمن الميزانية سياسة تكلفة لا يثبت اتصالًا جاهزًا؛ ووجود الاعتماد لا يثبت نجاح اتصال حي.',
    sessionInfo: 'معلومات الجلسة',
    task: 'المهمة',
    created: 'أُنشئت',
    updated: 'آخر تحديث',
    cost: 'التكلفة',
    localCost: 'محلي / صفر مثبت',
    details: 'تفاصيل الجلسة',
    statusSourceHint: 'هذه المؤشرات من /v1/studio/status وليست تقديرات للواجهة.',
    refreshSessions: 'تحديث الجلسات',
    connectionPending: 'جارٍ التحقق من الاتصال…',
  },
  en: {
    title: 'Agent Studio',
    subtitle: 'Celia runs tasks through Agent Studio: live status and evidence',
    source: 'Data source: Agent Studio APIs',
    refresh: 'Refresh data',
    retry: 'Retry',
    loading: 'Loading system status…',
    online: 'Agent Studio connected',
    offline: 'Agent Studio unavailable',
    offlineHint: 'Start Agent Studio on port 8080 and verify the Vite proxy.',
    errorRequest: 'The request could not be completed.',
    errorOffline: 'Could not reach Agent Studio. Make sure it is running, then retry.',
    errorReadOnly: 'The service is read-only; sessions and task runs are disabled.',
    errorUnauthorized: 'The service rejected the request. Check runtime permissions.',
    errorConflict: 'This session is busy. Wait for its current run to finish.',
    errorNotFound: 'This session is no longer available. Refresh the list and retry.',
    errorStream: 'Could not open the task event stream.',
    errorTaskRequired: 'Enter a task description first.',
    errorSessionCreate: 'Could not create a new session.',
    newSession: 'New session',
    sessions: 'Recent sessions',
    noSessions: 'No saved sessions yet.',
    sessionTitle: 'Session',
    selectSession: 'Choose a session to inspect its tasks and outputs.',
    selectedSession: 'Active session',
    taskLabel: 'What would you like to accomplish?',
    taskPlaceholder: 'Describe the desired outcome, files or evidence…',
    taskHint: 'The task runs through the real agent; events appear as the server emits them. No progress is fabricated.',
    maxSteps: 'Maximum steps',
    allowRemote: 'Allow remote provider processing',
    allowRemoteHint: 'When enabled, task text may be sent to a configured external provider. This stays off by default.',
    runTask: 'Run with Agent Studio',
    running: 'Task is running…',
    stop: 'Request stop',
    stopSent: 'Stop requested; wait for the service’s abort event.',
    taskFailed: 'The task could not run.',
    liveEvents: 'Live run events',
    noEvents: 'Plan, tool and result events will appear here as the server emits them.',
    eventStarted: 'Session started',
    eventResumed: 'Session resumed',
    eventPlan: 'Plan',
    eventStep: 'Step started',
    eventTool: 'Tool result',
    eventArtifact: 'New artifact',
    eventApproval: 'Approval required',
    eventFinal: 'Final result',
    eventError: 'Error',
    eventAborted: 'Run aborted',
    eventMessage: 'Event',
    step: 'Step',
    sessionStatus: 'Session status',
    statusIdle: 'Ready',
    statusRunning: 'Running',
    statusCompleted: 'Completed',
    statusAborted: 'Aborted',
    statusFailed: 'Failed',
    statusUnknown: 'Unknown',
    plan: 'Execution plan',
    noPlan: 'No plan has been recorded for this session.',
    artifacts: 'Outputs and evidence',
    noArtifacts: 'No outputs are recorded for this session.',
    verified: 'Independently verified',
    notVerified: 'Verification not established',
    preview: 'Safe text preview',
    previewLoading: 'Loading artifact…',
    previewError: 'Could not read this artifact.',
    previewClose: 'Close preview',
    previewOnlyText: 'Preview is plain text only; HTML and SVG are never executed.',
    download: 'Download',
    evidenceBundle: 'Download evidence bundle',
    model: 'Provider / model',
    deterministic: 'Deterministic without a live LLM',
    liveModel: 'LLM-backed execution',
    trace: 'Trace ID',
    finalAnswer: 'Agent summary',
    noFinalAnswer: 'No final answer has been recorded yet.',
    systemStatus: 'System status',
    kernel: 'L0 kernel',
    kernelReady: 'Available',
    kernelUnavailable: 'Unavailable',
    memory: 'L1 evidence memory',
    memoryCount: 'Memories',
    available: 'Available',
    unavailable: 'Unavailable',
    localProvider: 'local',
    remoteProvider: 'remote',
    observability: 'Observability and tracing',
    persistedSpans: 'Persisted spans',
    inMemorySpans: 'In-memory spans',
    otlpExporter: 'OTLP exporter',
    yes: 'Yes',
    no: 'No',
    budget: 'Budget guard',
    spent: 'Recorded spend',
    pendingApprovals: 'Pending approvals',
    approvalReadOnlyNote: 'Recording a review decision here does not execute an external change; Hub actions still require their authorized local path.',
    approvals: 'Review action proposals',
    noApprovals: 'No action proposals are recorded.',
    approve: 'Record approval',
    reject: 'Reject',
    confirmApprove: 'This records a decision in Agent Studio only; it does not execute an external action. Continue?',
    confirmReject: 'Record rejection for this proposal?',
    approvalUpdated: 'Review decision updated.',
    readOnly: 'Read-only mode',
    writable: 'Changes allowed behind safety gates',
    providers: 'Provider readiness',
    configured: 'Credentials configured',
    absent: 'Not configured',
    zeroBudgetAllowed: 'Allowed by cost guard',
    zeroBudgetBlocked: 'Blocked or not allowed',
    providerCaution: 'Cost-guard approval is not connection readiness; credentials still do not prove a successful live provider call.',
    sessionInfo: 'Session details',
    task: 'Task',
    created: 'Created',
    updated: 'Updated',
    cost: 'Cost',
    localCost: 'Local / proven zero',
    details: 'Session details',
    statusSourceHint: 'These values come from /v1/studio/status, not UI estimates.',
    refreshSessions: 'Refresh sessions',
    connectionPending: 'Checking connection…',
  },
} as const;

type Copy = { [Key in keyof (typeof copyByLanguage)['en']]: string };

interface ArtifactPreview {
  path: string;
  text: string;
  truncated: boolean;
}

interface StudioWorkspaceProps {
  lang: Language;
  resetKey: number;
  initialSessionId?: string | null;
  onConnectionChange: (state: ConnectionState) => void;
}

function labelForSessionStatus(status: string, copy: Copy): string {
  switch (status) {
    case 'idle': return copy.statusIdle;
    case 'running': return copy.statusRunning;
    case 'completed': return copy.statusCompleted;
    case 'aborted': return copy.statusAborted;
    case 'failed': return copy.statusFailed;
    default: return copy.statusUnknown;
  }
}

function eventLabel(event: string, copy: Copy): string {
  const labels: Record<string, string> = {
    session_started: copy.eventStarted,
    session_resumed: copy.eventResumed,
    plan: copy.eventPlan,
    step_start: copy.eventStep,
    tool_result: copy.eventTool,
    artifact: copy.eventArtifact,
    approval_required: copy.eventApproval,
    final: copy.eventFinal,
    error: copy.eventError,
    aborted: copy.eventAborted,
  };
  return labels[event] ?? copy.eventMessage;
}

function requestFailure(error: unknown, copy: Copy, fallback: string): string {
  if (!(error instanceof ApiError)) return fallback;
  if (error.status === 0) return copy.errorOffline;
  if (error.status === 401 || error.status === 403) return copy.errorUnauthorized;
  if (error.status === 404) return copy.errorNotFound;
  if (error.status === 409) return copy.errorConflict;
  return `${fallback} (${error.status})`;
}

function formatDollars(value: number | null | undefined, lang: Language): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  return new Intl.NumberFormat(lang === 'ar' ? 'ar-EG' : 'en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 4,
  }).format(value);
}

function formatCount(value: number | undefined, lang: Language): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  return new Intl.NumberFormat(lang === 'ar' ? 'ar-EG' : 'en-US').format(value);
}

function artifactDownloadHref(sessionId: string, artifact: StudioArtifact): string {
  return artifactEndpoint(sessionId, artifact.path);
}

export function StudioWorkspace({ lang, resetKey, initialSessionId, onConnectionChange }: StudioWorkspaceProps) {
  const copy = copyByLanguage[lang];
  const [status, setStatus] = useState<StudioStatus | null>(null);
  const [statusError, setStatusError] = useState('');
  const [statusLoading, setStatusLoading] = useState(true);
  const [sessions, setSessions] = useState<StudioSession[]>([]);
  const [approvals, setApprovals] = useState<StudioApproval[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [activeSession, setActiveSession] = useState<StudioSession | null>(null);
  const [sessionLoading, setSessionLoading] = useState(false);
  const [task, setTask] = useState('');
  const [maxSteps, setMaxSteps] = useState(5);
  const [allowRemote, setAllowRemote] = useState(false);
  const [running, setRunning] = useState(false);
  const [events, setEvents] = useState<StudioEvent[]>([]);
  const [actionError, setActionError] = useState('');
  const [notice, setNotice] = useState('');
  const [preview, setPreview] = useState<ArtifactPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState(false);
  const [approvalBusy, setApprovalBusy] = useState<string | null>(null);
  const sessionRequestRef = useRef(0);

  const refreshStudio = useCallback(async () => {
    setStatusLoading(true);
    setSessionsLoading(true);
    onConnectionChange('checking');
    const [statusResult, sessionsResult, approvalsResult] = await Promise.allSettled([
      requestJson<StudioStatus>('/studio-api/v1/studio/status'),
      requestJson<StudioSession[]>('/studio-api/v1/studio/sessions'),
      requestJson<StudioApproval[]>('/studio-api/v1/studio/approvals'),
    ]);

    if (statusResult.status === 'fulfilled') {
      setStatus(statusResult.value);
      setStatusError('');
      onConnectionChange('online');
    } else {
      setStatus(null);
      setStatusError(requestFailure(statusResult.reason, copy, copy.offlineHint));
      onConnectionChange('offline');
    }
    if (sessionsResult.status === 'fulfilled') setSessions(sessionsResult.value);
    else setSessions([]);
    if (approvalsResult.status === 'fulfilled') setApprovals(approvalsResult.value);
    else setApprovals([]);
    setStatusLoading(false);
    setSessionsLoading(false);
  }, [copy, onConnectionChange]);

  useEffect(() => {
    void refreshStudio();
    const timer = window.setInterval(() => void refreshStudio(), 30_000);
    return () => window.clearInterval(timer);
  }, [refreshStudio]);

  useEffect(() => {
    if (resetKey === 0) return;
    setTask('');
    setActionError('');
    setNotice('');
    setPreview(null);
    setPreviewError(false);
    if (!running) {
      setSelectedSessionId(null);
      setActiveSession(null);
      setEvents([]);
    }
  }, [resetKey, running]);

  const loadSession = useCallback(async (sessionId: string) => {
    const requestId = ++sessionRequestRef.current;
    setSelectedSessionId(sessionId);
    setSessionLoading(true);
    setActionError('');
    setNotice('');
    setEvents([]);
    setPreview(null);
    setPreviewError(false);
    try {
      const session = await requestJson<StudioSession>(
        `/studio-api/v1/studio/sessions/${encodeURIComponent(sessionId)}`,
      );
      if (requestId === sessionRequestRef.current) setActiveSession(session);
    } catch (error) {
      if (requestId === sessionRequestRef.current) {
        setActiveSession(null);
        setActionError(requestFailure(error, copy, copy.errorRequest));
      }
    } finally {
      if (requestId === sessionRequestRef.current) setSessionLoading(false);
    }
  }, [copy]);

  useEffect(() => {
    if (initialSessionId) void loadSession(initialSessionId);
  }, [initialSessionId, loadSession]);

  const createSession = useCallback(async (title: string) => {
    const created = await requestJson<StudioSession>('/studio-api/v1/studio/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: title.trim().slice(0, 120) || copy.sessionTitle }),
    });
    setSessions((current) => [created, ...current.filter((item) => item.session_id !== created.session_id)]);
    setSelectedSessionId(created.session_id);
    setActiveSession(created);
    setEvents([]);
    setPreview(null);
    return created;
  }, [copy.sessionTitle]);

  const createBlankSession = async () => {
    setActionError('');
    setNotice('');
    try {
      const title = `${copy.sessionTitle} · ${formatTimestamp(Date.now(), lang)}`;
      await createSession(title);
      await refreshStudio();
    } catch (error) {
      setActionError(requestFailure(error, copy, copy.errorSessionCreate));
    }
  };

  const refreshActiveSession = useCallback(async (sessionId: string) => {
    try {
      const session = await requestJson<StudioSession>(
        `/studio-api/v1/studio/sessions/${encodeURIComponent(sessionId)}`,
      );
      if (sessionId === selectedSessionId) setActiveSession(session);
      setSessions((current) => [session, ...current.filter((item) => item.session_id !== session.session_id)]);
    } catch (error) {
      setActionError(requestFailure(error, copy, copy.errorRequest));
    }
  }, [copy, selectedSessionId]);

  const handleRun = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const cleanTask = task.trim();
    if (!cleanTask) {
      setActionError(copy.errorTaskRequired);
      return;
    }
    if (!status) {
      setActionError(statusError || copy.errorOffline);
      return;
    }
    if (status.read_only) {
      setActionError(copy.errorReadOnly);
      return;
    }

    setRunning(true);
    setActionError('');
    setNotice('');
    setEvents([]);
    setPreview(null);
    let targetSessionId = selectedSessionId;
    try {
      if (!targetSessionId) {
        const created = await createSession(cleanTask.slice(0, 64) || copy.sessionTitle);
        targetSessionId = created.session_id;
      }
      const response = await fetch(
        `/studio-api/v1/studio/sessions/${encodeURIComponent(targetSessionId)}/stream`,
        {
          method: 'POST',
          cache: 'no-store',
          headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' },
          body: JSON.stringify({
            task: cleanTask,
            max_steps: maxSteps,
            allow_remote: allowRemote,
            language: lang,
          }),
        },
      );
      await readStudioEventStream(response, (incoming) => {
        setEvents((current) => [...current.slice(-59), incoming]);
        if (incoming.event === 'error' && incoming.message) {
          setActionError(incoming.message.slice(0, 500));
        }
      });
    } catch (error) {
      const fallback = error instanceof ApiError && error.status === 0 ? copy.errorStream : copy.taskFailed;
      setActionError(requestFailure(error, copy, fallback));
    } finally {
      setRunning(false);
      if (targetSessionId) await refreshActiveSession(targetSessionId);
      await refreshStudio();
    }
  };

  const requestStop = async () => {
    if (!selectedSessionId) return;
    setActionError('');
    try {
      await requestJson(`/studio-api/v1/studio/sessions/${encodeURIComponent(selectedSessionId)}/stop`, {
        method: 'POST',
      });
      setNotice(copy.stopSent);
    } catch (error) {
      setActionError(requestFailure(error, copy, copy.errorRequest));
    }
  };

  const previewArtifact = async (artifact: StudioArtifact) => {
    if (!activeSession || !isPreviewableArtifact(artifact.path)) return;
    setPreviewLoading(artifact.path);
    setPreviewError(false);
    setPreview(null);
    try {
      const response = await fetch(artifactEndpoint(activeSession.session_id, artifact.path), {
        cache: 'no-store',
        headers: { Accept: 'text/plain, application/json, text/markdown, */*' },
      });
      if (!response.ok) throw new Error('preview unavailable');
      const text = await response.text();
      const limit = 30_000;
      setPreview({ path: artifact.path, text: text.slice(0, limit), truncated: text.length > limit });
    } catch {
      setPreviewError(true);
    } finally {
      setPreviewLoading(null);
    }
  };

  const decideApproval = async (approval: StudioApproval, approve: boolean) => {
    const confirmed = window.confirm(approve ? copy.confirmApprove : copy.confirmReject);
    if (!confirmed) return;
    setApprovalBusy(approval.action_id);
    setActionError('');
    try {
      const decision = approve ? 'approve' : 'reject';
      const updated = await requestJson<StudioApproval>(
        `/studio-api/v1/studio/approvals/${encodeURIComponent(approval.action_id)}/${decision}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ note: approve ? 'Reviewed in workspace UI' : 'Rejected in workspace UI' }),
        },
      );
      setApprovals((current) => current.map((item) => item.action_id === updated.action_id ? updated : item));
      setNotice(copy.approvalUpdated);
      await refreshStudio();
    } catch (error) {
      setActionError(requestFailure(error, copy, copy.errorRequest));
    } finally {
      setApprovalBusy(null);
    }
  };

  const selectedSession = sessions.find((item) => item.session_id === selectedSessionId) ?? null;
  const pendingApprovals = approvals.filter((item) => item.status === 'pending');
  const completedSteps = activeSession?.plan.filter((item) => item.status === 'completed').length ?? 0;
  const costBudget = status?.budget;
  const stateClass = status?.read_only ? 'readonly' : 'writable';

  return (
    <div className="integration-page studio-page" dir={lang === 'ar' ? 'rtl' : 'ltr'}>
      <div className="integration-page-header">
        <div className="integration-heading">
          <div className="integration-heading-icon studio-heading-icon"><Activity size={19} /></div>
          <div>
            <div className="integration-eyebrow">{copy.source}</div>
            <h1>{copy.title}</h1>
            <p>{copy.subtitle}</p>
          </div>
        </div>
        <div className="integration-header-actions">
          <span className={`service-status ${status ? 'service-online' : statusLoading ? 'service-checking' : 'service-offline'}`}>
            <i />{status ? copy.online : statusLoading ? copy.connectionPending : copy.offline}
          </span>
          <button className="secondary-button" onClick={() => void refreshStudio()} disabled={statusLoading}>
            <RefreshCw size={14} className={statusLoading ? 'spin' : ''} />{copy.refresh}
          </button>
        </div>
      </div>

      {statusError && (
        <div className="integration-alert" role="alert">
          <AlertCircle size={16} /><span>{statusError}</span>
          <button className="secondary-button" onClick={() => void refreshStudio()}>{copy.retry}</button>
        </div>
      )}
      {status?.read_only && <div className="readonly-banner"><Shield size={15} />{copy.errorReadOnly}</div>}
      {actionError && <div className="integration-alert integration-alert-error" role="alert"><AlertCircle size={16} /><span>{actionError}</span><button className="icon-button" onClick={() => setActionError('')} aria-label="Dismiss"><X size={14} /></button></div>}
      {notice && <div className="integration-alert integration-alert-success" role="status"><CheckCircle2 size={16} /><span>{notice}</span><button className="icon-button" onClick={() => setNotice('')} aria-label="Dismiss"><X size={14} /></button></div>}

      <section className="studio-metrics" aria-label={copy.systemStatus}>
        <article className="studio-metric-card">
          <span className="studio-metric-icon metric-kernel"><HardDrive size={16} /></span>
          <div><span>{copy.kernel}</span><strong className={status?.kernel_available ? 'metric-good-text' : 'metric-muted-text'}>{status ? (status.kernel_available ? copy.kernelReady : copy.kernelUnavailable) : '—'}</strong></div>
        </article>
        <article className="studio-metric-card">
          <span className="studio-metric-icon metric-memory"><BadgeCheck size={16} /></span>
          <div><span>{copy.memory}</span><strong>{status ? formatCount(status.memory.total_memories, lang) : '—'} <small>{copy.memoryCount}</small></strong>{status && <small className={`studio-metric-footnote ${status.memory.available ? 'metric-footnote-good' : ''}`}>{status.memory.available ? copy.available : copy.unavailable} · {formatCount(status.memory.verified_artifacts, lang)} {lang === 'ar' ? 'دليل موثّق' : 'verified artifacts'}</small>}</div>
        </article>
        <article className="studio-metric-card">
          <span className="studio-metric-icon metric-budget"><Wallet size={16} /></span>
          <div><span>{copy.budget}</span><strong>{status ? formatDollars(status.budget.spent_usd, lang) : '—'} <small>/ {status ? formatDollars(status.budget.max_spend_usd, lang) : '—'}</small></strong></div>
        </article>
        <article className="studio-metric-card">
          <span className="studio-metric-icon metric-approval"><ShieldAlert size={16} /></span>
          <div><span>{copy.pendingApprovals}</span><strong>{status ? formatCount(status.pending_approvals, lang) : '—'}</strong></div>
        </article>
      </section>

      <div className="studio-layout">
        <div className="studio-main-column">
          <form className="studio-composer studio-surface" onSubmit={(event) => void handleRun(event)}>
            <div className="studio-section-heading">
              <div><span className="section-kicker">{copy.selectedSession}</span><h2>{selectedSession?.title || copy.taskLabel}</h2></div>
              {selectedSession && <span className={`session-status-chip session-${selectedSession.status}`}><i />{labelForSessionStatus(selectedSession.status, copy)}</span>}
            </div>
            <label className="studio-field">
              <span>{copy.taskLabel}</span>
              <textarea
                value={task}
                onChange={(event) => setTask(event.target.value)}
                placeholder={copy.taskPlaceholder}
                maxLength={16_000}
                rows={4}
                dir="auto"
                disabled={running || Boolean(status?.read_only)}
              />
              <small>{copy.taskHint}</small>
            </label>
            <div className="studio-form-options">
              <label className="studio-option-field"><span>{copy.maxSteps}</span><select value={maxSteps} onChange={(event) => setMaxSteps(Number(event.target.value))} disabled={running}><option value={3}>3</option><option value={5}>5</option><option value={8}>8</option><option value={12}>12</option></select></label>
              <label className="remote-consent">
                <input type="checkbox" checked={allowRemote} onChange={(event) => setAllowRemote(event.target.checked)} disabled={running} />
                <span><strong>{copy.allowRemote}</strong><small>{copy.allowRemoteHint}</small></span>
              </label>
            </div>
            <div className="studio-composer-footer">
              <span className="studio-safety-note"><ShieldCheck size={14} />{status?.read_only ? copy.readOnly : copy.writable}</span>
              <div className="studio-run-actions">
                {running && <button className="stop-button" type="button" onClick={() => void requestStop()}><Square size={13} />{copy.stop}</button>}
                <button className="primary-button" type="submit" disabled={running || !task.trim() || !status || status.read_only}>
                  {running ? <LoaderCircle size={15} className="spin" /> : <Play size={15} />}
                  <span>{running ? copy.running : copy.runTask}</span>
                  {!running && <span className="button-arrow"><ArrowUpLeft size={12} /></span>}
                </button>
              </div>
            </div>
          </form>

          <section className="studio-surface studio-events-card">
            <div className="studio-section-heading">
              <div><span className="section-kicker">{copy.liveEvents}</span><h2>{running ? copy.running : copy.liveEvents}</h2></div>
              {running && <span className="stream-live-chip"><i />LIVE</span>}
            </div>
            {events.length === 0 ? (
              <div className="studio-empty-state"><Activity size={20} /><p>{copy.noEvents}</p></div>
            ) : (
              <ol className="studio-event-list">
                {events.map((item, index) => (
                  <li key={`${item.timestamp}-${index}`} className={`studio-event-row studio-event-${item.event}`}>
                    <span className="studio-event-marker">{item.event === 'final' ? <Check size={12} /> : item.event === 'error' ? <X size={12} /> : <Circle size={8} />}</span>
                    <div className="studio-event-copy">
                      <div className="studio-event-topline"><strong>{eventLabel(item.event, copy)}</strong><span>{formatTimestamp(item.timestamp, lang)}</span></div>
                      <p>{item.message.slice(0, 500) || eventLabel(item.event, copy)}</p>
                      {item.step > 0 && <small>{copy.step} {item.step}</small>}
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </section>

          {sessionLoading && <div className="integration-loading"><LoaderCircle size={18} className="spin" />{copy.loading}</div>}
          {activeSession && (
            <>
              <section className="studio-surface studio-result-card">
                <div className="studio-section-heading">
                  <div><span className="section-kicker">{copy.sessionInfo}</span><h2>{activeSession.title}</h2></div>
                  <a className="studio-link-button" href={`/studio-api/v1/studio/sessions/${encodeURIComponent(activeSession.session_id)}/evidence`} download={`${activeSession.session_id}-evidence.json`}><ArrowDownToLine size={14} />{copy.evidenceBundle}</a>
                </div>
                <div className="studio-session-facts">
                  <div><span>{copy.sessionStatus}</span><strong className={`session-status-text session-text-${activeSession.status}`}>{labelForSessionStatus(activeSession.status, copy)}</strong></div>
                  <div><span>{copy.model}</span><strong dir="auto">{activeSession.provider} · {activeSession.model}</strong></div>
                  <div><span>{copy.cost}</span><strong>{formatDollars(activeSession.cost_usd, lang)}</strong></div>
                  <div><span>{copy.trace}</span><code dir="ltr">{activeSession.trace_id || '—'}</code></div>
                </div>
                <div className="studio-mode-note"><ShieldCheck size={14} /><span>{activeSession.simulated_llm ? copy.deterministic : copy.liveModel}</span></div>
                <div className="studio-result-summary"><h3>{copy.finalAnswer}</h3><p dir="auto">{activeSession.final_answer || copy.noFinalAnswer}</p></div>
                <details className="studio-details">
                  <summary>{copy.details}<ChevronDown size={14} /></summary>
                  <div><span>{copy.task}</span><p dir="auto">{activeSession.task || '—'}</p><span>{copy.created}: {formatTimestamp(activeSession.created_at, lang)} · {copy.updated}: {formatTimestamp(activeSession.updated_at, lang)}</span></div>
                </details>
              </section>

              <section className="studio-surface studio-plan-card">
                <div className="studio-section-heading"><div><span className="section-kicker">{copy.plan}</span><h2>{copy.plan}</h2></div><span className="section-count">{completedSteps}/{activeSession.plan.length}</span></div>
                {activeSession.plan.length ? <ol className="studio-plan-list">{activeSession.plan.map((step) => <li key={step.step_id} className={`plan-status-${step.status}`}><span>{step.status === 'completed' ? <Check size={12} /> : step.step_id}</span><strong>{step.title}</strong><small>{step.status}</small></li>)}</ol> : <p className="studio-subtle-empty">{copy.noPlan}</p>}
              </section>

              <section className="studio-surface studio-artifacts-card">
                <div className="studio-section-heading"><div><span className="section-kicker">{copy.artifacts}</span><h2>{copy.artifacts}</h2></div><span className="section-count">{activeSession.artifacts.length}</span></div>
                {activeSession.artifacts.length === 0 ? <p className="studio-subtle-empty">{copy.noArtifacts}</p> : (
                  <div className="studio-artifact-list">
                    {activeSession.artifacts.map((artifact) => (
                      <div className="studio-artifact-row" key={artifact.path}>
                        <span className="studio-artifact-icon"><FileText size={15} /></span>
                        <div className="studio-artifact-copy"><strong dir="auto">{artifact.path}</strong><small><span>{artifact.kind}</span><i>·</i><span>{formatCount(artifact.byte_size, lang)} B</span>{artifact.sha256 && <><i>·</i><code dir="ltr">SHA-256 {artifact.sha256.slice(0, 12)}…</code></>}</small></div>
                        <span className={`artifact-proof-chip ${artifact.verified ? 'proof-yes' : 'proof-no'}`}>{artifact.verified ? <CheckCircle2 size={13} /> : <ShieldAlert size={13} />}{artifact.verified ? copy.verified : copy.notVerified}</span>
                        {isPreviewableArtifact(artifact.path) && <button className="icon-button artifact-preview-button" onClick={() => void previewArtifact(artifact)} disabled={previewLoading === artifact.path} title={copy.preview}>{previewLoading === artifact.path ? <LoaderCircle size={15} className="spin" /> : <FileCheck2 size={15} />}</button>}
                        <a className="icon-button artifact-download-button" href={artifactDownloadHref(activeSession.session_id, artifact)} download={artifact.path.split('/').pop()} title={copy.download}><ArrowDownToLine size={15} /></a>
                      </div>
                    ))}
                  </div>
                )}
                {previewLoading && <div className="studio-preview-state"><LoaderCircle size={15} className="spin" />{copy.previewLoading}</div>}
                {previewError && <div className="studio-preview-state preview-failed"><AlertCircle size={15} />{copy.previewError}</div>}
                {preview && (
                  <div className="studio-safe-preview">
                    <div className="studio-safe-preview-heading"><strong>{copy.preview}: <code dir="auto">{preview.path}</code></strong><button className="icon-button" onClick={() => setPreview(null)} title={copy.previewClose}><X size={14} /></button></div>
                    <p>{copy.previewOnlyText}</p>
                    <pre dir="auto">{preview.text || '—'}</pre>
                    {preview.truncated && <small>…</small>}
                  </div>
                )}
              </section>
            </>
          )}
        </div>

        <aside className="studio-side-column">
          <section className="studio-surface studio-system-card">
            <div className="studio-section-heading"><div><span className="section-kicker">{copy.statusSourceHint}</span><h2><Server size={16} />{copy.systemStatus}</h2></div><span className={`session-status-chip ${status ? 'session-completed' : 'session-failed'}`}><i />{status ? 'LIVE' : 'OFFLINE'}</span></div>
            <div className="system-data-list">
              <div><span>{copy.sessionStatus}</span><strong>{status?.read_only ? copy.readOnly : status ? copy.writable : '—'}</strong></div>
              <div><span>{copy.sessions}</span><strong>{status ? formatCount(status.sessions_count, lang) : '—'}</strong></div>
              <div><span>{copy.pendingApprovals}</span><strong>{status ? formatCount(status.pending_approvals, lang) : '—'}</strong></div>
              <div><span>{copy.spent}</span><strong>{status ? formatDollars(costBudget?.spent_usd, lang) : '—'}</strong></div>
              <div><span>{copy.budget}</span><strong>{status ? formatDollars(costBudget?.max_spend_usd, lang) : '—'}</strong></div>
            </div>
            <div className={`system-mode-strip mode-${stateClass}`}><ShieldCheck size={14} /><span>{status?.read_only ? copy.readOnly : status ? copy.writable : copy.offline}</span></div>
          </section>

          <section className="studio-surface studio-providers-card">
            <div className="studio-section-heading"><div><span className="section-kicker">{copy.systemStatus}</span><h2>{copy.providers}</h2></div><span className="section-count">{status?.providers.length ?? '—'}</span></div>
            {status?.providers.length ? (
              <div className="provider-readiness-list">
                {status.providers.map((provider) => (
                  <div className="provider-readiness-row" key={`${provider.provider}-${provider.model}`} title={provider.budget_reason}>
                    <span className={`provider-presence ${provider.configured ? 'presence-configured' : 'presence-absent'}`}><i /></span>
                    <div><strong dir="auto">{provider.provider}</strong><small dir="auto">{provider.model} · {provider.is_remote ? copy.remoteProvider : copy.localProvider} · {provider.configured ? copy.configured : copy.absent}</small></div>
                    <span className={`provider-budget-chip ${provider.allowed_under_zero_budget ? 'provider-allowed' : 'provider-blocked'}`}>{provider.allowed_under_zero_budget ? copy.zeroBudgetAllowed : copy.zeroBudgetBlocked}</span>
                  </div>
                ))}
              </div>
            ) : <p className="studio-subtle-empty">{statusLoading ? copy.loading : '—'}</p>}
            <p className="provider-caution"><AlertCircle size={13} />{copy.providerCaution}</p>
          </section>

          <section className="studio-surface studio-observability-card">
            <div className="studio-section-heading"><div><span className="section-kicker">{copy.systemStatus}</span><h2><Activity size={15} />{copy.observability}</h2></div><span className="section-count">{typeof status?.observability.mode === 'string' ? status.observability.mode : '—'}</span></div>
            <div className="system-data-list">
              <div><span>{copy.persistedSpans}</span><strong>{typeof status?.observability.persisted_spans_count === 'number' ? formatCount(status.observability.persisted_spans_count, lang) : '—'}</strong></div>
              <div><span>{copy.inMemorySpans}</span><strong>{typeof status?.observability.in_memory_spans_count === 'number' ? formatCount(status.observability.in_memory_spans_count, lang) : '—'}</strong></div>
              <div><span>{copy.otlpExporter}</span><strong>{typeof status?.observability.otlp_configured === 'boolean' ? (status.observability.otlp_configured ? copy.yes : copy.no) : '—'}</strong></div>
            </div>
          </section>

          <section className="studio-surface studio-sessions-card">
            <div className="studio-section-heading"><div><span className="section-kicker">{copy.sessions}</span><h2>{copy.sessions}</h2></div><button className="icon-button" onClick={() => void refreshStudio()} disabled={sessionsLoading} title={copy.refreshSessions}><RefreshCw size={14} className={sessionsLoading ? 'spin' : ''} /></button></div>
            <button className="new-session-button" onClick={() => void createBlankSession()} disabled={running || Boolean(status?.read_only) || !status}><Plus size={15} />{copy.newSession}</button>
            {sessions.length === 0 ? <div className="studio-empty-state studio-sessions-empty"><History size={18} /><p>{sessionsLoading ? copy.loading : copy.noSessions}</p></div> : (
              <div className="studio-session-list">
                {sessions.slice(0, 16).map((session) => (
                  <button key={session.session_id} className={`studio-session-item ${selectedSessionId === session.session_id ? 'studio-session-selected' : ''}`} onClick={() => void loadSession(session.session_id)} disabled={running}>
                    <span className={`session-list-dot session-dot-${session.status}`} />
                    <span className="studio-session-item-copy"><strong>{session.title || copy.sessionTitle}</strong><small>{formatTimestamp(session.updated_at, lang)} · {session.session_id.slice(-8)}</small></span>
                    <span className={`session-mini-status mini-${session.status}`}>{labelForSessionStatus(session.status, copy)}</span>
                  </button>
                ))}
              </div>
            )}
          </section>

          <section className="studio-surface studio-approvals-card">
            <div className="studio-section-heading"><div><span className="section-kicker">{copy.pendingApprovals}</span><h2>{copy.approvals}</h2></div><span className="section-count">{pendingApprovals.length}</span></div>
            <p className="approval-safety-note"><Shield size={13} />{copy.approvalReadOnlyNote}</p>
            {approvals.length === 0 ? <p className="studio-subtle-empty">{copy.noApprovals}</p> : (
              <div className="studio-approval-list">
                {approvals.slice(0, 6).map((approval) => (
                  <article className="studio-approval-item" key={approval.action_id}>
                    <div className="approval-item-top"><span>{approval.connector}</span><span className={`approval-state approval-${approval.status}`}>{approval.status}</span></div>
                    <strong>{approval.summary}</strong>
                    <code dir="ltr">{approval.method} {approval.path}</code>
                    <small dir="ltr">SHA-256 {approval.request_sha256.slice(0, 16)}…</small>
                    {approval.status === 'pending' && status && !status.read_only && <div className="approval-actions"><button onClick={() => void decideApproval(approval, true)} disabled={approvalBusy === approval.action_id}>{approvalBusy === approval.action_id ? <LoaderCircle size={12} className="spin" /> : <Check size={12} />}{copy.approve}</button><button onClick={() => void decideApproval(approval, false)} disabled={approvalBusy === approval.action_id}><X size={12} />{copy.reject}</button></div>}
                  </article>
                ))}
              </div>
            )}
          </section>
        </aside>
      </div>
    </div>
  );
}
