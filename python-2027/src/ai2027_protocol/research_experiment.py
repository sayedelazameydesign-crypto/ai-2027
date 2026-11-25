"""Confined, reproducible experiment execution for the Research Radar (P4).

The research stack already verifies paper metadata, grounds every evaluation
quote in the fetched source text, and keeps a tamper-evident audit ledger. This
module adds the missing piece: a bounded runner that *executes* the experiment a
candidate paper implies and returns measured :class:`~.research_contracts.TestEvidence`
for the existing human-reviewed decision path.

Honest scope of the isolation
-----------------------------
This is a **confined subprocess, not a security sandbox**. The runner:

* bounds **output** by streaming (it never accumulates an unbounded buffer),
* bounds **resources** with ``RLIMIT_CPU`` / ``RLIMIT_AS`` / ``RLIMIT_FSIZE`` /
  ``RLIMIT_NPROC`` on POSIX hosts,
* terminates the **entire process group** on timeout, so children do not leak,
* measures **latency and peak memory from the host**, not from the experiment.

It does **not** block file reads, network access, or arbitrary syscalls. A
determined adversary can still escape; the guarantee is resource confinement
plus reproducible measurement, not OS-level isolation. Treat ``TestEvidence``
as evidence of a *confined run*, not as a security certification.

Measurement independence
------------------------
* ``latency_ms`` and ``peak_memory_bytes`` are measured **on the host** and
  cannot be faked by the experiment.
* ``accuracy`` is **computed on the host** by comparing the experiment's
  **predictions** against host-owned **reference results**. The experiment
  receives only the test ``inputs``; it never sees the reference results, so it
  cannot fabricate the metric. A self-declared ``accuracy``/``passed`` value is
  ignored.
* ``edge_cases`` remain **experiment-reported**; full independence for those
  requires a trusted external evaluator, which is out of scope here.

Rules enforced
--------------
* **No automatic adoption.** The runner emits ``TestEvidence`` only; adoption
  stays a human call through ``ResearchStore.decide``.
* **Reproducibility.** The artifact hash covers the **full spec** (hypothesis,
  code, environment, thresholds, edge cases).
* **Failure is data.** A crashing, timing-out, or malformed experiment yields a
  failing ``TestEvidence`` rather than raising.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import signal
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, model_validator

from .contracts import StrictModel
from .research_contracts import TestEvidence, VerificationGates
from .security import redact_secrets

try:  # POSIX-only resource limits; absent on Windows.
    import resource as _resource

    _HAS_RESOURCE = True
except ImportError:  # pragma: no cover - Windows has no resource module
    _resource = None  # type: ignore[assignment]
    _HAS_RESOURCE = False

#: Cap on captured experiment output; enforced while streaming, not after.
_MAX_OUTPUT_BYTES = 20_000
#: Hard resource caps applied to the child process on POSIX hosts.
_MAX_MEMORY_BYTES = 512 * 1024 * 1024
_MAX_FILE_BYTES = 10 * 1024 * 1024
_MAX_PROCESSES = 16
#: Bounded identifier for measurement / edge-case names.
_NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


class Hypothesis(StrictModel):
    """A falsifiable claim a paper motivates, kept separate from engineering inference."""

    statement: str = Field(min_length=8, max_length=2000)
    falsification_criterion: str = Field(min_length=8, max_length=2000)
    expected_measurements: dict[str, float] = Field(min_length=1, max_length=12)

    @field_validator("expected_measurements")
    @classmethod
    def _finite(cls, value: dict[str, float]) -> dict[str, float]:
        """Reject non-finite or unbounded measurement names."""
        if any(not math.isfinite(item) or not _NAME.fullmatch(key) for key, item in value.items()):
            raise ValueError("Expected measurement keys and values must be finite and bounded.")
        return value


class VerificationThresholds(StrictModel):
    """Acceptance bounds the runner checks the measured gates against."""

    min_accuracy: float = Field(ge=0.0, le=1.0)
    max_latency_ms: float = Field(gt=0.0, le=600_000.0)
    max_peak_memory_bytes: float = Field(gt=0.0, le=1_000_000_000.0)


class EvaluationSpec(StrictModel):
    """Host-owned test inputs and ground-truth reference results.

    The host owns both the inputs and the expected (reference) results. The
    experiment supplies only **predictions** for each input; the host performs
    the comparison and computes accuracy. This keeps the accuracy metric
    independent of the tested code: a child cannot fabricate the metric because
    it never sees the reference results, only the inputs.
    """

    inputs: tuple[Any, ...] = Field(min_length=1, max_length=100)
    reference_results: tuple[Any, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _lengths_match(self) -> EvaluationSpec:
        """Require one reference result per input."""
        if len(self.inputs) != len(self.reference_results):
            raise ValueError("inputs and reference_results must have the same length.")
        return self


class ExperimentSpec(StrictModel):
    """A bounded, reproducible experiment derived from a hypothesis and a paper."""

    hypothesis: Hypothesis
    code: str = Field(min_length=8, max_length=20_000)
    environment: str = Field(min_length=3, max_length=300)
    timeout_s: float = Field(default=5.0, ge=0.2, le=10.0)
    thresholds: VerificationThresholds
    evaluation: EvaluationSpec
    edge_cases: tuple[str, ...] = Field(min_length=1, max_length=12)

    @field_validator("edge_cases")
    @classmethod
    def _edge_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Keep edge-case names bounded identifiers for reproducible comparison."""
        clean = tuple(dict.fromkeys(value))
        if any(not _NAME.fullmatch(item) for item in clean):
            raise ValueError("Edge case names must be bounded identifiers.")
        return clean

    @property
    def experiment_label(self) -> str:
        """Return a bounded label for the produced evidence."""
        return self.hypothesis.statement[:1000]


def _canonical_spec(spec: ExperimentSpec) -> dict[str, object]:
    """Serialize the full spec for a content-addressed, reproducible fingerprint."""
    return {
        "hypothesis": spec.hypothesis.model_dump(mode="json"),
        "code": spec.code,
        "environment": spec.environment,
        "timeout_s": spec.timeout_s,
        "thresholds": spec.thresholds.model_dump(mode="json"),
        "evaluation": spec.evaluation.model_dump(mode="json"),
        "edge_cases": list(spec.edge_cases),
    }


def _artifact_hash(spec: ExperimentSpec) -> str:
    """Hash the entire spec, so any change to conditions changes the fingerprint."""
    payload = json.dumps(
        _canonical_spec(spec),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_env() -> dict[str, str]:
    """Return a minimal environment with no secrets for the confined subprocess."""
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


#: cgroup v2 mount point; used for whole-tree peak-memory measurement when the
#: host permits creating a per-experiment cgroup subtree.
_CGROUP_ROOT = "/sys/fs/cgroup"


def _create_cgroup() -> str | None:
    """Create a fresh cgroup v2 subtree for one experiment, or ``None`` if not permitted.

    cgroup v2 ``memory.peak`` measures the **cumulative peak** of every process
    in the cgroup, including grandchildren, which ``getrusage(RUSAGE_CHILDREN)``
    cannot provide. We attempt a real creation and fall back honestly when the
    host denies cgroup-create permission.
    """
    if not os.path.isdir(_CGROUP_ROOT):
        return None
    name = f"ai2027-research-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    path = os.path.join(_CGROUP_ROOT, name)
    try:
        os.mkdir(path)
    except OSError:
        return None
    return path


def _move_to_cgroup(pid: int, cgroup_path: str) -> None:
    """Move a process into a cgroup (best-effort; called in the child)."""
    try:
        with open(os.path.join(cgroup_path, "cgroup.procs"), "w", encoding="utf-8") as fh:
            fh.write(str(pid))
    except OSError:
        pass


def _read_cgroup_peak(cgroup_path: str) -> float | None:
    """Read ``memory.peak`` (bytes) from a cgroup v2 subtree, or ``None``."""
    try:
        with open(os.path.join(cgroup_path, "memory.peak"), encoding="utf-8") as fh:
            return float(fh.read().strip())
    except (OSError, ValueError):
        return None


def _remove_cgroup(cgroup_path: str) -> None:
    """Best-effort removal of a per-experiment cgroup subtree."""
    try:
        os.rmdir(cgroup_path)
    except OSError:
        pass


def _make_preexec(cpu_secs: int, cgroup_path: str | None) -> Any:  # noqa: ANN401
    """Build a ``preexec_fn`` applying rlimits and moving into a cgroup, or ``None``."""
    if not _HAS_RESOURCE and cgroup_path is None:
        return None

    def _apply() -> None:
        if _HAS_RESOURCE:
            _resource.setrlimit(_resource.RLIMIT_CPU, (cpu_secs, cpu_secs))
            _resource.setrlimit(_resource.RLIMIT_FSIZE, (_MAX_FILE_BYTES, _MAX_FILE_BYTES))
            _resource.setrlimit(_resource.RLIMIT_NPROC, (_MAX_PROCESSES, _MAX_PROCESSES))
            _resource.setrlimit(_resource.RLIMIT_AS, (_MAX_MEMORY_BYTES, _MAX_MEMORY_BYTES))
        if cgroup_path is not None:
            _move_to_cgroup(os.getpid(), cgroup_path)

    return _apply


def _peak_memory_bytes(cgroup_path: str | None = None) -> float:
    """Return the peak memory of the experiment, measured on the host.

    When a per-experiment cgroup is available, ``memory.peak`` gives the true
    cumulative peak of the whole process tree. Otherwise we fall back to
    ``getrusage(RUSAGE_CHILDREN)``, which reports only the largest single
    child's peak RSS and may understate a tree with many processes.
    """
    if cgroup_path is not None:
        peak = _read_cgroup_peak(cgroup_path)
        # A cgroup can be created but report a zero/absent peak (for example
        # when the child could not be moved into it, or the peak file is not
        # yet written). Treat a non-positive peak as "unavailable" and fall
        # back to getrusage rather than reporting a misleading zero.
        if peak is not None and peak > 0:
            return peak
    if not _HAS_RESOURCE:
        return 0.0
    usage = _resource.getrusage(_resource.RUSAGE_CHILDREN)
    # ru_maxrss is kilobytes on Linux/macOS.
    return float(usage.ru_maxrss) * 1024.0


def _close_pipes(proc: asyncio.subprocess.Process) -> None:
    """Close the child's pipe transports so ``wait()`` can return.

    If a reader stopped draining early (output-cap overflow) or the child was
    killed while still blocked writing, ``proc.wait()`` would otherwise hang on
    a non-EOF pipe. Closing the transports unblocks the wait without raising.
    The process transport is closed first (more stable across Python versions),
    then each stream transport as a fallback.
    """
    process_transport = getattr(proc, "_transport", None)
    if process_transport is not None:
        try:
            process_transport.close()
        except Exception:  # noqa: S110
            pass
    for stream in (proc.stdout, proc.stderr):
        transport = getattr(stream, "_transport", None) if stream is not None else None
        if transport is None:
            continue
        try:
            transport.close()
        except Exception:  # noqa: S110
            pass


async def _reap(proc: asyncio.subprocess.Process) -> int | None:
    """Reap a (usually killed) child without ever raising.

    ``Process.wait()`` can raise on some Python versions once the pipe
    transports have been closed, so this wraps it and falls back to the
    underlying subprocess object when the asyncio wait is unavailable. The
    runner must never propagate a reaping error as a raised exception.
    """
    try:
        return await proc.wait()
    except Exception:  # noqa: BLE001 - never let reaping break the run
        try:
            transport = getattr(proc, "_transport", None)
            inner = getattr(transport, "_proc", None)
            if inner is not None:
                return int(inner.wait(timeout=2.0))
        except Exception:  # noqa: BLE001, S110
            pass
        return proc.returncode


def _kill_group(
    proc: asyncio.subprocess.Process, pgid: int | None = None, *, close_pipes: bool = True
) -> None:
    """Terminate the child and its whole process group, tolerating already-exit.

    ``pgid`` should be the process-group id captured at spawn (== ``proc.pid``
    under ``start_new_session=True``) and reused so that killing after the parent
    has been reaped still targets the original group rather than a recycled id.
    Passing the captured pgid lets us reap lingering background children after the
    parent exits without a TOCTOU on the reaped PID. A group that is already gone
    raises ``ProcessLookupError``, which is swallowed. When ``close_pipes`` is
    set, the pipe transports are closed so a later ``wait()`` cannot block on a
    pipe a killed descendant still held open.
    """
    target = pgid if pgid is not None else proc.pid
    try:
        if os.name != "nt" and hasattr(os, "killpg"):
            os.killpg(target, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, OSError):
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass
    if close_pipes:
        _close_pipes(proc)


async def _read_bounded(
    proc: asyncio.subprocess.Process,
    stream: asyncio.StreamReader,
    cap: int,
    pgid: int | None = None,
) -> tuple[bytes, bool]:
    """Stream a pipe up to ``cap`` bytes; kill the group on overflow. Returns (data, overflow)."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            break
        if total + len(chunk) > cap:
            _kill_group(proc, pgid)
            chunks.append(chunk[: cap - total])
            total += len(chunk)
            return b"".join(chunks), True
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks), False


def _parse_result(stdout_text: str) -> dict[str, Any] | None:
    """Extract the last JSON object the experiment printed, if any."""
    stripped = stdout_text.strip()
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    for line in reversed(stripped.splitlines()):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            return candidate
    return None


def _accuracy_from_predictions(predictions: Any, reference: tuple[Any, ...]) -> float:  # noqa: ANN401
    """Compute accuracy on the host by comparing predictions to reference results.

    The reference results are host-owned and never exposed to the experiment.
    Missing, malformed, or non-matching predictions count as incorrect, so the
    tested code cannot inflate accuracy by omitting or fabricating entries.
    """
    if not isinstance(predictions, list) or not reference:
        return 0.0
    total = len(reference)
    matches = 0
    for idx, expected in enumerate(reference):
        if idx >= len(predictions):
            break
        try:
            matched = predictions[idx] == expected
        except Exception:  # noqa: BLE001 - never trust the child for the metric
            matched = False
        if matched:
            matches += 1
    return max(0.0, min(1.0, matches / total))


def _edge_cases_pass(spec: ExperimentSpec, edge_cases: Any) -> bool:  # noqa: ANN401
    """Return True only when every declared edge case was exercised and passed."""
    if not isinstance(edge_cases, dict):
        return False
    if set(edge_cases) != set(spec.edge_cases):
        return False
    for name in spec.edge_cases:
        entry = edge_cases[name]
        if not isinstance(entry, dict) or not bool(entry.get("passed", False)):
            return False
    return True


def _write_debug(base_dir: Path, message: str) -> None:
    """Best-effort diagnostic dump (temporary; helps CI failure triage)."""
    try:
        (base_dir / "run_debug.txt").write_text(message, encoding="utf-8")
    except OSError:
        pass


def _failed_evidence(
    spec: ExperimentSpec, artifact_sha256: str, elapsed_ms: float, peak_mem: float
) -> TestEvidence:
    """Build a failing ``TestEvidence`` so a broken run is data, not a crash."""
    return TestEvidence(
        experiment=spec.experiment_label,
        environment=spec.environment,
        artifact_sha256=artifact_sha256,
        gates=VerificationGates(
            accuracy_passed=False,
            latency_passed=False,
            resource_passed=False,
            edge_cases_passed=False,
        ),
        measurements={
            "accuracy": 0.0,
            "latency_ms": elapsed_ms,
            "peak_memory_bytes": peak_mem,
        },
        tested_at=datetime.now(UTC),
    )


class ExperimentRunner:
    """Run a confined experiment and measure its verification gates from the host."""

    def __init__(self, *, sandbox_dir: Path | None = None) -> None:
        """Bind an optional persistent sandbox directory for execution."""
        self._sandbox_dir = sandbox_dir

    async def run(self, spec: ExperimentSpec) -> TestEvidence:
        """Execute ``spec`` and return measured ``TestEvidence`` (never raises)."""
        artifact_sha256 = _artifact_hash(spec)
        started = time.perf_counter()
        base_dir = self._sandbox_dir
        tmp: tempfile.TemporaryDirectory[str] | None = None
        if base_dir is None:
            tmp = tempfile.TemporaryDirectory()
            base_dir = Path(tmp.name)
        cgroup_path: str | None = None
        try:
            script = base_dir / "experiment.py"
            script.write_text(spec.code, encoding="utf-8")
            # Write the host-owned test inputs so the experiment can produce
            # predictions. The reference results are never written to the
            # sandbox; only the host holds them for the comparison.
            (base_dir / "inputs.json").write_text(
                json.dumps(list(spec.evaluation.inputs), ensure_ascii=False),
                encoding="utf-8",
            )
            kwargs: dict[str, Any] = {
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
            }
            if os.name != "nt":
                kwargs["start_new_session"] = True
            cgroup_path = _create_cgroup()
            limits = _make_preexec(int(spec.timeout_s) + 1, cgroup_path)
            if limits is not None:
                kwargs["preexec_fn"] = limits
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                "-B",
                str(script),
                cwd=str(base_dir),
                env=_safe_env(),
                **kwargs,
            )
            assert proc.stdout is not None and proc.stderr is not None  # noqa: S101
            # Capture the process-group id at spawn (== proc.pid under
            # start_new_session=True) so every path can terminate the original
            # group, including descendants, without relying on a reaped PID.
            pgid = proc.pid if os.name != "nt" and hasattr(os, "killpg") else None
            try:
                async with asyncio.timeout(spec.timeout_s):
                    (stdout_b, stdout_ovf), (stderr_b, stderr_ovf) = await asyncio.gather(
                        _read_bounded(proc, proc.stdout, _MAX_OUTPUT_BYTES, pgid),
                        _read_bounded(proc, proc.stderr, _MAX_OUTPUT_BYTES, pgid),
                    )
            except TimeoutError:
                _kill_group(proc, pgid)
                await _reap(proc)
                elapsed = (time.perf_counter() - started) * 1000.0
                return _failed_evidence(
                    spec, artifact_sha256, elapsed, _peak_memory_bytes(cgroup_path)
                )
            # The parent has produced its output (we reached EOF). Let it exit
            # and reap it first so its exit code is set, then terminate any
            # lingering descendants by killing the group with the captured pgid
            # and closing the pipe transports. Closing the pipes (as on the
            # known-good path) keeps ``wait()``/transport cleanup well-defined
            # across Python versions; the captured pgid makes the descendant
            # kill TOCTOU-free even after the parent is reaped.
            await _reap(proc)
            _kill_group(proc, pgid)
            elapsed = (time.perf_counter() - started) * 1000.0
            if stdout_ovf or stderr_ovf:
                return _failed_evidence(
                    spec, artifact_sha256, elapsed, _peak_memory_bytes(cgroup_path)
                )
            stdout_text = redact_secrets(stdout_b.decode("utf-8", errors="replace"))
            result = _parse_result(stdout_text)
            if result is None:
                _stderr_snippet = stderr_b.decode("utf-8", errors="replace")[:2000]
                _write_debug(
                    base_dir,
                    f"no result; stdout={stdout_text[:500]!r} stderr={_stderr_snippet!r}",
                )
                return _failed_evidence(
                    spec, artifact_sha256, elapsed, _peak_memory_bytes(cgroup_path)
                )
            return self._evidence_from_result(
                spec, artifact_sha256, elapsed, _peak_memory_bytes(cgroup_path), result
            )
        except Exception as exc:  # noqa: BLE001 - run() must never raise; fail closed
            # Any unexpected error while driving the confined subprocess (for
            # example a transport/reader edge on newer Python versions) is turned
            # into failed evidence rather than escaping as a raised exception.
            import traceback

            _write_debug(base_dir, f"exception: {exc!r}\n{traceback.format_exc()}")
            elapsed = (time.perf_counter() - started) * 1000.0
            return _failed_evidence(spec, artifact_sha256, elapsed, _peak_memory_bytes(cgroup_path))
        finally:
            if cgroup_path is not None:
                _remove_cgroup(cgroup_path)
            if tmp is not None:
                tmp.cleanup()

    def run_sync(self, spec: ExperimentSpec) -> TestEvidence:
        """Run ``spec`` from a synchronous caller via a fresh event loop."""
        return asyncio.run(self.run(spec))

    @staticmethod
    def _evidence_from_result(
        spec: ExperimentSpec,
        artifact_sha256: str,
        elapsed_ms: float,
        peak_mem: float,
        result: dict[str, Any],
    ) -> TestEvidence:
        """Turn a parsed experiment result into host-measured, gated evidence."""
        predictions = result.get("predictions")
        accuracy = _accuracy_from_predictions(predictions, spec.evaluation.reference_results)
        gates = VerificationGates(
            accuracy_passed=accuracy >= spec.thresholds.min_accuracy,
            latency_passed=elapsed_ms <= spec.thresholds.max_latency_ms,
            resource_passed=peak_mem <= spec.thresholds.max_peak_memory_bytes,
            edge_cases_passed=_edge_cases_pass(spec, result.get("edge_cases")),
        )
        return TestEvidence(
            experiment=spec.experiment_label,
            environment=spec.environment,
            artifact_sha256=artifact_sha256,
            gates=gates,
            measurements={
                "accuracy": accuracy,
                "latency_ms": elapsed_ms,
                "peak_memory_bytes": peak_mem,
            },
            tested_at=datetime.now(UTC),
        )


__all__ = [
    "EvaluationSpec",
    "ExperimentRunner",
    "ExperimentSpec",
    "Hypothesis",
    "VerificationThresholds",
]
