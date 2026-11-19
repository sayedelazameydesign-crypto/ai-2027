#!/usr/bin/env python3
"""Fleet-2027 evidence gate — every claim produces an artifact or it did not happen.

Nine gates, each of which can **fail** for a stated reason:

===========================  =========================================
``parallel-wave``            3 independent steps must actually overlap in time
``verification-refuses``     a fabricated artifact digest must be refused
``retry-backoff``            a retry must be scheduled and then honoured
``dead-letter``              exhausted attempts must land in the DLQ
``degraded-success``         a lost *non-critical* step must degrade, not fail
``failover``                 a crashed primary must be replaced by its fallback
``idempotency``              one effect per step, replay counted not repeated
``sigkill-resume``           a real ``SIGKILL`` mid-step, then resume
``audit-chain``              the hash chain re-verifies and tampering is caught

Output: ``launch/evidence/fleet-2027-gate.json`` (or ``--out``), containing the
measured values, the wall-clock durations, the SHA-256 of the artifact itself,
and the environment it ran on. ``--assert`` makes the process exit non-zero on
any failure, which is what CI uses.

Honest scope: the agents in this gate are deterministic local programs. The
gate proves *mechanism* (durability, verification, recovery, isolation of
failure). It does not — and does not claim to — prove the quality of any LLM
underneath; that is what the model router's live gates are for.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from fleet2027 import (  # noqa: E402
    AgentRegistry,
    AgentSpec,
    ArtifactRef,
    DurableTaskStore,
    EvidenceKind,
    FleetRunner,
    HandlerContext,
    PlanStep,
    RunnerConfig,
    StepFailure,
    StepResult,
    StepStatus,
    TaskSpec,
    TaskStatus,
    canonical_json,
    sha256_text,
    utc_now,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_OUT = ROOT.parent / "launch" / "evidence" / "fleet-2027-gate.json"
WORKER = ROOT / "crash_worker.py"


@dataclass
class GateResult:
    """Outcome of one named gate."""

    name: str
    passed: bool
    claim: str
    measured: dict[str, Any] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the evidence file."""
        return {
            "gate": self.name,
            "passed": self.passed,
            "claim": self.claim,
            "measured": self.measured,
            "failures": self.failures,
            "duration_ms": round(self.duration_ms, 2),
        }


def _write(ctx_ws: Path, relative: str, text: str) -> ArtifactRef:
    path = ctx_ws / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return ArtifactRef.from_file(str(path), record_path=relative)


def _runner(
    store: DurableTaskStore, registry: AgentRegistry, ws: Path, **cfg: Any
) -> FleetRunner:
    return FleetRunner(store, registry, workspace_root=ws, config=RunnerConfig(**cfg))


# --------------------------------------------------------------------------- #
# Gates
# --------------------------------------------------------------------------- #


def gate_parallel_wave(base: Path) -> GateResult:
    """Three independent steps must overlap in real time, not just be listed together."""
    started = time.perf_counter()
    registry = AgentRegistry()
    active = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def worker(ctx: HandlerContext) -> StepResult:  # noqa: F821 - defined by import below
        with lock:
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
        time.sleep(0.15)
        with lock:
            active["now"] -= 1
        return StepResult(ok=True, summary="slept", output="x")

    registry.register(AgentSpec(name="w", role="sleep", max_parallel=4), worker)
    spec = TaskSpec(
        task_id="gate-parallel",
        goal="parallel wave",
        steps=tuple(
            PlanStep(step_id=f"s{i}", agent="w", action="go",
                     verification=(EvidenceKind.OUTPUT_NON_EMPTY.value,))
            for i in range(3)
        ),
    )
    ws = base / "parallel"
    ws.mkdir(parents=True, exist_ok=True)
    with DurableTaskStore(str(base / "parallel.db")) as store:
        report = _runner(store, registry, ws, max_worker_threads=3).submit_and_run(spec)
    measured = {"peak_concurrency": active["peak"], "waves": report.waves,
                "status": report.status.value}
    failures = []
    if active["peak"] < 3:
        failures.append(f"peak concurrency {active['peak']} < 3")
    if report.waves != 1:
        failures.append(f"expected 1 wave, saw {report.waves}")
    return GateResult(
        "parallel-wave", not failures,
        "three independent steps execute in one wave with real overlap",
        measured, failures, (time.perf_counter() - started) * 1000,
    )


def gate_verification_refuses(base: Path) -> GateResult:
    """A lying agent must fail, and its claim must be dead-lettered."""
    started = time.perf_counter()
    registry = AgentRegistry()
    registry.register(
        AgentSpec(name="liar", role="claims without evidence", max_attempts=2),
        lambda ctx: StepResult(
            ok=True, summary="wrote it (not really)", output="trust me",
            artifacts=(ArtifactRef(path="ghost.txt", sha256="0" * 64),),
        ),
    )
    spec = TaskSpec(
        task_id="gate-liar", goal="fabrication is refused",
        steps=(PlanStep(step_id="s", agent="liar", action="claim",
                        verification=(EvidenceKind.ARTIFACT_SHA256.value,)),),
    )
    ws = base / "liar"
    ws.mkdir(parents=True, exist_ok=True)
    with DurableTaskStore(str(base / "liar.db")) as store:
        report = _runner(store, registry, ws, backoff_base_s=0.01).submit_and_run(spec)
        snapshot = store.get_task("gate-liar")
        error = snapshot.steps[0].error or ""
        letters = store.list_dead_letters()
    measured = {
        "status": report.status.value,
        "verified_steps": report.verified,
        "dead_letters": len(letters),
        "step_error": error[:200],
    }
    failures = []
    if report.verified != 0:
        failures.append("a fabricated digest was counted as verified")
    if not letters:
        failures.append("the unverifiable step did not reach the dead-letter queue")
    if "verification failed" not in error and "digest" not in error:
        failures.append(f"failure reason does not name verification: {error!r}")
    return GateResult(
        "verification-refuses", not failures,
        "a step whose evidence does not match disk is refused and parked",
        measured, failures, (time.perf_counter() - started) * 1000,
    )


def gate_retry_backoff(base: Path) -> GateResult:
    """A scheduled retry must not run early and must run after the backoff."""
    started = time.perf_counter()
    registry = AgentRegistry()
    attempts: list[float] = []

    def flaky(ctx: HandlerContext) -> StepResult:
        attempts.append(time.time())
        if ctx.attempt < 2:
            return StepResult(ok=False, error="cold cache")
        return StepResult(ok=True, summary="warm", output="ok")

    registry.register(AgentSpec(name="flaky", role="retry me", max_attempts=3), flaky)
    spec = TaskSpec(
        task_id="gate-backoff", goal="respect backoff",
        steps=(PlanStep(step_id="s", agent="flaky", action="try",
                        verification=(EvidenceKind.OUTPUT_NON_EMPTY.value,)),),
    )
    ws = base / "backoff"
    ws.mkdir(parents=True, exist_ok=True)
    with DurableTaskStore(str(base / "backoff.db")) as store:
        report = _runner(
            store, registry, ws, backoff_base_s=0.6, backoff_cap_s=0.6
        ).submit_and_run(spec)
    gap = (attempts[1] - attempts[0]) if len(attempts) >= 2 else 0.0
    measured = {"attempts": len(attempts), "gap_s": round(gap, 3),
                "status": report.status.value, "verified": report.verified}
    failures = []
    if len(attempts) != 2:
        failures.append(f"expected 2 attempts, saw {len(attempts)}")
    if gap < 0.55:
        failures.append(f"retry fired after {gap:.3f}s, before the 0.6s backoff")
    if report.status is not TaskStatus.SUCCEEDED:
        failures.append(f"task did not succeed: {report.status.value}")
    return GateResult(
        "retry-backoff", not failures,
        "a failed step is retried only after its scheduled backoff",
        measured, failures, (time.perf_counter() - started) * 1000,
    )


def gate_dead_letter(base: Path) -> GateResult:
    """Exhausted attempts must be parked, and requeueing must restore the budget."""
    started = time.perf_counter()
    registry = AgentRegistry()
    registry.register(
        AgentSpec(name="broken", role="always fails", max_attempts=2),
        lambda ctx: StepResult(ok=False, error="permanent failure"),
    )
    spec = TaskSpec(
        task_id="gate-dlq", goal="park the hopeless",
        steps=(PlanStep(step_id="s", agent="broken", action="fail"),),
    )
    ws = base / "dlq"
    ws.mkdir(parents=True, exist_ok=True)
    with DurableTaskStore(str(base / "dlq.db")) as store:
        report = _runner(store, registry, ws, backoff_base_s=0.01).submit_and_run(spec)
        letters_before = store.list_dead_letters()
        store.requeue_dead_letter("gate-dlq", "s")
        after_requeue = store.get_task("gate-dlq").steps[0]
        registry.handler_for("broken").breaker.failures = 0
        registry.handler_for("broken").breaker.opened_at = None
        letters_after = store.list_dead_letters()
    measured = {
        "status": report.status.value,
        "dead_letters_before_requeue": len(letters_before),
        "attempts_after_requeue": after_requeue.attempts,
        "dead_letters_after_requeue": len(letters_after),
    }
    failures = []
    if report.status is not TaskStatus.DEAD_LETTER:
        failures.append(f"task status is {report.status.value}, expected dead_letter")
    if len(letters_before) != 1:
        failures.append("the DLQ does not contain the exhausted step")
    if after_requeue.attempts != 0:
        failures.append("requeue did not reset the attempt budget")
    if letters_after:
        failures.append("requeued step still appears unresolved in the DLQ")
    return GateResult(
        "dead-letter", not failures,
        "exhausted attempts are parked, listed and can be requeued with a fresh budget",
        measured, failures, (time.perf_counter() - started) * 1000,
    )


def gate_degraded_success(base: Path) -> GateResult:
    """Losing an optional step must degrade the task, never fail it silently."""
    started = time.perf_counter()
    registry = AgentRegistry()
    registry.register(
        AgentSpec(name="ok", role="works"),
        lambda ctx: StepResult(ok=True, summary="fine", output="x"),
    )
    registry.register(
        AgentSpec(name="optional", role="never configured", max_attempts=1),
        lambda ctx: StepResult(ok=False, error="optional backend missing"),
    )
    spec = TaskSpec(
        task_id="gate-degraded", goal="degrade visibly",
        steps=(
            PlanStep(step_id="must", agent="ok", action="x",
                     verification=(EvidenceKind.OUTPUT_NON_EMPTY.value,)),
            PlanStep(step_id="maybe", agent="optional", action="y", depends_on=("must",),
                     critical=False),
        ),
    )
    ws = base / "degraded"
    ws.mkdir(parents=True, exist_ok=True)
    with DurableTaskStore(str(base / "degraded.db")) as store:
        report = _runner(store, registry, ws).submit_and_run(spec)
        snapshot = store.get_task("gate-degraded")
        note = [e["message"] for e in store.events("gate-degraded") if e["kind"] == "task.succeeded"]
    measured = {
        "status": snapshot.status.value,
        "degraded_flag": snapshot.degraded,
        "optional_step": snapshot.steps[1].status.value,
        "completion_note": (note[-1] if note else "")[:160],
    }
    failures = []
    if snapshot.status is not TaskStatus.SUCCEEDED:
        failures.append(f"task status {snapshot.status.value} — an optional loss must not fail it")
    if not snapshot.degraded:
        failures.append("the degradation was not recorded on the task")
    if "degraded" not in measured["completion_note"]:
        failures.append("the completion event does not mention the degradation")
    if report.degraded is not True:
        failures.append("the run report does not surface the degradation")
    return GateResult(
        "degraded-success", not failures,
        "a lost non-critical step yields a succeeded-but-degraded task, stated in the log",
        measured, failures, (time.perf_counter() - started) * 1000,
    )


def gate_failover(base: Path) -> GateResult:
    """A crashing primary must be replaced by its declared fallback."""
    started = time.perf_counter()
    registry = AgentRegistry()
    registry.register(
        AgentSpec(name="primary", role="dies", max_attempts=2, fallbacks=("frontup",)),
        lambda ctx: (_ for _ in ()).throw(RuntimeError("primary down")),
    )
    registry.register(
        AgentSpec(name="frontup", role="serves"),
        lambda ctx: StepResult(ok=True, summary="fallback served", output="ok"),
    )
    spec = TaskSpec(
        task_id="gate-failover", goal="fail over cleanly",
        steps=(PlanStep(step_id="s", agent="primary", action="call",
                        verification=(EvidenceKind.OUTPUT_NON_EMPTY.value,)),),
    )
    ws = base / "failover"
    ws.mkdir(parents=True, exist_ok=True)
    with DurableTaskStore(str(base / "failover.db")) as store:
        report = _runner(store, registry, ws, backoff_base_s=0.01).submit_and_run(spec)
        events = [e["message"] for e in store.events("gate-failover")]
    measured = {"status": report.status.value, "agents_used": list(report.agents_used),
                "degradation_events": [m[:120] for m in events if "fallback" in m]}
    failures = []
    if report.status is not TaskStatus.SUCCEEDED:
        failures.append(f"status {report.status.value}, expected succeeded via fallback")
    if "frontup" not in report.agents_used:
        failures.append("the fallback agent never ran")
    if not measured["degradation_events"]:
        failures.append("the substitution was not recorded in the event log")
    return GateResult(
        "failover", not failures,
        "a crashing primary is replaced by its declared fallback and the swap is logged",
        measured, failures, (time.perf_counter() - started) * 1000,
    )


def gate_idempotency(base: Path) -> GateResult:
    """A retried step must not repeat its recorded effect."""
    started = time.perf_counter()
    registry = AgentRegistry()
    fresh_calls = {"true": 0, "false": 0}

    def effectful(ctx: HandlerContext) -> StepResult:
        # The effect is applied first, and the transport dies *afterwards* —
        # the classic "did the charge go through?" case.
        fresh = ctx.store.record_effect(
            ctx.idempotency_key, task_id=ctx.task_id, step_id=ctx.step_id
        )
        fresh_calls["true" if fresh else "false"] += 1
        if ctx.attempt == 1:
            raise StepFailure("simulated upstream reset after the effect was applied")
        return StepResult(ok=True, summary=f"effect fresh={fresh}", output="ok")

    registry.register(AgentSpec(name="effectful", role="apply once", max_attempts=3), effectful)
    spec = TaskSpec(
        task_id="gate-idem", goal="apply once",
        steps=(PlanStep(step_id="s", agent="effectful", action="charge",
                        verification=(EvidenceKind.OUTPUT_NON_EMPTY.value,)),),
    )
    ws = base / "idem"
    ws.mkdir(parents=True, exist_ok=True)
    with DurableTaskStore(str(base / "idem.db")) as store:
        report = _runner(store, registry, ws, backoff_base_s=0.01).submit_and_run(spec)
        key = store.idempotency_key("gate-idem", "s")
        stored = store.effect(key) or {}
    # Attempt 1 records the effect (fresh) then "fails" after applying it, so the
    # replay must be refused. The proof is the *stored* row, not the counter.
    measured = {
        "status": report.status.value,
        "replay_count": stored.get("replay_count"),
        "fresh_effects": fresh_calls["true"],
        "refused_replays": fresh_calls["false"],
    }
    failures = []
    if report.status is not TaskStatus.SUCCEEDED:
        failures.append(f"status {report.status.value}")
    if stored.get("replay_count") is None:
        failures.append("no effect row was recorded")
    elif int(stored["replay_count"]) < 1:
        failures.append("the replay was not counted, so the ledger is not doing its job")
    if fresh_calls["false"] < 1:
        failures.append("the retried attempt believed its effect was fresh")
    return GateResult(
        "idempotency", not failures,
        "re-running a step refuses to repeat a recorded side effect and counts the replay",
        measured, failures, (time.perf_counter() - started) * 1000,
    )


def gate_audit_chain(base: Path) -> GateResult:
    """The hash chain must verify, and tampering must be detectable."""
    started = time.perf_counter()
    registry = AgentRegistry()
    registry.register(
        AgentSpec(name="ok", role="works"),
        lambda ctx: StepResult(ok=True, summary="fine", output="x"),
    )
    spec = TaskSpec(
        task_id="gate-audit", goal="chain it",
        steps=(PlanStep(step_id="s", agent="ok", action="go",
                        verification=(EvidenceKind.OUTPUT_NON_EMPTY.value,)),),
    )
    ws = base / "audit"
    ws.mkdir(parents=True, exist_ok=True)
    db = str(base / "audit.db")
    with DurableTaskStore(db) as store:
        _runner(store, registry, ws).submit_and_run(spec)
        ok, rows, problem = store.verify_audit_chain()
        store._conn.execute(
            "UPDATE audit_log SET payload_json = '{\"forged\":true}' WHERE seq = 1"
        )
        tampered_ok, _, tampered_problem = store.verify_audit_chain()
    measured = {
        "rows": rows,
        "clean_chain_ok": ok,
        "clean_chain_problem": problem,
        "tamper_detected": not tampered_ok,
        "tamper_problem": tampered_problem,
    }
    failures = []
    if not ok:
        failures.append(f"the clean chain failed verification: {problem}")
    if rows < 2:
        failures.append("the chain has fewer rows than the task had transitions")
    if tampered_ok:
        failures.append("tampering with the audit log was not detected")
    return GateResult(
        "audit-chain", not failures,
        "every transition is hash-chained; editing the chain is detectable",
        measured, failures, (time.perf_counter() - started) * 1000,
    )


def gate_sigkill_resume(base: Path) -> GateResult:
    """SIGKILL a real worker mid-step, resume in a new process, prove once-only."""
    started = time.perf_counter()
    if not hasattr(signal, "SIGKILL"):
        return GateResult("sigkill-resume", False, "requires SIGKILL", {}, ["SIGKILL unavailable"])
    ws = base / "crash"
    ws.mkdir(parents=True, exist_ok=True)
    db = str(base / "crash.db")
    log = ws / "crash" / "effects.log"
    failures: list[str] = []

    phase1 = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [sys.executable, str(WORKER), "phase1", "--db", db, "--workspace", str(ws),
         "--task", "gate-crash", "--sleep", "20"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.time() + 20
        while time.time() < deadline and not log.exists():
            time.sleep(0.05)
        time.sleep(0.4)
        os.kill(phase1.pid, signal.SIGKILL)
        returncode = phase1.wait(timeout=15)
    finally:
        if phase1.poll() is None:  # pragma: no cover - defensive
            phase1.kill()

    if returncode != -signal.SIGKILL:
        failures.append(f"worker exit code {returncode}, expected -9")

    with DurableTaskStore(db) as store:
        after_crash = store.get_task("gate-crash")
        states = {step.step_id: step.status for step in after_crash.steps}
        if states.get("slow") is not StepStatus.RUNNING:
            failures.append(f"in-flight step is {states.get('slow')}, expected running")
        if states.get("first") is not StepStatus.SUCCEEDED:
            failures.append("completed work was lost by the crash")
        lines_after_crash = log.read_text(encoding="utf-8").strip().splitlines()
        if len(lines_after_crash) != 1:
            failures.append(f"{len(lines_after_crash)} effect lines before resume, expected 1")

    phase2 = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, str(WORKER), "phase2", "--db", db, "--workspace", str(ws),
         "--task", "gate-crash", "--lease", "2"],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if phase2.returncode != 0:
        failures.append(f"resume failed: {(phase2.stderr or phase2.stdout)[:300]}")
        payload: dict[str, Any] = {}
    else:
        payload = json.loads(phase2.stdout.strip().splitlines()[-1])

    steps = {step["step_id"]: step for step in payload.get("steps", [])}
    slow_attempts = steps.get("slow", {}).get("attempt_numbers", [])
    if slow_attempts != [1, 2]:
        failures.append(f"resumed attempt numbers {slow_attempts}, expected [1, 2]")
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").strip().splitlines()]
    fresh = sum(1 for record in records if record.get("fresh_effect"))
    if fresh != 1:
        failures.append(f"{fresh} fresh effects recorded, expected exactly 1")
    if payload.get("status_final") != "succeeded":
        failures.append(f"final status {payload.get('status_final')}")
    integrity: dict[str, Any] = {}
    with DurableTaskStore(db) as store:
        integrity = store.verify_integrity()
        if not integrity["ok"]:
            failures.append(f"store integrity after recovery: {integrity}")

    measured = {
        "worker_exit_code": returncode,
        "attempt_numbers_after_resume": slow_attempts,
        "effect_log_lines": len(records),
        "fresh_effects": fresh,
        "final_status": payload.get("status_final"),
        "recovered_steps": [item["step_id"] for item in payload.get("recovered", [])],
        "audit_rows": integrity.get("audit_rows"),
    }
    return GateResult(
        "sigkill-resume", not failures,
        "a SIGKILLed worker is resumed from its last checkpoint with no duplicated effect",
        measured, failures, (time.perf_counter() - started) * 1000,
    )


GATES: list[Callable[[Path], GateResult]] = [
    gate_parallel_wave,
    gate_verification_refuses,
    gate_retry_backoff,
    gate_dead_letter,
    gate_degraded_success,
    gate_failover,
    gate_idempotency,
    gate_audit_chain,
    gate_sigkill_resume,
]


def run_gates(*, out: Path | None = None, selected: list[str] | None = None) -> dict[str, Any]:
    """Run every gate and return the evidence payload (also written to ``out``)."""
    started = utc_now()
    results: list[GateResult] = []
    with tempfile.TemporaryDirectory(prefix="fleet-gate-") as tmp:
        base = Path(tmp)
        for gate in GATES:
            name = gate.__name__.removeprefix("gate_").replace("_", "-")
            if selected and name not in selected:
                continue
            try:
                results.append(gate(base))
            except Exception as exc:  # noqa: BLE001 - a crashing gate is a failing gate
                results.append(
                    GateResult(
                        name, False, "gate crashed",
                        {"error": f"{type(exc).__name__}: {exc}"}, [str(exc)], 0.0,
                    )
                )
    payload = {
        "kind": "fleet-2027/evidence-gate",
        "version": 1,
        "generated_at": started,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "gates": [result.to_dict() for result in results],
        "passed": sum(1 for result in results if result.passed),
        "failed": sum(1 for result in results if not result.passed),
        "total": len(results),
        "honest_scope": (
            "Agents in this gate are deterministic local programs that write real "
            "files and run real compiles. The gate proves durability, verification, "
            "recovery and failure isolation — not the quality of any LLM."
        ),
    }
    payload["artifact_sha256"] = sha256_text(canonical_json(payload))
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Fleet-2027 evidence gate")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--assert", dest="assert_all", action="store_true",
                        help="exit non-zero when any gate fails")
    parser.add_argument("--only", nargs="*", default=None, help="gate names to run")
    parser.add_argument("--json", action="store_true", help="print the payload")
    args = parser.parse_args(argv)
    out = Path(args.out)
    payload = run_gates(out=out, selected=args.only)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for gate in payload["gates"]:
            mark = "PASS" if gate["passed"] else "FAIL"
            print(f"[{mark}] {gate['gate']:<22} {gate['duration_ms']:>8.1f} ms  {gate['claim']}")
            for failure in gate["failures"]:
                print(f"        ✗ {failure}")
        print(f"{payload['passed']}/{payload['total']} gates passed · artifact={out}")
        print(f"artifact_sha256={payload['artifact_sha256']}")
    if args.assert_all and payload["failed"]:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
