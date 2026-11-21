"""Live-connector probe: one real provider call, recorded as evidence.

The ``live-connector`` PAP-2027 gate needs proof that a provider answered **our**
prompt over the network. This module does the network part and nothing else:

* it refuses to spend: a provider whose billing tier is not ``FREE_TIER`` or
  ``LOCAL`` is never called (``MAX_SPEND = $0`` is enforced here, not promised);
* it records the exchange — endpoint host, request/response timestamps, latency,
  HTTP status, response headers, server-asserted model version, request id when
  the provider supplies one, token usage, response SHA-256 and a redacted excerpt;
* it never returns a successful-looking result for a simulated answer: the
  deterministic offline fallback raises instead of pretending to be live.

A mock success must never be able to produce this evidence, which is why the
marker check and the ``mock`` flag live next to the HTTP call.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast, runtime_checkable
from urllib.parse import urlsplit

import httpx
from kernel2027.redaction import redact as kernel_redact

from .ai_providers import HttpJsonResult, HttpxJsonTransport, ProviderError
from .contracts import BillingTier, PromptRequest, ProviderKind

MARKER = "PAP-2027-LIVE-OK"
PROBE_PROMPT = (
    "This is a connectivity check for an automated gate. Reply with exactly this token "
    f"and nothing else: {MARKER}"
)

_HEADER_WHITELIST = (
    "content-type",
    "date",
    "server",
    "x-request-id",
    "request-id",
    "x-goog-request-id",
    "x-goog-response-id",
    "x-envoy-upstream-service-time",
)
_REQUEST_ID_KEYS = ("responseId", "response_id", "requestId", "request_id", "id")
_SERVER_MODEL_KEYS = ("modelVersion", "model_version", "model")
_SECRET_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"nvapi-[0-9A-Za-z_-]{20,}"),
    re.compile(r"gsk_[0-9A-Za-z]{20,}"),
    re.compile(r"sk-or-v1-[0-9a-f]{16,}"),
    re.compile(r"sk-[0-9A-Za-z_-]{20,}"),
)
_SECRET_ENV_VARS = (
    "GEMINI_API_KEY",
    "NVIDIA_API_KEY",
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "OMNIROUTERS_API_KEY",
    "OLLAMA_API_KEY",
)

AttemptStatus = Literal["ok", "no_key", "refused_unknown_cost", "config_error", "error"]


@runtime_checkable
class ProbeableProvider(Protocol):
    """The slice of a provider the probe needs; the real providers satisfy it."""

    @property
    def kind(self) -> ProviderKind:
        """Return the provider kind."""
        ...

    @property
    def env_var(self) -> str:
        """Return the environment variable holding the credential."""
        ...

    @property
    def is_remote(self) -> bool:
        """Return whether the provider talks to a remote endpoint."""
        ...

    @property
    def model(self) -> str:
        """Return the configured model identifier."""
        ...

    @property
    def billing_tier(self) -> BillingTier:
        """Return the declared billing tier."""
        ...

    @property
    def billing_reason(self) -> str:
        """Return why that tier was assigned."""
        ...

    @property
    def credential_state(self) -> str:
        """Return a non-sensitive credential presence label."""
        ...

    def is_configured(self) -> bool:
        """Return whether a usable credential is present."""
        ...

    async def complete(self, request: PromptRequest, *, timeout_s: float) -> str:
        """Return the provider's completion text."""
        ...


@dataclass(frozen=True)
class ProbeEvidence:
    """One recorded exchange with a live provider endpoint."""

    provider: str
    model: str
    billing_tier: str
    billing_reason: str
    credential: str
    endpoint_host: str
    requested_at: str
    responded_at: str
    latency_ms: float
    http_status: int
    request_id: str | None
    request_id_source: str
    server_model_version: str | None
    usage: dict[str, int]
    response_sha256: str
    response_bytes: int
    response_excerpt: str
    marker_found: bool
    mock: bool
    cost_usd: float | None
    response_headers: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        """Return the evidence as plain JSON-ready data."""
        return {
            "provider": self.provider,
            "model": self.model,
            "billing_tier": self.billing_tier,
            "billing_reason": self.billing_reason,
            "credential": self.credential,
            "endpoint_host": self.endpoint_host,
            "requested_at": self.requested_at,
            "responded_at": self.responded_at,
            "latency_ms": self.latency_ms,
            "http_status": self.http_status,
            "request_id": self.request_id,
            "request_id_source": self.request_id_source,
            "server_model_version": self.server_model_version,
            "usage": dict(self.usage),
            "response_sha256": self.response_sha256,
            "response_bytes": self.response_bytes,
            "response_excerpt": self.response_excerpt,
            "marker_found": self.marker_found,
            "mock": self.mock,
            "cost_usd": self.cost_usd,
            "response_headers": dict(self.response_headers),
        }


@dataclass(frozen=True)
class ProbeAttempt:
    """The outcome for one candidate provider, successful or not."""

    provider: str
    model: str
    status: AttemptStatus
    reason: str = ""
    evidence: ProbeEvidence | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the attempt as plain JSON-ready data."""
        return {
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "reason": self.reason,
            "evidence": self.evidence.as_dict() if self.evidence else None,
        }


@dataclass
class RecordingTransport:
    """Delegating transport that records the real exchange without changing policy.

    Request construction, timeout handling, status-code mapping and the 1 MiB cap
    all stay in :class:`HttpxJsonTransport`; this class only observes. Response
    headers are captured through an httpx response hook on the shared client.
    """

    status_code: int = 0
    payload: dict[str, Any] = field(default_factory=dict)
    endpoint_host: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    url: str = ""
    client: httpx.AsyncClient | None = None

    def __post_init__(self) -> None:
        """Create (or wrap) the shared client with a header-capturing response hook."""
        if self.client is not None:
            self._client = self.client
            self._client.event_hooks.setdefault("response", []).append(self._on_response)
        else:
            self._client = httpx.AsyncClient(
                follow_redirects=False, event_hooks={"response": [self._on_response]}
            )
        self._inner = HttpxJsonTransport(client=self._client)

    async def _on_response(self, response: httpx.Response) -> None:
        self.headers = {
            key.lower(): value
            for key, value in response.headers.items()
            if key.lower() in _HEADER_WHITELIST
        }

    async def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any],
        timeout_s: float,
    ) -> HttpJsonResult:
        """Forward to the production transport, recording the normalized result."""
        self.url = url
        self.endpoint_host = urlsplit(url).hostname or ""
        result = await self._inner.post_json(
            url, headers=headers, json_body=json_body, timeout_s=timeout_s
        )
        self.status_code = result.status_code
        self.payload = dict(result.payload)
        return result

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()


def redact(text: str, secrets: list[str] | None = None) -> str:
    """Remove anything that looks like a provider credential from ``text``.

    Environment-derived secrets are collected here (this module owns that
    knowledge) and the scrubbing itself is the kernel's, so the pattern list
    cannot drift from the rest of the repository.
    """
    return cast(str, kernel_redact(text, [*_env_secrets(), *(secrets or [])]))


def _env_secrets() -> list[str]:
    values = [os.environ.get(name, "").strip() for name in _SECRET_ENV_VARS]
    return [value for value in values if value]


def _excerpt(text: str, limit: int = 200) -> str:
    flat = " ".join(redact(text).split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


def _lookup(payload: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int):
            return str(value)
    return None


def _usage(payload: Mapping[str, Any]) -> dict[str, int]:
    usage: dict[str, int] = {}
    for key in ("usageMetadata", "usage"):
        block = payload.get(key)
        if isinstance(block, Mapping):
            for alias, canonical in (
                ("promptTokenCount", "input_tokens"),
                ("prompt_tokens", "input_tokens"),
                ("candidatesTokenCount", "output_tokens"),
                ("completion_tokens", "output_tokens"),
                ("totalTokenCount", "total_tokens"),
                ("total_tokens", "total_tokens"),
            ):
                value = block.get(alias)
                if isinstance(value, int):
                    usage.setdefault(canonical, value)
    return usage


def _contains_marker(text: str, marker: str) -> bool:
    """Return True when the answer carries the marker, ignoring punctuation/spacing.

    ``PAP-2027-LIVE-OK``, `PAP 2027 LIVE OK` and ``**PAP-2027-LIVE-OK**`` all pass:
    the gate asks for a token, not for a formatting convention.
    """

    def squash(value: str) -> str:
        return re.sub(r"[^A-Z0-9]", "", value.upper())

    return squash(marker) in squash(text)


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def probe(
    provider_name: str,
    factory: Callable[[Any], ProbeableProvider],
    *,
    marker: str = MARKER,
    timeout_s: float = 45.0,
    prompt: str = PROBE_PROMPT,
    client: httpx.AsyncClient | None = None,
) -> ProbeAttempt:
    """Probe one provider: refuse to spend, call it for real, record what happened."""
    recorder = RecordingTransport(client=client)
    try:
        try:
            provider = factory(recorder)
        except ValueError as exc:
            return ProbeAttempt(
                provider=provider_name, model="", status="config_error", reason=str(exc)
            )

        model = provider.model
        if not provider.is_configured():
            return ProbeAttempt(
                provider=provider_name,
                model=model,
                status="no_key",
                reason=f"{provider.env_var} is absent or a placeholder",
            )
        tier = provider.billing_tier
        if tier not in (BillingTier.FREE_TIER, BillingTier.LOCAL):
            return ProbeAttempt(
                provider=provider_name,
                model=model,
                status="refused_unknown_cost",
                reason=f"{tier.value}: {provider.billing_reason}",
            )

        requested_at = _now()
        started = time.perf_counter()
        try:
            # 512 output tokens, not 32: thinking models spend part of the budget on
            # internal reasoning, and an empty completion would look like an outage.
            text = await provider.complete(
                PromptRequest(prompt=prompt, temperature=0.0, max_tokens=512),
                timeout_s=timeout_s,
            )
        except ProviderError as exc:
            return ProbeAttempt(
                provider=provider_name,
                model=model,
                status="error",
                reason=redact(f"{exc.code}: {exc}"),
            )
        except (httpx.HTTPError, OSError) as exc:
            return ProbeAttempt(
                provider=provider_name,
                model=model,
                status="error",
                reason=redact(f"transport_error: {type(exc).__name__}: {exc}"),
            )
        latency_ms = round((time.perf_counter() - started) * 1000.0, 1)
        responded_at = _now()

        payload = recorder.payload
        request_id = _lookup(payload, _REQUEST_ID_KEYS)
        source = "payload" if request_id else "absent"
        if not request_id:
            request_id = recorder.headers.get("x-request-id") or recorder.headers.get(
                "x-goog-request-id"
            )
            source = "response-header" if request_id else "absent"

        evidence = ProbeEvidence(
            provider=provider_name,
            model=model,
            billing_tier=tier.value,
            billing_reason=provider.billing_reason,
            credential=provider.credential_state,
            endpoint_host=recorder.endpoint_host,
            requested_at=requested_at,
            responded_at=responded_at,
            latency_ms=latency_ms,
            http_status=recorder.status_code,
            request_id=request_id,
            request_id_source=source,
            server_model_version=_lookup(payload, _SERVER_MODEL_KEYS),
            usage=_usage(payload),
            response_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            response_bytes=len(text.encode("utf-8")),
            response_excerpt=_excerpt(text),
            marker_found=_contains_marker(text, marker),
            mock=provider.kind.value == "deterministic" or not provider.is_remote,
            cost_usd=0.0 if tier in (BillingTier.FREE_TIER, BillingTier.LOCAL) else None,
            response_headers=dict(recorder.headers),
        )
        return ProbeAttempt(provider=provider_name, model=model, status="ok", evidence=evidence)
    finally:
        await recorder.aclose()


async def probe_many(
    candidates: Mapping[str, Callable[[Any], ProbeableProvider]],
    *,
    marker: str = MARKER,
    timeout_s: float = 45.0,
) -> list[ProbeAttempt]:
    """Probe each candidate in order and return every attempt, including refusals."""
    attempts: list[ProbeAttempt] = []
    for name, factory in candidates.items():
        attempts.append(await probe(name, factory, marker=marker, timeout_s=timeout_s))
    return attempts
