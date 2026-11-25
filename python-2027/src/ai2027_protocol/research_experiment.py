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
* ``accuracy`` is **computed on the host** from raw counts (``correct`` /
  ``total``) the experiment reports, not from a self-declared success value.
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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator

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


class ExperimentSpec(StrictModel):
    """A bounded, reproducible experiment derived from a hypothesis and a paper."""

    hypothesis: Hypothesis
    code: str = Field(min_length=8, max_length=20_000)
    environment: str = Field(min_length=3, max_length=300)
    timeout_s: float = Field(default=5.0, ge=0.2, le=10.0)
    thresholds: VerificationThresholds
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


def _child_limits_fn(cpu_secs: int) -> Any:  # noqa: ANN401
    """Build a ``preexec_fn`` applying hard resource caps, or ``None`` off-POSIX."""
    if not _HAS_RESOURCE:
        return None

    def _apply() -> None:
        _resource.setrlimit(_resource.RLIMIT_CPU, (cpu_secs, cpu_secs))
        _resource.setrlimit(_resource.RLIMIT_FSIZE, (_MAX_FILE_BYTES, _MAX_FILE_BYTES))
        _resource.setrlimit(_resource.RLIMIT_NPROC, (_MAX_PROCESSES, _MAX_PROCESSES))
        _resource.setrlimit(_resource.RLIMIT_AS, (_MAX_MEMORY_BYTES, _MAX_MEMORY_BYTES))

    return _apply


def _peak_memory_bytes() -> float:
    """Return the peak resident set size of child processes, measured on the host."""
    if not _HAS_RESOURCE:
        return 0.0
    usage = _resource.getrusage(_resource.RUSAGE_CHILDREN)
    # ru_maxrss is kilobytes on Linux/macOS.
    return float(usage.ru_maxrss) * 1024.0


def _close_pipes(proc: asyncio.subprocess.Process) -> None:
    """Close the child's stdout/stderr transports so ``wait()`` can return.

    If a reader stopped draining early (output-cap overflow) or the child was
    killed while still blocked writing, ``proc.wait()`` would otherwise hang on
    a non-EOF pipe. Closing the transports unblocks the wait without raising.
    """
    for stream in (proc.stdout, proc.stderr):
        transport = getattr(stream, "_transport", None) if stream is not None else None
        if transport is None:
            continue
        try:
            transport.close()
        except Exception:  # noqa: S110
            pass


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    """Terminate the child and its whole process group, tolerating already-exit.

    The child is spawned with ``start_new_session=True``, so it is its own
    process-group leader and ``pgid == pid``. Using ``proc.pid`` as the pgid
    works even after the main process has exited, which lets us reap lingering
    background children. A group that is already gone raises
    ``ProcessLookupError``, which is swallowed.
    """
    try:
        if os.name != "nt" and hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, OSError):
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass
    _close_pipes(proc)


async def _read_bounded(
    proc: asyncio.subprocess.Process, stream: asyncio.StreamReader, cap: int
) -> tuple[bytes, bool]:
    """Stream a pipe up to ``cap`` bytes; kill the group on overflow. Returns (data, overflow)."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            break
        if total + len(chunk) > cap:
            _kill_group(proc)
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


def _compute_accuracy(raw: Any) -> float:  # noqa: ANN401
    """Compute accuracy on the host from raw ``correct`` / ``total`` counts."""
    if not isinstance(raw, dict):
        return 0.0
    try:
        correct = float(raw.get("correct", 0))
        total = float(raw.get("total", 0))
    except (TypeError, ValueError):
        return 0.0
    if not (math.isfinite(correct) and math.isfinite(total)) or total <= 0:
        return 0.0
    return max(0.0, min(1.0, correct / total))


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
        try:
            script = base_dir / "experiment.py"
            script.write_text(spec.code, encoding="utf-8")
            kwargs: dict[str, Any] = {
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
            }
            if os.name != "nt":
                kwargs["start_new_session"] = True
            limits = _child_limits_fn(int(spec.timeout_s) + 1)
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
            try:
                async with asyncio.timeout(spec.timeout_s):
                    (stdout_b, stdout_ovf), (stderr_b, stderr_ovf) = await asyncio.gather(
                        _read_bounded(proc, proc.stdout, _MAX_OUTPUT_BYTES),
                        _read_bounded(proc, proc.stderr, _MAX_OUTPUT_BYTES),
                    )
            except TimeoutError:
                _kill_group(proc)
                await proc.wait()
                elapsed = (time.perf_counter() - started) * 1000.0
                return _failed_evidence(spec, artifact_sha256, elapsed, _peak_memory_bytes())
            await proc.wait()
            # Reap any background children the experiment may have spawned.
            _kill_group(proc)
            elapsed = (time.perf_counter() - started) * 1000.0
            if stdout_ovf or stderr_ovf or proc.returncode != 0:
                return _failed_evidence(spec, artifact_sha256, elapsed, _peak_memory_bytes())
            stdout_text = redact_secrets(stdout_b.decode("utf-8", errors="replace"))
            result = _parse_result(stdout_text)
            if result is None:
                return _failed_evidence(spec, artifact_sha256, elapsed, _peak_memory_bytes())
            return self._evidence_from_result(
                spec, artifact_sha256, elapsed, _peak_memory_bytes(), result
            )
        finally:
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
        raw_measurements = result.get("raw_measurements")
        accuracy = _compute_accuracy(raw_measurements)
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
    "ExperimentRunner",
    "ExperimentSpec",
    "Hypothesis",
    "VerificationThresholds",
]
