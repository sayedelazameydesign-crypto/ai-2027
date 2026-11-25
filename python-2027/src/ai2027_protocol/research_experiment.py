"""Isolated, reproducible experiment execution for the Research Radar (P4).

The research stack already verifies paper metadata, grounds every evaluation
quote in the fetched source text, and keeps a tamper-evident audit ledger. What
it does not yet do is *run* the experiment a candidate paper implies. This
module closes that gap with a bounded, local, reproducible experiment runner
that produces :class:`~.research_contracts.TestEvidence` for the existing
human-reviewed decision path.

Rules it enforces
-----------------
* **No automatic adoption.** The runner emits ``TestEvidence``; deciding that a
  candidate is ``tested`` stays a human call through ``ResearchStore.decide``.
* **Reproducibility.** The artifact hash is content-addressed over the
  experiment code, so re-running the same spec reproduces the same evidence
  hash.
* **Bounded, local execution.** The experiment runs in an isolated subprocess
  (``python -I -B``) with a sanitized environment, a hard timeout, and bounded
  output. No paid or remote call is involved.
* **Failure is data.** A crashing, timing-out, or malformed experiment yields a
  ``TestEvidence`` whose gates fail, rather than raising.

What it deliberately does not do
-------------------------------
* It is a library component. There is **no public HTTP endpoint** that executes
  arbitrary Python: the Research API's write paths are not authenticated, and
  adding one would create a remote code-execution risk.
* It does not claim OS-level sandboxing (containers). The guarantee is the
  subprocess boundary plus time/output caps plus a sanitized environment.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
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

#: Cap on captured experiment output, mirroring the tool-execution boundary.
_MAX_OUTPUT_BYTES = 20_000
#: Bounded identifier for hypothesis measurement / edge-case names.
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


def _artifact_hash(code: str) -> str:
    """Return the content-addressed hash of the reproducible experiment code."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _safe_env() -> dict[str, str]:
    """Return a minimal environment with no secrets for the isolated subprocess."""
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _finite_float(value: object) -> float:
    """Coerce a reported measurement to a finite float, defaulting to 0.0."""
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else 0.0
    if isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return 0.0
        return number if math.isfinite(number) else 0.0
    return 0.0


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


def _edge_cases_pass(spec: ExperimentSpec, edge_cases: object) -> bool:
    """Return True only when every declared edge case was exercised and passed."""
    if not isinstance(edge_cases, dict):
        return False
    if set(edge_cases) != set(spec.edge_cases):
        return False
    return all(bool(value) for value in edge_cases.values())


def _failed_evidence(spec: ExperimentSpec, artifact_sha256: str, elapsed_ms: float) -> TestEvidence:
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
        measurements={"accuracy": 0.0, "latency_ms": elapsed_ms, "peak_memory_bytes": 0.0},
        tested_at=datetime.now(UTC),
    )


class ExperimentRunner:
    """Run a bounded experiment in isolation and measure its verification gates."""

    def __init__(self, *, sandbox_dir: Path | None = None) -> None:
        """Bind an optional persistent sandbox directory for execution."""
        self._sandbox_dir = sandbox_dir

    async def run(self, spec: ExperimentSpec) -> TestEvidence:
        """Execute ``spec`` and return measured ``TestEvidence`` (never raises)."""
        artifact_sha256 = _artifact_hash(spec.code)
        started = time.perf_counter()
        base_dir = self._sandbox_dir
        tmp: tempfile.TemporaryDirectory[str] | None = None
        if base_dir is None:
            tmp = tempfile.TemporaryDirectory()
            base_dir = Path(tmp.name)
        try:
            script = base_dir / "experiment.py"
            script.write_text(spec.code, encoding="utf-8")
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                "-B",
                str(script),
                cwd=str(base_dir),
                env=_safe_env(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                async with asyncio.timeout(spec.timeout_s):
                    stdout_b, _ = await proc.communicate()
            except TimeoutError:
                proc.kill()
                await proc.wait()
                elapsed = (time.perf_counter() - started) * 1000.0
                return _failed_evidence(spec, artifact_sha256, elapsed)
            elapsed = (time.perf_counter() - started) * 1000.0
            stdout_text = redact_secrets(
                stdout_b[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
            )
            if proc.returncode != 0:
                return _failed_evidence(spec, artifact_sha256, elapsed)
            result = _parse_result(stdout_text)
            if result is None:
                return _failed_evidence(spec, artifact_sha256, elapsed)
            return self._evidence_from_result(spec, artifact_sha256, elapsed, result)
        finally:
            if tmp is not None:
                tmp.cleanup()

    def run_sync(self, spec: ExperimentSpec) -> TestEvidence:
        """Run ``spec`` from a synchronous caller via a fresh event loop."""
        return asyncio.run(self.run(spec))

    @staticmethod
    def _evidence_from_result(
        spec: ExperimentSpec, artifact_sha256: str, elapsed_ms: float, result: dict[str, Any]
    ) -> TestEvidence:
        """Turn a parsed experiment result into measured, gated evidence."""
        raw_measurements = result.get("measurements")
        measurements = raw_measurements if isinstance(raw_measurements, dict) else {}
        accuracy = _finite_float(measurements.get("accuracy", 0.0))
        peak_memory = _finite_float(measurements.get("peak_memory_bytes", 0.0))
        gates = VerificationGates(
            accuracy_passed=accuracy >= spec.thresholds.min_accuracy,
            latency_passed=elapsed_ms <= spec.thresholds.max_latency_ms,
            resource_passed=peak_memory <= spec.thresholds.max_peak_memory_bytes,
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
                "peak_memory_bytes": peak_memory,
            },
            tested_at=datetime.now(UTC),
        )


__all__ = [
    "ExperimentRunner",
    "ExperimentSpec",
    "Hypothesis",
    "VerificationThresholds",
]
