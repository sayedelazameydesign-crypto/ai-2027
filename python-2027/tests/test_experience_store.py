"""Tests for the evidence-gated experience store (``ai2027_protocol.experience_store``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai2027_protocol.experience_store import (
    ExperienceIntegrityError,
    ExperienceRecord,
    ExperienceStore,
    fingerprint_text,
)


def _record(**overrides: object) -> ExperienceRecord:
    """Return a verified experience record, with optional field overrides."""
    base: dict[str, object] = {
        "episode_id": "ep_test_0001",
        "task_type": "coding",
        "complexity": "standard",
        "task_summary": "fix a failing test",
        "provider": "ollama",
        "model": "llama3",
        "latency_ms": 120.0,
        "attempts": 1,
        "verified": True,
        "test_result": "passed",
        "quality_score": 0.8,
        "accepted": True,
        "output_fingerprint": fingerprint_text("verified output"),
        "cost_usd": 0.0,
        "billing_tier": "local",
        "cost_status": "proven_zero_local",
        "created_at": "2026-11-25T00:00:00Z",
    }
    base.update(overrides)
    return ExperienceRecord(**base)  # type: ignore[arg-type]


class TestFingerprint:
    def test_fingerprint_is_sha256(self) -> None:
        digest = fingerprint_text("hello")
        assert len(digest) == 64
        assert digest == fingerprint_text("hello")
        assert digest != fingerprint_text("world")


class TestAdmission:
    def test_admits_a_verified_record(self, tmp_path: Path) -> None:
        store = ExperienceStore(tmp_path / "exp.sqlite")
        assert store.record(_record()) is True
        assert store.get("ep_test_0001") is not None
        store.close()

    def test_refuses_an_unverified_record(self, tmp_path: Path) -> None:
        store = ExperienceStore(tmp_path / "exp.sqlite")
        assert store.record(_record(verified=False, test_result="unverified")) is False
        assert store.get("ep_test_0001") is None
        assert store.stats()["refused"] == 1
        store.close()

    def test_refuses_a_failed_test_result(self, tmp_path: Path) -> None:
        store = ExperienceStore(tmp_path / "exp.sqlite")
        assert store.record(_record(verified=False, test_result="failed", accepted=None)) is False
        assert store.get("ep_test_0001") is None
        store.close()

    def test_raises_on_feedback_recorded_for_an_unverified_outcome(self, tmp_path: Path) -> None:
        # A user accept on an unverified outcome is contradictory data, not a
        # refusal to log: it must raise rather than silently pass.
        store = ExperienceStore(tmp_path / "exp.sqlite")
        with pytest.raises(ExperienceIntegrityError):
            store.record(_record(verified=False, test_result="failed", accepted=True))
        store.close()

    def test_refuses_a_malformed_fingerprint(self, tmp_path: Path) -> None:
        store = ExperienceStore(tmp_path / "exp.sqlite")
        assert store.record(_record(output_fingerprint="not-a-sha256")) is False
        store.close()

    def test_refuses_a_nonzero_cost(self, tmp_path: Path) -> None:
        store = ExperienceStore(tmp_path / "exp.sqlite")
        with pytest.raises(ExperienceIntegrityError):
            store.record(_record(cost_usd=1.5))
        store.close()

    def test_duplicate_episode_is_not_re_added(self, tmp_path: Path) -> None:
        store = ExperienceStore(tmp_path / "exp.sqlite")
        assert store.record(_record()) is True
        assert store.record(_record()) is False
        assert store.stats()["stored"] == 1
        store.close()

    def test_rejected_outcome_is_still_refused_from_learning(self, tmp_path: Path) -> None:
        # A verified-but-rejected outcome is admissible for learning about
        # failure; it is stored, not dropped.
        store = ExperienceStore(tmp_path / "exp.sqlite")
        assert store.record(_record(accepted=False)) is True
        stored = store.get("ep_test_0001")
        assert stored is not None and stored.is_rejected
        store.close()


class TestRead:
    def test_get_returns_none_for_missing(self, tmp_path: Path) -> None:
        store = ExperienceStore(tmp_path / "exp.sqlite")
        assert store.get("nope") is None
        store.close()

    def test_recent_returns_newest_first(self, tmp_path: Path) -> None:
        store = ExperienceStore(tmp_path / "exp.sqlite")
        store.record(_record(episode_id="ep_1", created_at="2026-01-01T00:00:00Z"))
        store.record(_record(episode_id="ep_2", created_at="2026-02-01T00:00:00Z"))
        recent = store.recent()
        assert [record.episode_id for record in recent] == ["ep_2", "ep_1"]
        store.close()

    def test_recent_filters_by_task_type(self, tmp_path: Path) -> None:
        store = ExperienceStore(tmp_path / "exp.sqlite")
        store.record(_record(episode_id="ep_1", task_type="coding"))
        store.record(_record(episode_id="ep_2", task_type="research"))
        coding = store.recent(task_type="coding")
        assert [record.episode_id for record in coding] == ["ep_1"]
        store.close()

    def test_search_retrieves_by_lexical_similarity(self, tmp_path: Path) -> None:
        store = ExperienceStore(tmp_path / "exp.sqlite")
        store.record(
            _record(episode_id="ep_a", task_type="coding", task_summary="fix a python test failure")
        )
        store.record(
            _record(
                episode_id="ep_b", task_type="research", task_summary="summarise a research paper"
            )
        )
        hits = store.search("python test failure", k=1)
        assert hits and hits[0].episode_id == "ep_a"
        store.close()


class TestDurability:
    def test_verify_durability_is_clean_after_writes(self, tmp_path: Path) -> None:
        store = ExperienceStore(tmp_path / "exp.sqlite")
        store.record(_record())
        assert store.verify_durability() == []
        store.close()

    def test_store_survives_reopen(self, tmp_path: Path) -> None:
        db = tmp_path / "exp.sqlite"
        store = ExperienceStore(db)
        store.record(_record())
        store.close()
        # Reopen from the same file; the record must still be there.
        reopened = ExperienceStore(db)
        assert reopened.get("ep_test_0001") is not None
        assert reopened.verify_durability() == []
        reopened.close()

    def test_stats_reports_stored_and_refused(self, tmp_path: Path) -> None:
        store = ExperienceStore(tmp_path / "exp.sqlite")
        store.record(_record())
        store.record(_record(episode_id="ep_bad", verified=False, test_result="unverified"))
        stats = store.stats()
        assert stats["stored"] == 1
        assert stats["refused"] == 1
        store.close()


class TestComplexityValidation:
    def test_unknown_complexity_is_rejected(self, tmp_path: Path) -> None:
        store = ExperienceStore(tmp_path / "exp.sqlite")
        with pytest.raises(ExperienceIntegrityError):
            store.record(_record(complexity="galactic"))
        store.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
