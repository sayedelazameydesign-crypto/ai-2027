"""Async Ollama HTTP transport and Cloud-guarded remote adapter."""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Mapping
from typing import Any, Literal, Protocol, runtime_checkable

import httpx

from .ai_providers import ProviderError, _extract_openai_text
from .budget import classify_billing_tier
from .contracts import BillingTier, PromptRequest, ProviderKind, StrictModel
from .security import validate_provider_url

_MAX_OLLAMA_BYTES = 2 * 1024 * 1024
_OLLAMA_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}")


class OllamaHttpResult(StrictModel):
    """Normalized JSON response returned by an async Ollama transport."""

    status_code: int
    payload: dict[str, Any]


@runtime_checkable
class AsyncOllamaTransport(Protocol):
    """Replaceable async transport supporting both GET and POST Ollama endpoints."""

    async def get_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_s: float,
    ) -> OllamaHttpResult:
        """Issue an async GET request and return parsed JSON object."""
        ...

    async def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any],
        timeout_s: float,
    ) -> OllamaHttpResult:
        """Issue an async POST JSON request and return parsed JSON object."""
        ...


class HttpxOllamaTransport:
    """Default async Ollama HTTP transport backed by httpx.AsyncClient."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        """Initialize transport with an optional shared httpx.AsyncClient."""
        self._client = client

    async def get_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_s: float,
    ) -> OllamaHttpResult:
        """Perform a bounded non-redirecting JSON GET request."""
        return await self._request(
            "GET", url=url, headers=headers, json_body=None, timeout_s=timeout_s
        )

    async def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any],
        timeout_s: float,
    ) -> OllamaHttpResult:
        """Perform a bounded non-redirecting JSON POST request."""
        return await self._request(
            "POST", url=url, headers=headers, json_body=json_body, timeout_s=timeout_s
        )

    async def _request(
        self,
        method: Literal["GET", "POST"],
        *,
        url: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None,
        timeout_s: float,
    ) -> OllamaHttpResult:
        if self._client is not None:
            return await self._send(
                self._client,
                method=method,
                url=url,
                headers=headers,
                json_body=json_body,
                timeout_s=timeout_s,
            )
        async with httpx.AsyncClient(follow_redirects=False) as client:
            return await self._send(
                client,
                method=method,
                url=url,
                headers=headers,
                json_body=json_body,
                timeout_s=timeout_s,
            )

    async def _send(
        self,
        client: httpx.AsyncClient,
        *,
        method: Literal["GET", "POST"],
        url: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None,
        timeout_s: float,
    ) -> OllamaHttpResult:
        try:
            async with asyncio.timeout(timeout_s):
                if method == "GET":
                    response = await client.get(
                        url,
                        headers=dict(headers),
                        timeout=timeout_s,
                        follow_redirects=False,
                    )
                else:
                    response = await client.post(
                        url,
                        headers=dict(headers),
                        json=dict(json_body or {}),
                        timeout=timeout_s,
                        follow_redirects=False,
                    )
        except TimeoutError as exc:
            raise ProviderError("timeout", "Ollama request timed out", retryable=True) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", "Ollama HTTP client timed out", retryable=True) from exc
        except httpx.RequestError as exc:
            raise ProviderError(
                "network_error", "Ollama endpoint unreachable", retryable=True
            ) from exc

        content = response.content
        if len(content) > _MAX_OLLAMA_BYTES:
            raise ProviderError("response_too_large", "Ollama response exceeded 2 MiB cap")
        if response.status_code >= 500 or response.status_code in {408, 429}:
            raise ProviderError(
                f"http_{response.status_code}",
                f"Transient Ollama HTTP status {response.status_code}",
                retryable=True,
                status_code=response.status_code,
            )
        if response.status_code >= 400:
            raise ProviderError(
                f"http_{response.status_code}",
                f"Non-retryable Ollama HTTP status {response.status_code}",
                retryable=False,
                status_code=response.status_code,
            )
        try:
            parsed = json.loads(content)
        except ValueError as exc:
            raise ProviderError("invalid_json", "Ollama returned malformed JSON") from exc
        if not isinstance(parsed, dict):
            raise ProviderError("invalid_json", "Ollama JSON root must be an object")
        return OllamaHttpResult(status_code=response.status_code, payload=parsed)


class OllamaCloudProvider:
    """Explicitly remote Ollama Cloud adapter guarded as UNKNOWN_COST under MAX_SPEND=0."""

    kind = ProviderKind.OLLAMA
    is_remote = True
    env_var = "OLLAMA_API_KEY"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        endpoint_url: str | None = None,
        transport: AsyncOllamaTransport | None = None,
    ) -> None:
        """Configure Ollama Cloud HTTPS endpoint without assuming free-tier billing."""
        self._api_key = (
            api_key if api_key is not None else os.environ.get("OLLAMA_API_KEY", "")
        ).strip()
        raw_base = (
            endpoint_url
            or os.environ.get("OLLAMA_CLOUD_BASE_URL", "").strip()
            or "https://ollama.com"
        )
        base = validate_provider_url(raw_base, allow_local_http=False)
        self._endpoint_url = (
            base if base.endswith("/v1/chat/completions") else f"{base}/v1/chat/completions"
        )
        chosen = (model or os.environ.get("OLLAMA_CLOUD_MODEL", "") or "qwen3.5:0.8b").strip()
        if not _OLLAMA_MODEL_RE.fullmatch(chosen):
            raise ValueError("Invalid Ollama Cloud model identifier")
        self._model = chosen
        self._transport = transport or HttpxOllamaTransport()
        self._billing_tier, _ = classify_billing_tier(
            kind=self.kind, model=self._model, is_remote=self.is_remote
        )

    @property
    def model(self) -> str:
        """Return configured Ollama Cloud model identifier."""
        return self._model

    @property
    def billing_tier(self) -> BillingTier:
        """Return UNKNOWN_COST so BudgetGuard prevents unintended cloud charges at $0."""
        return self._billing_tier

    def is_configured(self) -> bool:
        """Return whether a non-placeholder OLLAMA_API_KEY is present."""
        return bool(self._api_key) and "placeholder" not in self._api_key.lower()

    async def complete(self, request: PromptRequest, *, timeout_s: float) -> str:
        """Invoke Ollama Cloud OpenAI-compatible chat endpoint when explicitly authorized."""
        if not self.is_configured():
            raise ProviderError("not_configured", "OLLAMA_API_KEY is not configured")
        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})
        res = await self._transport.post_json(
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
        return _extract_openai_text(res.payload)


def create_ollama_cloud_provider(
    *,
    api_key: str | None = None,
    model: str | None = None,
    endpoint_url: str | None = None,
    transport: AsyncOllamaTransport | None = None,
) -> OllamaCloudProvider:
    """Build an Ollama Cloud adapter strictly separated from $0 local Ollama."""
    return OllamaCloudProvider(
        api_key=api_key,
        model=model,
        endpoint_url=endpoint_url,
        transport=transport,
    )
