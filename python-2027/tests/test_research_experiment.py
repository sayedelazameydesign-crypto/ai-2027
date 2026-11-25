"""Tests for the isolated experiment runner (``ai2027_protocol.research_experiment``)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from ai2027_protocol.research_contracts import (
    ResearchPaper,
    ResearchSource,
    ResearchStatus,
)
from ai2027_protocol.research_experiment import (
    ExperimentRunner,
    ExperimentSpec,
    Hypothesis,
    VerificationThresholds,
)
from ai2027_protocol.research_store import ResearchStore

SUCCESS_CODE = (
    "import json\n"
    'measurements = {"accuracy": 0.92, "peak_memory_bytes": 4096.0}\n'
    'edge_cases = {"empty_input": True, "large_input": True}\n'
    'print(json.dumps({"measurements": measurements, "edge_cases": edge_cases}))\n'
)
LOW_ACCURACY_CODE = (
    "import json\n"
    'print(json.dumps({"measurements": {"accuracy": 0.1, "peak_memory_bytes": 100.0},\n'
    '                  "edge_cases": {"empty_input": True, "large_input": True}}))\n'
)
EDGE_MISMATCH_CODE = (
    "import json\n"
    'print(json.dumps({"measurements": {"accuracy": 0.9, "peak_memory_bytes": 100.0},\n'
    '                  "edge_cases": {"empty_input": True}}))\n'
)
CRASH_CODE = 'raise RuntimeError("experiment failed before reporting")\n'
TIMEOUT_CODE = "import time\ntime.sleep(3.0)\n"


def _hypothesis() -> Hypothesis:
    """Return a bounded, falsifiable hypothesis for the test specs."""
    return Hypothesis(
        statement="A local held-out task suite measures agent correctness.",
        falsification_criterion="Accuracy below 0.5 falsifies the claim.",
        expected_measurements={"accuracy": 0.9},
    )


def _thresholds() -> VerificationThresholds:
    """Return acceptance bounds the test experiments are measured against."""
    return VerificationThresholds(
        min_accuracy=0.5, max_latency_ms=2000.0, max_peak_memory_bytes=1_000_000.0
    )


def _spec(
    *,
    code: str = SUCCESS_CODE,
    edge_cases: tuple[str, ...] = ("empty_input", "large_input"),
    timeout_s: float = 5.0,
) -> ExperimentSpec:
    """Return a valid experiment spec with swappable code and bounds."""
    return ExperimentSpec(
        hypothesis=_hypothesis(),
        code=code,
        environment="Python subprocess -I -B, sanitized env, no secrets.",
        timeout_s=timeout_s,
        thresholds=_thresholds(),
        edge_cases=edge_cases,
    )


def _make_paper() -> ResearchPaper:
    """Return source-bound paper metadata for the integration test."""
    arxiv_id = "2401.01234v2"
    doi = "10.1234/research.paper"
    return ResearchPaper.from_verified_source(
        paper_key=f"doi:{doi}",
        source=ResearchSource.ARXIV,
        source_id=arxiv_id,
        arxiv_id=arxiv_id,
        doi=doi,
        title="Robust Agent Evaluation in Local Systems",
        abstract="We measure an evaluation framework for local engineering agents.",
        published_at=date(2025, 1, 10),
        categories=("cs.AI",),
        authors=("Example Researcher",),
        source_url=f"https://arxiv.org/abs/{arxiv_id}",
        fetched_at=datetime(2026, 11, 19, tzinfo=UTC),
    )


class TestHypothesis:
    def test_accepts_a_bounded_hypothesis(self) -> None:
        hypothesis = _hypothesis()
        assert hypothesis.expected_measurements["accuracy"] == 0.9

    def test_rejects_non_finite_measurement(self) -> None:
        with pytest.raises(ValueError):
            Hypothesis(
                statement="A local held-out task suite measures agent correctness.",
                falsification_criterion="Accuracy below 0.5 falsifies the claim.",
                expected_measurements={"accuracy": float("nan")},
            )

    def test_rejects_unbounded_measurement_name(self) -> None:
        with pytest.raises(ValueError):
            Hypothesis(
                statement="A local held-out task suite measures agent correctness.",
                falsification_criterion="Accuracy below 0.5 falsifies the claim.",
                expected_measurements={"Not A Valid Name": 0.9},
            )


class TestExperimentSpec:
    def test_experiment_label_uses_hypothesis(self) -> None:
        assert _spec().experiment_label == _hypothesis().statement

    def test_rejects_unbounded_edge_case_name(self) -> None:
        with pytest.raises(ValueError):
            _spec(edge_cases=("bad name",))


class TestRunner:
    async def test_passing_experiment_passes_all_gates(self) -> None:
        evidence = await ExperimentRunner().run(_spec())
        assert evidence.gates.all_passed is True
        assert evidence.measurements["accuracy"] == pytest.approx(0.92)
        assert evidence.measurements["peak_memory_bytes"] == pytest.approx(4096.0)

    async def test_low_accuracy_fails_the_accuracy_gate(self) -> None:
        evidence = await ExperimentRunner().run(_spec(code=LOW_ACCURACY_CODE))
        assert evidence.gates.accuracy_passed is False
        assert evidence.gates.all_passed is False

    async def test_edge_case_mismatch_fails_the_edge_gate(self) -> None:
        evidence = await ExperimentRunner().run(_spec(code=EDGE_MISMATCH_CODE))
        assert evidence.gates.edge_cases_passed is False
        assert evidence.gates.all_passed is False

    async def test_crashing_experiment_yields_failing_evidence(self) -> None:
        evidence = await ExperimentRunner().run(_spec(code=CRASH_CODE))
        assert evidence.gates.all_passed is False
        assert len(evidence.artifact_sha256) == 64

    async def test_timeout_yields_failing_evidence(self) -> None:
        evidence = await ExperimentRunner().run(_spec(code=TIMEOUT_CODE, timeout_s=0.3))
        assert evidence.gates.all_passed is False
        assert evidence.measurements["latency_ms"] >= 0.0

    async def test_artifact_hash_is_reproducible(self) -> None:
        first = await ExperimentRunner().run(_spec())
        second = await ExperimentRunner().run(_spec())
        assert first.artifact_sha256 == second.artifact_sha256

    def test_run_sync_produces_evidence(self) -> None:
        evidence = ExperimentRunner().run_sync(_spec())
        assert evidence.gates.all_passed is True


class TestIntegrationWithStore:
    async def test_experiment_evidence_persists_via_human_decision(self, tmp_path: Path) -> None:
        store = ResearchStore(tmp_path / "research.sqlite")
        paper = _make_paper()
        store.add_candidate(paper, topic_id="agent-evaluation")

        # Running an experiment does not adopt anything by itself.
        evidence = await ExperimentRunner().run(_spec())
        assert store.get_by_key(paper.paper_key).status is ResearchStatus.CANDIDATE

        # A human decision persists the evidence through the existing ledger.
        record = store.decide(
            paper.paper_key,
            status=ResearchStatus.TESTED,
            evidence=evidence,
            actor="human",
        )
        assert record.status is ResearchStatus.TESTED
        assert record.test_evidence is not None
        assert record.test_evidence.artifact_sha256 == evidence.artifact_sha256

        report = store.audit_report()
        assert report["integrity_valid"] is True

        # The evidence survives a restart (NO PERSISTENCE = NO COMPLETION).
        reopened = ResearchStore(tmp_path / "research.sqlite")
        again = reopened.get_by_key(paper.paper_key)
        assert again.status is ResearchStatus.TESTED
        assert again.test_evidence is not None
        assert again.test_evidence.artifact_sha256 == evidence.artifact_sha256

    async def test_failing_evidence_cannot_be_used_to_adopt(self, tmp_path: Path) -> None:
        store = ResearchStore(tmp_path / "research.sqlite")
        paper = _make_paper()
        store.add_candidate(paper, topic_id="agent-evaluation")
        evidence = await ExperimentRunner().run(_spec(code=CRASH_CODE))

        from ai2027_protocol.research_store_errors import ResearchTransitionError

        with pytest.raises(ResearchTransitionError):
            store.decide(
                paper.paper_key,
                status=ResearchStatus.TESTED,
                evidence=evidence,
                actor="human",
            )
