"""Free-First AI providers with replaceable HTTP transport and budget provenance."""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .budget import BudgetGuard, BudgetPolicy, classify_billing_tier
from .contracts import AIProvider, BillingTier, PromptRequest, ProviderKind, ProviderStatus
from .model_catalog import (
    DEFAULT_MODELS,
    MODEL_ENV_VARS,
    find_retired_model,
    retirement_reason,
)
from .provider_transport import (
    AsyncHttpTransport as AsyncHttpTransport,
)
from .provider_transport import (
    HttpJsonResult as HttpJsonResult,
)
from .provider_transport import (
    HttpxJsonTransport as HttpxJsonTransport,
)
from .provider_transport import (
    ProviderError as ProviderError,
)
from .provider_transport import (
    _extract_openai_text as _extract_openai_text,
)
from .security import validate_provider_url

_MODEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}")


def _env_or_default(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def credential_label(secret: str) -> str:
    """Return a static, non-sensitive presence label without exposing key or length."""
    cleaned = secret.strip()
    if not cleaned or "placeholder" in cleaned.lower():
        return "absent"
    return "configured"


class OllamaLocalProvider:
    """Local $0 Ollama provider talking only to validated loopback HTTP."""

    kind = ProviderKind.OLLAMA
    is_remote = False
    env_var = "OLLAMA_BASE_URL"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        num_ctx: int | None = None,
        enabled: bool | None = None,
        transport: AsyncHttpTransport | None = None,
    ) -> None:
        """Configure local Ollama endpoint, 4GB-safe defaults, and optional transport."""
        raw_url = base_url if base_url is not None else os.environ.get("OLLAMA_BASE_URL", "")
        self._base_url = (
            validate_provider_url(raw_url, allow_local_http=True) if raw_url.strip() else ""
        )
        self._model = (
            model.strip()
            if model and model.strip()
            else _env_or_default("OLLAMA_MODEL", "qwen3.5:0.8b")
        )
        raw_ctx = os.environ.get("OLLAMA_NUM_CTX", "2048").strip()
        try:
            env_ctx = int(raw_ctx) if raw_ctx else 2048
        except ValueError:
            env_ctx = 2048
        self._num_ctx = max(256, min(32768, env_ctx if num_ctx is None else num_ctx))
        self._enabled = bool(self._base_url) if enabled is None else enabled
        self._transport = transport or HttpxJsonTransport()
        self._billing_tier, _ = classify_billing_tier(
            kind=self.kind, model=self._model, is_remote=self.is_remote
        )

    @property
    def model(self) -> str:
        """Return configured Ollama model identifier."""
        return self._model

    @property
    def num_ctx(self) -> int:
        """Return context window size passed to Ollama options."""
        return self._num_ctx

    @property
    def billing_tier(self) -> BillingTier:
        """Return proven LOCAL billing classification for loopback Ollama."""
        return self._billing_tier

    @property
    def credential_state(self) -> str:
        """Return sanitized endpoint readiness state."""
        return "loopback_configured" if self.is_configured() else "absent"

    def is_configured(self) -> bool:
        """Return whether a loopback Ollama URL is configured and enabled."""
        return self._enabled and bool(self._base_url)

    async def complete(self, request: PromptRequest, *, timeout_s: float) -> str:
        """Invoke local Ollama chat endpoint without blocking the loop."""
        if not self.is_configured():
            raise ProviderError("not_configured", "Ollama base URL is not configured")
        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})
        result = await self._transport.post_json(
            f"{self._base_url}/api/chat",
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json_body={
                "model": self._model,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": request.temperature,
                    "num_predict": request.max_tokens,
                    "num_ctx": self._num_ctx,
                },
            },
            timeout_s=timeout_s,
        )
        message = result.payload.get("message")
        text = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("invalid_payload", "Ollama response missing message.content")
        return text.strip()


class GeminiFreeProvider:
    """Google Gemini free-tier provider passing keys strictly via x-goog-api-key."""

    kind = ProviderKind.GEMINI
    is_remote = True
    env_var = "GEMINI_API_KEY"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str = "https://generativelanguage.googleapis.com",
        transport: AsyncHttpTransport | None = None,
        billing_tier: BillingTier | None = None,
    ) -> None:
        """Configure Gemini free-tier provider without assuming keys prove $0 billing."""
        self._api_key = (
            api_key if api_key is not None else os.environ.get("GEMINI_API_KEY", "")
        ).strip()
        chosen_model = (
            model.strip()
            if model and model.strip()
            else _env_or_default("GEMINI_MODEL", "gemini-2.5-flash")
        )
        if not _MODEL_PATTERN.fullmatch(chosen_model):
            raise ValueError("Invalid Gemini model identifier")
        self._model = chosen_model
        self._base_url = validate_provider_url(base_url, allow_local_http=False)
        self._transport = transport or HttpxJsonTransport()
        self._billing_tier, self._billing_reason = classify_billing_tier(
            kind=self.kind,
            model=self._model,
            is_remote=self.is_remote,
            explicit_tier=billing_tier,
        )

    @property
    def model(self) -> str:
        """Return configured Gemini model identifier."""
        return self._model

    @property
    def billing_tier(self) -> BillingTier:
        """Return explicit billing classification (FREE_TIER or UNKNOWN_COST)."""
        return self._billing_tier

    @property
    def billing_reason(self) -> str:
        """Return why the tier was assigned, so the guard can explain a block."""
        return self._billing_reason

    @property
    def credential_state(self) -> str:
        """Return non-sensitive credential presence and character count."""
        return credential_label(self._api_key)

    def is_configured(self) -> bool:
        """Return whether a non-placeholder Gemini API key is present."""
        return bool(self._api_key) and "placeholder" not in self._api_key.lower()

    async def complete(self, request: PromptRequest, *, timeout_s: float) -> str:
        """Call Gemini generateContent endpoint using header-based authentication."""
        if not self.is_configured():
            raise ProviderError("not_configured", "Gemini API key is not configured")
        contents: list[dict[str, Any]] = []
        if request.system_prompt:
            contents.append({"role": "user", "parts": [{"text": request.system_prompt}]})
        contents.append({"role": "user", "parts": [{"text": request.prompt}]})
        url = f"{self._base_url}/v1beta/models/{self._model}:generateContent"
        result = await self._transport.post_json(
            url,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "x-goog-api-key": self._api_key,
            },
            json_body={
                "contents": contents,
                "generationConfig": {
                    "temperature": request.temperature,
                    "maxOutputTokens": request.max_tokens,
                },
            },
            timeout_s=timeout_s,
        )
        candidates = result.payload.get("candidates")
        if (
            not isinstance(candidates, list)
            or not candidates
            or not isinstance(candidates[0], dict)
        ):
            raise ProviderError("invalid_payload", "Gemini response missing candidates")
        content = candidates[0].get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list) or not parts or not isinstance(parts[0], dict):
            raise ProviderError("invalid_payload", "Gemini response missing parts")
        text = parts[0].get("text")
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("invalid_payload", "Gemini response text was empty")
        return text.strip()


class OpenAICompatibleFreeProvider:
    """Shared adapter for free-tier OpenAI-compatible endpoints (NVIDIA NIM, Groq, OpenRouter)."""

    is_remote = True

    def __init__(
        self,
        *,
        kind: ProviderKind,
        endpoint_url: str,
        api_key: str,
        model: str,
        env_var: str = "API_KEY",
        extra_headers: Mapping[str, str] | None = None,
        transport: AsyncHttpTransport | None = None,
        billing_tier: BillingTier | None = None,
    ) -> None:
        """Configure a validated HTTPS OpenAI-compatible provider with explicit billing tier."""
        if not _MODEL_PATTERN.fullmatch(model.strip()):
            raise ValueError(f"Invalid model identifier for {kind.value}")
        self._kind = kind
        self._endpoint_url = validate_provider_url(endpoint_url, allow_local_http=False)
        self._api_key = api_key.strip()
        self._model = model.strip()
        self.env_var = env_var
        self._extra_headers = dict(extra_headers or {})
        self._transport = transport or HttpxJsonTransport()
        self._retired = find_retired_model(self._kind, self._model)
        self._billing_tier, self._billing_reason = classify_billing_tier(
            kind=self._kind,
            model=self._model,
            is_remote=self.is_remote,
            explicit_tier=billing_tier,
        )

    @property
    def kind(self) -> ProviderKind:
        """Return the configured provider kind."""
        return self._kind

    @property
    def model(self) -> str:
        """Return the configured remote model ID."""
        return self._model

    @property
    def endpoint_url(self) -> str:
        """Return the validated HTTPS endpoint this provider will call."""
        return self._endpoint_url

    @property
    def retirement_reason(self) -> str | None:
        """Return why this model ID can no longer be served, or None when it is live."""
        return None if self._retired is None else retirement_reason(self._retired)

    @property
    def billing_tier(self) -> BillingTier:
        """Return model-verified billing tier (never inferred from API key presence)."""
        return self._billing_tier

    @property
    def billing_reason(self) -> str:
        """Return why the tier was assigned, so the guard can explain a block."""
        return self._billing_reason

    @property
    def credential_state(self) -> str:
        """Return non-sensitive credential presence and character count."""
        return credential_label(self._api_key)

    def is_configured(self) -> bool:
        """Return whether a non-placeholder API key is available."""
        return bool(self._api_key) and "placeholder" not in self._api_key.lower()

    async def complete(self, request: PromptRequest, *, timeout_s: float) -> str:
        """Send a chat completion request to the OpenAI-compatible free endpoint."""
        if not self.is_configured():
            raise ProviderError("not_configured", f"{self._kind.value} key is not configured")
        if self._retired is not None:
            raise ProviderError("model_retired", retirement_reason(self._retired))
        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})
        result = await self._transport.post_json(
            self._endpoint_url,
            headers={
                **self._extra_headers,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            json_body={
                "model": self._model,
                "messages": messages,
                "temperature": request.temperature,
                "max_tokens": request.max_tokens,
            },
            timeout_s=timeout_s,
        )
        return _extract_openai_text(result.payload)


def _choose_openai_model(kind: ProviderKind, model: str | None) -> str:
    if model and model.strip():
        return model.strip()
    return _env_or_default(MODEL_ENV_VARS[kind], DEFAULT_MODELS[kind])


def create_nvidia_nim_provider(
    *,
    api_key: str | None = None,
    model: str | None = None,
    transport: AsyncHttpTransport | None = None,
    billing_tier: BillingTier | None = None,
) -> OpenAICompatibleFreeProvider:
    """Build an NVIDIA NIM provider reading NVIDIA_API_KEY and NVIDIA_MODEL from env."""
    return OpenAICompatibleFreeProvider(
        kind=ProviderKind.NVIDIA,
        endpoint_url="https://integrate.api.nvidia.com/v1/chat/completions",
        api_key=api_key if api_key is not None else os.environ.get("NVIDIA_API_KEY", ""),
        model=_choose_openai_model(ProviderKind.NVIDIA, model),
        env_var="NVIDIA_API_KEY",
        transport=transport,
        billing_tier=billing_tier,
    )


def create_groq_free_provider(
    *,
    api_key: str | None = None,
    model: str | None = None,
    transport: AsyncHttpTransport | None = None,
    billing_tier: BillingTier | None = None,
) -> OpenAICompatibleFreeProvider:
    """Build a Groq provider; the default replacement model is not claimed as free."""
    return OpenAICompatibleFreeProvider(
        kind=ProviderKind.GROQ,
        endpoint_url="https://api.groq.com/openai/v1/chat/completions",
        api_key=api_key if api_key is not None else os.environ.get("GROQ_API_KEY", ""),
        model=_choose_openai_model(ProviderKind.GROQ, model),
        env_var="GROQ_API_KEY",
        transport=transport,
        billing_tier=billing_tier,
    )


def create_openrouter_free_provider(
    *,
    api_key: str | None = None,
    model: str | None = None,
    transport: AsyncHttpTransport | None = None,
    billing_tier: BillingTier | None = None,
) -> OpenAICompatibleFreeProvider:
    """Build an OpenRouter provider; ':free' or 'openrouter/free' pass MAX_SPEND=0."""
    return OpenAICompatibleFreeProvider(
        kind=ProviderKind.OPENROUTER,
        endpoint_url="https://openrouter.ai/api/v1/chat/completions",
        api_key=api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", ""),
        model=_choose_openai_model(ProviderKind.OPENROUTER, model),
        env_var="OPENROUTER_API_KEY",
        extra_headers={
            "HTTP-Referer": "https://github.com/sayedelazameydesign-crypto/ai-2027",
            "X-Title": "AI-2027 Protocol",
        },
        transport=transport,
        billing_tier=billing_tier,
    )


class MissingProviderModelError(ValueError):
    """Raised when a provider requires an explicitly configured model ID."""


def create_omnirouters_provider(
    *,
    api_key: str | None = None,
    model: str | None = None,
    transport: AsyncHttpTransport | None = None,
) -> OpenAICompatibleFreeProvider:
    """Build an OmniRouters provider from the documented OpenAI-compatible API.

    OmniRouters is NOT free-guaranteed: accounts do not receive free credit by
    default and the minimum top-up is USD 10. This factory therefore has three
    deliberate differences from the other provider builders:

    * It accepts NO ``billing_tier`` parameter. Passing one would let a caller
      classify a paid provider as ``FREE_TIER``; ``classify_billing_tier``
      trusts an explicit tier for any kind except OpenRouter's ``:free`` check.
      OmniRouters is classified only by policy, so the bypass is unavailable
      at the API surface rather than merely unused.
    * It has NO default model. The available catalogue is per-account, so
      ``OMNIROUTERS_MODEL`` must be set explicitly; without it nothing is
      constructed and no request can be attempted.
    * It never claims free tier. An unlisted model falls through to
      ``UNKNOWN_COST`` and stays blocked while ``MAX_SPEND=0``.
    """
    resolved_model = (model or os.environ.get("OMNIROUTERS_MODEL", "")).strip()
    if not resolved_model:
        raise MissingProviderModelError(
            "OMNIROUTERS_MODEL is required: OmniRouters has no default model and no "
            "free-tier guarantee. Set it to a model id from your account catalogue "
            "(GET https://omnirouters.com/v1/models)."
        )
    return OpenAICompatibleFreeProvider(
        kind=ProviderKind.OMNIROUTERS,
        endpoint_url="https://omnirouters.com/v1/chat/completions",
        api_key=api_key if api_key is not None else os.environ.get("OMNIROUTERS_API_KEY", ""),
        model=resolved_model,
        env_var="OMNIROUTERS_API_KEY",
        transport=transport,
    )


class MissingProviderConfigError(MissingProviderModelError):
    """Required provider configuration (workspace, region, or model) is missing."""


#: Hostname-safe shapes for the Model Studio endpoint parts. The values are
#: interpolated into a URL hostname, so anything outside these sets is
#: refused before a request can be attempted.
_WORKSPACE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$")
_REGION_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def create_qwen_model_studio_provider(
    *,
    api_key: str | None = None,
    workspace_id: str | None = None,
    region: str | None = None,
    model: str | None = None,
    transport: AsyncHttpTransport | None = None,
) -> OpenAICompatibleFreeProvider:
    """Build an Alibaba Cloud Model Studio (Qwen) provider (OpenAI-compatible API).

    Integration audit: docs recorded in ``docs/QWEN-PROVIDER-INTEGRATION.md``.

    Deliberate properties, mirroring ``create_omnirouters_provider``:

    * It accepts NO ``billing_tier`` parameter. Model Studio serves a free
      quota and then charges, so it is classified ``UNKNOWN_COST`` only —
      admitted under ``MAX_SPEND=0`` never, and only via an explicit
      non-zero budget otherwise (``NON_RECLASSIFIABLE_PROVIDERS``).
    * It has NO default model. Official docs advertised ``qwen3.8-max`` while
      operators report ``qwen4-max``; ids rotate, so ``QWEN_MODEL`` must be
      set explicitly from the account's model list.
    * The endpoint is ``https://{WorkspaceId}.{region}.maas.aliyuncs.com/
      compatible-mode/v1/chat/completions`` per the official OpenAI-compatible
      docs; ``DASHSCOPE_WORKSPACE_ID`` is required and ``DASHSCOPE_REGION``
      defaults to ``ap-southeast-1`` (the region the docs lead with).

    Args:
        api_key: ``DASHSCOPE_API_KEY`` value; falls back to the environment.
        workspace_id: Model Studio workspace id; falls back to ``DASHSCOPE_WORKSPACE_ID``.
        region: Endpoint region; falls back to ``DASHSCOPE_REGION``, then ``ap-southeast-1``.
        model: Model id; falls back to ``QWEN_MODEL`` (required, no default).
        transport: Injected HTTP transport for hermetic tests.

    Returns:
        A configured provider that the budget guard still evaluates per call.

    Raises:
        MissingProviderConfigError: The model or workspace id is missing.
        ValueError: The workspace id or region is not hostname-safe.
    """
    resolved_model = (model or os.environ.get("QWEN_MODEL", "")).strip()
    if not resolved_model:
        raise MissingProviderConfigError(
            "QWEN_MODEL is required: Model Studio model ids rotate (official docs "
            "showed qwen3.8-max; accounts may serve qwen4-max). Set it to an id "
            "from your account's model list."
        )
    resolved_workspace = (workspace_id or os.environ.get("DASHSCOPE_WORKSPACE_ID", "")).strip()
    if not resolved_workspace:
        raise MissingProviderConfigError(
            "DASHSCOPE_WORKSPACE_ID is required: the OpenAI-compatible base_url is "
            "https://{WorkspaceId}.{region}.maas.aliyuncs.com/compatible-mode/v1 and "
            "cannot be built without your workspace id."
        )
    if not _WORKSPACE_ID_RE.match(resolved_workspace):
        raise ValueError("DASHSCOPE_WORKSPACE_ID must be hostname-safe (letters, digits, hyphens).")
    resolved_region = (region or os.environ.get("DASHSCOPE_REGION", "")).strip() or "ap-southeast-1"
    if not _REGION_RE.match(resolved_region):
        raise ValueError(
            "DASHSCOPE_REGION must be hostname-safe (lowercase letters, digits, hyphens)."
        )
    endpoint = (
        f"https://{resolved_workspace}.{resolved_region}.maas.aliyuncs.com"
        "/compatible-mode/v1/chat/completions"
    )
    return OpenAICompatibleFreeProvider(
        kind=ProviderKind.QWEN,
        endpoint_url=endpoint,
        api_key=api_key if api_key is not None else os.environ.get("DASHSCOPE_API_KEY", ""),
        model=resolved_model,
        env_var="DASHSCOPE_API_KEY",
        transport=transport,
    )


class DeterministicFallbackProvider:
    """Zero-cost offline fallback explicitly marked as deterministic simulation."""

    kind = ProviderKind.DETERMINISTIC
    is_remote = False
    env_var = "NONE"
    billing_tier = BillingTier.LOCAL

    def __init__(self, *, model: str = "deterministic-echo-v1") -> None:
        """Initialize the offline deterministic fallback provider."""
        self._model = model

    @property
    def model(self) -> str:
        """Return deterministic fallback identifier."""
        return self._model

    @property
    def credential_state(self) -> str:
        """Return static readiness label because fallback needs no secret."""
        return "always_ready"

    def is_configured(self) -> bool:
        """Always return True so the $0 fallback chain never deadlocks offline."""
        return True

    async def complete(self, request: PromptRequest, *, timeout_s: float) -> str:
        """Return a deterministic structured response asynchronously without claiming LLM output."""
        async with asyncio.timeout(timeout_s):
            await asyncio.sleep(0)
        summary = " ".join(request.prompt.split())
        preview = summary[:160] + ("..." if len(summary) > 160 else "")
        return (
            "[deterministic-offline-fallback | simulated=true | cost=$0.00] "
            f"Processed bounded request ({len(request.prompt)} chars, "
            f"max_tokens={request.max_tokens}): {preview}"
        )


def build_default_providers(
    *, transport: AsyncHttpTransport | None = None
) -> tuple[
    OllamaLocalProvider,
    GeminiFreeProvider,
    OpenAICompatibleFreeProvider,
    OpenAICompatibleFreeProvider,
    OpenAICompatibleFreeProvider,
    DeterministicFallbackProvider,
]:
    """Assemble the default Free-First provider chain ending with deterministic fallback."""
    shared = transport or HttpxJsonTransport()
    return (
        OllamaLocalProvider(transport=shared),
        GeminiFreeProvider(transport=shared),
        create_nvidia_nim_provider(transport=shared),
        create_groq_free_provider(transport=shared),
        create_openrouter_free_provider(transport=shared),
        DeterministicFallbackProvider(),
    )


def describe_provider_readiness(
    providers: Sequence[AIProvider] | None = None,
    *,
    budget_policy: BudgetPolicy | None = None,
) -> tuple[ProviderStatus, ...]:
    """Return non-sensitive readiness and budget provenance for every provider."""
    items = providers if providers is not None else build_default_providers()
    guard = BudgetGuard(budget_policy or BudgetPolicy.from_env())
    statuses: list[ProviderStatus] = []
    for item in items:
        env_var = str(getattr(item, "env_var", "CUSTOM"))
        is_ready = bool(item.is_configured())
        cred_state = (
            "always_ready"
            if item.kind == ProviderKind.DETERMINISTIC
            else ("configured" if is_ready else "absent")
        )
        decision = guard.evaluate_provider(item)
        statuses.append(
            ProviderStatus(
                provider=item.kind,
                model=item.model,
                configured=item.is_configured(),
                is_remote=item.is_remote,
                env_var=env_var,
                credential_state=cred_state,
                billing_tier=decision.billing_tier,
                cost_status=decision.cost_status,
                allowed_under_zero_budget=decision.allowed,
                budget_reason=decision.reason,
            )
        )
    return tuple(statuses)
