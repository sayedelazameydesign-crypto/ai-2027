"""Tests for the meta router (``ai2027_protocol.meta_router``).

The router is exercised offline through a deterministic local provider so the
tests prove the learning/verification wiring without network or paid calls.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai2027_protocol.ai_providers import DeterministicFallbackProvider
from ai2027_protocol.budget import BudgetPolicy
from ai2027_protocol.contracts import ProviderKind
from ai2027_protocol.experience_store import ExperienceStore
from ai2027_protocol.meta_router import (
    FreeFirstPolicyError,
    MetaRouter,
    PrivacyPolicy,
    VerificationResult,
)
from ai2027_protocol.provider_scoring import ProviderScorecard
from ai2027_protocol.router import FreeFirstRouter, RouterConfig

#: A fast, offline config: no retries, no backoff, short per-provider timeout.
_FAST_CONFIG = RouterConfig(
    per_provider_timeout_s=0.05, max_retries_per_provider=0, base_backoff_s=0.0
)


def _router(**overrides: object) -> MetaRouter:
    """Return a MetaRouter over the deterministic offline provider."""
    kwargs: dict[str, object] = {
        "config": _FAST_CONFIG,
        "budget_policy": BudgetPolicy(max_spend_usd=0.0),
    }
    kwargs.update(overrides)
    router = FreeFirstRouter(providers=[DeterministicFallbackProvider()], **kwargs)  # type: ignore[arg-type]
    return MetaRouter(router, registry=None, scorecard=None)


class TestPolicy:
    def test_free_first_enforces_zero_spend(self) -> None:
        router = _router()
        router.assert_free_first()  # no raise

    def test_max_spend_enforcement_blocks_a_nonzero_budget(self) -> None:
        router = MetaRouter(
            FreeFirstRouter(
                providers=[DeterministicFallbackProvider()],
                config=_FAST_CONFIG,
                budget_policy=BudgetPolicy(max_spend_usd=5.0),
            ),
            registry=None,
            scorecard=None,
        )
        with pytest.raises(FreeFirstPolicyError):
            router.assert_free_first()

    def test_allow_unknown_cost_enforcement(self) -> None:
        router = MetaRouter(
            FreeFirstRouter(
                providers=[DeterministicFallbackProvider()],
                config=_FAST_CONFIG,
                budget_policy=BudgetPolicy(max_spend_usd=0.0, allow_unknown_cost=True),
            ),
            registry=None,
            scorecard=None,
        )
        with pytest.raises(FreeFirstPolicyError):
            router.assert_free_first()


class TestPrivacy:
    def test_privacy_default_is_local_only(self) -> None:
        policy = PrivacyPolicy()
        assert policy.allow_remote is False
        assert policy.permits(ProviderKind.GEMINI) is False

    def test_permits_only_explicitly_allowed_kinds(self) -> None:
        policy = PrivacyPolicy(
            allow_remote=True, allowed_remote_kinds=frozenset({ProviderKind.GEMINI.value})
        )
        assert policy.permits(ProviderKind.GEMINI) is True
        assert policy.permits(ProviderKind.OLLAMA) is False


class TestClassify:
    def test_classify_resolves_a_capability(self) -> None:
        router = _router()
        decision = router.classify("help me write and run a pytest test")
        assert decision.task_type is not None
        assert decision.profile is not None


class TestExecute:
    async def test_execute_returns_a_verified_record(self) -> None:
        router = _router()
        record = await router.execute("help me fix a failing pytest case")
        assert record.verified is True
        assert record.test_result == "passed"
        assert record.cost_usd == 0.0
        assert record.task_type is not None
        assert len(record.output_fingerprint) == 64

    async def test_execute_records_real_provider_metadata(self) -> None:
        router = _router()
        record = await router.execute("reason about a data structure", task_type="reasoning")
        assert record.provider != "none"
        assert record.attempts >= 1
        assert record.latency_ms >= 0.0

    async def test_execute_survives_a_router_error_as_data(self) -> None:
        router = FreeFirstRouter(
            providers=[], config=_FAST_CONFIG, budget_policy=BudgetPolicy(max_spend_usd=0.0)
        )
        meta = MetaRouter(router, registry=None, scorecard=None)
        record = await meta.execute("a task no provider can serve")
        # A total failure is captured as an unverified attempt, not swallowed.
        assert record.verified is False
        assert record.test_result == "unverified"


class TestRecommend:
    def test_recommend_none_without_scorecard(self) -> None:
        router = _router()
        assert router.recommend("coding") is None

    def test_recommend_delegates_to_scorecard(self) -> None:
        scorecard = ProviderScorecard()
        router = MetaRouter(
            FreeFirstRouter(
                providers=[DeterministicFallbackProvider()],
                config=_FAST_CONFIG,
                budget_policy=BudgetPolicy(max_spend_usd=0.0),
            ),
            registry=None,
            scorecard=scorecard,
        )
        assert router.recommend("coding") is None


class TestVerifier:
    def test_default_verifier_passes_real_content(self) -> None:
        from ai2027_protocol.meta_router import default_verifier

        assert default_verifier("a real answer").passed is True

    def test_default_verifier_fails_placeholders(self) -> None:
        from ai2027_protocol.meta_router import default_verifier

        assert default_verifier("TODO: fill in").passed is False
        assert default_verifier("").passed is False

    def test_custom_verifier_is_used(self) -> None:
        router = MetaRouter(
            FreeFirstRouter(
                providers=[DeterministicFallbackProvider()],
                config=_FAST_CONFIG,
                budget_policy=BudgetPolicy(max_spend_usd=0.0),
            ),
            registry=None,
            scorecard=None,
            verifier=lambda text: VerificationResult(False, "custom reject"),
        )
        record = asyncio.run(router.execute("anything"))
        assert record.verified is False
        assert record.test_result == "failed"


class TestStorageWiring:
    async def test_execute_records_can_persist_to_store(self, tmp_path: Path) -> None:
        # The router emits a record; the store is the persistence boundary.
        store = ExperienceStore(tmp_path / "exp.sqlite")
        router = _router()
        record = await router.execute("a persistent task")
        assert store.record(record) is True
        assert store.get(record.episode_id) is not None
        store.close()
