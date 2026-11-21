export type Language = 'ar' | 'en';
export type ConnectionState = 'checking' | 'online' | 'offline';

export interface BudgetSnapshot {
  max_spend_usd: number;
  spent_usd: number;
  allow_unknown_cost: boolean;
  known_zero_local_calls: number;
  declared_free_tier_calls: number;
  unknown_cost_blocked: number;
  paid_blocked: number;
  api_key_never_implies_free: boolean;
}

export interface MemoryStats {
  available: boolean;
  total_memories: number;
  verified_artifacts: number;
  refusals: number;
  noise_floor_p95: number;
}

export interface ProviderReadiness {
  provider: string;
  model: string;
  configured: boolean;
  is_remote: boolean;
  credential_state: string;
  billing_tier: string;
  cost_status: string;
  allowed_under_zero_budget: boolean;
  budget_reason: string;
}

export interface StudioConnectorStatus {
  connector_id: string;
  name: string;
  configured: boolean;
  credential_state: string;
  read_supported: boolean;
  write_requires_approval: boolean;
  notes: string[];
}

export interface StudioStatus {
  service: string;
  read_only: boolean;
  cost_usd: number;
  kernel_available: boolean;
  memory: MemoryStats;
  budget: BudgetSnapshot;
  observability: Record<string, unknown>;
  providers: ProviderReadiness[];
  connectors: StudioConnectorStatus[];
  sessions_count: number;
  pending_approvals: number;
}

export interface StudioPlanStep {
  step_id: number;
  title: string;
  status: string;
}

export interface StudioArtifact {
  path: string;
  kind: string;
  sha256: string | null;
  byte_size: number;
  verified: boolean;
  preview_url: string;
}

export interface StudioStep {
  step: number;
  thought: string;
  provider: string;
  model: string;
  latency_ms: number;
  invocations: Array<Record<string, unknown>>;
  outcomes: Array<Record<string, unknown>>;
}

export interface StudioSession {
  session_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  status: 'idle' | 'running' | 'completed' | 'aborted' | 'failed' | string;
  mode: 'live-llm' | 'offline-deterministic' | string;
  provider: string;
  model: string;
  simulated_llm: boolean;
  cost_usd: number | null;
  trace_id: string;
  task: string;
  plan: StudioPlanStep[];
  steps: StudioStep[];
  artifacts: StudioArtifact[];
  approvals: string[];
  final_answer: string;
  checkpoint_step: number;
}

export interface StudioEvent {
  event: string;
  session_id: string;
  step: number;
  message: string;
  data: Record<string, unknown>;
  timestamp: string;
}

export interface StudioApproval {
  action_id: string;
  session_id: string;
  connector: string;
  summary: string;
  method: string;
  path: string;
  request_sha256: string;
  status: 'pending' | 'approved' | 'rejected' | string;
  created_at: string;
  decided_at: string | null;
  decision_note: string | null;
}

export interface HubConnector {
  id: string;
  name: string;
  state: 'unconfigured' | 'configured' | 'needs_resource_config' | string;
  auth_group: string;
  search_supported: boolean;
  read_supported: boolean;
  notes: string[];
}

export interface HubCatalog {
  source: 'connectors-hub';
  available: boolean;
  reason:
    | 'connected'
    | 'token_missing'
    | 'unauthorized'
    | 'service_unavailable'
    | 'upstream_error'
    | 'invalid_endpoint'
    | 'invalid_payload'
    | string;
  checked_at: string;
  connectors: HubConnector[];
}

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      cache: 'no-store',
      ...init,
      headers: { Accept: 'application/json', ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, 'network');
  }

  const bodyText = await response.text();
  let body: unknown = null;
  try {
    body = bodyText ? JSON.parse(bodyText) as unknown : null;
  } catch {
    body = null;
  }

  if (!response.ok) {
    const candidate = body && typeof body === 'object'
      ? ('detail' in body ? body.detail : 'error' in body ? body.error : null)
      : null;
    const message = typeof candidate === 'string' ? candidate : `HTTP ${response.status}`;
    throw new ApiError(response.status, message);
  }
  return body as T;
}

async function responseError(response: Response): Promise<ApiError> {
  let message = `HTTP ${response.status}`;
  try {
    const body = await response.json() as { detail?: unknown; error?: unknown };
    const detail = typeof body.detail === 'string' ? body.detail : body.error;
    if (typeof detail === 'string') message = detail;
  } catch {
    // Keep the status code as a truthful fallback for non-JSON error bodies.
  }
  return new ApiError(response.status, message);
}

export async function readStudioEventStream(
  response: Response,
  onEvent: (event: StudioEvent) => void,
): Promise<void> {
  if (!response.ok) throw await responseError(response);
  if (!response.body) throw new ApiError(0, 'stream_unavailable');

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  const dispatch = (chunk: string) => {
    const lines = chunk.replace(/^\uFEFF/, '').split(/\r?\n/);
    let eventName = '';
    const data: string[] = [];
    for (const line of lines) {
      if (line.startsWith('event:')) eventName = line.slice(6).trim();
      else if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
    }
    if (data.length === 0) return;

    try {
      const parsed = JSON.parse(data.join('\n')) as Partial<StudioEvent>;
      if (!parsed || typeof parsed !== 'object') return;
      onEvent({
        event: eventName || (typeof parsed.event === 'string' ? parsed.event : 'message'),
        session_id: typeof parsed.session_id === 'string' ? parsed.session_id : '',
        step: typeof parsed.step === 'number' ? parsed.step : 0,
        message: typeof parsed.message === 'string' ? parsed.message : '',
        data: parsed.data && typeof parsed.data === 'object' ? parsed.data : {},
        timestamp: typeof parsed.timestamp === 'string' ? parsed.timestamp : new Date().toISOString(),
      });
    } catch {
      onEvent({
        event: eventName || 'message',
        session_id: '',
        step: 0,
        message: data.join('\n').slice(0, 500),
        data: {},
        timestamp: new Date().toISOString(),
      });
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.match(/\r?\n\r?\n/);
    while (boundary && boundary.index !== undefined) {
      const block = buffer.slice(0, boundary.index);
      buffer = buffer.slice(boundary.index + boundary[0].length);
      dispatch(block);
      boundary = buffer.match(/\r?\n\r?\n/);
    }
  }
  buffer += decoder.decode();
  if (buffer.trim()) dispatch(buffer);
}

export function formatTimestamp(value: string | number | undefined, lang: Language): string {
  if (value === undefined || value === '') return '—';
  const asNumber = typeof value === 'number' ? value : Number.NaN;
  const date = typeof value === 'number'
    ? new Date(asNumber > 10_000_000_000 ? asNumber : asNumber * 1000)
    : new Date(value);
  if (!Number.isFinite(date.getTime())) return '—';
  return new Intl.DateTimeFormat(lang === 'ar' ? 'ar-EG' : 'en-US', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}

export function artifactEndpoint(sessionId: string, artifactPath: string): string {
  const encodedPath = artifactPath.split('/').map((part) => encodeURIComponent(part)).join('/');
  return `/studio-api/v1/studio/sessions/${encodeURIComponent(sessionId)}/artifacts/${encodedPath}`;
}

export function isPreviewableArtifact(path: string): boolean {
  return /\.(txt|md|json|csv|log|yaml|yml|xml|html|css|js|ts|py|svg)$/i.test(path);
}
