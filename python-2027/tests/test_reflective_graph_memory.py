"""Unit, durability, and Hypothesis tests for ReflectiveGraphMemory (Cognee + RMM)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ai2027_protocol.experience_store import (
    ExperienceIntegrityError,
    ExperienceRecord,
    fingerprint_text,
)
from ai2027_protocol.reflective_graph_memory import (
    GraphEdgeSpec,
    GraphNodeSpec,
    ReflectiveGraphMemory,
    compute_utility_score,
    extract_prospective_graph,
)


def _verified_record(
    episode_id: str,
    summary: str,
    *,
    output_text: str | None = None,
    accepted: bool | None = None,
    task_type: str = "coding",
) -> ExperienceRecord:
    artifact = output_text if output_text is not None else f"verified::{episode_id}::{summary}"
    return ExperienceRecord(
        episode_id=episode_id,
        task_type=task_type,
        complexity="standard",
        task_summary=summary,
        provider="deterministic",
        model="local-v1",
        latency_ms=4.0,
        attempts=1,
        verified=True,
        test_result="passed",
        quality_score=0.95,
        accepted=accepted,
        output_fingerprint=fingerprint_text(artifact),
        cost_usd=0.0,
    )


def test_admissibility_gate_blocks_unverified_or_paid_episodes(tmp_path: Path) -> None:
    """Unverified or paid episodes never create graph nodes or edges."""
    db_file = tmp_path / "graph_gate.sqlite3"
    with ReflectiveGraphMemory(db_file) as mem:
        unverified = ExperienceRecord(
            episode_id="ep_unverified",
            task_type="coding",
            task_summary="Modify src/ai2027_protocol/reflexion.py without verification",
            provider="deterministic",
            model="local-v1",
            verified=False,
            test_result="failed",
            output_fingerprint="",
            cost_usd=0.0,
        )
        assert mem.index_episode(unverified) is False
        stats = mem.stats()
        assert stats["stored"] == 0
        assert stats["refused"] == 1
        assert stats["nodes"] == 0
        assert stats["edges"] == 0

        paid = ExperienceRecord(
            episode_id="ep_paid",
            task_type="coding",
            task_summary="Paid run on src/app.py",
            provider="deterministic",
            model="local-v1",
            verified=True,
            test_result="passed",
            output_fingerprint=fingerprint_text("paid-output"),
            cost_usd=0.02,
        )
        with pytest.raises(ExperienceIntegrityError):
            mem.index_episode(paid)


def test_idempotent_reindexing_same_fingerprint_does_not_inflate_counts(
    tmp_path: Path,
) -> None:
    """Re-indexing an admitted episode with the same fingerprint is idempotent."""
    db_file = tmp_path / "graph_idempotent.sqlite3"
    rec = _verified_record(
        "ep_01",
        "Refactor python-2027/src/ai2027_protocol/reflexion.py and verify schema.json",
        output_text="stable-artifact-v1",
    )
    extra_node = GraphNodeSpec(
        node_kind="failure_mode",
        key="self_eval_false_positive",
        label="Self-Eval False Positive",
    )
    extra_edge = GraphEdgeSpec(
        source_id="capability:coding",
        target_id=extra_node.node_id,
        relation="mitigates_failure",
    )

    with ReflectiveGraphMemory(db_file) as mem:
        assert mem.index_episode(rec, extra_nodes=(extra_node,), extra_edges=(extra_edge,)) is True
        digest_before = mem.graph_digest()
        node_before = mem.get_node("artifact:schema.json")
        assert node_before is not None
        assert node_before.fingerprint_count == 1
        assert mem.get_node("node:missing") is None

        # Re-indexing the exact same record + fingerprint must keep identical counts & digest.
        assert mem.index_episode(rec, extra_nodes=(extra_node,), extra_edges=(extra_edge,)) is True
        assert mem.graph_digest() == digest_before
        node_after = mem.get_node("artifact:schema.json")
        assert node_after is not None
        assert node_after.fingerprint_count == 1

        # Attempting to re-index the same episode_id with a tampered fingerprint is refused.
        tampered = rec.model_copy(update={"output_fingerprint": fingerprint_text("other-output")})
        assert mem.index_episode(tampered) is False

        # Indexing a second distinct verified artifact touching schema.json increments count to 2.
        rec2 = _verified_record(
            "ep_02",
            "Validate schema.json against strict contract",
            output_text="stable-artifact-v2",
        )
        assert mem.index_episode(rec2) is True
        node_two = mem.get_node("artifact:schema.json")
        assert node_two is not None
        assert node_two.fingerprint_count == 2

        incident = mem.neighbors("capability:coding")
        assert any(edge.target_id == extra_node.node_id for edge in incident)
        assert mem.verify_durability() == []


def test_rmm_retrospective_utility_reranks_helpful_over_unhelpful_without_deletion(
    tmp_path: Path,
) -> None:
    """RMM retrospective feedback promotes verified-helpful episodes over unhelpful ones."""
    db_file = tmp_path / "graph_rmm.sqlite3"
    ep_helpful = _verified_record(
        "ep_helpful",
        "Fix sqlite transaction lock contention in store.py for concurrent readers",
    )
    ep_stale = _verified_record(
        "ep_stale",
        "Fix sqlite transaction lock contention in store.py for legacy batch mode",
    )
    ep_rejected = _verified_record(
        "ep_rejected",
        "Fix sqlite transaction lock contention in store.py quick workaround",
        accepted=False,
    )

    with ReflectiveGraphMemory(db_file) as mem:
        assert mem.search("sqlite lock contention") == []
        assert mem.index_episode(ep_helpful) is True
        assert mem.index_episode(ep_stale) is True
        assert mem.index_episode(ep_rejected) is True

        # Unknown episode IDs are safely ignored; duplicates in one call count once.
        updated = mem.record_retrieval_outcome(
            ["ep_helpful", "ep_helpful", "ep_nonexistent"],
            downstream_verified=True,
        )
        assert updated == 1
        for _ in range(3):
            mem.record_retrieval_outcome(["ep_helpful"], downstream_verified=True)
            mem.record_retrieval_outcome(["ep_stale"], downstream_verified=False)

        hits = mem.search("sqlite lock contention in store.py", k=3, rerank_by_utility=True)
        assert [h.record.episode_id for h in hits] == [
            "ep_helpful",
            "ep_stale",
            "ep_rejected",
        ]
        assert hits[0].utility_score > hits[1].utility_score
        assert hits[0].verified_helpful_count == 4
        assert hits[1].verified_helpful_count == 0
        # Historical episodes remain intact in SQLite (no silent deletion).
        assert mem.stats()["stored"] == 3

        # Disabling utility reranking still works cleanly, and empty symbol queries skip seeds.
        no_util_hits = mem.search("???", k=2, rerank_by_utility=False)
        assert len(no_util_hits) == 2


def test_prospective_extractor_and_durability_orphan_detection(tmp_path: Path) -> None:
    """Extractor caps concepts cleanly and durability check catches corrupt foreign links."""
    rec = _verified_record(
        "ep_concepts",
        "alpha beta gamma delta epsilon zeta theta iota kappa lambda 12345 with from",
    )
    nodes, edges = extract_prospective_graph(rec, max_concepts=4)
    concept_nodes = [n for n in nodes if n.node_kind == "concept"]
    assert len(concept_nodes) == 4
    assert any(e.relation == "co_occurs_with" for e in edges)

    missing_db = tmp_path / "ephemeral.sqlite3"
    mem = ReflectiveGraphMemory(missing_db)
    assert mem.index_episode(rec) is True
    mem.close()

    # Inject corrupt orphan link & dangling edge directly into SQLite to prove verify_durability.
    raw = sqlite3.connect(str(missing_db))
    raw.execute(
        "INSERT INTO episode_node_links VALUES ('deadbeef', 'ep_ghost', 'concept:alpha', 'now')"
    )
    raw.execute(
        "INSERT INTO memory_edges VALUES "
        "('concept:missing_a', 'concept:missing_b', 'rel', 1, 'fp', 'now')"
    )
    raw.commit()
    raw.close()

    with ReflectiveGraphMemory(missing_db) as reopened:
        problems = reopened.verify_durability()
        assert any("orphan episode_node_link" in p for p in problems)
        assert any("dangling memory_edge" in p for p in problems)

    detached = ReflectiveGraphMemory(missing_db)
    missing_db.unlink()
    try:
        assert detached.verify_durability() != []
    finally:
        detached.close()


@settings(max_examples=40, deadline=None)
@given(
    retrieved=st.integers(min_value=0, max_value=200),
    helpful=st.integers(min_value=0, max_value=200),
)
def test_property_utility_score_is_strictly_bounded_and_monotonic(
    retrieved: int,
    helpful: int,
) -> None:
    """Laplace-smoothed RMM utility stays strictly inside (0, 1) and increases with wins."""
    clamped_helpful = min(retrieved, helpful)
    score = compute_utility_score(retrieved, clamped_helpful)
    assert 0.0 < score < 1.0
    score_plus_win = compute_utility_score(retrieved + 1, clamped_helpful + 1)
    score_plus_loss = compute_utility_score(retrieved + 1, clamped_helpful)
    assert score_plus_win > score_plus_loss
