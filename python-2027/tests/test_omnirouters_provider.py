"""OmniRouters provider guarantees: fail-closed by default, and no bypass.

OmniRouters is an OpenAI-compatible provider whose accounts receive no free
credit by default and whose catalogue is per-account. These tests pin the
behaviour that keeps it unusable until an operator deliberately configures a
model and approves spend:

* it is classified ``UNKNOWN_COST``, never ``FREE_TIER``;
* ``MAX_SPEND=0`` blocks it before any request is attempted;
* a missing ``OMNIROUTERS_MODEL`` prevents construction entirely;
* neither the public constructor nor the free-model allowlist can reclassify
  it, so the guarantee holds for every caller and not only for the factory;
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
    DeterministicFallbackProvider,
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
from ai2027_protocol.router import AllProvidersFailedError, FreeFirstRouter

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
    assert "not free-guaranteed" in reason


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


def test_the_general_classifier_refuses_to_reclassify_an_omnirouters_provider() -> None:
    """Classification itself refuses an asserted tier for this provider.

    ``classify_billing_tier`` honours an explicit tier for most kinds, so
    before the central fix a caller could assert ``FREE_TIER`` for OmniRouters
    and the guard would admit it at ``MAX_SPEND=0``. The provider is now on the
    non-reclassifiable list, so the classifier refuses outright — the guarantee
    no longer depends on which constructor the caller happened to use.
    """
    explicit_tier, reason = classify_billing_tier(
        kind=ProviderKind.OMNIROUTERS,
        model="account/model-v1",
        is_remote=True,
        explicit_tier=BillingTier.FREE_TIER,
    )
    assert explicit_tier == BillingTier.UNKNOWN_COST
    assert "cannot be asserted by the caller" in reason

    # The provider the factory returns is never classified that way either.
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
    assert "not free-guaranteed" in reason


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


def test_the_ceiling_is_an_authorisation_not_a_financial_cap() -> None:
    """The dollar ceiling still cannot bind — so a daily count binds instead.

    An ``UNKNOWN_COST`` decision carries no ``estimated_cost_usd``, so
    ``record_completed`` never advances ``spent_usd``: ``MAX_SPEND_USD`` must
    never be described as a spend cap for an unknown-cost provider. That
    caveat used to mean *unbounded* admission under the override; the budget
    now pairs the override with ``unknown_cost_daily_call_limit`` — a count
    the system can actually measure. This test pins both halves: dollars stay
    at zero, and admission stops at the daily limit.
    """
    guard = BudgetGuard(BudgetPolicy(max_spend_usd=10.0, allow_unknown_cost=True))
    provider = _provider()

    admitted = 0
    for _ in range(100):
        decision = guard.evaluate_provider(provider)
        guard.record_completed(decision)
        if not decision.allowed:
            assert "daily call limit" in decision.reason
            assert decision.cost_status.value == "unknown_cost_blocked"
            break
        admitted += 1
        assert decision.estimated_cost_usd is None
    else:
        raise AssertionError("the daily unknown-cost limit never engaged")

    assert admitted == guard.policy.unknown_cost_daily_call_limit
    snapshot = guard.snapshot()
    assert snapshot.spent_usd == 0.0
    assert snapshot.max_spend_usd == 10.0
    assert snapshot.unknown_cost_admitted_today == admitted


async def test_no_network_attempt_occurs_for_a_blocked_provider() -> None:
    """The budget must block the call BEFORE any request leaves the process.

    This has to go through ``FreeFirstRouter``: ``provider.complete()`` does
    not consult ``BudgetGuard`` at all, so calling the provider directly would
    attempt the request and prove nothing about the guard. The router evaluates
    the budget first, records ``budget_blocked``, and never reaches the
    transport. The counting transport is what makes "no request was sent"
    observable rather than merely asserted.
    """
    transport = _CountingTransport()
    provider = create_omnirouters_provider(
        api_key="omni-fixture-key",
        model="account/model-v1",
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

    # The decisive assertion: the transport was never invoked.
    assert transport.call_count == 0


async def test_a_blocked_provider_makes_no_request_even_alongside_others() -> None:
    """A blocked OmniRouters entry must not send a request even when a
    deterministic fallback is available and succeeds."""
    transport = _CountingTransport()
    provider = create_omnirouters_provider(
        api_key="omni-fixture-key",
        model="account/model-v1",
        transport=transport,
    )
    router = FreeFirstRouter(
        providers=[provider, DeterministicFallbackProvider()],
        budget_policy=BudgetPolicy(max_spend_usd=0.0),
    )

    result = await router.route(PromptRequest(prompt="pong"))

    assert transport.call_count == 0
    blocked = [a for a in result.attempts if a.provider == ProviderKind.OMNIROUTERS]
    assert blocked and blocked[0].error_code == "budget_blocked"


def test_the_general_constructor_cannot_assert_a_free_or_local_tier() -> None:
    """The bypass is closed at the policy layer, not only at the factory.

    ``OpenAICompatibleFreeProvider`` is public and accepts ``billing_tier``.
    Before this was enforced centrally, a caller could construct an OmniRouters
    provider directly with ``FREE_TIER`` — or ``LOCAL``, which would report a
    remote call as proven-zero-cost — and the guard would admit it at
    ``MAX_SPEND=0``. Both must now be refused by classification itself.
    """
    for tier in (BillingTier.FREE_TIER, BillingTier.LOCAL, BillingTier.PAID):
        provider = OpenAICompatibleFreeProvider(
            kind=ProviderKind.OMNIROUTERS,
            endpoint_url="https://omnirouters.com/v1/chat/completions",
            api_key="omni-fixture-key",
            model="account/model-v1",
            env_var="OMNIROUTERS_API_KEY",
            billing_tier=tier,
        )
        assert provider.billing_tier == BillingTier.UNKNOWN_COST

        decision = BudgetGuard(BudgetPolicy(max_spend_usd=0.0)).evaluate_provider(provider)
        assert decision.allowed is False
        assert decision.cost_status.value == "unknown_cost_blocked"


def test_the_free_model_allowlist_cannot_reclassify_the_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``AI2027_FREE_MODELS`` is a second route to ``FREE_TIER``; it is refused."""
    monkeypatch.setenv("AI2027_FREE_MODELS", "account/model-v1")

    tier, reason = classify_billing_tier(
        kind=ProviderKind.OMNIROUTERS,
        model="account/model-v1",
        is_remote=True,
    )
    assert tier == BillingTier.UNKNOWN_COST
    assert "AI2027_FREE_MODELS cannot reclassify" in reason

    provider = _provider()
    assert provider.billing_tier == BillingTier.UNKNOWN_COST
    decision = BudgetGuard(BudgetPolicy(max_spend_usd=0.0)).evaluate_provider(provider)
    assert decision.allowed is False


def test_other_providers_keep_their_explicit_tier_and_allowlist_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The central fix must not weaken the providers that are allowed to assert."""
    monkeypatch.setenv("AI2027_FREE_MODELS", "account/model-v1")

    allowed, _ = classify_billing_tier(
        kind=ProviderKind.GEMINI,
        model="account/model-v1",
        is_remote=True,
    )
    assert allowed == BillingTier.FREE_TIER

    explicit, _ = classify_billing_tier(
        kind=ProviderKind.OPENROUTER,
        model="openrouter/free",
        is_remote=True,
        explicit_tier=BillingTier.FREE_TIER,
    )
    assert explicit == BillingTier.FREE_TIER

    local, _ = classify_billing_tier(
        kind=ProviderKind.OLLAMA,
        model="qwen3.5:0.8b",
        is_remote=False,
    )
    assert local == BillingTier.LOCAL


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
