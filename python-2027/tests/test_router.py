from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest

from ai2027_protocol.ai_providers import (
    DeterministicFallbackProvider,
    GeminiFreeProvider,
    HttpJsonResult,
    HttpxJsonTransport,
    OllamaLocalProvider,
    OpenAICompatibleFreeProvider,
    ProviderError,
    build_default_providers,
    create_groq_free_provider,
    create_nvidia_nim_provider,
    create_openrouter_free_provider,
)
from ai2027_protocol.budget import BudgetGuard, BudgetPolicy
from ai2027_protocol.contracts import BillingTier, PromptRequest, ProviderKind
from ai2027_protocol.router import AllProvidersFailedError, FreeFirstRouter, RouterConfig
from ai2027_protocol.telemetry import ObservabilityRuntime

_Responder = Callable[[str, Mapping[str, str], Mapping[str, Any], float], Awaitable[HttpJsonResult]]


class _FakeTransport:
    def __init__(self, responder: _Responder) -> None:
        self._responder = responder
        self.calls: list[tuple[str, Mapping[str, str], Mapping[str, Any]]] = []

    async def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any],
        timeout_s: float,
    ) -> HttpJsonResult:
        self.calls.append((url, headers, json_body))
        return await self._responder(url, headers, json_body, timeout_s)


class _ScriptedProvider:
    def __init__(
        self,
        kind: ProviderKind,
        model: str,
        outcomes: list[str | Exception],
        *,
        remote: bool = True,
        configured: bool = True,
        billing_tier: BillingTier | None = None,
    ) -> None:
        self.kind = kind
        self.model = model
        self._outcomes = list(outcomes)
        self.is_remote = remote
        self._configured = configured
        self.billing_tier = (
            billing_tier
            if billing_tier is not None
            else (BillingTier.LOCAL if not remote else BillingTier.FREE_TIER)
        )
        self.calls = 0

    def is_configured(self) -> bool:
        return self._configured

    async def complete(self, request: PromptRequest, *, timeout_s: float) -> str:
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


async def test_providers_send_expected_headers_and_parse_responses() -> None:
    async def responder(
        url: str, headers: Mapping[str, str], body: Mapping[str, Any], timeout_s: float
    ) -> HttpJsonResult:
        parsed = urlparse(url)
        if parsed.port == 11434:
            return HttpJsonResult(200, {"message": {"content": "ollama reply"}})
        if parsed.hostname == "generativelanguage.googleapis.com":
            assert not parsed.query
            assert headers["x-goog-api-key"] == "AIza-test-fixture"
            return HttpJsonResult(
                200, {"candidates": [{"content": {"parts": [{"text": "gemini reply"}]}}]}
            )
        return HttpJsonResult(200, {"choices": [{"message": {"content": "openai reply"}}]})

    transport = _FakeTransport(responder)
    req = PromptRequest(prompt="Say hi", system_prompt="Be concise")

    ollama = OllamaLocalProvider(base_url="http://127.0.0.1:11434", transport=transport)
    assert await ollama.complete(req, timeout_s=2.0) == "ollama reply"

    gemini = GeminiFreeProvider(api_key="AIza-test-fixture", transport=transport)
    assert await gemini.complete(req, timeout_s=2.0) == "gemini reply"

    groq = create_groq_free_provider(api_key="gsk-test-fixture", transport=transport)
    assert groq.kind == ProviderKind.GROQ
    assert await groq.complete(req, timeout_s=2.0) == "openai reply"

    nvidia = create_nvidia_nim_provider(api_key="nvapi-test-fixture", transport=transport)
    assert nvidia.kind == ProviderKind.NVIDIA
    assert await nvidia.complete(req, timeout_s=2.0) == "openai reply"

    openrouter = create_openrouter_free_provider(api_key="sk-or-test-fixture", transport=transport)
    assert openrouter.kind == ProviderKind.OPENROUTER
    assert openrouter.model == "openrouter/free"
    assert openrouter.billing_tier == BillingTier.FREE_TIER
    or_decision = BudgetGuard(BudgetPolicy(max_spend_usd=0.0)).evaluate_provider(openrouter)
    assert or_decision.allowed is True
    assert or_decision.billing_tier == BillingTier.FREE_TIER
    assert await openrouter.complete(req, timeout_s=2.0) == "openai reply"
    or_headers = transport.calls[-1][1]
    assert or_headers["HTTP-Referer"] == "https://github.com/sayedelazameydesign-crypto/ai-2027"
    assert or_headers["X-Title"] == "AI-2027 Protocol"

    defaults = build_default_providers(transport=transport)
    assert defaults[-1].kind == ProviderKind.DETERMINISTIC
    assert defaults[-1].is_configured() is True


async def test_httpx_transport_error_mapping() -> None:
    observed_agents: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed_agents.append(request.headers.get("User-Agent", ""))
        if "rate-limit" in str(request.url):
            return httpx.Response(429, json={"error": "rate limit"})
        if "bad-auth" in str(request.url):
            return httpx.Response(401, json={"error": "unauthorized"})
        if "bad-json" in str(request.url):
            return httpx.Response(200, content=b"{bad")
        if "list-json" in str(request.url):
            return httpx.Response(200, json=[1, 2, 3])
        if "huge" in str(request.url):
            return httpx.Response(200, content=b"x" * (1024 * 1024 + 5))
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = HttpxJsonTransport(client=client)
        res = await transport.post_json(
            "https://example.test/ok", headers={}, json_body={}, timeout_s=2.0
        )
        assert res.payload == {"ok": True}
        assert observed_agents[0] == "ai2027-protocol/1.0"

        await transport.post_json(
            "https://example.test/ok",
            headers={"User-Agent": "custom-agent/2.0"},
            json_body={},
            timeout_s=2.0,
        )
        assert observed_agents[1] == "custom-agent/2.0"

        with pytest.raises(ProviderError) as rate_err:
            await transport.post_json(
                "https://example.test/rate-limit", headers={}, json_body={}, timeout_s=2.0
            )
        assert rate_err.value.retryable is True

        with pytest.raises(ProviderError) as auth_err:
            await transport.post_json(
                "https://example.test/bad-auth", headers={}, json_body={}, timeout_s=2.0
            )
        assert auth_err.value.retryable is False

        for bad_path in ("bad-json", "list-json", "huge"):
            with pytest.raises(ProviderError):
                await transport.post_json(
                    f"https://example.test/{bad_path}", headers={}, json_body={}, timeout_s=2.0
                )


async def test_router_retries_transient_then_falls_back_to_backup() -> None:
    primary = _ScriptedProvider(
        ProviderKind.GEMINI,
        "gemini-2.5-flash",
        [
            ProviderError("http_429", "rate limited", retryable=True, status_code=429),
            ProviderError("http_503", "still busy", retryable=True, status_code=503),
        ],
    )
    backup = _ScriptedProvider(
        ProviderKind.GROQ,
        "llama-3.3-70b-versatile",
        ["backup answer"],
    )
    router = FreeFirstRouter(
        providers=[primary, backup, DeterministicFallbackProvider()],
        config=RouterConfig(max_retries_per_provider=1, base_backoff_s=0.0),
        telemetry=ObservabilityRuntime(),
    )
    resp = await router.route(PromptRequest(prompt="Test fallback"))
    assert resp.provider == ProviderKind.GROQ
    assert resp.text == "backup answer"
    assert resp.cost_usd == 0.0
    assert primary.calls == 2
    assert len(resp.attempts) == 3
    assert resp.attempts[0].ok is False
    assert resp.attempts[2].ok is True


async def test_router_circuit_breaker_and_allow_remote_and_preferred_order() -> None:
    now = [100.0]
    remote_broken = _ScriptedProvider(
        ProviderKind.GEMINI,
        "g1",
        [ProviderError("http_401", "bad auth", retryable=False)],
        remote=True,
    )
    local_ok = _ScriptedProvider(
        ProviderKind.OLLAMA,
        "qwen2.5:3b",
        ["local answer", "second local", "third local"],
        remote=False,
    )
    router = FreeFirstRouter(
        providers=[remote_broken, local_ok],
        config=RouterConfig(
            circuit_breaker_threshold=1,
            circuit_cooldown_s=30.0,
            base_backoff_s=0.0,
        ),
        clock=lambda: now[0],
    )
    r1 = await router.route(PromptRequest(prompt="first"))
    assert r1.provider == ProviderKind.OLLAMA
    assert remote_broken.calls == 1

    r2 = await router.route(PromptRequest(prompt="second"))
    assert r2.provider == ProviderKind.OLLAMA
    assert remote_broken.calls == 1
    assert any(a.error_code == "circuit_open" for a in r2.attempts)

    r3 = await router.route(
        PromptRequest(
            prompt="offline only",
            allow_remote=False,
            preferred_providers=(ProviderKind.GEMINI, ProviderKind.OLLAMA),
        )
    )
    assert r3.provider == ProviderKind.OLLAMA


async def test_router_timeout_and_all_providers_failed() -> None:
    class _SlowProvider:
        kind = ProviderKind.GROQ
        model = "llama-3.3-70b-versatile"
        is_remote = True
        billing_tier = BillingTier.FREE_TIER

        def is_configured(self) -> bool:
            return True

        async def complete(self, request: PromptRequest, *, timeout_s: float) -> str:
            await asyncio.sleep(0.2)
            return "too late"

    router = FreeFirstRouter(
        providers=[_SlowProvider()],
        config=RouterConfig(per_provider_timeout_s=0.02, max_retries_per_provider=0),
    )
    with pytest.raises(AllProvidersFailedError) as exc_info:
        await router.route(PromptRequest(prompt="timeout check"))
    assert exc_info.value.attempts[0].error_code == "timeout"


async def test_router_batch_taskgroup_preserves_order() -> None:
    router = FreeFirstRouter(providers=[DeterministicFallbackProvider()])
    assert await router.route_batch([]) == []
    reqs = [PromptRequest(prompt=f"item-{idx}") for idx in range(5)]
    results = await router.route_batch(reqs, max_concurrency=3)
    assert len(results) == 5
    for idx, res in enumerate(results):
        assert f"item-{idx}" in res.text
        assert res.simulated is True


async def test_provider_payload_errors() -> None:
    async def _bad_responder(
        _url: str, _headers: Mapping[str, str], _body: Mapping[str, Any], _timeout: float
    ) -> HttpJsonResult:
        return HttpJsonResult(200, {"unexpected": True})

    bad_transport = _FakeTransport(_bad_responder)
    req = PromptRequest(prompt="hello")
    with pytest.raises(ProviderError):
        await OllamaLocalProvider(
            base_url="http://127.0.0.1:11434", transport=bad_transport
        ).complete(req, timeout_s=1.0)
    with pytest.raises(ProviderError):
        await GeminiFreeProvider(api_key="realish-test-val", transport=bad_transport).complete(
            req, timeout_s=1.0
        )
    with pytest.raises(ProviderError):
        await OpenAICompatibleFreeProvider(
            kind=ProviderKind.GROQ,
            endpoint_url="https://api.groq.com/openai/v1/chat/completions",
            api_key="realish-test-val",
            model="m1",
            transport=bad_transport,
        ).complete(req, timeout_s=1.0)
    with pytest.raises(ProviderError):
        await OllamaLocalProvider(base_url="").complete(req, timeout_s=1.0)
    with pytest.raises(ProviderError):
        await GeminiFreeProvider(api_key="").complete(req, timeout_s=1.0)
    assert json.loads(json.dumps({"ok": True})) == {"ok": True}
