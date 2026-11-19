"""Independent verification: a step is done when disk agrees, not when an agent says so.

The repository's doctrine is *evidence-first*: ``python-2027``'s auditor refuses
``VERIFIED`` without a hash, and the kernel's verifier is re-run rather than
trusted. This module applies the same rule to fleet steps:

=====================  =====================================================
check                  passes only when
=====================  =====================================================
``output_non_empty``   the handler returned non-whitespace output (or evidence)
``exit_code_zero``     ``evidence['exit_code'] == 0``
``test_passed``        ``evidence['tests_run'] > 0`` and ``tests_failed == 0``
``artifact_exists``    every claimed artifact resolves inside the workspace
``artifact_sha256``    the digest recomputed **from disk** equals the claim
``json_schema``        the declared JSON payload satisfies required keys/types
``independent_review`` a reviewer that is *not* the producing agent agrees
``no_pending_compensation`` no recorded rollback is still pending
=====================  =====================================================

Two rules make this verifier honest:

1. **Claimed artifacts are always re-hashed.** Even if a step declares no
   verification at all, any artifact it reports is checked against disk, so a
   fabricated digest becomes a failing check instead of a passing summary.
2. **A missing reviewer fails the check.** ``independent_review`` never passes
   by default; if you ask for a second pair of eyes and there is none, the
   answer is "not verified".
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .contracts import (
    EvidenceKind,
    PlanStep,
    StepResult,
    VerificationReport,
    sha256_file,
)

#: A reviewer receives the step and the claimed result and returns a verdict.
Reviewer = Callable[[PlanStep, StepResult], "tuple[bool, str]"]


@dataclass(frozen=True)
class VerificationPolicy:
    """Tunable knobs that stay explicit instead of hiding in defaults."""

    require_artifacts_on_disk: bool = True
    allow_absolute_artifact_paths: bool = False
    max_artifacts: int = 64
    max_output_bytes_checked: int = 4_000_000


class PathEscape(ValueError):
    """Raised when a claimed artifact points outside the workspace."""


def resolve_artifact(workspace_root: Path, claimed: str, policy: VerificationPolicy) -> Path:
    """Resolve a claimed path safely; refuse traversal and (by default) absolute paths."""
    root = workspace_root.resolve()
    raw = PurePosixPath(claimed)
    if raw.is_absolute():
        candidate = Path(claimed).resolve()
        if policy.allow_absolute_artifact_paths:
            return candidate
        # An absolute path is refused even when it happens to sit inside the
        # workspace: portability matters, because the verifier often runs on a
        # different machine than the agent that produced the artifact.
        raise PathEscape(f"absolute artifact path refused (use a workspace-relative path): {claimed!r}")
    candidate = (root / claimed).resolve()
    if candidate != root and root not in candidate.parents:
        raise PathEscape(f"artifact path escapes the workspace: {claimed!r}")
    return candidate


def verify_step(
    step: PlanStep,
    result: StepResult,
    *,
    workspace_root: Path,
    reviewer: Reviewer | None = None,
    pending_compensations: int = 0,
    policy: VerificationPolicy | None = None,
) -> VerificationReport:
    """Independently verify one claimed step result against declared evidence."""
    policy = policy or VerificationPolicy()
    root = workspace_root.resolve()
    declared = tuple(EvidenceKind(item) for item in step.verification)
    checks: dict[str, bool] = {}
    failures: list[str] = []

    def record(name: str, ok: bool, reason: str) -> None:
        checks[name] = ok
        if not ok:
            failures.append(f"{name}: {reason}")

    # --- Universal checks: never trust a self-reported digest. ------------- #
    artifact_paths: list[Path] = []
    if len(result.artifacts) > policy.max_artifacts:
        record("artifacts_bounded", False, f"{len(result.artifacts)} > {policy.max_artifacts}")
    for artifact in result.artifacts:
        try:
            path = resolve_artifact(root, artifact.path, policy)
        except PathEscape as exc:
            record(f"artifact_path[{artifact.path}]", False, str(exc))
            continue
        if not path.is_file():
            record(f"artifact_exists[{artifact.path}]", False, "file does not exist on disk")
            continue
        artifact_paths.append(path)
        if sha256_file(str(path)) != artifact.sha256:
            record(
                f"artifact_sha256[{artifact.path}]",
                False,
                "digest on disk differs from the claimed digest",
            )
        else:
            record(f"artifact_sha256[{artifact.path}]", True, "")

    # --- Declared checks ---------------------------------------------------- #
    for kind in declared:
        if kind is EvidenceKind.ARTIFACT_EXISTS:
            record(
                "artifact_exists",
                bool(result.artifacts) and all(
                    _safe_isfile(root, a.path, policy) for a in result.artifacts
                ),
                "no artifact was claimed, or a claimed artifact is missing",
            )
        elif kind is EvidenceKind.ARTIFACT_SHA256:
            mismatched = [
                a.path
                for a in result.artifacts
                if not checks.get(f"artifact_sha256[{a.path}]", False)
            ]
            record(
                "artifact_sha256",
                bool(result.artifacts) and not mismatched,
                f"missing or mismatched digests for {mismatched or 'all artifacts'}",
            )
        elif kind is EvidenceKind.OUTPUT_NON_EMPTY:
            nonempty = bool(result.output.strip()) or bool(result.evidence)
            record("output_non_empty", nonempty, "handler returned neither output nor evidence")
        elif kind is EvidenceKind.EXIT_CODE_ZERO:
            code = result.evidence.get("exit_code")
            record(
                "exit_code_zero",
                code == 0 and not isinstance(code, bool),
                f"exit_code evidence missing or non-zero ({code!r})",
            )
        elif kind is EvidenceKind.TEST_PASSED:
            run = result.evidence.get("tests_run")
            failed = result.evidence.get("tests_failed")
            ok = (
                isinstance(run, int)
                and not isinstance(run, bool)
                and run > 0
                and isinstance(failed, int)
                and not isinstance(failed, bool)
                and failed == 0
            )
            record("test_passed", ok, f"tests_run={run!r} tests_failed={failed!r}")
        elif kind is EvidenceKind.JSON_SCHEMA:
            spec = step.inputs.get("json_schema")
            payload = result.evidence.get("json")
            ok, reason = _check_json_schema(spec, payload)
            record("json_schema", ok, reason)
        elif kind is EvidenceKind.INDEPENDENT_REVIEW:
            if reviewer is None:
                record(
                    "independent_review",
                    False,
                    "no reviewer configured — an unverified claim cannot pass",
                )
            else:
                ok, note = reviewer(step, result)
                record("independent_review", bool(ok), note or "reviewer rejected the result")
        elif kind is EvidenceKind.NO_PENDING_COMPENSATION:
            record(
                "no_pending_compensation",
                pending_compensations == 0,
                f"{pending_compensations} rollback(s) still pending",
            )

    if not declared and result.artifacts:
        # No declared policy, but artifacts were claimed: their digests above
        # are the evidence. Make that explicit rather than implicit.
        record(
            "artifact_sha256",
            all(checks.get(f"artifact_sha256[{a.path}]", False) for a in result.artifacts),
            "claimed artifact digests could not be confirmed on disk",
        )

    ok = bool(checks) and all(checks.values())
    if not checks:
        failures.append("no check could be computed — refusing to call this verified")
    return VerificationReport(ok=ok, checks=checks, failures=tuple(failures))


def _safe_isfile(root: Path, claimed: str, policy: VerificationPolicy) -> bool:
    try:
        return resolve_artifact(root, claimed, policy).is_file()
    except PathEscape:
        return False


def _check_json_schema(spec: Any, payload: Any) -> tuple[bool, str]:
    """Validate a deliberately tiny JSON-schema subset (type/required/properties)."""
    if not isinstance(spec, Mapping):
        return False, "step.inputs.json_schema is missing or not an object"
    if payload is None:
        return False, "evidence.json is missing"
    expected = spec.get("type")
    type_map = {
        "object": dict,
        "array": list,
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
    }
    if expected is not None:
        py_type = type_map.get(str(expected))
        if py_type is None:
            return False, f"unsupported schema type {expected!r}"
        if isinstance(payload, bool) and py_type not in (bool,):
            return False, "boolean payload does not satisfy a numeric schema"
        if not isinstance(payload, py_type):
            return False, f"payload is {type(payload).__name__}, schema wants {expected}"
    required = spec.get("required", [])
    if required:
        if not isinstance(payload, Mapping):
            return False, "required keys declared but payload is not an object"
        missing = [key for key in required if key not in payload]
        if missing:
            return False, f"missing required keys {missing}"
    props = spec.get("properties", {})
    if isinstance(props, Mapping) and isinstance(payload, Mapping):
        for key, sub in props.items():
            if key in payload and isinstance(sub, Mapping):
                ok, reason = _check_json_schema(sub, payload[key])
                if not ok:
                    return False, f"property {key!r}: {reason}"
    return True, ""


def aggregate_report(
    verifications: Mapping[str, VerificationReport],
) -> tuple[int, int, float]:
    """Return ``(verified, unverified, ratio)`` — the honest progress meter."""
    total = len(verifications)
    verified = sum(1 for report in verifications.values() if report.ok)
    ratio = (verified / total) if total else 0.0
    return verified, total - verified, ratio
