"""Model-catalogue guarantees: no retired ID is advertised as a free default."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx
import pytest

from ai2027_protocol.ai_providers import (
    DeterministicFallbackProvider,
    HttpJsonResult,
    HttpxJsonTransport,
    OpenAICompatibleFreeProvider,
    ProviderError,
    build_default_providers,
    create_groq_free_provider,
    create_nvidia_nim_provider,
    create_openrouter_free_provider,
)
from ai2027_protocol.budget import (
    FREE_TIER_MODELS,
    BudgetGuard,
    BudgetPolicy,
    classify_billing_tier,
)
from ai2027_protocol.cli import main as cli_main
from ai2027_protocol.contracts import BillingTier, PromptRequest, ProviderKind
from ai2027_protocol.demo import verify_live_providers
from ai2027_protocol.model_catalog import (
    DEFAULT_MODELS,
    OPENROUTER_FREE_ROUTER,
    RETIRED_MODELS,
    find_retired_model,
    is_openrouter_free_model,
    normalize_model_id,
)

_MODEL_ENV_VARS = (
    "GEMINI_MODEL",
    "NVIDIA_MODEL",
    "GROQ_MODEL",
    "OPENROUTER_MODEL",
    "AI2027_FREE_MODELS",
)


@pytest.fixture(autouse=True)
def _clear_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _MODEL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


class _ExplodingTransport:
    """Transport that fails the test if a request is attempted."""

    async def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any],
        timeout_s: float,
    ) -> HttpJsonResult:
        raise AssertionError(f"no outbound request expected, got {url}")


def test_openrouter_free_router_is_classified_free_tier() -> None:
    provider = create_openrouter_free_provider(api_key="sk-or-fixture")
    assert provider.model == OPENROUTER_FREE_ROUTER
    assert provider.billing_tier == BillingTier.FREE_TIER
    tier, reason = classify_billing_tier(
        kind=ProviderKind.OPENROUTER,
        model=OPENROUTER_FREE_ROUTER,
        is_remote=True,
    )
    assert tier == BillingTier.FREE_TIER
    assert "free routing" in reason
    explicit_tier, _ = classify_billing_tier(
        kind=ProviderKind.OPENROUTER,
        model=OPENROUTER_FREE_ROUTER,
        is_remote=True,
        explicit_tier=BillingTier.FREE_TIER,
    )
    assert explicit_tier == BillingTier.FREE_TIER
    assert is_openrouter_free_model("meta-llama/llama-3.3-70b-instruct:free") is True
    assert is_openrouter_free_model("openai/gpt-4o") is False


def test_openrouter_paid_model_is_blocked_before_any_request() -> None:
    provider = OpenAICompatibleFreeProvider(
        kind=ProviderKind.OPENROUTER,
        endpoint_url="https://openrouter.ai/api/v1/chat/completions",
        api_key="sk-or-fixture",
        model="openai/gpt-4o",
        transport=_ExplodingTransport(),
    )
    assert provider.billing_tier == BillingTier.UNKNOWN_COST
    decision = BudgetGuard(BudgetPolicy(max_spend_usd=0.0)).evaluate_provider(provider)
    assert decision.allowed is False


@pytest.mark.parametrize(
    ("kind", "model", "retired_on", "replacement"),
    [
        (ProviderKind.GROQ, "llama-3.1-8b-instant", "2026-08-16", "openai/gpt-oss-20b"),
        (ProviderKind.GROQ, "llama-3.3-70b-versatile", "2026-08-16", "openai/gpt-oss-120b"),
        (
            ProviderKind.NVIDIA,
            "meta/llama-3.1-8b-instruct",
            "2026-08-26",
            "z-ai/glm-5.3-flash",
        ),
        (
            ProviderKind.NVIDIA,
            "meta/llama-3.3-70b-instruct",
            "2026-08-26",
            "z-ai/glm-5.3-flash",
        ),
    ],
)
def test_retired_ids_are_never_classified_free(
    kind: ProviderKind, model: str, retired_on: str, replacement: str
) -> None:
    tier, reason = classify_billing_tier(kind=kind, model=model, is_remote=True)
    assert tier == BillingTier.UNKNOWN_COST
    assert "retired" in reason
    assert retired_on in reason
    assert replacement in reason

    # An explicit FREE_TIER claim must not override a provider retirement either.
    explicit_tier, _ = classify_billing_tier(
        kind=kind,
        model=model,
        is_remote=True,
        explicit_tier=BillingTier.FREE_TIER,
    )
    assert explicit_tier == BillingTier.UNKNOWN_COST


def test_free_tier_allowlist_contains_no_retired_ids() -> None:
    for entry in RETIRED_MODELS:
        assert entry.model not in FREE_TIER_MODELS.get(entry.kind, frozenset())
    # Groq's set was empty while llama was retired from the free plan; the
    # published gpt-oss ids restored it. The invariant is "no retired id is
    # ever declared free", not "some provider's set is empty".
    assert "openai/gpt-oss-120b" in FREE_TIER_MODELS[ProviderKind.GROQ]
    assert find_retired_model(ProviderKind.GEMINI, "gemini-2.5-flash") is None


def test_defaults_follow_the_catalogue(monkeypatch: pytest.MonkeyPatch) -> None:
    providers = {p.kind: p for p in build_default_providers()}
    assert providers[ProviderKind.NVIDIA].model == "z-ai/glm-5.3-flash"
    assert providers[ProviderKind.GROQ].model == DEFAULT_MODELS[ProviderKind.GROQ]
    assert providers[ProviderKind.OPENROUTER].model == OPENROUTER_FREE_ROUTER
    for provider in providers.values():
        assert find_retired_model(provider.kind, provider.model) is None

    monkeypatch.setenv("NVIDIA_MODEL", "z-ai/glm-5.3-flash")
    assert create_nvidia_nim_provider().model == "z-ai/glm-5.3-flash"


def test_groq_free_models_are_the_published_ones() -> None:
    """Groq's free plan covers gpt-oss; llama left it.

    The earlier test asserted that *no* Groq model was free, which was true
    when Llama was retired from the free plan. Groq publishes 30 requests/min,
    1,000/day and 200,000 tokens/day on gpt-oss-120b and -20b, so the default
    is now allowlisted — 1,000 requests a day is 20x OpenRouter's free 50.
    """
    allowed = create_groq_free_provider(api_key="gsk-fixture")
    assert allowed.model == "openai/gpt-oss-20b"
    decision = BudgetGuard(BudgetPolicy(max_spend_usd=0.0)).evaluate_provider(allowed)
    assert decision.allowed is True
    assert decision.cost_status.value == "declared_free_tier_unbilled"


def test_a_groq_model_outside_the_published_set_is_still_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The allowlist must stay a list: an unknown Groq id is not assumed free."""
    # Not retired, simply not on the published free list.
    blocked = create_groq_free_provider(api_key="gsk-fixture", model="groq/compound")
    decision = BudgetGuard(BudgetPolicy(max_spend_usd=0.0)).evaluate_provider(blocked)
    assert decision.allowed is False
    assert "AI2027_FREE_MODELS" in decision.reason


async def test_retired_provider_fails_before_network() -> None:
    provider = OpenAICompatibleFreeProvider(
        kind=ProviderKind.NVIDIA,
        endpoint_url="https://integrate.api.nvidia.com/v1/chat/completions",
        api_key="nvapi-fixture",
        model="meta/llama-3.1-8b-instruct",
        transport=_ExplodingTransport(),
    )
    assert provider.retirement_reason is not None
    assert "meta/llama-3.1-8b-instruct" in provider.retirement_reason
    with pytest.raises(ProviderError) as excinfo:
        await provider.complete(PromptRequest(prompt="ping"), timeout_s=1.0)
    assert excinfo.value.code == "model_retired"


async def test_http_410_is_reported_as_model_retired() -> None:
    def _gone(request: httpx.Request) -> httpx.Response:
        return httpx.Response(410, json={"error": "gone"})

    transport = HttpxJsonTransport(client=httpx.AsyncClient(transport=httpx.MockTransport(_gone)))
    with pytest.raises(ProviderError) as excinfo:
        await transport.post_json(
            "https://example.test/v1/chat/completions",
            headers={},
            json_body={"model": "m"},
            timeout_s=2.0,
        )
    assert excinfo.value.code == "model_retired"
    assert excinfo.value.retryable is False


async def test_live_probe_reports_model_retired_without_calling() -> None:
    retired = OpenAICompatibleFreeProvider(
        kind=ProviderKind.GROQ,
        endpoint_url="https://api.groq.com/openai/v1/chat/completions",
        api_key="gsk-fixture",
        model="llama-3.3-70b-versatile",
        transport=_ExplodingTransport(),
    )
    report = await verify_live_providers(
        providers=[retired, DeterministicFallbackProvider()],
        target="groq",
    )
    probe = report["probes"][0]
    assert probe["status"] == "model_retired"
    assert "openai/gpt-oss-120b" in probe["error"]
    assert probe["latency_ms"] == 0.0


def test_cli_models_command_lists_defaults_and_retirements(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli_main(["models", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    defaults = {row["provider"]: row["model"] for row in payload["defaults"]}
    assert defaults["openrouter"] == OPENROUTER_FREE_ROUTER
    assert defaults["nvidia"] == "z-ai/glm-5.3-flash"
    retired_models = {row["model"] for row in payload["retired"]}
    assert "llama-3.3-70b-versatile" in retired_models
    assert "meta/llama-3.1-8b-instruct" in retired_models

    assert cli_main(["models"]) == 0
    text = capsys.readouterr().out
    assert "openrouter/free" in text
    assert "retired 2026-08-16" in text


def test_no_retirement_points_at_another_retired_model() -> None:
    """The registry must not send a caller from one dead model to another.

    meta/llama-3.1-8b-instruct recorded meta/llama-3.3-70b-instruct as its
    replacement, and 3.3 was retired on the same day. Every default that
    followed the registry therefore landed on a dead endpoint, which is how
    three files came to ship a model NVIDIA answers with HTTP 410.
    """
    retired = {(entry.kind, normalize_model_id(entry.model)) for entry in RETIRED_MODELS}
    for entry in RETIRED_MODELS:
        key = (entry.kind, normalize_model_id(entry.replacement))
        assert key not in retired, (
            f"{entry.model} is replaced by {entry.replacement}, which is itself retired"
        )
