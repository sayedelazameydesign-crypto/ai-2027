"""Live probe: one real provider call recorded as evidence, with $0 enforced first."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from ai2027_protocol.ai_providers import ProviderError
from ai2027_protocol.contracts import BillingTier, PromptRequest
from ai2027_protocol.live_probe import MARKER, RecordingTransport, probe, redact

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
)


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


def _gemini_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"x-request-id": "req-header-1", "content-type": "application/json"},
        json={
            "responseId": "resp_abc123",
            "modelVersion": "gemini-2.5-flash-001",
            "candidates": [{"content": {"parts": [{"text": "PAP-2027-LIVE-OK"}]}}],
            "usageMetadata": {
                "promptTokenCount": 31,
                "candidatesTokenCount": 5,
                "totalTokenCount": 36,
            },
        },
    )


class _FakeProvider:
    """Minimal provider double; prints what a provider must expose to the probe."""

    env_var = "FAKE_API_KEY"
    is_remote = True

    def __init__(self, *, tier: BillingTier, configured: bool, text: str = MARKER) -> None:
        self.kind = _Kind("gemini")
        self.billing_tier = tier
        self.billing_reason = f"test tier {tier.value}"
        self.credential_state = "configured" if configured else "absent"
        self._configured = configured
        self._text = text
        self.calls = 0
        self.transport: Any = None

    @property
    def model(self) -> str:
        return "fake-model-1"

    def is_configured(self) -> bool:
        return self._configured

    async def complete(self, request: PromptRequest, *, timeout_s: float) -> str:
        self.calls += 1
        raise AssertionError("the probe must not call an unverified provider")


class _Kind:
    def __init__(self, value: str) -> None:
        self.value = value


def _factory(provider: _FakeProvider) -> Any:
    return lambda transport: provider


async def test_probe_refuses_to_call_a_provider_that_is_not_proven_free() -> None:
    provider = _FakeProvider(tier=BillingTier.UNKNOWN_COST, configured=True)

    attempt = await probe("fake", _factory(provider))

    assert attempt.status == "refused_unknown_cost"
    assert "unknown_cost" in attempt.reason
    assert provider.calls == 0
    assert attempt.evidence is None


async def test_probe_reports_a_missing_key_without_calling() -> None:
    provider = _FakeProvider(tier=BillingTier.FREE_TIER, configured=False)

    attempt = await probe("fake", _factory(provider))

    assert attempt.status == "no_key"
    assert "FAKE_API_KEY" in attempt.reason
    assert provider.calls == 0


async def test_probe_records_a_real_exchange_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "AIza" + "x" * 33)
    from ai2027_protocol.ai_providers import GeminiFreeProvider

    attempt = await probe(
        "gemini",
        lambda transport: GeminiFreeProvider(transport=transport),
        client=_client(_gemini_ok),
    )

    assert attempt.status == "ok", attempt.reason
    evidence = attempt.evidence
    assert evidence is not None
    assert evidence.http_status == 200
    assert evidence.endpoint_host == "generativelanguage.googleapis.com"
    assert evidence.marker_found is True
    assert evidence.mock is False
    assert evidence.cost_usd == 0.0
    assert evidence.billing_tier == BillingTier.FREE_TIER.value
    assert evidence.request_id == "resp_abc123"
    assert evidence.request_id_source == "payload"
    assert evidence.server_model_version == "gemini-2.5-flash-001"
    assert evidence.usage == {"input_tokens": 31, "output_tokens": 5, "total_tokens": 36}
    assert evidence.response_sha256 == __import__("hashlib").sha256(MARKER.encode()).hexdigest()
    assert evidence.response_headers["x-request-id"] == "req-header-1"
    assert evidence.latency_ms >= 0
    assert "T" in evidence.responded_at


async def test_probe_falls_back_to_the_response_header_for_an_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "AIza" + "y" * 33)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"x-request-id": "req-from-header"},
            json={"candidates": [{"content": {"parts": [{"text": "PAP-2027-LIVE-OK"}]}}]},
        )

    from ai2027_protocol.ai_providers import GeminiFreeProvider

    attempt = await probe(
        "gemini",
        lambda transport: GeminiFreeProvider(transport=transport),
        client=_client(handler),
    )

    assert attempt.status == "ok"
    assert attempt.evidence is not None
    assert attempt.evidence.request_id == "req-from-header"
    assert attempt.evidence.request_id_source == "response-header"


async def test_probe_reports_a_retired_model_as_an_error_not_a_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "AIza" + "z" * 33)
    from ai2027_protocol.ai_providers import GeminiFreeProvider

    attempt = await probe(
        "gemini",
        lambda transport: GeminiFreeProvider(transport=transport),
        client=_client(lambda request: httpx.Response(410, json={"error": "gone"})),
    )

    assert attempt.status == "error"
    assert "model_retired" in attempt.reason
    assert attempt.evidence is None


async def test_probe_reports_a_server_error_without_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "AIza" + "q" * 33)
    from ai2027_protocol.ai_providers import GeminiFreeProvider

    attempt = await probe(
        "gemini",
        lambda transport: GeminiFreeProvider(transport=transport),
        client=_client(lambda request: httpx.Response(503, json={"error": "busy"})),
    )

    assert attempt.status == "error"
    assert "http_503" in attempt.reason


async def test_a_provider_that_echoes_the_key_is_redacted_in_the_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "AIza" + "s" * 33
    monkeypatch.setenv("GEMINI_API_KEY", key)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": f"key={key} PAP-2027-LIVE-OK"}]}}]
            },
        )

    from ai2027_protocol.ai_providers import GeminiFreeProvider

    attempt = await probe(
        "gemini",
        lambda transport: GeminiFreeProvider(transport=transport),
        client=_client(handler),
    )

    assert attempt.status == "ok"
    assert attempt.evidence is not None
    # Marker unified by kernel-2027: this module used to emit lowercase
    # "[redacted]" while security.py emitted "[REDACTED]" for the same secret, so
    # an alerting rule could match one path and miss the other. One marker now.
    assert "[REDACTED]" in attempt.evidence.response_excerpt
    assert key not in json.dumps(attempt.as_dict())


@pytest.mark.parametrize(
    "answer",
    [
        "PAP-2027-LIVE-OK",
        "PAP 2027 LIVE OK",
        "**PAP-2027-LIVE-OK**",
        "Sure, here it is: `PAP-2027-LIVE-OK`.",
        "pap-2027-live-ok",
    ],
)
async def test_the_marker_is_formatting_agnostic(
    monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "AIza" + "m" * 33)
    from ai2027_protocol.ai_providers import GeminiFreeProvider

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": answer}]}}]},
        )

    attempt = await probe(
        "gemini",
        lambda transport: GeminiFreeProvider(transport=transport),
        client=_client(handler),
    )

    assert attempt.status == "ok"
    assert attempt.evidence is not None
    assert attempt.evidence.marker_found is True


async def test_an_unrelated_answer_does_not_count_as_the_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "AIza" + "n" * 33)
    from ai2027_protocol.ai_providers import GeminiFreeProvider

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "I cannot help with that."}]}}]},
        )

    attempt = await probe(
        "gemini",
        lambda transport: GeminiFreeProvider(transport=transport),
        client=_client(handler),
    )

    assert attempt.status == "ok"
    assert attempt.evidence is not None
    assert attempt.evidence.marker_found is False


def test_redact_hides_known_credential_shapes() -> None:
    # Built from parts on purpose: no credential-shaped literal stays in the tree.
    fake_nvapi = "nvapi-" + "a" * 24
    fake_groq = "gsk_" + "b" * 24
    fake_openrouter = "sk-or-v1-" + "c" * 24

    cleaned = redact(f"{fake_nvapi} {fake_groq} {fake_openrouter}")

    assert "nvapi-" not in cleaned
    assert "gsk_" not in cleaned
    assert "sk-or-v1-" not in cleaned


async def test_recording_transport_keeps_production_error_policy() -> None:
    transport = RecordingTransport(client=_client(lambda request: httpx.Response(500, json={})))

    with pytest.raises(ProviderError) as excinfo:
        await transport.post_json(GEMINI_URL, headers={}, json_body={}, timeout_s=5.0)

    assert excinfo.value.code == "http_500"
    assert excinfo.value.retryable is True
    assert transport.status_code == 0  # nothing was recorded as a success
    await transport.aclose()
