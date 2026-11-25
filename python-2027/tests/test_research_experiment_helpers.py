"""Host-side helper coverage for the confined experiment runner.

These tests exercise the measurement, parsing, and cleanup helpers directly so
the runner's host-owned guarantees stay covered. They live in their own module
so each test file stays under the project's per-file LOC gate.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import cast

import pytest

import ai2027_protocol.research_experiment as rexp
from ai2027_protocol.research_experiment import (
    EvaluationSpec,
    ExperimentRunner,
    _accuracy_from_predictions,
    _close_pipes,
    _create_cgroup,
    _edge_cases_pass,
    _kill_group,
    _make_preexec,
    _move_to_cgroup,
    _parse_result,
    _peak_memory_bytes,
    _read_cgroup_peak,
    _reap,
    _remove_cgroup,
)
from test_research_experiment import SUCCESS_CODE, _spec


class TestRunnerHelpers:
    """Cover the host-side helper functions and fail-closed edges directly.

    These exercise the measurement, parsing, and cleanup helpers so the
    runner's host-owned guarantees stay covered regardless of which Python
    version the suite runs on (the coverage margin is thin, so version
    variation in hypothesis examples must not drop it below the gate).
    """

    def test_evaluation_spec_rejects_mismatched_lengths(self) -> None:
        with pytest.raises(ValueError):
            EvaluationSpec(inputs=(1, 2, 3), reference_results=(1, 2))

    def test_cgroup_helpers_round_trip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Point the cgroup root at a writable temp dir to exercise the real
        # create/move/read/remove path (on hosts that deny cgroup creation the
        # runner falls back, but the helpers themselves are tested here).
        monkeypatch.setattr(rexp, "_CGROUP_ROOT", str(tmp_path))
        path = _create_cgroup()
        assert path is not None
        assert path.startswith(str(tmp_path))
        # Write a fake memory.peak and read it back.
        (Path(path) / "memory.peak").write_text("123456", encoding="utf-8")
        assert _read_cgroup_peak(path) == 123456.0
        # Move the current process in (best-effort). Remove the probe files so
        # the subtree is empty and rmdir can reclaim it (real cgroupfs files
        # are not regular files and do not block removal).
        _move_to_cgroup(os.getpid(), path)
        (Path(path) / "memory.peak").unlink()
        (Path(path) / "cgroup.procs").unlink()
        _remove_cgroup(path)
        assert not Path(path).exists()

    def test_cgroup_helpers_tolerate_missing_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(rexp, "_CGROUP_ROOT", "/nonexistent/cgroup/root")
        assert _create_cgroup() is None
        # Read/remove on a path that does not exist must not raise.
        assert _read_cgroup_peak("/nonexistent/cgroup/x") is None
        _remove_cgroup("/nonexistent/cgroup/x")

    def test_make_preexec_returns_callable(self, tmp_path: Path) -> None:
        without_cgroup = _make_preexec(2, None)
        assert callable(without_cgroup)
        with_cgroup = _make_preexec(2, str(tmp_path))
        assert callable(with_cgroup)

    def test_peak_memory_bytes_falls_back_without_cgroup(self) -> None:
        # No cgroup path: the getrusage fallback is used (>= 0 on Linux).
        assert _peak_memory_bytes(None) >= 0.0

    def test_close_pipes_is_exception_safe(self) -> None:
        proc = _FakeProc()
        _close_pipes(cast(asyncio.subprocess.Process, proc))  # must not raise
        assert proc.stdout._transport.closed
        assert proc.stderr._transport.closed
        assert proc._transport.closed

    def test_reap_returns_returncode(self) -> None:
        proc = _FakeProc(returncode=0)

        assert asyncio.run(_reap(cast(asyncio.subprocess.Process, proc))) == 0

    def test_reap_falls_back_when_wait_raises(self) -> None:
        proc = _FakeProc(returncode=-9, wait_raises=True)

        # Even if wait() raises, _reap must not propagate.
        assert asyncio.run(_reap(cast(asyncio.subprocess.Process, proc))) == -9

    def test_kill_group_falls_back_to_proc_kill(self) -> None:
        proc = _FakeProc()
        # An already-gone pgid makes killpg raise; _kill_group must fall back
        # to proc.kill() and still close the pipes.
        _kill_group(cast(asyncio.subprocess.Process, proc), pgid=999_999_999)
        assert proc.killed
        assert proc.stdout._transport.closed

    def test_parse_result_handles_non_dict_and_garbage(self) -> None:
        assert _parse_result("[1, 2, 3]") is None
        assert _parse_result("not json at all") is None
        assert _parse_result("") is None
        # A valid dict embedded among other lines is still extracted.
        line = "noise\n" + '{"predictions": [1], "edge_cases": {"empty_input": {"passed": true}}}'
        parsed = _parse_result(line)
        assert parsed is not None and parsed["predictions"] == [1]

    def test_accuracy_from_predictions_edge_cases(self) -> None:
        ref = (1, 2, 3)
        # Non-list predictions count as zero.
        assert _accuracy_from_predictions("nope", ref) == 0.0
        # Short predictions: missing entries are incorrect.
        assert _accuracy_from_predictions([1], ref) == pytest.approx(1 / 3)

        # A comparison that raises is treated as incorrect, never trusted.
        class _Exploding:
            def __eq__(self, _other: object) -> bool:
                raise RuntimeError("boom")

        assert _accuracy_from_predictions([_Exploding()], (object(),)) == 0.0

    def test_edge_cases_pass_rejects_non_dict(self) -> None:
        spec = _spec(edge_cases=("empty_input",))
        assert _edge_cases_pass(spec, "not-a-dict") is False
        assert _edge_cases_pass(spec, {"empty_input": {"passed": True}}) is True

    async def test_run_fails_closed_on_internal_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # If driving the subprocess raises unexpectedly (a transport/reader
        # edge on a newer interpreter), run() must return failed evidence
        # rather than propagating the exception.
        def _boom(_text: str) -> None:
            raise RuntimeError("internal runner error")

        monkeypatch.setattr(rexp, "_parse_result", _boom)
        evidence = await ExperimentRunner().run(_spec(code=SUCCESS_CODE))
        assert evidence.gates.all_passed is False
        assert len(evidence.artifact_sha256) == 64


class _FakeTransport:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeStream:
    def __init__(self) -> None:
        self._transport = _FakeTransport()


class _FakeProc:
    """Minimal stand-in for asyncio.subprocess.Process for helper tests."""

    def __init__(self, returncode: int = 0, *, wait_raises: bool = False) -> None:
        self.stdout = _FakeStream()
        self.stderr = _FakeStream()
        self._transport = _FakeTransport()
        self.returncode = returncode
        self.killed = False
        self.pid = 4242
        self._wait_raises = wait_raises

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        if self._wait_raises:
            raise RuntimeError("wait boom")
        return self.returncode
