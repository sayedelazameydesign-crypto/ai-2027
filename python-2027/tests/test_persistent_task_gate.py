"""The persistent-task gate must pass honestly and fail closed when tampered with."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

GATE = Path(__file__).resolve().parents[2] / "scripts" / "persistent_task_gate.py"


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed interpreter and repo-owned gate path
        [sys.executable, str(GATE), *args],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        cwd=str(cwd) if cwd else None,
    )


def test_gate_passes_and_writes_evidence(tmp_path: Path) -> None:
    report = tmp_path / "evidence.json"
    result = _run("--python", sys.executable, "--report", str(report), "--assert")

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["verdict"] == "PASS"
    assert payload["gate"] == "persistent-task"
    assert len(payload["checks"]) >= 9
    assert all(check["ok"] for check in payload["checks"])

    names = {check["name"] for check in payload["checks"]}
    assert "resume-started-at-step-3-not-step-1" in names
    assert "checkpoint-survived-the-kill" in names
    assert "artifacts-match-their-recorded-sha256" in names

    assert payload["session_before_resume"]["status"] == "running"
    assert payload["session_before_resume"]["checkpoint_step"] == 2
    assert payload["session_after_resume"]["status"] == "completed"
    assert payload["session_after_resume"]["steps"] == [1, 2, 3, 4]
    assert payload["phase_returncodes"] == [-9, 0]
    assert payload["session_sha256"]["before_resume"] != payload["session_sha256"]["after_resume"]


def test_gate_fails_closed_when_the_worker_never_crashes(tmp_path: Path) -> None:
    """A worker that always exits 0 must not be able to produce a PASS."""
    fake = tmp_path / "fake_worker.py"
    fake.write_text(
        "import sys\n"
        "args = dict(zip(sys.argv[1::2], sys.argv[2::2]))\n"
        "with open(args['--log'], 'a', encoding='utf-8') as log:\n"
        "    log.write('attempt:1\\ndone:1:' + '0' * 64 + '\\n')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    report = tmp_path / "evidence.json"
    result = _run("--worker", str(fake), "--report", str(report), "--assert")

    assert result.returncode == 1
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["verdict"] == "FAIL"
    failed = {check["name"] for check in payload["checks"] if not check["ok"]}
    assert "phase-1-killed-by-signal" in failed


def test_a_relative_interpreter_resolves_against_the_caller_cwd(tmp_path: Path) -> None:
    venv = tmp_path / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").symlink_to(sys.executable)
    report = tmp_path / "evidence.json"

    result = _run("--python", ".venv/bin/python", "--report", str(report), "--assert", cwd=tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(report.read_text(encoding="utf-8"))["verdict"] == "PASS"


def test_a_missing_interpreter_writes_a_fail_report(tmp_path: Path) -> None:
    report = tmp_path / "evidence.json"

    result = _run("--python", "./nowhere/python", "--report", str(report), "--assert", cwd=tmp_path)

    assert result.returncode == 1
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["verdict"] == "FAIL"
    assert "interpreter not found" in payload["error"]


def test_gate_reports_a_missing_worker_instead_of_passing(tmp_path: Path) -> None:
    report = tmp_path / "evidence.json"
    result = _run("--worker", str(tmp_path / "nope.py"), "--report", str(report), "--assert")

    assert result.returncode == 1
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["verdict"] == "FAIL"
    assert "worker not found" in payload["error"]


def test_gate_reports_a_broken_interpreter_instead_of_crashing(tmp_path: Path) -> None:
    """A broken interpreter must produce a FAIL report, never a traceback or a pass."""
    report = tmp_path / "evidence.json"
    result = _run("--python", "/bin/false", "--report", str(report))

    assert result.returncode == 0  # no --assert: the report carries the verdict
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["verdict"] == "FAIL"
    assert payload["error"]
    assert payload["checks"] and not any(check["ok"] for check in payload["checks"])


def _load_gate_module() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location("gate_under_test", GATE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


def test_the_interpreter_path_is_not_resolved_through_its_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A venv shim is a symlink to the base python; following it loses site-packages."""
    monkeypatch.chdir(tmp_path)
    venv = tmp_path / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").symlink_to(sys.executable)
    module = _load_gate_module()

    resolved = module.resolve_interpreter(".venv/bin/python")

    assert resolved.endswith(".venv/bin/python")
    assert Path(resolved).is_file()
