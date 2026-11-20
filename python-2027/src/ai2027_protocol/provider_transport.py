"""Bounded async HTTP transport and provider response contracts."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx

from .security import redact_secrets

_MAX_RESPONSE_BYTES = 1024 * 1024


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
        merged_headers = {"User-Agent": "ai2027-protocol/1.0", **dict(headers)}
        try:
            async with asyncio.timeout(timeout_s):
                response = await client.post(
                    url,
                    headers=merged_headers,
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
        if response.status_code == 410:
            raise ProviderError(
                "model_retired",
                "Provider answered HTTP 410 Gone: this model endpoint was withdrawn. "
                "Update the provider's *_MODEL variable to an ID that is still served "
                "(see docs/MODEL-CATALOG.md).",
                retryable=False,
                status_code=410,
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
    if isinstance(message, dict):
        for key in ("content", "reasoning_content", "reasoning"):
            text = message.get(key)
            if isinstance(text, str) and text.strip():
                return text.strip()
    raise ProviderError("invalid_payload", "Completion text missing in provider response")
