"""Tests for the provider quality scorecard (``ai2027_protocol.provider_scoring``)."""

from __future__ import annotations

from ai2027_protocol.experience_store import ExperienceRecord, fingerprint_text
from ai2027_protocol.provider_scoring import ProviderScore, ProviderScorecard


def _record(**overrides: object) -> ExperienceRecord:
    """Return a verified experience record with optional overrides."""
    base: dict[str, object] = {
        "episode_id": "ep_x",
        "task_type": "coding",
        "provider": "ollama",
        "model": "llama3",
        "latency_ms": 100.0,
        "attempts": 1,
        "verified": True,
        "test_result": "passed",
        "accepted": True,
        "output_fingerprint": fingerprint_text("out"),
        "created_at": "2026-11-25T00:00:00Z",
    }
    base.update(overrides)
    return ExperienceRecord(**base)  # type: ignore[arg-type]


class TestScore:
    def test_score_with_no_data_is_zero(self) -> None:
        score = ProviderScore(provider="p", model="m", capability="coding")
        assert score.quality_score == 0.0
        assert score.verification_rate == 0.0

    def test_verified_success_raises_quality(self) -> None:
        scorecard = ProviderScorecard()
        scorecard.update([_record()])
        score = scorecard.get("ollama", "llama3", "coding")
        assert score is not None
        assert score.verified == 1
        assert score.successes == 1
        assert score.quality_score > 0.0

    def test_signals_are_explainable(self) -> None:
        scorecard = ProviderScorecard()
        scorecard.update([_record()])
        score = scorecard.get("ollama", "llama3", "coding")
        assert score is not None
        signals = score.explain()
        assert set(signals) == {
            "verification",
            "success",
            "preference",
            "efficiency",
            "quality_score",
        }
        assert signals["verification"] == 1.0


class TestUpdate:
    def test_update_returns_verified_count(self) -> None:
        scorecard = ProviderScorecard()
        records = [_record(episode_id=f"ep_{i}") for i in range(3)]
        assert scorecard.update(records) == 3

    def test_rejected_outcome_lowers_preference(self) -> None:
        scorecard = ProviderScorecard()
        scorecard.update([_record(episode_id="ep_ok", accepted=True)])
        scorecard.update([_record(episode_id="ep_bad", accepted=False)])
        score = scorecard.get("ollama", "llama3", "coding")
        assert score is not None
        assert score.accepted == 1
        assert score.rejected == 1
        assert score.preference == 0.5

    def test_unverified_outcome_counts_as_failure(self) -> None:
        scorecard = ProviderScorecard()
        scorecard.update([_record(episode_id="ep_bad", verified=False, test_result="failed")])
        score = scorecard.get("ollama", "llama3", "coding")
        assert score is not None
        assert score.failures == 1
        assert score.verified == 0

    def test_efficiency_penalises_slow_high_retry_runs(self) -> None:
        scorecard = ProviderScorecard()
        scorecard.update([_record(episode_id="ep_slow", latency_ms=40_000.0, attempts=5)])
        score = scorecard.get("ollama", "llama3", "coding")
        assert score is not None
        assert score.quality_score < 1.0


class TestRecommend:
    def test_recommend_returns_best_for_capability(self) -> None:
        scorecard = ProviderScorecard()
        scorecard.update([_record(provider="ollama", model="m1")])
        scorecard.update(
            [
                _record(episode_id="ep_a", provider="gemini", model="g1", accepted=False),
                _record(episode_id="ep_b", provider="gemini", model="g1", accepted=False),
            ]
        )
        recommended = scorecard.recommend("coding")
        assert recommended is not None
        assert recommended.provider == "ollama"

    def test_recommend_requires_min_attempts(self) -> None:
        scorecard = ProviderScorecard()
        scorecard.update([_record(provider="gemini", model="g1")])
        assert scorecard.recommend("coding", min_attempts=2) is None
        assert scorecard.recommend("coding", min_attempts=1) is not None

    def test_for_capability_is_sorted_by_quality(self) -> None:
        scorecard = ProviderScorecard()
        scorecard.update([_record(provider="weak", model="w", accepted=False)])
        scorecard.update([_record(episode_id="ep2", provider="strong", model="s")])
        ranked = scorecard.for_capability("coding")
        assert ranked[0].provider == "strong"

    def test_recommend_returns_none_for_unknown_capability(self) -> None:
        scorecard = ProviderScorecard()
        assert scorecard.recommend("nonexistent") is None

    def test_best_global_returns_highest(self) -> None:
        scorecard = ProviderScorecard()
        scorecard.update([_record(provider="a", model="1")])
        scorecard.update([_record(episode_id="ep2", provider="b", model="2", accepted=False)])
        best = scorecard.best_global()
        assert best is not None
        assert best.provider == "a"

    def test_summary_is_serializable(self) -> None:
        scorecard = ProviderScorecard()
        scorecard.update([_record()])
        summary = scorecard.summary()
        assert "ollama/llama3" in summary
        quality = summary["ollama/llama3"]["quality_score"]
        assert isinstance(quality, float) and 0.0 <= quality <= 1.0
