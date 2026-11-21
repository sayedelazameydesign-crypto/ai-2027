from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from ai2027_protocol.research_contracts import (
    EngineeringAssessment,
    QuotedEvidence,
    ResearchEvaluation,
    ResearchPaper,
    ResearchSource,
    ResearchStatus,
    VerificationGates,
    arxiv_base_id,
)
from ai2027_protocol.research_contracts import (
    TestEvidence as ResearchTestEvidence,
)
from ai2027_protocol.research_store import ResearchStore
from ai2027_protocol.research_store_errors import (
    ResearchConflictError,
    ResearchIntegrityError,
    ResearchNotFoundError,
    ResearchTransitionError,
)


def make_paper(
    *,
    source: ResearchSource = ResearchSource.ARXIV,
    arxiv_id: str | None = "2401.01234v2",
    doi: str | None = "10.1234/research.paper",
    title: str = "Robust Agent Evaluation in Local Systems",
    abstract: str = "We measure an evaluation framework for local engineering agents.",
    published: date = date(2025, 1, 10),
) -> ResearchPaper:
    """Create source-bound metadata for transactional registry tests."""
    source_id = arxiv_id if source is ResearchSource.ARXIV else "W1234567890"
    assert source_id is not None
    paper_key = f"doi:{doi}" if doi else f"arxiv:{arxiv_base_id(arxiv_id or '')}"
    return ResearchPaper.from_verified_source(
        paper_key=paper_key,
        source=source,
        source_id=source_id,
        arxiv_id=arxiv_id if source is ResearchSource.ARXIV else None,
        doi=doi,
        title=title,
        abstract=abstract if source is ResearchSource.ARXIV else "",
        published_at=published,
        categories=("cs.AI",) if source is ResearchSource.ARXIV else (),
        authors=("Example Researcher",),
        source_url=(
            f"https://arxiv.org/abs/{arxiv_id}"
            if source is ResearchSource.ARXIV
            else f"https://openalex.org/{source_id}"
        ),
        fetched_at=datetime(2026, 11, 19, tzinfo=UTC),
    )


def make_evaluation() -> ResearchEvaluation:
    """Create a sourced excerpt and separately labeled engineering inference."""
    return ResearchEvaluation(
        source_evidence=(
            QuotedEvidence(
                field="abstract", quote="evaluation framework for local engineering agents"
            ),
        ),
        engineering_assessment=EngineeringAssessment(
            problem_and_method="The paper evaluates agent behavior using measured tasks.",
            assumptions_and_limitations="The abstract does not establish production-scale results.",
            system_fit="This may inform the repository's deterministic task evaluation.",
            proposed_experiment="Run a small held-out task suite against the current baseline.",
            incremental_cost="Local CPU time only; no paid API is required.",
            likely_breakage="An incomplete benchmark could reward the wrong behavior.",
        ),
    )


def make_test_evidence(*, all_passed: bool = True) -> ResearchTestEvidence:
    """Construct a bounded sandbox artifact with independently reported gates."""
    return ResearchTestEvidence(
        experiment="Run the candidate technique against a fixed local benchmark.",
        environment="Python 3.13, local sandbox, no secrets.",
        artifact_sha256=hashlib.sha256(b"benchmark evidence").hexdigest(),
        gates=VerificationGates(
            accuracy_passed=all_passed,
            latency_passed=all_passed,
            resource_passed=all_passed,
            edge_cases_passed=all_passed,
        ),
        measurements={"latency_ms": 12.5, "memory_mb": 32.0},
        tested_at=datetime(2026, 11, 19, tzinfo=UTC),
    )


def test_verified_candidate_is_persisted_with_aliases_and_chain(tmp_path: Path) -> None:
    """Candidate, canonical aliases, restrictive database mode, and audit are one transaction."""
    store = ResearchStore(tmp_path / "research.sqlite3")
    paper = make_paper()
    record = store.add_candidate(paper, topic_id="agent-evaluation")
    assert record.paper == paper
    assert record.status is ResearchStatus.CANDIDATE
    assert record.topics == ("agent-evaluation",)
    assert store.get("2401.01234").paper.paper_key == paper.paper_key
    assert store.get("doi:10.1234/research.paper").paper.paper_key == paper.paper_key
    audit = store.audit_report()
    assert audit["integrity_valid"] is True
    assert audit["event_count"] == 1
    assert audit["events"][0]["event_type"] == "paper_ingested"
    assert audit["events"][0]["details"]["metadata_sha256"] == paper.metadata_sha256
    if __import__("os").name != "nt":
        assert store.path.stat().st_mode & 0o777 == 0o600


def test_duplicate_id_is_deduplicated_and_topics_are_merged(tmp_path: Path) -> None:
    """Repeated source records add an alias/topic audit event instead of another paper."""
    store = ResearchStore(tmp_path / "research.sqlite3")
    paper = make_paper()
    first = store.add_candidate(paper, topic_id="agent-evaluation")
    second = store.add_candidate(paper, topic_id="systems-security")
    assert first.paper.paper_key == second.paper.paper_key
    assert second.topics == ("agent-evaluation", "systems-security")
    assert len(store.list_records()) == 1
    assert store.audit_report()["event_count"] == 2
    assert store.audit_report()["events"][-1]["event_type"] == "paper_reverified"


def test_cross_source_doi_check_is_kept_without_overwriting_primary_metadata(
    tmp_path: Path,
) -> None:
    """OpenAlex can independently verify a DOI but cannot replace arXiv's stored abstract."""
    store = ResearchStore(tmp_path / "research.sqlite3")
    arxiv_paper = make_paper()
    store.add_candidate(arxiv_paper, topic_id="agent-evaluation")
    openalex = make_paper(source=ResearchSource.OPENALEX)
    record = store.add_candidate(openalex, topic_id="systems-security")
    assert record.paper.source is ResearchSource.ARXIV
    assert record.paper.abstract == arxiv_paper.abstract
    assert record.topics == ("agent-evaluation", "systems-security")
    audit = store.audit_report()
    assert audit["events"][-1]["event_type"] == "source_crosscheck_added"
    assert audit["event_count"] == 2


def test_conflicting_source_title_date_or_changed_source_snapshot_is_refused_and_logged(
    tmp_path: Path,
) -> None:
    """Conflicts are auditable, but cannot silently replace verified metadata."""
    store = ResearchStore(tmp_path / "research.sqlite3")
    original = make_paper()
    store.add_candidate(original, topic_id="agent-evaluation")
    changed_title = make_paper(source=ResearchSource.OPENALEX, title="Invented Conflicting Title")
    with pytest.raises(ResearchConflictError, match="title/date"):
        store.add_candidate(changed_title, topic_id="agent-evaluation")
    changed_abstract = make_paper(abstract="A different upstream abstract for this paper.")
    with pytest.raises(ResearchConflictError, match="changed metadata"):
        store.add_candidate(changed_abstract, topic_id="agent-evaluation")
    assert store.get_by_key(original.paper_key).paper.abstract == original.abstract
    audit = store.audit_report()
    assert audit["event_count"] == 3
    assert all(event["event_type"] == "source_metadata_conflict" for event in audit["events"][1:])


def test_conflicting_aliases_that_point_to_multiple_records_fail_closed(tmp_path: Path) -> None:
    """One new record cannot merge two unrelated DOI/arXiv rows implicitly."""
    store = ResearchStore(tmp_path / "research.sqlite3")
    first = make_paper(arxiv_id="2401.01234v2", doi=None)
    second = make_paper(arxiv_id="2401.09999v1", doi="10.1234/research.paper")
    store.add_candidate(first, topic_id="agent-evaluation")
    store.add_candidate(second, topic_id="agent-evaluation")
    bridging = make_paper(arxiv_id="2401.01234v2", doi="10.1234/research.paper")
    with pytest.raises(ResearchConflictError, match="multiple records"):
        store.add_candidate(bridging, topic_id="agent-evaluation")
    assert len(store.list_records()) == 2


def test_evaluation_requires_an_exact_source_excerpt(tmp_path: Path) -> None:
    """Inference can be saved only alongside a quote actually present in the fetched abstract."""
    store = ResearchStore(tmp_path / "research.sqlite3")
    paper = make_paper()
    store.add_candidate(paper, topic_id="agent-evaluation")
    saved = store.save_evaluation("2401.01234", make_evaluation())
    assert saved.evaluation is not None
    assert saved.evaluation.source_evidence[0].field == "abstract"
    assert saved.evaluation.engineering_assessment.proposed_experiment.startswith("Run a small")
    fabricated = make_evaluation().model_copy(
        update={
            "source_evidence": (
                QuotedEvidence(field="abstract", quote="the paper proves production readiness"),
            )
        }
    )
    with pytest.raises(ResearchIntegrityError, match="does not occur"):
        store.save_evaluation("2401.01234", fabricated)
    assert store.get("2401.01234").evaluation == saved.evaluation


def test_candidate_can_be_marked_tested_only_with_all_four_gates(tmp_path: Path) -> None:
    """Tested state requires an artifact hash, measurements, environment, and passing gates."""
    store = ResearchStore(tmp_path / "research.sqlite3")
    paper = make_paper()
    store.add_candidate(paper, topic_id="agent-evaluation")
    with pytest.raises(ResearchTransitionError, match="four verification gates"):
        store.decide(
            "2401.01234",
            status=ResearchStatus.TESTED,
            evidence=make_test_evidence(all_passed=False),
        )
    tested = store.decide(
        "2401.01234",
        status=ResearchStatus.TESTED,
        evidence=make_test_evidence(),
    )
    assert tested.status is ResearchStatus.TESTED
    assert tested.test_evidence is not None
    assert tested.test_evidence.artifact_sha256 == make_test_evidence().artifact_sha256
    with pytest.raises(ResearchTransitionError, match="Only candidate"):
        store.decide("2401.01234", status=ResearchStatus.REJECTED, reason="Later changed")


def test_rejection_requires_reason_and_decisions_cannot_be_reset(tmp_path: Path) -> None:
    """A rejection is explicit and terminal; no implicit move back to Candidate is exposed."""
    store = ResearchStore(tmp_path / "research.sqlite3")
    paper = make_paper(arxiv_id="2401.09999v1", doi=None)
    store.add_candidate(paper, topic_id="systems-security")
    with pytest.raises(ResearchTransitionError, match="requires a reason"):
        store.decide("2401.09999", status=ResearchStatus.REJECTED)
    rejected = store.decide(
        "2401.09999", status=ResearchStatus.REJECTED, reason="No relevant system fit."
    )
    assert rejected.status is ResearchStatus.REJECTED
    assert rejected.decision_reason == "No relevant system fit."
    with pytest.raises(ResearchTransitionError, match="not candidate"):
        store.decide("2401.09999", status=ResearchStatus.CANDIDATE)
    with pytest.raises(ResearchTransitionError, match="Decision status"):
        store.decide("2401.09999", status="unknown")  # type: ignore[arg-type]
    with pytest.raises(ResearchTransitionError, match="not exceed"):
        store.decide("2401.09999", status=ResearchStatus.REJECTED, reason="x" * 1001)


def test_store_rejects_unknown_ids_topics_missing_records_and_invalid_limits(
    tmp_path: Path,
) -> None:
    """Repository boundaries reject invalid aliases and unbounded query sizes."""
    store = ResearchStore(tmp_path / "research.sqlite3")
    with pytest.raises(ValueError, match="topic_id"):
        store.add_candidate(make_paper(), topic_id="../private")
    with pytest.raises(ResearchNotFoundError):
        store.get("2401.01234")
    with pytest.raises(ResearchNotFoundError):
        store.get_by_key("arxiv:missing")
    with pytest.raises(ValueError, match="limit"):
        store.list_records(limit=201)
    with pytest.raises(ValueError, match="audit limit"):
        store.audit_report(limit=1001)


def test_registry_fails_closed_if_current_row_or_event_chain_is_tampered(tmp_path: Path) -> None:
    """Changing indexed status or audit details is detectable before any record is returned."""
    db = tmp_path / "research.sqlite3"
    store = ResearchStore(db)
    paper = make_paper()
    store.add_candidate(paper, topic_id="agent-evaluation")
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE research_papers SET status = 'rejected', decision_reason = 'tampered'"
            " WHERE paper_key = ?",
            (paper.paper_key,),
        )
    with pytest.raises(ResearchIntegrityError, match="last audited state"):
        store.list_records()

    audit_db = tmp_path / "tampered-audit.sqlite3"
    audit_store = ResearchStore(audit_db)
    audit_store.add_candidate(paper, topic_id="agent-evaluation")
    with sqlite3.connect(audit_db) as connection:
        connection.execute(
            "UPDATE research_audit SET event_hash = ? WHERE event_id = 1", ("f" * 64,)
        )
    with pytest.raises(ResearchIntegrityError, match="event hash"):
        audit_store.audit_report()


def test_registry_detects_tampered_alias_and_source_check_payload(tmp_path: Path) -> None:
    """Alias/source cross-check indices are bound into the most recent audit snapshot."""
    db = tmp_path / "research.sqlite3"
    store = ResearchStore(db)
    paper = make_paper()
    store.add_candidate(paper, topic_id="agent-evaluation")
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE research_identifiers SET value = '10.9999/changed' WHERE kind = 'doi'"
        )
    with pytest.raises(ResearchIntegrityError, match="last audited state"):
        store.list_records()

    source_db = tmp_path / "tampered-source.sqlite3"
    source_store = ResearchStore(source_db)
    source_store.add_candidate(paper, topic_id="agent-evaluation")
    with sqlite3.connect(source_db) as connection:
        connection.execute(
            "UPDATE research_source_checks SET paper_json = '{}' WHERE paper_key = ?",
            (paper.paper_key,),
        )
    with pytest.raises(ResearchIntegrityError, match="source check digest"):
        source_store.audit_report()
