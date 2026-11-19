"""AI-2027 Python 2027 Software Excellence Protocol reference package."""

from __future__ import annotations

from .ai_providers import (
    DeterministicFallbackProvider,
    GeminiFreeProvider,
    OllamaLocalProvider,
    OpenAICompatibleFreeProvider,
    ProviderError,
    build_default_providers,
    create_groq_free_provider,
    create_nvidia_nim_provider,
    create_openrouter_free_provider,
    credential_label,
    describe_provider_readiness,
)
from .auditor import ProtocolAuditor, format_audit_markdown
from .benchmark import run_benchmark_suite, summarize_nanoseconds
from .contracts import (
    AIProvider,
    CompletionResponse,
    PromptRequest,
    ProtocolAuditReport,
    ProviderKind,
    ProviderStatus,
    RuleStatus,
    TelemetrySink,
)
from .router import AllProvidersFailedError, FreeFirstRouter, RouterConfig
from .server import create_app
from .telemetry import ObservabilityRuntime

__all__ = [
    "AIProvider",
    "AllProvidersFailedError",
    "CompletionResponse",
    "DeterministicFallbackProvider",
    "FreeFirstRouter",
    "GeminiFreeProvider",
    "ObservabilityRuntime",
    "OllamaLocalProvider",
    "OpenAICompatibleFreeProvider",
    "PromptRequest",
    "ProtocolAuditReport",
    "ProtocolAuditor",
    "ProviderError",
    "ProviderKind",
    "ProviderStatus",
    "RouterConfig",
    "RuleStatus",
    "TelemetrySink",
    "build_default_providers",
    "create_app",
    "create_groq_free_provider",
    "create_nvidia_nim_provider",
    "create_openrouter_free_provider",
    "credential_label",
    "describe_provider_readiness",
    "format_audit_markdown",
    "run_benchmark_suite",
    "summarize_nanoseconds",
]
