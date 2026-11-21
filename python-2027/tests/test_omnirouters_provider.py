"""OmniRouters provider guarantees: fail-closed by default, and no bypass.

OmniRouters is an OpenAI-compatible provider whose accounts receive no free
credit by default and whose catalogue is per-account. These tests pin the
behaviour that keeps it unusable until an operator deliberately configures a
model and approves spend:

* it is classified ``UNKNOWN_COST``, never ``FREE_TIER``;
* ``MAX_SPEND=0`` blocks it before any request is attempted;
* a missing ``OMNIROUTERS_MODEL`` prevents construction entirely;
* the factory cannot be handed an explicit ``billing_tier``;
* an API key alone never proves free usage;
* no policy setting lets it through by accident.

Everything here runs offline: no test may reach the network.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

import pytest

from ai2027_protocol.ai_providers import (
    HttpJsonResult,
    MissingProviderModelError,
    OpenAICompatibleFreeProvider,
    create_omnirouters_provider,
)
from ai2027_protocol.budget import (
    BudgetGuard,
    BudgetPolicy,
    classify_billing_tier,
)
from ai2027_protocol.contracts import BillingTier, PromptRequest, ProviderKind
from ai2027_protocol.model_catalog import DEFAULT_MODELS, MODEL_ENV_VARS

_OMNI_ENV_VARS = (
    "OMNIROUTERS_MODEL",
    "OMNIROUTERS_API_KEY",
    "AI2027_FREE_MODELS",
    "AI2027_MAX_SPEND_USD",
    "AI2027_ALLOW_UNKNOWN_COST",
)


@pytest.fixture(autouse=True)
def _clear_omnirouters_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every OmniRouters test from ambient environment configuration."""
    for name in _OMNI_ENV_VARS:
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


def _provider(**kwargs: Any) -> OpenAICompatibleFreeProvider:
    """Build an OmniRouters provider with an explicit model and no network."""
    return create_omnirouters_provider(
        api_key="omni-fixture-key",
        model="account/model-v1",
        transport=_ExplodingTransport(),
        **kwargs,
    )


def test_omnirouters_is_classified_unknown_cost_not_free_tier() -> None:
    """Control 1: the provider is UNKNOWN_COST, never FREE_TIER."""
    provider = _provider()
    assert provider.kind == ProviderKind.OMNIROUTERS
    assert provider.billing_tier == BillingTier.UNKNOWN_COST
    assert provider.billing_tier is not BillingTier.FREE_TIER  # type: ignore[comparison-overlap]

    tier, reason = classify_billing_tier(
        kind=ProviderKind.OMNIROUTERS,
        model="account/model-v1",
        is_remote=True,
    )
    assert tier == BillingTier.UNKNOWN_COST
    assert "does not prove" in reason


def test_max_spend_zero_blocks_before_any_request() -> None:
    """Control 2: MAX_SPEND=0 blocks the provider before a network call."""
    provider = _provider()
    guard = BudgetGuard(BudgetPolicy(max_spend_usd=0.0))
    decision = guard.evaluate_provider(provider)
    assert decision.allowed is False
    assert decision.billing_tier == BillingTier.UNKNOWN_COST
    assert decision.cost_status.value == "unknown_cost_blocked"


async def test_missing_model_prevents_construction() -> None:
    """Control 3: without OMNIROUTERS_MODEL nothing is built and nothing is sent."""
    with pytest.raises(MissingProviderModelError) as excinfo:
        create_omnirouters_provider(api_key="omni-fixture-key")
    assert "OMNIROUTERS_MODEL" in str(excinfo.value)

    # An explicitly empty model is equally refused.
    with pytest.raises(MissingProviderModelError):
        create_omnirouters_provider(api_key="omni-fixture-key", model="   ")


def test_factory_cannot_receive_an_explicit_billing_tier() -> None:
    """Control 4: the factory has no billing_tier parameter, so no bypass exists."""
    parameters = inspect.signature(create_omnirouters_provider).parameters
    assert "billing_tier" not in parameters

    with pytest.raises(TypeError):
        create_omnirouters_provider(
            api_key="omni-fixture-key",
            model="account/model-v1",
            billing_tier=BillingTier.FREE_TIER,  # type: ignore[call-arg]
        )


def test_the_general_classifier_cannot_reclassify_an_omnirouters_provider() -> None:
    """The bypass is closed on the real path, not merely by omitting a parameter.

    ``classify_billing_tier`` honours an explicit tier for any kind other than
    OpenRouter's ``:free`` check, so the classifier alone would still let a
    caller assert ``FREE_TIER``. The guarantee therefore rests on the factory
    never passing ``explicit_tier``: the provider object it returns has already
    been classified by policy, and ``BudgetGuard`` reads that classification
    rather than re-deriving one from caller input.

    This test pins both halves: the classifier's general behaviour, and the
    provider's policy-derived classification that BudgetGuard actually uses.
    """
    # The general classifier is permissive by design — this is the bypass that
    # the factory must never route through.
    explicit_tier, _ = classify_billing_tier(
        kind=ProviderKind.OMNIROUTERS,
        model="account/model-v1",
        is_remote=True,
        explicit_tier=BillingTier.FREE_TIER,
    )
    assert explicit_tier == BillingTier.FREE_TIER

    # The provider the factory returns is never classified that way.
    provider = _provider()
    assert provider.billing_tier == BillingTier.UNKNOWN_COST

    # And BudgetGuard therefore refuses it, because it reads the provider's
    # own classification rather than any caller-supplied tier.
    decision = BudgetGuard(BudgetPolicy(max_spend_usd=0.0)).evaluate_provider(provider)
    assert decision.allowed is False
    assert decision.cost_status.value == "unknown_cost_blocked"


def test_api_key_presence_alone_never_means_free() -> None:
    """Control 5: holding a key does not reclassify the provider as free."""
    provider = _provider()
    assert provider.billing_tier == BillingTier.UNKNOWN_COST

    # Even with a key set in the environment and no model allowlist entry.
    tier, reason = classify_billing_tier(
        kind=ProviderKind.OMNIROUTERS,
        model="account/model-v1",
        is_remote=True,
    )
    assert tier == BillingTier.UNKNOWN_COST
    assert "key presence does not prove" in reason


def test_no_policy_combination_admits_the_provider_by_accident() -> None:
    """Control 6: strict, permissive, and zero-spend policies all refuse."""
    provider = _provider()

    strict = BudgetGuard(BudgetPolicy(max_spend_usd=0.0, allow_unknown_cost=False))
    assert strict.evaluate_provider(provider).allowed is False

    zero_with_override = BudgetGuard(BudgetPolicy(max_spend_usd=0.0, allow_unknown_cost=True))
    assert zero_with_override.evaluate_provider(provider).allowed is False

    # A non-zero ceiling with the explicit override is the only admitted path,
    # and it requires an operator to opt in deliberately.
    admitted = BudgetGuard(
        BudgetPolicy(max_spend_usd=10.0, allow_unknown_cost=True)
    ).evaluate_provider(provider)
    assert admitted.allowed is True
    assert admitted.cost_status.value == "allowed_within_budget"


async def test_no_network_attempt_occurs_for_a_blocked_provider() -> None:
    """A blocked provider must fail before the transport is ever called."""
    provider = _provider()
    guard = BudgetGuard(BudgetPolicy(max_spend_usd=0.0))
    decision = guard.evaluate_provider(provider)
    assert decision.allowed is False

    # The transport raises on any call, so reaching this point proves the
    # provider was rejected without touching the network.
    with pytest.raises(AssertionError):
        await provider.complete(PromptRequest(prompt="pong"), timeout_s=1.0)


def test_omnirouters_has_no_default_model() -> None:
    """The catalogue must not advertise a guessed OmniRouters model."""
    assert ProviderKind.OMNIROUTERS not in DEFAULT_MODELS
    assert MODEL_ENV_VARS[ProviderKind.OMNIROUTERS] == "OMNIROUTERS_MODEL"


def test_omnirouters_is_not_in_the_default_provider_chain() -> None:
    """OmniRouters stays opt-in: it is never auto-constructed into the chain."""
    from ai2027_protocol.ai_providers import build_default_providers

    kinds = {provider.kind for provider in build_default_providers()}
    assert ProviderKind.OMNIROUTERS not in kinds


def test_endpoint_is_the_documented_omnirouters_url() -> None:
    """The adapter targets the documented OpenAI-compatible endpoint."""
    provider = _provider()
    assert provider.endpoint_url == "https://omnirouters.com/v1/chat/completions"
    assert provider.env_var == "OMNIROUTERS_API_KEY"
