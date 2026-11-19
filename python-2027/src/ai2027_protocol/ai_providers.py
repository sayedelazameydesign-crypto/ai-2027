"""Free-First AI providers with replaceable HTTP transport and budget provenance."""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx

from .budget import BudgetGuard, BudgetPolicy, classify_billing_tier
from .contracts import AIProvider, BillingTier, PromptRequest, ProviderKind, ProviderStatus
from .security import redact_secrets, validate_provider_url

_MAX_RESPONSE_BYTES = 1024 * 1024
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


class ProviderError(RuntimeError):
    """Structured error raised by an AI provider with retry semantics."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        """Store error classification without exposing raw credentials."""
        super().__init__(redact_secrets(message))
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


@dataclass(frozen=True)
class HttpJsonResult:
    """Normalized JSON response returned by an async HTTP transport."""

    status_code: int
    payload: dict[str, Any]


@runtime_checkable
class AsyncHttpTransport(Protocol):
    """Replaceable async transport protocol for JSON POST requests."""

    async def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any],
        timeout_s: float,
    ) -> HttpJsonResult:
        """Send an async JSON POST request and return parsed JSON."""
        ...


class HttpxJsonTransport:
    """Default async HTTP transport backed by httpx.AsyncClient."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        """Create a transport wrapping an optional shared httpx.AsyncClient."""
        self._external_client = client

    async def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any],
        timeout_s: float,
    ) -> HttpJsonResult:
        """Perform a bounded non-redirecting JSON POST via httpx."""
        if self._external_client is not None:
            return await self._send(
                self._external_client,
                url=url,
                headers=headers,
                json_body=json_body,
                timeout_s=timeout_s,
            )
        async with httpx.AsyncClient(follow_redirects=False) as client:
            return await self._send(
                client, url=url, headers=headers, json_body=json_body, timeout_s=timeout_s
            )

    async def _send(
        self,
        client: httpx.AsyncClient,
        *,
        url: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any],
        timeout_s: float,
    ) -> HttpJsonResult:
        try:
            async with asyncio.timeout(timeout_s):
                response = await client.post(
                    url,
                    headers=dict(headers),
                    json=dict(json_body),
                    timeout=timeout_s,
                    follow_redirects=False,
                )
        except TimeoutError as exc:
            raise ProviderError("timeout", "Provider request timed out", retryable=True) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "timeout", "Provider HTTP client timed out", retryable=True
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderError(
                "network_error", "Provider endpoint unreachable", retryable=True
            ) from exc

        content = response.content
        if len(content) > _MAX_RESPONSE_BYTES:
            raise ProviderError("response_too_large", "Provider response exceeded 1 MiB cap")
        if response.status_code >= 500 or response.status_code in {408, 429}:
            raise ProviderError(
                f"http_{response.status_code}",
                f"Transient provider HTTP status {response.status_code}",
                retryable=True,
                status_code=response.status_code,
            )
        if response.status_code >= 400:
            raise ProviderError(
                f"http_{response.status_code}",
                f"Non-retryable provider HTTP status {response.status_code}",
                retryable=False,
                status_code=response.status_code,
            )
        try:
            parsed = json.loads(content)
        except ValueError as exc:
            raise ProviderError("invalid_json", "Provider returned malformed JSON") from exc
        if not isinstance(parsed, dict):
            raise ProviderError("invalid_json", "Provider JSON root must be an object")
        return HttpJsonResult(status_code=response.status_code, payload=parsed)


def _extract_openai_text(payload: Mapping[str, Any]) -> str:
    """Extract completion text from OpenAI-compatible chat responses."""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ProviderError("invalid_payload", "Missing choices in provider response")
    message = choices[0].get("message")
    text = message.get("content") if isinstance(message, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise ProviderError("invalid_payload", "Completion text missing in provider response")
    return text.strip()


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
        enabled: bool | None = None,
        transport: AsyncHttpTransport | None = None,
    ) -> None:
        """Configure local Ollama endpoint and optional injectable transport."""
        raw_url = base_url if base_url is not None else os.environ.get("OLLAMA_BASE_URL", "")
        self._base_url = (
            validate_provider_url(raw_url, allow_local_http=True) if raw_url.strip() else ""
        )
        self._model = (
            model.strip()
            if model and model.strip()
            else _env_or_default("OLLAMA_MODEL", "qwen2.5:3b")
        )
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
        self._billing_tier, _ = classify_billing_tier(
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
        self._transport = transport or HttpxJsonTransport()
        self._billing_tier, _ = classify_billing_tier(
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
    def billing_tier(self) -> BillingTier:
        """Return model-verified billing tier (never inferred from API key presence)."""
        return self._billing_tier

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
        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})
        result = await self._transport.post_json(
            self._endpoint_url,
            headers={
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


def create_nvidia_nim_provider(
    *,
    api_key: str | None = None,
    model: str | None = None,
    transport: AsyncHttpTransport | None = None,
    billing_tier: BillingTier | None = None,
) -> OpenAICompatibleFreeProvider:
    """Build an NVIDIA NIM provider reading NVIDIA_API_KEY and NVIDIA_MODEL from env."""
    chosen_model = (
        model.strip()
        if model and model.strip()
        else _env_or_default("NVIDIA_MODEL", "meta/llama-3.1-8b-instruct")
    )
    return OpenAICompatibleFreeProvider(
        kind=ProviderKind.NVIDIA,
        endpoint_url="https://integrate.api.nvidia.com/v1/chat/completions",
        api_key=api_key if api_key is not None else os.environ.get("NVIDIA_API_KEY", ""),
        model=chosen_model,
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
    """Build a Groq free-tier provider reading credentials from arguments or env."""
    chosen_model = (
        model.strip()
        if model and model.strip()
        else _env_or_default("GROQ_MODEL", "llama-3.3-70b-versatile")
    )
    return OpenAICompatibleFreeProvider(
        kind=ProviderKind.GROQ,
        endpoint_url="https://api.groq.com/openai/v1/chat/completions",
        api_key=api_key if api_key is not None else os.environ.get("GROQ_API_KEY", ""),
        model=chosen_model,
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
    """Build an OpenRouter provider; only ':free' models pass MAX_SPEND=0 BudgetGuard."""
    chosen_model = (
        model.strip()
        if model and model.strip()
        else _env_or_default("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
    )
    return OpenAICompatibleFreeProvider(
        kind=ProviderKind.OPENROUTER,
        endpoint_url="https://openrouter.ai/api/v1/chat/completions",
        api_key=(api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "")),
        model=chosen_model,
        env_var="OPENROUTER_API_KEY",
        transport=transport,
        billing_tier=billing_tier,
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
