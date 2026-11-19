"""Strict Pydantic V2 schemas and replaceable Protocol contracts."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .security import redact_secrets, sanitize_prompt_text


class StrictModel(BaseModel):
    """Base model enforcing immutable fields and forbidding unknown keys."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ProviderKind(StrEnum):
    """Supported Free-First AI providers in priority order."""

    OLLAMA = "ollama"
    GEMINI = "gemini"
    NVIDIA = "nvidia"
    GROQ = "groq"
    OPENROUTER = "openrouter"
    DETERMINISTIC = "deterministic"


class BillingTier(StrEnum):
    """Explicit provider/model billing classification used by BudgetGuard."""

    LOCAL = "local"
    FREE_TIER = "free_tier"
    UNKNOWN_COST = "unknown_cost"
    PAID = "paid"


class CostStatus(StrEnum):
    """Audit trail explaining how cost_usd was proven or guarded."""

    PROVEN_ZERO_LOCAL = "proven_zero_local"
    DECLARED_FREE_TIER_UNBILLED = "declared_free_tier_unbilled"
    UNKNOWN_COST_BLOCKED = "unknown_cost_blocked"
    PAID_BLOCKED = "paid_blocked"
    ALLOWED_WITHIN_BUDGET = "allowed_within_budget"


class BudgetSnapshot(StrictModel):
    """Measured runtime ledger of known-zero, free-tier, and blocked attempts."""

    max_spend_usd: float = Field(default=0.0, ge=0.0)
    spent_usd: float = Field(default=0.0, ge=0.0)
    allow_unknown_cost: bool = False
    known_zero_local_calls: int = Field(default=0, ge=0)
    declared_free_tier_calls: int = Field(default=0, ge=0)
    unknown_cost_blocked: int = Field(default=0, ge=0)
    paid_blocked: int = Field(default=0, ge=0)
    api_key_never_implies_free: bool = True


class ProviderStatus(StrictModel):
    """Sanitized readiness and budget-classification report for one provider."""

    provider: ProviderKind
    model: str = Field(min_length=1, max_length=160)
    configured: bool
    is_remote: bool
    env_var: str
    credential_state: str
    billing_tier: BillingTier = BillingTier.UNKNOWN_COST
    cost_status: CostStatus = CostStatus.UNKNOWN_COST_BLOCKED
    allowed_under_zero_budget: bool = False
    budget_reason: str = ""


class RuleStatus(StrEnum):
    """Evaluation status for one Python 2027 protocol rule."""

    PASS = "pass"  # noqa: S105
    WARN = "warn"
    FAIL = "fail"
    EXEMPT = "exempt"


class PromptRequest(StrictModel):
    """Validated input contract for AI routing."""

    prompt: str = Field(min_length=1, max_length=16_000)
    system_prompt: str | None = Field(default=None, max_length=4_000)
    temperature: float = Field(default=0.2, ge=0.0, le=1.5)
    max_tokens: int = Field(default=256, ge=1, le=4096)
    allow_remote: bool = True
    preferred_providers: tuple[ProviderKind, ...] | None = None

    @field_validator("prompt")
    @classmethod
    def _validate_prompt(cls, value: str) -> str:
        return sanitize_prompt_text(value, max_length=16_000)

    @field_validator("system_prompt")
    @classmethod
    def _validate_system_prompt(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return sanitize_prompt_text(value, max_length=4_000)


class ProviderAttempt(StrictModel):
    """Recorded execution trace for a single provider call, block, or retry."""

    provider: ProviderKind
    model: str = Field(min_length=1, max_length=160)
    attempt: int = Field(ge=1)
    ok: bool
    latency_ms: float = Field(ge=0.0)
    billing_tier: BillingTier = BillingTier.UNKNOWN_COST
    cost_known: bool = False
    cost_status: CostStatus = CostStatus.UNKNOWN_COST_BLOCKED
    estimated_cost_usd: float | None = Field(default=None, ge=0.0)
    error_code: str | None = None
    detail: str | None = None

    @field_validator("detail")
    @classmethod
    def _scrub_detail(cls, value: str | None) -> str | None:
        return redact_secrets(value) if value is not None else None


class CompletionResponse(StrictModel):
    """Normalized output from Free-First routing with verified cost and trace metadata."""

    provider: ProviderKind
    model: str = Field(min_length=1, max_length=160)
    text: str
    simulated: bool = False
    cost_usd: float | None = Field(default=0.0, ge=0.0)
    billing_tier: BillingTier = BillingTier.LOCAL
    cost_status: CostStatus = CostStatus.PROVEN_ZERO_LOCAL
    cost_known: bool = True
    cost_verified_by_invoice: bool = False
    max_spend_usd: float = Field(default=0.0, ge=0.0)
    trace_id: str = Field(min_length=8, max_length=64)
    attempts: tuple[ProviderAttempt, ...]
    latency_ms: float = Field(ge=0.0)


class RuleResult(StrictModel):
    """Outcome for a single rule in the 10-rule Python 2027 protocol."""

    rule_id: int = Field(ge=1, le=10)
    name: str
    status: RuleStatus
    score: float = Field(ge=0.0, le=1.0)
    evidence: str
    remediation: str | None = None


class ProjectAuditSummary(StrictModel):
    """Protocol audit scorecard for one Python project directory."""

    project_name: str
    project_path: str
    python_files: int = Field(ge=0)
    total_loc: int = Field(ge=0)
    score_pct: float = Field(ge=0.0, le=100.0)
    adr_exempt: bool = False
    rules: tuple[RuleResult, ...]


class ProtocolAuditReport(StrictModel):
    """Aggregated protocol audit across one or more projects."""

    root_path: str
    scanned_projects: int = Field(ge=0)
    average_score_pct: float = Field(ge=0.0, le=100.0)
    projects: tuple[ProjectAuditSummary, ...]
    duration_ms: float = Field(ge=0.0)
    trace_id: str = Field(min_length=8, max_length=64)


class TimingStats(StrictModel):
    """Measured latency statistics with spread and throughput."""

    name: str
    n: int = Field(ge=1)
    min_ms: float = Field(ge=0.0)
    p50_ms: float = Field(ge=0.0)
    p95_ms: float = Field(ge=0.0)
    max_ms: float = Field(ge=0.0)
    spread_pct: float = Field(ge=0.0)
    ops_per_sec: float = Field(ge=0.0)


class ComparisonResult(StrictModel):
    """Before/after measured comparison proving optimization impact."""

    baseline: TimingStats
    optimized: TimingStats
    speedup_x: float = Field(ge=0.0)
    measured_note: str


class BenchmarkReport(StrictModel):
    """Complete reproducible benchmark payload."""

    python_version: str
    platform: str
    comparisons: tuple[ComparisonResult, ...]
    standalone: tuple[TimingStats, ...]
    trace_id: str = Field(min_length=8, max_length=64)


class SpanRecord(StrictModel):
    """Serializable OpenTelemetry span snapshot."""

    name: str
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    duration_ms: float = Field(ge=0.0)
    status: Literal["OK", "ERROR"]
    attributes: dict[str, str | int | float | bool]


class AuditRequest(StrictModel):
    """Validated HTTP request contract for protocol auditing."""

    target_path: str = Field(default="python-2027", min_length=1, max_length=256)
    include_archived: bool = False


class BatchPromptRequest(StrictModel):
    """Validated HTTP request contract for concurrent TaskGroup batch routing."""

    requests: tuple[PromptRequest, ...] = Field(min_length=1, max_length=16)
    max_concurrency: int = Field(default=4, ge=1, le=8)


@runtime_checkable
class AIProvider(Protocol):
    """Replaceable interface for any Free-First or fallback completion provider."""

    @property
    def kind(self) -> ProviderKind:
        """Return the provider identifier."""
        ...

    @property
    def model(self) -> str:
        """Return the configured model name."""
        ...

    @property
    def is_remote(self) -> bool:
        """Return whether requests leave the local machine."""
        ...

    def is_configured(self) -> bool:
        """Return whether required configuration/credentials exist."""
        ...

    async def complete(self, request: PromptRequest, *, timeout_s: float) -> str:
        """Execute a bounded completion call without blocking the event loop."""
        ...


@runtime_checkable
class TelemetrySink(Protocol):
    """Replaceable interface for structured logging and distributed tracing."""

    def start_span(
        self,
        name: str,
        attributes: Mapping[str, str | int | float | bool] | None = None,
    ) -> AbstractContextManager[Any]:
        """Open a tracing span context."""
        ...

    def current_trace_id(self) -> str:
        """Return the active trace ID or generate a fresh hex trace ID."""
        ...

    def log(self, level: str, event: str, **fields: object) -> None:
        """Emit a structured log record with automatic secret scrubbing."""
        ...

    def exported_spans(self) -> list[SpanRecord]:
        """Return captured spans for inspection or export."""
        ...
