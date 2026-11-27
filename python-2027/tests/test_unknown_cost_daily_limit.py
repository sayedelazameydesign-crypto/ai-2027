"""A measurable daily cap for unknown-cost calls (the P0 spend-gate review).

``MAX_SPEND_USD`` cannot bind ``UNKNOWN_COST`` calls: they carry no
``estimated_cost_usd``, so ``spent_usd`` never advances. Pairing the dollar
override with ``unknown_cost_daily_call_limit`` gives the guard a count it
can actually enforce. These tests pin:

* the default limit is a small positive number (unset is not unbounded);
* env parsing: explicit override honoured, garbage fails closed to 0;
* admission stops at the limit with a reason naming the cap;
* limit 0 refuses unknown-cost calls even under the override;
* at ``MAX_SPEND=0`` nothing changes (the spend reason still applies);
* free-tier and local providers never consume the counter;
* readiness evaluations (no ``record_completed``) never burn the limit;
* the counter rolls over at the UTC day boundary.

Everything here runs offline: no test may reach the network.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest

from ai2027_protocol.ai_providers import (
    DeterministicFallbackProvider,
    HttpJsonResult,
    OpenAICompatibleFreeProvider,
    create_qwen_model_studio_provider,
)
from ai2027_protocol.budget import (
    BudgetGuard,
    BudgetPolicy,
)
from ai2027_protocol.contracts import BillingTier, ProviderKind

_ENV_VARS = (
    "QWEN_MODEL",
    "DASHSCOPE_API_KEY",
    "DASHSCOPE_WORKSPACE_ID",
    "DASHSCOPE_REGION",
    "AI2027_FREE_MODELS",
    "AI2027_MAX_SPEND_USD",
    "AI2027_ALLOW_UNKNOWN_COST",
    "AI2027_UNKNOWN_COST_DAILY_LIMIT",
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from ambient environment configuration."""
    for name in _ENV_VARS:
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


def _qwen_provider() -> OpenAICompatibleFreeProvider:
    """A configured unknown-cost provider (Qwen) with no network."""
    return create_qwen_model_studio_provider(
        api_key="dashscope-fixture-key",
        workspace_id="ws-fixture-001",
        model="qwen-test-model",
        transport=_ExplodingTransport(),
    )


def _open_guard(limit: int | None = None, *, max_spend: float = 10.0) -> BudgetGuard:
    kwargs: dict[str, Any] = {}
    if limit is not None:
        kwargs["unknown_cost_daily_call_limit"] = limit
    policy = BudgetPolicy(max_spend_usd=max_spend, allow_unknown_cost=True, **kwargs)
    return BudgetGuard(policy)


def test_default_policy_is_bounded_not_unbounded() -> None:
    """Unset configuration still carries a small positive daily limit."""
    policy = BudgetPolicy()
    assert policy.unknown_cost_daily_call_limit == 5
    assert policy.unknown_cost_daily_call_limit > 0

    from_env = BudgetPolicy.from_env()
    assert from_env.unknown_cost_daily_call_limit == 5


def test_env_override_and_garbage_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit limit is honoured; a malformed one refuses instead of guessing."""
    monkeypatch.setenv("AI2027_UNKNOWN_COST_DAILY_LIMIT", "3")
    assert BudgetPolicy.from_env().unknown_cost_daily_call_limit == 3

    monkeypatch.setenv("AI2027_UNKNOWN_COST_DAILY_LIMIT", "not-a-number")
    assert BudgetPolicy.from_env().unknown_cost_daily_call_limit == 0

    monkeypatch.setenv("AI2027_UNKNOWN_COST_DAILY_LIMIT", "-7")
    assert BudgetPolicy.from_env().unknown_cost_daily_call_limit == 0


def test_admission_stops_at_the_daily_limit() -> None:
    """Completed unknown-cost calls consume a measurable count, then refuse."""
    guard = _open_guard(limit=3)
    provider = _qwen_provider()

    admitted = 0
    for _ in range(10):
        decision = guard.evaluate_provider(provider)
        if not decision.allowed:
            break
        guard.record_completed(decision)
        admitted += 1

    assert admitted == 3
    refusal = guard.evaluate_provider(provider)
    assert refusal.allowed is False
    assert refusal.cost_status.value == "unknown_cost_blocked"
    assert "daily call limit" in refusal.reason
    assert refusal.estimated_cost_usd is None

    snapshot = guard.snapshot()
    assert snapshot.unknown_cost_admitted_today == 3
    assert snapshot.unknown_cost_daily_call_limit == 3
    assert snapshot.spent_usd == 0.0  # dollars remain unmeasured, by design


def test_zero_limit_refuses_even_with_the_override() -> None:
    """A limit of 0 is an explicit 'never' — the override alone admits nothing."""
    guard = _open_guard(limit=0)
    decision = guard.evaluate_provider(_qwen_provider())
    assert decision.allowed is False
    assert "daily call limit" in decision.reason


def test_zero_spend_policy_is_unchanged() -> None:
    """At MAX_SPEND=0 the spend reason still applies; the limit never matters."""
    guard = BudgetGuard(BudgetPolicy(max_spend_usd=0.0, allow_unknown_cost=False))
    decision = guard.evaluate_provider(_qwen_provider())
    assert decision.allowed is False
    assert "daily call limit" not in decision.reason
    assert decision.cost_status.value == "unknown_cost_blocked"


def test_free_tier_and_local_never_consume_the_counter() -> None:
    """The counter is for unknown-cost admissions only."""
    guard = _open_guard(limit=2)

    deterministic = DeterministicFallbackProvider()
    free_tier = OpenAICompatibleFreeProvider(
        kind=ProviderKind.GROQ,
        endpoint_url="https://groq.example.invalid/v1/chat/completions",
        api_key="groq-fixture-key",
        model="openai/gpt-oss-20b",
        env_var="GROQ_API_KEY",
        transport=_ExplodingTransport(),
    )

    for _ in range(10):
        for provider in (deterministic, free_tier):
            decision = guard.evaluate_provider(provider)
            assert decision.allowed is True, decision.reason
            guard.record_completed(decision)

    snapshot = guard.snapshot()
    assert snapshot.unknown_cost_admitted_today == 0
    # The unknown-cost path still has its full quota available.
    assert guard.evaluate_provider(_qwen_provider()).allowed is True


def test_readiness_evaluations_do_not_burn_the_limit() -> None:
    """Status reporting evaluates without completing — it must not spend quota."""
    guard = _open_guard(limit=3)
    provider = _qwen_provider()

    for _ in range(20):
        assert guard.evaluate_provider(provider).allowed is True

    # Real completions still get the full limit afterwards.
    admitted = 0
    for _ in range(3):
        decision = guard.evaluate_provider(provider)
        assert decision.allowed is True
        guard.record_completed(decision)
        admitted += 1
    assert admitted == 3
    assert guard.evaluate_provider(provider).allowed is False


def test_counter_rolls_over_at_the_utc_day_boundary() -> None:
    """The daily cap is daily: a new UTC day restores the quota."""
    current = {"at": datetime(2026, 11, 26, 12, 0, tzinfo=UTC)}
    guard = BudgetGuard(
        BudgetPolicy(max_spend_usd=10.0, allow_unknown_cost=True, unknown_cost_daily_call_limit=2),
        now_fn=lambda: current["at"],
    )
    provider = _qwen_provider()

    for _ in range(2):
        decision = guard.evaluate_provider(provider)
        assert decision.allowed is True
        guard.record_completed(decision)
    assert guard.evaluate_provider(provider).allowed is False

    current["at"] = datetime(2026, 11, 27, 0, 0, tzinfo=UTC)
    assert guard.evaluate_provider(provider).allowed is True
    assert guard.snapshot().unknown_cost_admitted_today == 0


def test_qwen_and_omnirouters_share_the_same_gate() -> None:
    """Both non-reclassifiable providers are bounded by one counter."""
    guard = _open_guard(limit=2)
    qwen = _qwen_provider()
    omni = OpenAICompatibleFreeProvider(
        kind=ProviderKind.OMNIROUTERS,
        endpoint_url="https://omnirouters.com/v1/chat/completions",
        api_key="omni-fixture-key",
        model="account/model-v1",
        env_var="OMNIROUTERS_API_KEY",
        transport=_ExplodingTransport(),
    )

    for provider in (qwen, omni):
        decision = guard.evaluate_provider(provider)
        assert decision.allowed is True
        guard.record_completed(decision)

    assert guard.evaluate_provider(qwen).allowed is False
    assert guard.evaluate_provider(omni).allowed is False
    assert guard.snapshot().unknown_cost_admitted_today == 2
    assert BillingTier.UNKNOWN_COST in {
        qwen.billing_tier,
        omni.billing_tier,
    }
