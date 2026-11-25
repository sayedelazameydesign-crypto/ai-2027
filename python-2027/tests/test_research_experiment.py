"""Tests for the confined experiment runner (``ai2027_protocol.research_experiment``).

These tests exercise the real execution path: a subprocess is spawned under
resource caps, its output is streamed to a hard cap, and accuracy/latency/
memory are measured from the host rather than trusted from the child's own
report. Adversarial cases verify that output floods, memory/CPU blowups, and
child-process leaks are bounded and terminate.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from ai2027_protocol.research_contracts import (
    ResearchPaper,
    ResearchSource,
    ResearchStatus,
)
from ai2027_protocol.research_experiment import (
    EvaluationSpec,
    ExperimentRunner,
    ExperimentSpec,
    Hypothesis,
    VerificationThresholds,
)
from ai2027_protocol.research_store import ResearchStore

SUCCESS_CODE = (
    "import json\n"
    "inputs = json.load(open('inputs.json'))\n"
    "print(json.dumps({'predictions': inputs,\n"
    "                  'edge_cases': {'empty_input': {'passed': True},\n"
    "                               'large_input': {'passed': True}}}))\n"
)
LOW_ACCURACY_CODE = (
    "import json\n"
    "inputs = json.load(open('inputs.json'))\n"
    "preds = [inputs[0]] + [0] * (len(inputs) - 1)\n"
    "print(json.dumps({'predictions': preds,\n"
    "                  'edge_cases': {'empty_input': {'passed': True},\n"
    "                               'large_input': {'passed': True}}}))\n"
)
EDGE_MISMATCH_CODE = (
    "import json\n"
    "inputs = json.load(open('inputs.json'))\n"
    "print(json.dumps({'predictions': inputs,\n"
    "                  'edge_cases': {'empty_input': {'passed': True}}}))\n"
)
EDGE_FAILED_CODE = (
    "import json\n"
    "inputs = json.load(open('inputs.json'))\n"
    "print(json.dumps({'predictions': inputs,\n"
    "                  'edge_cases': {'empty_input': {'passed': False},\n"
    "                               'large_input': {'passed': True}}}))\n"
)
NO_RESULT_CODE = 'raise RuntimeError("experiment failed before reporting")\n'
TIMEOUT_CODE = "import time\ntime.sleep(3.0)\n"
# Output flood: far more than the 20 KB streaming cap.
FLOOD_CODE = "print('x' * 10_000_000)\n"
# Memory blowup above the 512 MB RLIMIT_AS.
MEMORY_BLOWUP_CODE = "x = bytearray(1024 * 1024 * 1024)\nprint('done')\n"
# Infinite loop: exceeds RLIMIT_CPU / wall-clock timeout.
CPU_BOMB_CODE = "while True:\n    pass\n"
# Child-process leak: spawns a background process that keeps running.
CHILD_LEAK_CODE = (
    "import subprocess, json\n"
    "child = subprocess.Popen(['sleep', '30'],\n"
    "                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
    "with open('child_pid.txt', 'w') as fh:\n"
    "    fh.write(str(child.pid))\n"
    "inputs = json.load(open('inputs.json'))\n"
    "print(json.dumps({'predictions': inputs,\n"
    "                  'edge_cases': {'empty_input': {'passed': True}}}))\n"
)
# File-access probe: writes outside the sandbox dir (allowed by subprocess, but
# bounded and not privileged). We assert the cap still terminates it.
FILE_WRITE_CODE = (
    "import json\n"
    "open('/tmp/research_experiment_probe.txt', 'w').write('x')\n"
    "inputs = json.load(open('inputs.json'))\n"
    "print(json.dumps({'predictions': inputs,\n"
    "                  'edge_cases': {'empty_input': {'passed': True}}}))\n"
)
# Malicious "measurements" object: self-declared accuracy must be ignored.
FORGED_SELF_CERT_CODE = (
    "import json\n"
    "inputs = json.load(open('inputs.json'))\n"
    "preds = [0] * len(inputs)\n"
    "print(json.dumps({'predictions': preds, 'accuracy': 1.0, 'passed': True,\n"
    "                  'edge_cases': {'empty_input': {'passed': True}}}))\n"
)
# No predictions key: the host must fall back to zero accuracy (fail-closed).
NO_PREDICTIONS_CODE = (
    "import json\nprint(json.dumps({'edge_cases': {'empty_input': {'passed': True}}}))\n"
)


def _hypothesis() -> Hypothesis:
    """Return a bounded, falsifiable hypothesis for the test specs."""
    return Hypothesis(
        statement="A local held-out task suite measures agent correctness.",
        falsification_criterion="Accuracy below 0.5 falsifies the claim.",
        expected_measurements={"accuracy": 0.9},
    )


def _thresholds(*, mem_cap: float = 256_000_000.0) -> VerificationThresholds:
    """Return acceptance bounds the test experiments are measured against.

    The default memory cap is generous because the host-measured peak RSS
    includes the Python interpreter itself, not just the experiment's data.
    """
    return VerificationThresholds(
        min_accuracy=0.5, max_latency_ms=2000.0, max_peak_memory_bytes=mem_cap
    )


#: Host-owned test inputs and reference results shared by the test specs.
_EVAL_INPUTS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
_EVAL_REFERENCE = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)


def _evaluation() -> EvaluationSpec:
    """Return host-owned inputs and matching reference results."""
    return EvaluationSpec(inputs=_EVAL_INPUTS, reference_results=_EVAL_REFERENCE)


def _spec(
    *,
    code: str = SUCCESS_CODE,
    edge_cases: tuple[str, ...] = ("empty_input", "large_input"),
    timeout_s: float = 5.0,
    thresholds: VerificationThresholds | None = None,
) -> ExperimentSpec:
    """Return a valid experiment spec with swappable code and bounds."""
    return ExperimentSpec(
        hypothesis=_hypothesis(),
        code=code,
        environment="Python subprocess -I -B, sanitized env, resource-capped.",
        timeout_s=timeout_s,
        thresholds=thresholds or _thresholds(),
        evaluation=_evaluation(),
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

    def test_full_spec_fingerprint_differs_when_any_field_changes(self) -> None:
        from ai2027_protocol.research_experiment import _artifact_hash

        base = _spec()
        assert _artifact_hash(base) == _artifact_hash(_spec())
        # Changing only the hypothesis statement changes the fingerprint.
        changed_hypothesis = ExperimentSpec(
            hypothesis=Hypothesis(
                statement="A different statement entirely.",
                falsification_criterion="Accuracy below 0.5 falsifies the claim.",
                expected_measurements={"accuracy": 0.9},
            ),
            code=SUCCESS_CODE,
            environment="Python subprocess -I -B, sanitized env, resource-capped.",
            timeout_s=5.0,
            thresholds=_thresholds(),
            evaluation=_evaluation(),
            edge_cases=("empty_input", "large_input"),
        )
        assert _artifact_hash(base) != _artifact_hash(changed_hypothesis)
        # Changing thresholds also changes the fingerprint.
        changed_thresholds = ExperimentSpec(
            hypothesis=_hypothesis(),
            code=SUCCESS_CODE,
            environment="Python subprocess -I -B, sanitized env, resource-capped.",
            timeout_s=5.0,
            thresholds=_thresholds(mem_cap=2_000_000.0),
            evaluation=_evaluation(),
            edge_cases=("empty_input", "large_input"),
        )
        assert _artifact_hash(base) != _artifact_hash(changed_thresholds)


class TestRunnerMeasurementIndependence:
    async def test_accuracy_is_computed_from_predictions_on_host(self) -> None:
        # The experiment echoes the inputs as predictions, all matching the
        # host-owned reference results, so the host computes full accuracy.
        evidence = await ExperimentRunner().run(_spec())
        assert evidence.gates.accuracy_passed is True
        assert evidence.measurements["accuracy"] == pytest.approx(1.0)

    async def test_self_declared_success_fields_are_ignored(self) -> None:
        # The child claims accuracy 1.0 / passed True, but its predictions are
        # all wrong. The host must ignore the self-cert and compute 0.0 from the
        # predictions vs the host-owned reference results.
        evidence = await ExperimentRunner().run(_spec(code=FORGED_SELF_CERT_CODE))
        assert evidence.measurements["accuracy"] == pytest.approx(0.0)
        # The gate reflects the host-computed value, not the child's self-cert.
        assert evidence.gates.accuracy_passed is False

    async def test_low_accuracy_fails_the_accuracy_gate(self) -> None:
        evidence = await ExperimentRunner().run(_spec(code=LOW_ACCURACY_CODE))
        assert evidence.gates.accuracy_passed is False
        assert evidence.measurements["accuracy"] == pytest.approx(0.10)
        assert evidence.gates.all_passed is False

    async def test_missing_predictions_yield_zero_accuracy(self) -> None:
        # A child that omits predictions cannot inflate accuracy; fail-closed.
        evidence = await ExperimentRunner().run(_spec(code=NO_PREDICTIONS_CODE))
        assert evidence.measurements["accuracy"] == pytest.approx(0.0)
        assert evidence.gates.accuracy_passed is False

    async def test_latency_and_memory_are_measured_on_host(self) -> None:
        evidence = await ExperimentRunner().run(_spec())
        assert evidence.measurements["latency_ms"] >= 0.0
        # Peak RSS is a real host measurement (non-zero on POSIX).
        assert evidence.measurements["peak_memory_bytes"] > 0.0

    async def test_edge_case_mismatch_fails_the_edge_gate(self) -> None:
        evidence = await ExperimentRunner().run(_spec(code=EDGE_MISMATCH_CODE))
        assert evidence.gates.edge_cases_passed is False

    async def test_edge_case_reported_failed_fails_the_edge_gate(self) -> None:
        evidence = await ExperimentRunner().run(_spec(code=EDGE_FAILED_CODE))
        assert evidence.gates.edge_cases_passed is False
        assert evidence.gates.all_passed is False


class TestRunnerExecution:
    async def test_passing_experiment_passes_all_gates(self) -> None:
        evidence = await ExperimentRunner().run(_spec())
        assert evidence.gates.all_passed is True
        assert len(evidence.artifact_sha256) == 64

    async def test_crashing_experiment_yields_failing_evidence(self) -> None:
        evidence = await ExperimentRunner().run(_spec(code=NO_RESULT_CODE))
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

    def test_uses_persistent_sandbox_dir(self, tmp_path: Path) -> None:
        evidence = ExperimentRunner(sandbox_dir=tmp_path).run_sync(_spec())
        assert evidence.gates.all_passed is True
        assert (tmp_path / "experiment.py").is_file()


class TestRunnerAdversarial:
    """Verify the execution boundary: floods, blowups, and leaks are bounded."""

    async def test_output_flood_is_bounded_and_fails(self) -> None:
        evidence = await ExperimentRunner().run(_spec(code=FLOOD_CODE, timeout_s=5.0))
        # A flood that exceeds the streaming cap must not pass.
        assert evidence.gates.all_passed is False

    async def test_overflow_kill_does_not_hit_unrelated_group(self) -> None:
        # When the experiment floods output past the cap, the runner kills the
        # experiment's process group. An unrelated process living in a different
        # process group must survive: this guards the captured-pgid kill against
        # a TOCTOU that could otherwise hit an unrelated group.
        import shutil

        sleep_bin = shutil.which("sleep")
        assert sleep_bin is not None
        sentinel = await asyncio.create_subprocess_exec(
            sleep_bin,
            "30",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        sentinel_pid = sentinel.pid
        try:
            evidence = await ExperimentRunner().run(_spec(code=FLOOD_CODE, timeout_s=5.0))
            assert evidence.gates.all_passed is False
            await asyncio.sleep(0.2)
            assert self._pid_running(sentinel_pid), (
                "unrelated process group was killed by the overflow kill"
            )
        finally:
            sentinel.kill()
            await sentinel.wait()

    async def test_memory_blowup_exceeding_limit_fails(self) -> None:
        evidence = await ExperimentRunner().run(
            _spec(
                code=MEMORY_BLOWUP_CODE,
                timeout_s=5.0,
                thresholds=_thresholds(mem_cap=1_000_000_000.0),
            )
        )
        assert evidence.gates.all_passed is False

    async def test_cpu_bomb_terminates_and_fails(self) -> None:
        evidence = await ExperimentRunner().run(_spec(code=CPU_BOMB_CODE, timeout_s=1.0))
        assert evidence.gates.all_passed is False

    async def test_child_process_leak_is_reaped(self, tmp_path: Path) -> None:
        # The child spawns a background process then exits cleanly. The runner
        # must terminate and, before returning, reap every descendant: the
        # spawned child's PID must be gone by the time run() resolves.
        spec = _spec(code=CHILD_LEAK_CODE, timeout_s=5.0, edge_cases=("empty_input",))
        evidence = await ExperimentRunner(sandbox_dir=tmp_path).run(spec)
        assert evidence.measurements["latency_ms"] >= 0.0
        assert len(evidence.artifact_sha256) == 64
        # The experiment recorded its spawned child's PID; verify it is gone.
        # A killed process may briefly remain as a zombie, so poll until the
        # PID is fully removed rather than checking once.
        child_pid = int((tmp_path / "child_pid.txt").read_text().strip())
        gone = await self._wait_pid_gone(child_pid, budget=3.0)
        assert gone, f"background child PID {child_pid} still alive after run()"

    @staticmethod
    async def _wait_pid_gone(pid: int, *, budget: float = 3.0) -> bool:
        """Return True once PID ``pid`` no longer exists (zombies count as gone)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + budget
        while loop.time() < deadline:
            if not TestRunnerAdversarial._pid_running(pid):
                return True
            await asyncio.sleep(0.1)
        return not TestRunnerAdversarial._pid_running(pid)

    @staticmethod
    def _pid_running(pid: int) -> bool:
        """Return True if PID ``pid`` exists and is not a (terminated) zombie."""
        import os

        # /proc is authoritative on Linux: a zombie still has a PID but has
        # terminated, so it must count as gone.
        stat_path = f"/proc/{pid}/stat"
        try:
            with open(stat_path, encoding="utf-8") as fh:
                fields = fh.read().rsplit(")", 1)[-1].split()
                # State is the field right after the closing paren of comm.
                return fields[0] not in ("Z", "X", "x")
        except (OSError, IndexError):
            # No /proc (non-Linux) or process already reaped -> gone.
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return False
            except PermissionError:
                return True
            return True

    async def test_timeout_kills_grandchild_holding_pipe(self) -> None:
        # A grandchild inheriting the runner's stdout pipe keeps it open, so the
        # reader never sees EOF. The timeout path must kill the whole live group
        # (parent + grandchild) and return bounded evidence, not hang.
        inherit_code = (
            "import subprocess, sys, json\n"
            "subprocess.Popen([sys.executable, '-I', '-B', '-c', 'import time; time.sleep(30)'])\n"
            "print(json.dumps({'raw_measurements': {'correct': 1, 'total': 1},\n"
            "                  'edge_cases': {'empty_input': {'passed': True}}}))\n"
        )
        spec = _spec(code=inherit_code, timeout_s=1.0, edge_cases=("empty_input",))
        t0 = time.perf_counter()
        evidence = await ExperimentRunner().run(spec)
        elapsed = time.perf_counter() - t0
        assert evidence.gates.all_passed is False
        assert elapsed < spec.timeout_s + 2.0  # terminated near the timeout, not 30s

    async def test_file_write_outside_sandbox_is_bounded(self) -> None:
        # The subprocess is confined by resource caps but is NOT a security
        # sandbox: file writes are possible. This test asserts the run still
        # terminates and produces evidence (it does not claim isolation).
        spec = _spec(code=FILE_WRITE_CODE, timeout_s=5.0, edge_cases=("empty_input",))
        evidence = await ExperimentRunner().run(spec)
        assert evidence.gates.all_passed is True
        assert evidence.measurements["latency_ms"] >= 0.0


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
            proof="test-proof",
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

    async def test_failed_experiment_result_is_persisted_durably(self, tmp_path: Path) -> None:
        """A failed experiment is durable data: recorded without adopting anything."""
        store = ResearchStore(tmp_path / "research.sqlite")
        paper = _make_paper()
        store.add_candidate(paper, topic_id="agent-evaluation")

        failed = await ExperimentRunner().run(_spec(code=NO_RESULT_CODE))
        assert failed.gates.all_passed is False
        # The paper stays a candidate; the failed result is still recorded.
        assert store.get_by_key(paper.paper_key).status is ResearchStatus.CANDIDATE
        store.record_experiment_result(paper.paper_key, failed)

        stored = store.list_experiment_results(paper.paper_key)
        assert len(stored) == 1
        assert stored[0]["artifact_sha256"] == failed.artifact_sha256
        assert stored[0]["all_passed"] is False

        # Persistence survives a restart (NO PERSISTENCE = NO COMPLETION).
        reopened = ResearchStore(tmp_path / "research.sqlite")
        again = reopened.list_experiment_results(paper.paper_key)
        assert len(again) == 1
        assert again[0]["artifact_sha256"] == failed.artifact_sha256

    async def test_failing_evidence_cannot_be_used_to_adopt(self, tmp_path: Path) -> None:
        from ai2027_protocol.research_store_errors import ResearchTransitionError

        store = ResearchStore(tmp_path / "research.sqlite")
        paper = _make_paper()
        store.add_candidate(paper, topic_id="agent-evaluation")
        evidence = await ExperimentRunner().run(_spec(code=NO_RESULT_CODE))
        assert evidence.gates.all_passed is False

        with pytest.raises(ResearchTransitionError):
            store.decide(
                paper.paper_key,
                status=ResearchStatus.TESTED,
                evidence=evidence,
                actor="human",
                proof="test-proof",
            )
