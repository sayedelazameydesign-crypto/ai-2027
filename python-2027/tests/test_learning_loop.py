"""End-to-end integration test for the P2+P3 learning loop.

This wires the five new modules together against the existing free-first
router and exercises the whole loop offline through the deterministic provider:
execute -> verify -> persist -> feedback -> score -> benchmark -> compare ->
reopen. It proves the three non-negotiable rules are enforced by running code,
not by documentation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai2027_protocol.ai_providers import DeterministicFallbackProvider
from ai2027_protocol.benchmark_suite import BenchmarkSuite
from ai2027_protocol.budget import BudgetPolicy
from ai2027_protocol.experience_store import (
    ExperienceIntegrityError,
    ExperienceRecord,
    ExperienceStore,
    fingerprint_text,
)
from ai2027_protocol.feedback_engine import Feedback, FeedbackEngine, FeedbackError
from ai2027_protocol.meta_router import FreeFirstPolicyError, MetaRouter
from ai2027_protocol.provider_scoring import ProviderScorecard
from ai2027_protocol.router import FreeFirstRouter, RouterConfig

_FAST_CONFIG = RouterConfig(
    per_provider_timeout_s=0.05, max_retries_per_provider=0, base_backoff_s=0.0
)


def _build(store: ExperienceStore, scorecard: ProviderScorecard) -> MetaRouter:
    router = FreeFirstRouter(
        providers=[DeterministicFallbackProvider()],
        config=_FAST_CONFIG,
        budget_policy=BudgetPolicy(max_spend_usd=0.0),
    )
    return MetaRouter(router, registry=None, scorecard=scorecard)


async def test_full_learning_loop(tmp_path: Path) -> None:
    db = tmp_path / "learning.sqlite"
    store = ExperienceStore(db)
    scorecard = ProviderScorecard()
    meta = _build(store, scorecard)

    # 1. Execute tasks; every verified episode is persisted.
    first = await meta.execute("fix a failing pytest case", task_type="python_engineering")
    assert first.verified is True
    assert store.record(first) is True
    assert store.get(first.episode_id) is not None

    # 2. Feedback on a verified episode updates the scorecard (learning).
    engine = FeedbackEngine(scorecard)
    assert engine.apply(first, Feedback.accept()) is True
    score = scorecard.get(first.provider, first.model, first.task_type or "")
    assert score is not None
    assert score.attempts >= 1

    # 3. Benchmark runs, persists a baseline, and a fresh run shows no regression.
    baseline = tmp_path / "baseline.json"
    suite = BenchmarkSuite(meta, baseline_path=baseline)
    report, episodes = await suite.run()
    assert len(episodes) == len(suite.cases)
    suite.save_baseline(report)
    fresh, _ = await suite.run()
    assert suite.compare(fresh) == []

    # 4. Persistence survives a restart (NO PERSISTENCE = NO COMPLETION).
    store.close()
    reopened = ExperienceStore(db)
    assert reopened.get(first.episode_id) is not None
    assert reopened.verify_durability() == []
    reopened.close()


async def test_unverified_results_are_never_learned(tmp_path: Path) -> None:
    store = ExperienceStore(tmp_path / "learning.sqlite")
    scorecard = ProviderScorecard()

    # A total router failure yields an unverified episode: it is data, and it
    # is refused from durable memory and from the feedback signal.
    empty_router = FreeFirstRouter(
        providers=[], config=_FAST_CONFIG, budget_policy=BudgetPolicy(max_spend_usd=0.0)
    )
    failed_meta = MetaRouter(empty_router, registry=None, scorecard=scorecard)
    record = await failed_meta.execute("unreachable task")
    assert record.verified is False

    # Refused from the store (NO VERIFIED RESULT = NO LEARNING).
    assert store.record(record) is False
    assert store.get(record.episode_id) is None
    assert store.stats()["refused"] == 1

    # Refused by the feedback engine with an explicit error.
    engine = FeedbackEngine(scorecard)
    with pytest.raises(FeedbackError):
        engine.apply(record, Feedback.accept())
    store.close()


async def test_max_spend_is_enforced_before_any_execution(tmp_path: Path) -> None:
    router = FreeFirstRouter(
        providers=[DeterministicFallbackProvider()],
        config=_FAST_CONFIG,
        budget_policy=BudgetPolicy(max_spend_usd=2.0),
    )
    meta = MetaRouter(router, registry=None, scorecard=None)
    with pytest.raises(FreeFirstPolicyError):
        await meta.execute("any task")


def test_nonzero_cost_record_is_refused(tmp_path: Path) -> None:
    store = ExperienceStore(tmp_path / "learning.sqlite")
    record = ExperienceRecord(
        episode_id="ep_paid",
        task_type="coding",
        provider="ollama",
        model="llama3",
        verified=True,
        test_result="passed",
        output_fingerprint=fingerprint_text("v"),
        cost_usd=1.0,
        created_at="2026-11-25T00:00:00Z",
    )
    with pytest.raises(ExperienceIntegrityError):
        store.record(record)
    store.close()
