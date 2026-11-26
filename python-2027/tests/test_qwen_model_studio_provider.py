"""Qwen (Alibaba Cloud Model Studio) provider guarantees: fail-closed by default.

Model Studio serves a free quota and then charges, its model ids rotate, and
the endpoint is built from an account workspace id. These tests pin the
behaviour that keeps the provider unusable until an operator deliberately
configures workspace + model and approves spend:

* it is classified ``UNKNOWN_COST``, never ``FREE_TIER``, and is on the
  non-reclassifiable list alongside OmniRouters;
* ``MAX_SPEND=0`` blocks it before any request is attempted;
* a missing ``QWEN_MODEL`` or ``DASHSCOPE_WORKSPACE_ID`` prevents
  construction entirely;
* neither the factory, the general constructor, nor ``AI2027_FREE_MODELS``
  can reclassify it;
* the endpoint matches the documented
  ``https://{WorkspaceId}.{region}.maas.aliyuncs.com/compatible-mode/v1``
  shape, with hostname-safe workspace/region parts only;
* it stays OUT of the default provider chain (opt-in, like OmniRouters).

Everything here runs offline: no test may reach the network.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

import pytest

from ai2027_protocol.ai_providers import (
    DeterministicFallbackProvider,
    HttpJsonResult,
    MissingProviderConfigError,
    MissingProviderModelError,
    OpenAICompatibleFreeProvider,
    build_default_providers,
    create_qwen_model_studio_provider,
)
from ai2027_protocol.budget import (
    NON_RECLASSIFIABLE_PROVIDERS,
    BudgetGuard,
    BudgetPolicy,
    classify_billing_tier,
)
from ai2027_protocol.contracts import BillingTier, PromptRequest, ProviderKind
from ai2027_protocol.model_catalog import DEFAULT_MODELS, MODEL_ENV_VARS
from ai2027_protocol.router import AllProvidersFailedError, FreeFirstRouter
from ai2027_protocol.routing_profiles import SUPPORTED_PROVIDER_KINDS

_QWEN_ENV_VARS = (
    "QWEN_MODEL",
    "DASHSCOPE_API_KEY",
    "DASHSCOPE_WORKSPACE_ID",
    "DASHSCOPE_REGION",
    "AI2027_FREE_MODELS",
    "AI2027_MAX_SPEND_USD",
    "AI2027_ALLOW_UNKNOWN_COST",
)

_EXPECTED_ENDPOINT = (
    "https://ws-fixture-001.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/chat/completions"
)


@pytest.fixture(autouse=True)
def _clear_qwen_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every Qwen test from ambient environment configuration."""
    for name in _QWEN_ENV_VARS:
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


class _CountingTransport:
    """Transport that records every call, so "no request was sent" is observable."""

    def __init__(self) -> None:
        self.call_count = 0
        self.urls: list[str] = []

    async def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any],
        timeout_s: float,
    ) -> HttpJsonResult:
        self.call_count += 1
        self.urls.append(url)
        raise AssertionError(f"no outbound request expected, got {url}")


def _provider(**kwargs: Any) -> OpenAICompatibleFreeProvider:
    """Build a Qwen provider with explicit workspace/model and no network."""
    return create_qwen_model_studio_provider(
        api_key="dashscope-fixture-key",
        workspace_id="ws-fixture-001",
        model="qwen-test-model",
        transport=_ExplodingTransport(),
        **kwargs,
    )


def test_qwen_is_classified_unknown_cost_not_free_tier() -> None:
    """Control 1: the provider is UNKNOWN_COST, never FREE_TIER."""
    provider = _provider()
    assert provider.kind == ProviderKind.QWEN
    assert provider.billing_tier == BillingTier.UNKNOWN_COST
    assert provider.billing_tier is not BillingTier.FREE_TIER  # type: ignore[comparison-overlap]

    tier, reason = classify_billing_tier(
        kind=ProviderKind.QWEN,
        model="qwen-test-model",
        is_remote=True,
    )
    assert tier == BillingTier.UNKNOWN_COST
    assert "not free-guaranteed" in reason


def test_qwen_is_on_the_non_reclassifiable_list() -> None:
    """The policy layer refuses reclassification for Qwen by construction."""
    assert ProviderKind.QWEN in NON_RECLASSIFIABLE_PROVIDERS
    assert ProviderKind.OMNIROUTERS in NON_RECLASSIFIABLE_PROVIDERS


def test_max_spend_zero_blocks_before_any_request() -> None:
    """Control 2: MAX_SPEND=0 blocks the provider before a network call."""
    provider = _provider()
    guard = BudgetGuard(BudgetPolicy(max_spend_usd=0.0))
    decision = guard.evaluate_provider(provider)
    assert decision.allowed is False
    assert decision.billing_tier == BillingTier.UNKNOWN_COST
    assert decision.cost_status.value == "unknown_cost_blocked"


def test_missing_model_prevents_construction() -> None:
    """Control 3: without QWEN_MODEL nothing is built and nothing is sent."""
    with pytest.raises(MissingProviderConfigError) as excinfo:
        create_qwen_model_studio_provider(
            api_key="dashscope-fixture-key",
            workspace_id="ws-fixture-001",
        )
    assert "QWEN_MODEL" in str(excinfo.value)
    # The config error stays compatible with the existing model-error type.
    assert issubclass(MissingProviderConfigError, MissingProviderModelError)

    with pytest.raises(MissingProviderConfigError):
        create_qwen_model_studio_provider(
            api_key="dashscope-fixture-key",
            workspace_id="ws-fixture-001",
            model="   ",
        )


def test_missing_workspace_prevents_construction() -> None:
    """The documented URL cannot be built without a workspace id."""
    with pytest.raises(MissingProviderConfigError) as excinfo:
        create_qwen_model_studio_provider(
            api_key="dashscope-fixture-key",
            model="qwen-test-model",
        )
    assert "DASHSCOPE_WORKSPACE_ID" in str(excinfo.value)


def test_factory_cannot_receive_an_explicit_billing_tier() -> None:
    """Control 4: the factory has no billing_tier parameter, so no bypass exists."""
    parameters = inspect.signature(create_qwen_model_studio_provider).parameters
    assert "billing_tier" not in parameters

    with pytest.raises(TypeError):
        create_qwen_model_studio_provider(  # type: ignore[call-arg]
            api_key="dashscope-fixture-key",
            workspace_id="ws-fixture-001",
            model="qwen-test-model",
            billing_tier=BillingTier.FREE_TIER,
        )


def test_the_general_classifier_refuses_to_reclassify_qwen() -> None:
    """An asserted tier is refused, exactly like OmniRouters."""
    explicit_tier, reason = classify_billing_tier(
        kind=ProviderKind.QWEN,
        model="qwen-test-model",
        is_remote=True,
        explicit_tier=BillingTier.FREE_TIER,
    )
    assert explicit_tier == BillingTier.UNKNOWN_COST
    assert "cannot be asserted by the caller" in reason

    provider = _provider()
    assert provider.billing_tier == BillingTier.UNKNOWN_COST
    decision = BudgetGuard(BudgetPolicy(max_spend_usd=0.0)).evaluate_provider(provider)
    assert decision.allowed is False
    assert decision.cost_status.value == "unknown_cost_blocked"


def test_api_key_presence_alone_never_means_free() -> None:
    """Control 5: holding a key does not reclassify the provider as free."""
    provider = _provider()
    assert provider.env_var == "DASHSCOPE_API_KEY"
    assert provider.is_configured() is True

    tier, _reason = classify_billing_tier(
        kind=ProviderKind.QWEN,
        model="qwen-test-model",
        is_remote=True,
    )
    assert tier == BillingTier.UNKNOWN_COST

    unconfigured = create_qwen_model_studio_provider(
        workspace_id="ws-fixture-001",
        model="qwen-test-model",
        transport=_ExplodingTransport(),
    )
    assert unconfigured.is_configured() is False


def test_no_policy_combination_admits_the_provider_by_accident() -> None:
    """Control 6: strict, permissive, and zero-spend policies all refuse."""
    provider = _provider()

    strict = BudgetGuard(BudgetPolicy(max_spend_usd=0.0, allow_unknown_cost=False))
    assert strict.evaluate_provider(provider).allowed is False

    zero_with_override = BudgetGuard(BudgetPolicy(max_spend_usd=0.0, allow_unknown_cost=True))
    assert zero_with_override.evaluate_provider(provider).allowed is False

    admitted = BudgetGuard(
        BudgetPolicy(max_spend_usd=10.0, allow_unknown_cost=True)
    ).evaluate_provider(provider)
    assert admitted.allowed is True
    assert admitted.cost_status.value == "allowed_within_budget"


async def test_no_network_attempt_occurs_for_a_blocked_provider() -> None:
    """The budget must block the call BEFORE any request leaves the process."""
    transport = _CountingTransport()
    provider = create_qwen_model_studio_provider(
        api_key="dashscope-fixture-key",
        workspace_id="ws-fixture-001",
        model="qwen-test-model",
        transport=transport,
    )
    router = FreeFirstRouter(
        providers=[provider],
        budget_policy=BudgetPolicy(max_spend_usd=0.0),
    )

    with pytest.raises(AllProvidersFailedError) as excinfo:
        await router.route(PromptRequest(prompt="pong"))

    attempt = excinfo.value.attempts[0]
    assert attempt.error_code == "budget_blocked"
    assert attempt.billing_tier == BillingTier.UNKNOWN_COST
    assert attempt.cost_status.value == "unknown_cost_blocked"
    assert transport.call_count == 0


async def test_a_blocked_provider_makes_no_request_even_alongside_others() -> None:
    """A blocked Qwen entry sends nothing even when fallback can answer."""
    transport = _CountingTransport()
    provider = create_qwen_model_studio_provider(
        api_key="dashscope-fixture-key",
        workspace_id="ws-fixture-001",
        model="qwen-test-model",
        transport=transport,
    )
    router = FreeFirstRouter(
        providers=[provider, DeterministicFallbackProvider()],
        budget_policy=BudgetPolicy(max_spend_usd=0.0),
    )

    result = await router.route(PromptRequest(prompt="pong"))

    assert transport.call_count == 0
    blocked = [a for a in result.attempts if a.provider == ProviderKind.QWEN]
    assert blocked and blocked[0].error_code == "budget_blocked"


def test_the_general_constructor_cannot_assert_a_free_or_local_tier() -> None:
    """Both dangerous assertions (FREE_TIER and LOCAL) are refused centrally."""
    for tier in (BillingTier.FREE_TIER, BillingTier.LOCAL, BillingTier.PAID):
        provider = OpenAICompatibleFreeProvider(
            kind=ProviderKind.QWEN,
            endpoint_url=_EXPECTED_ENDPOINT,
            api_key="dashscope-fixture-key",
            model="qwen-test-model",
            env_var="DASHSCOPE_API_KEY",
            billing_tier=tier,
        )
        assert provider.billing_tier == BillingTier.UNKNOWN_COST

        decision = BudgetGuard(BudgetPolicy(max_spend_usd=0.0)).evaluate_provider(provider)
        assert decision.allowed is False
        assert decision.cost_status.value == "unknown_cost_blocked"


def test_the_free_model_allowlist_cannot_reclassify_the_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``AI2027_FREE_MODELS`` is a second route to FREE_TIER; it is refused."""
    monkeypatch.setenv("AI2027_FREE_MODELS", "qwen-test-model")

    tier, reason = classify_billing_tier(
        kind=ProviderKind.QWEN,
        model="qwen-test-model",
        is_remote=True,
    )
    assert tier == BillingTier.UNKNOWN_COST
    assert "AI2027_FREE_MODELS cannot reclassify" in reason

    provider = _provider()
    assert provider.billing_tier == BillingTier.UNKNOWN_COST
    decision = BudgetGuard(BudgetPolicy(max_spend_usd=0.0)).evaluate_provider(provider)
    assert decision.allowed is False


def test_qwen_has_no_default_model() -> None:
    """The catalogue must not advertise a guessed Model Studio model id."""
    assert ProviderKind.QWEN not in DEFAULT_MODELS
    assert MODEL_ENV_VARS[ProviderKind.QWEN] == "QWEN_MODEL"


def test_qwen_is_not_in_the_default_provider_chain() -> None:
    """Qwen stays opt-in: it is never auto-constructed into the chain."""
    kinds = {provider.kind for provider in build_default_providers()}
    assert ProviderKind.QWEN not in kinds
    assert ProviderKind.OMNIROUTERS not in kinds


def test_qwen_kind_is_synced_with_routing_profiles() -> None:
    """``preferred_providers`` accepts exactly the kinds the enum defines."""
    assert ProviderKind.QWEN.value in SUPPORTED_PROVIDER_KINDS
    assert SUPPORTED_PROVIDER_KINDS == frozenset(kind.value for kind in ProviderKind)


def test_endpoint_matches_the_documented_model_studio_shape() -> None:
    """The adapter targets the documented OpenAI-compatible endpoint."""
    provider = _provider()
    assert provider.endpoint_url == _EXPECTED_ENDPOINT
    assert provider.env_var == "DASHSCOPE_API_KEY"
    assert provider.model == "qwen-test-model"


def test_region_and_workspace_come_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env fallbacks build the URL exactly as the official docs describe."""
    monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "ws-env-42")
    monkeypatch.setenv("DASHSCOPE_REGION", "cn-beijing")
    monkeypatch.setenv("QWEN_MODEL", "qwen-env-model")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "env-fixture-key")

    provider = create_qwen_model_studio_provider(transport=_ExplodingTransport())
    assert provider.endpoint_url == (
        "https://ws-env-42.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions"
    )
    assert provider.model == "qwen-env-model"


def test_region_defaults_to_ap_southeast_1() -> None:
    """The region the official docs lead with is the documented default."""
    provider = create_qwen_model_studio_provider(
        api_key="dashscope-fixture-key",
        workspace_id="ws-fixture-001",
        model="qwen-test-model",
        transport=_ExplodingTransport(),
    )
    assert ".ap-southeast-1.maas.aliyuncs.com" in provider.endpoint_url


def test_hostname_unsafe_workspace_or_region_is_refused() -> None:
    """Workspace and region are interpolated into a hostname: validate both."""
    with pytest.raises(ValueError, match="hostname-safe"):
        create_qwen_model_studio_provider(
            api_key="dashscope-fixture-key",
            workspace_id="evil.example.com/",
            model="qwen-test-model",
            transport=_ExplodingTransport(),
        )
    with pytest.raises(ValueError, match="hostname-safe"):
        create_qwen_model_studio_provider(
            api_key="dashscope-fixture-key",
            workspace_id="ws-fixture-001",
            region="AP-SOUTHEAST-1/..",
            model="qwen-test-model",
            transport=_ExplodingTransport(),
        )
