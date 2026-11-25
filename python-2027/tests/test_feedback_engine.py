"""Tests for the feedback engine (``ai2027_protocol.feedback_engine``)."""

from __future__ import annotations

import pytest

from ai2027_protocol.experience_store import ExperienceRecord, fingerprint_text
from ai2027_protocol.feedback_engine import Feedback, FeedbackEngine, FeedbackError
from ai2027_protocol.provider_scoring import ProviderScorecard


def _record(**overrides: object) -> ExperienceRecord:
    """Return a verified experience record with optional overrides."""
    base: dict[str, object] = {
        "episode_id": "ep_fb",
        "task_type": "coding",
        "provider": "ollama",
        "model": "llama3",
        "verified": True,
        "test_result": "passed",
        "accepted": None,
        "output_fingerprint": fingerprint_text("out"),
        "created_at": "2026-11-25T00:00:00Z",
    }
    base.update(overrides)
    return ExperienceRecord(**base)  # type: ignore[arg-type]


class TestFeedbackSignal:
    def test_from_rating_maps_above_threshold_to_accept(self) -> None:
        signal = Feedback.from_rating(0.9)
        assert signal.accepted is True

    def test_from_rating_maps_below_threshold_to_reject(self) -> None:
        signal = Feedback.from_rating(0.2)
        assert signal.accepted is False


class TestApply:
    def test_apply_accepts_a_verified_episode(self) -> None:
        engine = FeedbackEngine(ProviderScorecard())
        assert engine.apply(_record(), Feedback.accept()) is True

    def test_apply_rejects_an_unverified_episode(self) -> None:
        engine = FeedbackEngine(ProviderScorecard())
        with pytest.raises(FeedbackError):
            engine.apply(_record(verified=False, test_result="unverified"), Feedback.accept())

    def test_apply_rejection_updates_scorecard(self) -> None:
        scorecard = ProviderScorecard()
        engine = FeedbackEngine(scorecard)
        engine.apply(_record(), Feedback.reject())
        score = scorecard.get("ollama", "llama3", "coding")
        assert score is not None
        assert score.rejected == 1

    def test_apply_acceptance_updates_preference(self) -> None:
        scorecard = ProviderScorecard()
        engine = FeedbackEngine(scorecard)
        engine.apply(_record(), Feedback.accept())
        score = scorecard.get("ollama", "llama3", "coding")
        assert score is not None
        assert score.accepted == 1
        assert score.preference == 1.0


class TestResultTests:
    def test_apply_test_result_passed_marks_verified(self) -> None:
        engine = FeedbackEngine(ProviderScorecard())
        result = engine.apply_test_result(
            _record(verified=False, test_result="unverified"), "passed"
        )
        assert result is True

    def test_apply_test_result_failed_marks_unverified(self) -> None:
        engine = FeedbackEngine(ProviderScorecard())
        assert engine.apply_test_result(_record(), "failed") is False


class TestRecommend:
    def test_recommend_delegates_to_scorecard(self) -> None:
        scorecard = ProviderScorecard()
        engine = FeedbackEngine(scorecard)
        assert engine.recommend("coding") is None
        scorecard.update([_record()])
        assert engine.recommend("coding") is not None
