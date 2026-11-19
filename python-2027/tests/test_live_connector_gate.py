"""The live-connector gate must pass only on real, clean evidence — and fail closed."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

GATE = Path(__file__).resolve().parents[2] / "scripts" / "live_connector_gate.py"


def _evidence(**overrides: Any) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "billing_tier": "free_tier",
        "billing_reason": "allowlisted free-tier gemini model",
        "credential": "configured",
        "endpoint_host": "generativelanguage.googleapis.com",
        "requested_at": "2026-11-19T14:00:00.000000+00:00",
        "responded_at": "2026-11-19T14:00:00.812000+00:00",
        "latency_ms": 812.0,
        "http_status": 200,
        "request_id": "resp_abc123",
        "request_id_source": "payload",
        "server_model_version": "gemini-2.5-flash-001",
        "usage": {"input_tokens": 31, "output_tokens": 5, "total_tokens": 36},
        "response_sha256": "a" * 64,
        "response_bytes": 17,
        "response_excerpt": "PAP-2027-LIVE-OK",
        "marker_found": True,
        "mock": False,
        "cost_usd": 0.0,
        "response_headers": {"content-type": "application/json"},
    }
    evidence.update(overrides)
    return evidence


def _worker(tmp_path: Path, report: dict[str, Any], *, rc: int = 0, name: str = "w") -> Path:
    """Write a fake worker that prints ``report`` (a JSON file) verbatim."""
    payload = tmp_path / f"{name}.json"
    payload.write_text(json.dumps(report), encoding="utf-8")
    path = tmp_path / f"{name}.py"
    path.write_text(
        "import pathlib, sys\n"
        f"print(pathlib.Path({str(payload)!r}).read_text(encoding='utf-8'))\n"
        f"sys.exit({rc})\n",
        encoding="utf-8",
    )
    return path


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed interpreter and repo-owned gate path
        [sys.executable, str(GATE), *args],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def _report(path: Path) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return payload


def test_gate_passes_on_clean_live_evidence(tmp_path: Path) -> None:
    worker = _worker(
        tmp_path,
        {"attempts": [{"provider": "gemini", "status": "ok", "evidence": _evidence()}]},
        name="w1",
    )
    report_path = tmp_path / "evidence.json"

    result = _run("--worker", str(worker), "--report", str(report_path), "--assert")

    assert result.returncode == 0, result.stdout + result.stderr
    payload = _report(report_path)
    assert payload["verdict"] == "PASS"
    assert payload["gate"] == "live-connector"
    assert all(check["ok"] for check in payload["checks"])
    assert len(payload["checks"]) >= 7


def test_no_key_is_a_failure_not_a_skip(tmp_path: Path) -> None:
    worker = _worker(
        tmp_path,
        {
            "attempts": [
                {"provider": "gemini", "status": "no_key", "evidence": None},
                {"provider": "nvidia", "status": "no_key", "evidence": None},
            ]
        },
        name="w2",
    )
    report_path = tmp_path / "evidence.json"

    result = _run("--worker", str(worker), "--report", str(report_path), "--assert")

    assert result.returncode == 1
    payload = _report(report_path)
    assert payload["verdict"] == "FAIL"
    assert [c["name"] for c in payload["checks"]] == ["a-provider-answered"]
    assert "skipped_no_key counts as FAILURE" in payload["checks"][0]["detail"]


def test_a_mock_answer_cannot_pass(tmp_path: Path) -> None:
    worker = _worker(
        tmp_path,
        {
            "attempts": [
                {
                    "provider": "deterministic",
                    "status": "ok",
                    "evidence": _evidence(provider="deterministic", mock=True),
                }
            ]
        },
        name="w3",
    )
    report_path = tmp_path / "evidence.json"

    result = _run("--worker", str(worker), "--report", str(report_path), "--assert")

    assert result.returncode == 1
    failed = {c["name"] for c in _report(report_path)["checks"] if not c["ok"]}
    assert "not-a-mock" in failed


def test_a_missing_marker_fails_even_with_http_200(tmp_path: Path) -> None:
    worker = _worker(
        tmp_path,
        {
            "attempts": [
                {
                    "provider": "gemini",
                    "status": "ok",
                    "evidence": _evidence(marker_found=False, response_excerpt="Hello there"),
                }
            ]
        },
        name="w4",
    )
    report_path = tmp_path / "evidence.json"

    result = _run("--worker", str(worker), "--report", str(report_path), "--assert")

    assert result.returncode == 1
    failed = {c["name"] for c in _report(report_path)["checks"] if not c["ok"]}
    assert "prompt-marker-echoed" in failed


def test_an_unproven_billing_tier_fails(tmp_path: Path) -> None:
    worker = _worker(
        tmp_path,
        {
            "attempts": [
                {
                    "provider": "groq",
                    "status": "ok",
                    "evidence": _evidence(billing_tier="unknown_cost", cost_usd=None),
                }
            ]
        },
        name="w5",
    )
    report_path = tmp_path / "evidence.json"

    result = _run("--worker", str(worker), "--report", str(report_path), "--assert")

    assert result.returncode == 1
    failed = {c["name"] for c in _report(report_path)["checks"] if not c["ok"]}
    assert "billing-tier-is-free" in failed


def test_evidence_without_an_id_or_time_fails(tmp_path: Path) -> None:
    worker = _worker(
        tmp_path,
        {
            "attempts": [
                {
                    "provider": "gemini",
                    "status": "ok",
                    "evidence": _evidence(
                        request_id=None, server_model_version=None, responded_at=""
                    ),
                }
            ]
        },
        name="w6",
    )
    report_path = tmp_path / "evidence.json"

    result = _run("--worker", str(worker), "--report", str(report_path), "--assert")

    assert result.returncode == 1
    failed = {c["name"] for c in _report(report_path)["checks"] if not c["ok"]}
    assert "evidence-has-id-time-and-sanitized-response" in failed


def test_a_leaked_credential_fails_the_gate(tmp_path: Path) -> None:
    leaked = "AIza" + "B" * 33
    worker = _worker(
        tmp_path,
        {
            "attempts": [
                {
                    "provider": "gemini",
                    "status": "ok",
                    "evidence": _evidence(response_excerpt=f"PAP-2027-LIVE-OK {leaked}"),
                }
            ]
        },
        name="w7",
    )
    report_path = tmp_path / "evidence.json"

    result = _run("--worker", str(worker), "--report", str(report_path), "--assert")

    assert result.returncode == 1
    payload = _report(report_path)
    failed = {c["name"] for c in payload["checks"] if not c["ok"]}
    assert "no-credential-material-in-evidence" in failed
    # The artifact keeps the verdict but never the credential itself.
    assert leaked not in report_path.read_text(encoding="utf-8")
    assert payload["redactions"] >= 1


def test_non_object_worker_output_exits_2(tmp_path: Path) -> None:
    worker = tmp_path / "list_worker.py"
    worker.write_text("print('[1, 2, 3]')\n", encoding="utf-8")
    report_path = tmp_path / "evidence.json"

    result = _run("--worker", str(worker), "--report", str(report_path), "--assert")

    assert result.returncode == 2
    assert "not an object" in _report(report_path)["error"]


def test_a_missing_worker_exits_2_with_a_reason(tmp_path: Path) -> None:
    report_path = tmp_path / "evidence.json"

    result = _run("--worker", str(tmp_path / "nope.py"), "--report", str(report_path), "--assert")

    assert result.returncode == 2
    payload = _report(report_path)
    assert payload["verdict"] == "FAIL"
    assert "worker not found" in payload["error"]


def test_worker_crash_exits_2(tmp_path: Path) -> None:
    worker = tmp_path / "crashing_worker.py"
    worker.write_text("raise SystemExit(3)\n", encoding="utf-8")
    report_path = tmp_path / "evidence.json"

    result = _run("--worker", str(worker), "--report", str(report_path), "--assert")

    assert result.returncode == 2
    assert _report(report_path)["verdict"] == "FAIL"


def test_a_broken_interpreter_exits_2_instead_of_passing(tmp_path: Path) -> None:
    report_path = tmp_path / "evidence.json"

    result = _run("--python", "/bin/false", "--report", str(report_path), "--assert")

    assert result.returncode == 2
    payload = _report(report_path)
    assert payload["verdict"] == "FAIL"
    assert payload["error"]
