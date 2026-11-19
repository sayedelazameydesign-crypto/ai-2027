#!/usr/bin/env python3
"""PAP-2027 ``live-connector`` gate: one real provider call, judged as evidence.

    python3 scripts/live_connector_gate.py            # human table
    python3 scripts/live_connector_gate.py --json     # machine report
    python3 scripts/live_connector_gate.py --assert   # CI gate (no key = FAIL)

The audit defined this gate more strictly than the others, on purpose:

* ``skipped_no_key`` counts as a **failure** here, not as a skip — "we could not
  reach anyone" is not evidence of connectivity;
* a simulated/deterministic reply is a failure, not a success;
* the artifact must contain an id, a timestamp and the sanitized response.

So the gate refuses to pass unless a provider actually answered, over the
network, with our marker token, at a $0 billing tier, and the recorded evidence
contains no credential material. ``--assert`` returns 1 on any failure and 2 when
the worker could not run at all (wrong interpreter), never 0 by default.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "python-2027" / "tests" / "live_connector_worker.py"
DEFAULT_REPORT = ROOT / "launch" / "evidence" / "live-connector-gate.json"
MARKER = "PAP-2027-LIVE-OK"
FREE_TIERS = {"free_tier", "local"}
_SECRET_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"nvapi-[0-9A-Za-z_-]{20,}"),
    re.compile(r"gsk_[0-9A-Za-z]{20,}"),
    re.compile(r"sk-or-v1-[0-9a-f]{16,}"),
    re.compile(r"sk-[0-9A-Za-z_-]{20,}"),
)


@dataclass
class Check:
    """One falsifiable assertion in the gate."""

    name: str
    ok: bool
    detail: str


@dataclass
class GateResult:
    """The worker report plus the verdict derived from it."""

    checks: list[Check] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] | None = None
    worker_returncode: int | None = None
    worker_stderr: str = ""
    duration_ms: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        """Return True only when the gate ran, a provider answered, and all checks passed."""
        return not self.error and bool(self.checks) and all(c.ok for c in self.checks)

    def check(self, name: str, ok: bool, detail: str) -> None:
        """Record one check result."""
        self.checks.append(Check(name=name, ok=ok, detail=detail))


def _run_worker(
    python: str, worker: Path, *, providers: list[str], timeout_s: float
) -> subprocess.CompletedProcess[str]:
    command = [python, str(worker), "--marker", MARKER, "--timeout-s", str(timeout_s)]
    for provider in providers:
        command.extend(["--provider", provider])
    return subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, timeout=600, check=False
    )


def resolve_interpreter(value: str) -> str:
    """Resolve the worker interpreter against the caller's cwd, or report it missing.

    ``--python .venv/bin/python`` is what a human types from ``python-2027/``. The
    worker runs with ``cwd=repo root``, so a relative path must be resolved here —
    otherwise the gate dies before it can write any evidence.
    """
    candidate = value
    if os.sep in value or (os.altsep and os.altsep in value):
        # ``absolute()``, never ``resolve()``: a venv's ``bin/python`` is a symlink
        # to the base interpreter, and following it leaves the venv (and its
        # site-packages) behind.
        candidate = str(Path(value).expanduser().absolute())
    else:
        found = shutil.which(value)
        candidate = found or value
    return candidate


def _missing_interpreter(python: str) -> str:
    if os.sep in python and not Path(python).is_file():
        return f"interpreter not found: {python}"
    return ""


def _parse_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _no_secret(payload: Any) -> bool:
    """Return True when no credential-shaped value appears anywhere in the evidence."""
    text = json.dumps(payload, ensure_ascii=False)
    return not any(pattern.search(text) for pattern in _SECRET_PATTERNS)


def _strip_secrets(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Redact credential-shaped values from the artifact; the verdict stays FAIL."""
    text = json.dumps(payload, ensure_ascii=False)
    redactions = 0
    for pattern in _SECRET_PATTERNS:
        text, count = pattern.subn("[redacted]", text)
        redactions += count
    stripped = json.loads(text)
    assert isinstance(stripped, dict)
    return stripped, redactions


def judge(report: dict[str, Any], result: GateResult) -> GateResult:
    """Turn a worker report into PASS/FAIL checks; never infer success."""
    attempts = report.get("attempts") or []
    result.attempts = attempts
    statuses = {a.get("provider"): a.get("status") for a in attempts}
    live = [a for a in attempts if a.get("status") == "ok" and a.get("evidence")]

    result.check(
        "a-provider-answered",
        bool(live),
        "statuses: "
        + (", ".join(f"{k}={v}" for k, v in statuses.items()) or "none")
        + (" — skipped_no_key counts as FAILURE for this gate" if not live else ""),
    )
    if not live:
        return result

    evidence = live[0]["evidence"]
    result.evidence = evidence

    result.check(
        "http-2xx",
        isinstance(evidence.get("http_status"), int) and 200 <= evidence["http_status"] < 300,
        f"http_status={evidence.get('http_status')}",
    )
    result.check(
        "not-a-mock",
        evidence.get("mock") is False and evidence.get("provider") not in {"deterministic"},
        f"provider={evidence.get('provider')} mock={evidence.get('mock')}",
    )
    result.check(
        "prompt-marker-echoed",
        evidence.get("marker_found") is True,
        f"marker={MARKER} found={evidence.get('marker_found')} "
        f"excerpt={evidence.get('response_excerpt', '')[:80]!r}",
    )
    has_identity = bool(evidence.get("request_id")) or bool(evidence.get("server_model_version"))
    result.check(
        "evidence-has-id-time-and-sanitized-response",
        has_identity
        and _parse_timestamp(evidence.get("requested_at"))
        and _parse_timestamp(evidence.get("responded_at"))
        and bool(evidence.get("response_excerpt"))
        and len(str(evidence.get("response_sha256", ""))) == 64,
        f"id_source={evidence.get('request_id_source')} "
        f"server_model={evidence.get('server_model_version')} "
        f"requested_at={evidence.get('requested_at')} sha256={'set' if evidence.get('response_sha256') else 'missing'}",
    )
    result.check(
        "billing-tier-is-free",
        evidence.get("billing_tier") in FREE_TIERS and evidence.get("cost_usd") == 0.0,
        f"tier={evidence.get('billing_tier')} cost_usd={evidence.get('cost_usd')}",
    )
    clean = _no_secret(report)
    result.check(
        "no-credential-material-in-evidence",
        clean,
        "clean"
        if clean
        else "credential-shaped value found in the evidence (redacted in this artifact)",
    )
    result.check(
        "worker-ran-under-a-real-interpreter",
        result.worker_returncode == 0,
        f"worker returncode={result.worker_returncode}",
    )
    return result


def run_gate(*, python: str, worker: Path, providers: list[str], timeout_s: float) -> GateResult:
    """Run the worker and judge its report."""
    result = GateResult()
    started = time.monotonic()
    if not worker.is_file():
        result.error = f"worker not found: {worker}"
        return result
    missing = _missing_interpreter(python)
    if missing:
        result.error = missing
        return result

    completed = _run_worker(python, worker, providers=providers, timeout_s=timeout_s)
    result.worker_returncode = completed.returncode
    result.worker_stderr = (completed.stderr or "").strip()[:400]
    if completed.returncode == 3 or "ModuleNotFoundError" in (completed.stderr or ""):
        result.error = (
            "the worker could not run — use the python-2027 virtualenv interpreter "
            f"(--python .venv/bin/python). stderr={result.worker_stderr[:200]}"
        )
        return result
    try:
        report = json.loads(completed.stdout)
    except ValueError:
        result.error = f"worker produced no JSON. stderr={result.worker_stderr[:300]}"
        return result
    if not isinstance(report, dict):
        result.error = "worker produced JSON that is not an object"
        return result

    judge(report, result)
    result.duration_ms = round((time.monotonic() - started) * 1000.0, 1)
    return result


def render(result: GateResult) -> str:
    """Return a readable table of the gate verdict."""
    lines = [f"{'Check':<46} | Verdict | Detail", "-" * 118]
    for check in result.checks:
        lines.append(f"{check.name:<46} | {'PASS' if check.ok else 'FAIL':<7} | {check.detail}")
    lines.append("-" * 118)
    lines.append(
        f"verdict={'PASS' if result.ok else 'FAIL'} · checks={len(result.checks)} · "
        f"{result.duration_ms} ms"
    )
    if result.evidence:
        lines.append(
            f"live: {result.evidence.get('provider')}:{result.evidence.get('model')} · "
            f"host={result.evidence.get('endpoint_host')} · "
            f"{result.evidence.get('latency_ms')} ms · "
            f"id={result.evidence.get('request_id') or '(server model version only)'} · "
            f"excerpt={result.evidence.get('response_excerpt', '')[:60]!r}"
        )
    if result.error:
        lines.append(f"error: {result.error}")
    return "\n".join(lines)


def write_report(result: GateResult, path: Path) -> None:
    """Persist the gate evidence; a gate without an artifact did not run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "gate": "live-connector",
        "protocol": "PAP-2027",
        "verdict": "PASS" if result.ok else "FAIL",
        "error": result.error,
        "duration_ms": result.duration_ms,
        "marker": MARKER,
        "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in result.checks],
        "attempts": result.attempts,
        "evidence": result.evidence,
        "worker_returncode": result.worker_returncode,
        "worker_stderr": result.worker_stderr,
    }
    payload, redactions = _strip_secrets(payload)
    payload["redactions"] = redactions
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Run the gate and return a process exit code."""
    parser = argparse.ArgumentParser(description="PAP-2027 live-connector gate (one real call).")
    parser.add_argument("--python", default=sys.executable, help="Interpreter for the worker.")
    parser.add_argument("--worker", type=Path, default=WORKER, help="Worker script path.")
    parser.add_argument(
        "--provider",
        action="append",
        default=[],
        choices=["gemini", "nvidia", "groq", "openrouter"],
        help="Restrict the probe to these providers (default: all).",
    )
    parser.add_argument("--timeout-s", type=float, default=45.0, help="Per-provider timeout.")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="Evidence JSON path.")
    parser.add_argument("--json", action="store_true", help="Print the report JSON.")
    parser.add_argument(
        "--assert", dest="assert_ok", action="store_true", help="Fail on any check."
    )
    args = parser.parse_args(argv)

    try:
        result = run_gate(
            python=resolve_interpreter(args.python),
            worker=args.worker,
            providers=args.provider,
            timeout_s=args.timeout_s,
        )
    except Exception as exc:  # noqa: BLE001 - a crash must still leave evidence
        result = GateResult(error=f"{type(exc).__name__}: {exc}")

    try:
        write_report(result, args.report)
    except OSError as exc:
        print(f"FAIL: cannot write evidence report {args.report}: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(args.report.read_text(encoding="utf-8"))
    else:
        print(render(result))
        print(f"evidence: {args.report}")

    # Opt-in: ``::error::`` marks the *step* as failed, which is right for a real
    # gate run and wrong for the tests that deliberately provoke a FAIL verdict.
    if not result.ok and os.environ.get("PAP_GATE_ANNOTATE") == "1":
        reason = result.error or "; ".join(
            f"{check.name}: {check.detail}" for check in result.checks if not check.ok
        )
        print(f"::error title=PAP-2027 live-connector gate::{reason[:900]}")

    if result.error:
        return 2
    if args.assert_ok and not result.ok:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
