#!/usr/bin/env python3
"""PAP-2027 ``persistent-task`` gate: SIGKILL a real run, then resume it.

    python3 scripts/persistent_task_gate.py            # human table
    python3 scripts/persistent_task_gate.py --json     # machine report
    python3 scripts/persistent_task_gate.py --assert   # CI gate

What it does, in order:

1. starts ``python-2027/tests/resume_worker.py`` (the real ``AutonomousAgent``
   loop) with a step journal and ``--crash-on-step 3``;
2. the worker SIGKILLs itself **after** step 2 was checkpointed and **while**
   step 3 is executing — a real OS signal, not a simulated exception;
3. the gate reads the persisted ``session.json`` and proves the checkpoint is
   ``checkpoint_step == 2`` with status ``running``;
4. a second process resumes the same session (``resume=True``) and must start at
   step 3 — restarting from step 1 fails the gate;
5. every step must complete exactly once, artifacts must match their recorded
   SHA-256, and the report is written as JSON evidence.

A gate without a stored artifact did not run: ``--report`` is written on success
*and* on failure, and ``--assert`` exits non-zero when any check fails.

Stdlib only. Needs an interpreter that can import ``ai2027_protocol`` (pass
``--python .venv/bin/python`` from ``python-2027/``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "python-2027" / "tests" / "resume_worker.py"
DEFAULT_REPORT = ROOT / "launch" / "evidence" / "persistent-task-gate.json"
TASK = "PAP persistent-task gate run"
MAX_STEPS = 4
CRASH_ON_STEP = 3


def resolve_interpreter(value: str) -> str:
    """Resolve the worker interpreter against the caller's cwd, or leave it for the check.

    ``--python .venv/bin/python`` is what a human types from ``python-2027/``; the
    worker runs with ``cwd=repo root``, so a relative path has to be resolved here.
    """
    if os.sep in value or (os.altsep and os.altsep in value):
        # ``absolute()``, never ``resolve()``: a venv's ``bin/python`` is a symlink
        # to the base interpreter, and following it leaves the venv (and its
        # site-packages) behind.
        return str(Path(value).expanduser().absolute())
    return shutil.which(value) or value


def _missing_interpreter(python: str) -> str:
    if os.sep in python and not Path(python).is_file():
        return f"interpreter not found: {python}"
    return ""


@dataclass
class Check:
    """One falsifiable assertion in the gate."""

    name: str
    ok: bool
    detail: str


@dataclass
class GateResult:
    """Everything the gate observed, including the raw journal."""

    checks: list[Check] = field(default_factory=list)
    start_returncode: int | None = None
    resume_returncode: int | None = None
    start_log: list[str] = field(default_factory=list)
    resume_log: list[str] = field(default_factory=list)
    session_before_resume: dict = field(default_factory=dict)
    session_after_resume: dict = field(default_factory=dict)
    session_sha256_before: str = ""
    session_sha256_after: str = ""
    duration_ms: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        """Return True only when every check passed and no error was recorded."""
        return not self.error and bool(self.checks) and all(c.ok for c in self.checks)

    def check(self, name: str, ok: bool, detail: str) -> None:
        """Record one check result."""
        self.checks.append(Check(name=name, ok=ok, detail=detail))


def _run_worker(
    python: str, worker: Path, *, mode: str, work: Path, max_steps: int, crash_on_step: int
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            python,
            str(worker),
            "--mode",
            mode,
            "--studio-dir",
            str(work / "studio"),
            "--log",
            str(work / "journal.log"),
            "--session-file",
            str(work / "session_id.txt"),
            "--task",
            TASK,
            "--max-steps",
            str(max_steps),
            "--crash-on-step",
            str(crash_on_step),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def _read_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _parse_journal(lines: list[str]) -> tuple[list[int], dict[int, str], list[int]]:
    """Return (attempted steps, completed step -> sha256, crashed steps)."""
    attempts: list[int] = []
    done: dict[int, str] = {}
    crashes: list[int] = []
    for line in lines:
        parts = line.split(":")
        if parts[0] == "attempt" and len(parts) == 2:
            attempts.append(int(parts[1]))
        elif parts[0] == "done" and len(parts) == 3:
            done[int(parts[1])] = parts[2]
        elif parts[0] == "crash" and len(parts) == 2:
            crashes.append(int(parts[1]))
    return attempts, done, crashes


def _session_path(work: Path) -> Path:
    session_id = (work / "session_id.txt").read_text(encoding="utf-8").strip()
    return work / "studio" / "sessions" / session_id / "session.json"


def run_gate(*, python: str, worker: Path, work: Path) -> GateResult:
    """Execute the crash/resume scenario and return every observed fact."""
    result = GateResult()
    started = time.monotonic()

    if not worker.is_file():
        result.error = f"worker not found: {worker}"
        return result
    missing = _missing_interpreter(python)
    if missing:
        result.error = missing
        return result
    work.mkdir(parents=True, exist_ok=True)

    start = _run_worker(
        python,
        worker,
        mode="start",
        work=work,
        max_steps=MAX_STEPS,
        crash_on_step=CRASH_ON_STEP,
    )
    result.start_returncode = start.returncode
    if "ModuleNotFoundError" in (start.stderr or ""):
        result.error = (
            "the worker interpreter cannot import ai2027_protocol — run the gate with the "
            "python-2027 virtualenv interpreter (--python .venv/bin/python)"
        )
        return result

    result.start_log = _read_lines(work / "journal.log")
    attempts_a, done_a, crashes = _parse_journal(result.start_log)
    result.check(
        "phase-1-killed-by-signal",
        start.returncode == -9,
        f"phase 1 returncode={start.returncode} (expected -9 = SIGKILL)",
    )
    result.check(
        "phase-1-executed-steps-1-and-2",
        done_a and sorted(done_a) == [1, 2],
        f"completed steps before the crash: {sorted(done_a)}",
    )
    result.check(
        "phase-1-died-inside-step-3",
        crashes == [CRASH_ON_STEP] and attempts_a == [1, 2, 3],
        f"attempts={attempts_a} crashes={crashes}",
    )

    try:
        session_file = _session_path(work)
    except OSError:
        result.error = (
            f"phase 1 wrote no session id (returncode={start.returncode}): "
            f"{(start.stderr or start.stdout or '').strip()[:300]}"
        )
        return result
    if not session_file.is_file():
        result.error = f"session state was not persisted at {session_file}"
        return result
    raw_before = session_file.read_bytes()
    result.session_sha256_before = hashlib.sha256(raw_before).hexdigest()
    state_before = json.loads(raw_before.decode("utf-8"))
    result.session_before_resume = {
        "status": state_before.get("status"),
        "checkpoint_step": state_before.get("checkpoint_step"),
        "steps": [s.get("step") for s in state_before.get("steps", [])],
    }
    result.check(
        "checkpoint-survived-the-kill",
        state_before.get("status") == "running" and state_before.get("checkpoint_step") == 2,
        f"persisted status={state_before.get('status')} "
        f"checkpoint_step={state_before.get('checkpoint_step')}",
    )

    resume = _run_worker(
        python, worker, mode="resume", work=work, max_steps=MAX_STEPS, crash_on_step=0
    )
    result.resume_returncode = resume.returncode
    full_log = _read_lines(work / "journal.log")
    result.resume_log = full_log[len(result.start_log) :]
    attempts_all, done_all, _ = _parse_journal(full_log)
    resume_attempts, _, _ = _parse_journal(result.resume_log)

    result.check(
        "phase-2-exited-cleanly",
        resume.returncode == 0,
        f"phase 2 returncode={resume.returncode} stderr={(resume.stderr or '').strip()[:200]}",
    )
    result.check(
        "resume-started-at-step-3-not-step-1",
        bool(resume_attempts) and resume_attempts[0] == CRASH_ON_STEP,
        f"first attempted step after restart: {resume_attempts[0] if resume_attempts else None}",
    )
    result.check(
        "no-step-was-executed-twice",
        attempts_all.count(1) == 1 and attempts_all.count(2) == 1,
        f"attempts per step: {{1: {attempts_all.count(1)}, 2: {attempts_all.count(2)}, "
        f"3: {attempts_all.count(3)}}}",
    )
    result.check(
        "every-step-completed-exactly-once",
        sorted(done_all) == list(range(1, MAX_STEPS + 1)),
        f"completed steps: {sorted(done_all)}",
    )

    raw_after = session_file.read_bytes()
    result.session_sha256_after = hashlib.sha256(raw_after).hexdigest()
    state_after = json.loads(raw_after.decode("utf-8"))
    result.session_after_resume = {
        "status": state_after.get("status"),
        "checkpoint_step": state_after.get("checkpoint_step"),
        "steps": [s.get("step") for s in state_after.get("steps", [])],
        "artifacts": [a.get("path") for a in state_after.get("artifacts", [])],
    }
    result.check(
        "final-session-is-completed-with-all-steps",
        state_after.get("status") == "completed"
        and [s.get("step") for s in state_after.get("steps", [])] == list(range(1, MAX_STEPS + 1)),
        f"status={state_after.get('status')} "
        f"steps={[s.get('step') for s in state_after.get('steps', [])]}",
    )

    artifacts = state_after.get("artifacts", [])
    hashes_match = bool(artifacts) and all(
        done_all.get(int(a["path"].split("-")[1].split(".")[0])) == a.get("sha256")
        for a in artifacts
    )
    result.check(
        "artifacts-match-their-recorded-sha256",
        hashes_match,
        f"{len(artifacts)} artifacts, sha256 verified against the journal",
    )

    result.duration_ms = round((time.monotonic() - started) * 1000.0, 1)
    return result


def render(result: GateResult) -> str:
    """Return a readable table of the gate verdict."""
    lines = [f"{'Check':<44} | Verdict | Detail", "-" * 110]
    for check in result.checks:
        verdict = "PASS" if check.ok else "FAIL"
        lines.append(f"{check.name:<44} | {verdict:<7} | {check.detail}")
    lines.append("-" * 110)
    lines.append(
        f"verdict={'PASS' if result.ok else 'FAIL'} · checks={len(result.checks)} · "
        f"{result.duration_ms} ms · session sha256 "
        f"{result.session_sha256_before[:12]}→{result.session_sha256_after[:12]}"
    )
    if result.error:
        lines.append(f"error: {result.error}")
    return "\n".join(lines)


def write_report(result: GateResult, path: Path) -> None:
    """Persist the gate evidence; a gate without an artifact did not run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "gate": "persistent-task",
        "protocol": "PAP-2027",
        "verdict": "PASS" if result.ok else "FAIL",
        "error": result.error,
        "duration_ms": result.duration_ms,
        "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in result.checks],
        "phase_returncodes": [result.start_returncode, result.resume_returncode],
        "session_sha256": {
            "before_resume": result.session_sha256_before,
            "after_resume": result.session_sha256_after,
        },
        "session_before_resume": result.session_before_resume,
        "session_after_resume": result.session_after_resume,
        "journal": {"phase_1": result.start_log, "phase_2": result.resume_log},
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Run the gate and return a process exit code."""
    parser = argparse.ArgumentParser(
        description="PAP-2027 persistent-task gate (SIGKILL + resume)."
    )
    parser.add_argument(
        "--python", default=sys.executable, help="Interpreter that can import the package."
    )
    parser.add_argument("--worker", type=Path, default=WORKER, help="Resume worker script.")
    parser.add_argument("--work-dir", type=Path, default=None, help="Keep the run directory here.")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="Evidence JSON path.")
    parser.add_argument("--json", action="store_true", help="Print the report JSON.")
    parser.add_argument(
        "--assert", dest="assert_ok", action="store_true", help="Fail on any check."
    )
    args = parser.parse_args(argv)

    owned_temp = args.work_dir is None
    work = args.work_dir or Path(tempfile.mkdtemp(prefix="pap-persistent-task-"))
    try:
        result = run_gate(python=resolve_interpreter(args.python), worker=args.worker, work=work)
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

    if owned_temp and not args.json:
        print(f"run directory kept for inspection: {work}")

    # Opt-in: ``::error::`` marks the *step* as failed, which is right for a real
    # gate run and wrong for the tests that deliberately provoke a FAIL verdict.
    if not result.ok and os.environ.get("PAP_GATE_ANNOTATE") == "1":
        reason = result.error or "; ".join(
            f"{check.name}: {check.detail}" for check in result.checks if not check.ok
        )
        print(f"::error title=PAP-2027 persistent-task gate::{reason[:900]}")

    if args.assert_ok and not result.ok:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
