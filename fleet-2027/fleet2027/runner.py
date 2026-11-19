"""Fleet runner: dependency-ordered waves, parallel execution, verify-then-commit.

The loop is deliberately boring, which is the point:

1. ``recover`` — requeue anything whose lease expired (crash, SIGKILL, network).
2. ``claim``   — atomically take every executable step in one transaction.
3. ``execute`` — run the wave in threads with per-step timeouts and heartbeats.
4. ``verify``  — re-derive evidence from disk; a failed verification is a
   *failure with a reason*, and the step is retried (bounded by attempts).
5. ``commit``  — write the result and the idempotency ledger in one transaction.
6. repeat until every step is terminal, or the task is cancelled / dead-lettered.

Honest limits, stated here rather than discovered in production:

* Threads share a process. A hard OS-level crash takes the whole wave with it;
  that is precisely what the lease + ``recover`` path exists for, and the
  durability gate kills a real process to prove it.
* ``timeout`` is enforced *around* the handler call. A handler that blocks
  inside a C extension cannot be interrupted from Python; that case is left to
  the lease expiry, which is why heartbeats are separate from timeouts.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .contracts import (
    TERMINAL_TASK_STATUSES,
    ContractError,
    PlanStep,
    StepResult,
    StepSnapshot,
    TaskSnapshot,
    TaskSpec,
    TaskStatus,
    VerificationReport,
    utc_now,
)
from .registry import AgentEntry, AgentRegistrationError, AgentRegistry, HandlerContext
from .store import DurableTaskStore
from .verifier import Reviewer, VerificationPolicy, verify_step

logger = logging.getLogger("fleet2027.runner")


class FleetError(RuntimeError):
    """Raised when a task cannot be executed as specified."""


class StepFailure(RuntimeError):
    """Raised by a handler for a retryable, non-crashing failure.

    Use it for "the remote provider returned 503" or "the cache was cold".
    Use it instead of returning ``ok=False`` when the failure should count
    against the circuit breaker as an infrastructure problem, not a business
    result.
    """


@dataclass
class RunnerConfig:
    """Explicit, inspectable runtime policy (no hidden magic numbers)."""

    lease_seconds: float = 30.0
    heartbeat_interval_s: float = 5.0
    default_step_timeout_s: float = 60.0
    max_waves: int = 64
    backoff_base_s: float = 0.25
    backoff_cap_s: float = 8.0
    #: Longest single backoff the runner will sit out; beyond this it stops and
    #: reports "incomplete" rather than blocking a caller indefinitely.
    max_backoff_wait_s: float = 30.0
    max_worker_threads: int = 8
    verification_policy: VerificationPolicy = field(default_factory=VerificationPolicy)
    on_event: Callable[[dict[str, Any]], None] | None = None


@dataclass
class TaskRunReport:
    """Everything a caller needs to judge a run without reading the database."""

    task_id: str
    status: TaskStatus
    waves: int
    executed: int
    verified: int
    failed: int
    skipped: int
    dead_letters: int
    degraded: bool
    duration_ms: float
    verifications: dict[str, VerificationReport] = field(default_factory=dict)
    agents_used: tuple[str, ...] = ()
    per_step_ms: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for CLI/JSON output."""
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "waves": self.waves,
            "executed": self.executed,
            "verified": self.verified,
            "failed": self.failed,
            "skipped": self.skipped,
            "dead_letters": self.dead_letters,
            "degraded": self.degraded,
            "duration_ms": round(self.duration_ms, 3),
            "verifications": {k: v.to_dict() for k, v in self.verifications.items()},
            "agents_used": list(self.agents_used),
            "per_step_ms": {k: round(v, 3) for k, v in self.per_step_ms.items()},
        }


@dataclass
class _Outcome:
    """Internal carrier: one finished wave member."""

    step_id: str
    agent: str
    status: str  # "succeeded" | "failed" | "timeout" | "unavailable" | "crashed"
    result: StepResult | None
    verification: VerificationReport | None
    error: str | None
    duration_ms: float
    metadata: dict[str, Any] = field(default_factory=dict)


class FleetRunner:
    """Executes a durable task by running its DAG in dependency-ordered waves."""

    def __init__(
        self,
        store: DurableTaskStore,
        registry: AgentRegistry,
        *,
        config: RunnerConfig | None = None,
        workspace_root: Path | str = ".",
        reviewer: Reviewer | None = None,
        scratch_root: Path | str | None = None,
    ) -> None:
        """Bind a store, a registry and a policy; nothing executes yet."""
        self.store = store
        self.registry = registry
        self.config = config or RunnerConfig()
        self.workspace_root = Path(workspace_root).resolve()
        self.reviewer = reviewer
        self.scratch_root = Path(scratch_root) if scratch_root else self.workspace_root / ".fleet-scratch"
        self.worker_id = f"worker-{threading.get_ident()}-{int(time.time() * 1000) % 100000}"
        self._stop = threading.Event()
        self.recovered_leases = False
        self.lease_seconds_used = self.config.lease_seconds

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def request_stop(self) -> None:
        """Ask the runner to stop before starting the next wave (graceful)."""
        self._stop.set()

    def resume(self, task_id: str) -> TaskRunReport:
        """Requeue every expired lease, then continue an interrupted task.

        Returns the run report; ``recovered`` is exposed through
        :meth:`recover` so a caller can record how many steps were requeued.
        """
        self.recover()
        return self.run(task_id)

    def recover(self, *, force: bool = False, reason: str = "operator forced") -> tuple[dict[str, Any], ...]:
        """Requeue steps whose owner heartbeat expired (callable by any process).

        ``force=True`` skips the expiry check and is recorded in the audit
        chain. Use it only when you know the previous worker is dead — that is
        the ``SIGKILL`` case, and the gate uses it deliberately.
        """
        self.recovered_leases = True
        self.lease_seconds_used = self.config.lease_seconds
        if force:
            return self.store.force_recover_running_steps(reason=reason)
        return self.store.recover_expired_leases(
            lease_seconds=self.config.lease_seconds, now_ts=time.time()
        )

    def submit_and_run(self, spec: TaskSpec, *, worker: str | None = None) -> TaskRunReport:
        """Idempotently submit ``spec`` and run it to a terminal state."""
        self.store.submit(spec, worker=worker or self.worker_id)
        return self.run(spec.task_id)

    def run(self, task_id: str) -> TaskRunReport:
        """Run one task to a terminal state or until the wave budget is spent."""
        started = time.perf_counter()
        spec = self.store.spec(task_id)
        step_by_id = {step.step_id: step for step in spec.steps}
        verifications: dict[str, VerificationReport] = {}
        executed = 0
        verified = 0
        failed = 0
        waves = 0
        per_step_ms: dict[str, float] = {}
        agents_used: list[str] = []
        last_error: str | None = None

        while waves < self.config.max_waves:
            if self._stop.is_set():
                break
            snapshot = self.store.get_task(task_id)
            if snapshot.status in TERMINAL_TASK_STATUSES:
                break
            claimed = self.store.claim_ready_steps(
                task_id,
                worker=self.worker_id,
                lease_seconds=self.config.lease_seconds,
                limit=min(64, self.config.max_worker_threads * 4),
            )
            if not claimed:
                # Nothing is runnable *right now*. Distinguish the three
                # honest reasons before giving up, so the report explains
                # itself instead of saying "stuck".
                finished = self.store.finalize(task_id)
                if finished in TERMINAL_TASK_STATUSES:
                    break
                pending = self.store.get_task(task_id).pending_steps()
                if not pending:
                    break
                delay = self.store.next_attempt_delay(task_id, now_ts=time.time())
                if delay is not None and delay <= self.config.max_backoff_wait_s:
                    # A retry is scheduled: wait for it rather than burning waves.
                    time.sleep(delay + 0.01)
                    continue
                recovered = self.store.recover_expired_leases(
                    lease_seconds=self.config.lease_seconds, now_ts=time.time()
                )
                if recovered:
                    continue
                stuck = ", ".join(f"{s.step_id}:{s.status.value}" for s in pending[:6])
                last_error = f"no runnable step remains and no retry is scheduled ({stuck})"
                break
            waves += 1
            outcomes = self._execute_wave(task_id, spec, claimed, step_by_id)
            for outcome in outcomes:
                executed += 1
                per_step_ms[outcome.step_id] = outcome.duration_ms
                if outcome.agent not in agents_used:
                    agents_used.append(outcome.agent)
                if outcome.verification is not None:
                    verifications[outcome.step_id] = outcome.verification
                if outcome.status == "succeeded":
                    verified += 1
                else:
                    failed += 1
                    last_error = outcome.error

        snapshot = self.store.get_task(task_id)
        dead = sum(1 for step in snapshot.steps if step.status.value == "dead_letter")
        skipped = sum(1 for step in snapshot.steps if step.status.value == "skipped")
        report = TaskRunReport(
            task_id=task_id,
            status=snapshot.status,
            waves=waves,
            executed=executed,
            verified=verified,
            failed=failed,
            skipped=skipped,
            dead_letters=dead,
            degraded=snapshot.degraded,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            verifications=verifications,
            agents_used=tuple(agents_used),
            per_step_ms=per_step_ms,
        )
        if snapshot.status not in TERMINAL_TASK_STATUSES:
            self._emit(
                task_id,
                "run.incomplete",
                f"stopped after {waves} wave(s): {last_error or 'budget or stop requested'}",
                persist=True,
            )
        return report

    # ------------------------------------------------------------------ #
    # Wave execution
    # ------------------------------------------------------------------ #

    def _execute_wave(
        self,
        task_id: str,
        spec: TaskSpec,
        claimed: Sequence[StepSnapshot],
        step_by_id: dict[str, PlanStep],
    ) -> list[_Outcome]:
        """Run one wave in parallel threads and commit each outcome as it lands."""
        budget = max(1, min(self.config.max_worker_threads, len(claimed)))
        queue = list(claimed)
        queue_lock = threading.Lock()
        outcomes: list[_Outcome] = []
        outcomes_lock = threading.Lock()

        def worker_loop() -> None:
            while True:
                with queue_lock:
                    if not queue:
                        return
                    snapshot = queue.pop(0)
                outcome = self._run_one(task_id, spec, snapshot, step_by_id[snapshot.step_id])
                with outcomes_lock:
                    outcomes.append(outcome)

        threads = [threading.Thread(target=worker_loop, daemon=True) for _ in range(budget)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return outcomes

    def _run_one(
        self, task_id: str, spec: TaskSpec, snapshot: StepSnapshot, step: PlanStep
    ) -> _Outcome:
        """Execute + verify + commit exactly one claimed step."""
        started = time.perf_counter()
        key = snapshot.idempotency_key
        try:
            chain = self.registry.candidates(snapshot.agent)
        except AgentRegistrationError as exc:
            error = f"unroutable step: {exc}"
            self.store.record_failure(
                task_id,
                snapshot.step_id,
                worker=self.worker_id,
                error=error,
                will_retry=False,
                dead_letter_reason="no agent can execute this step",
            )
            return _Outcome(snapshot.step_id, snapshot.agent, "unavailable", None, None, error, 0.0)

        if not chain:
            entry = self.registry.handler_for(snapshot.agent)
            # Breaker open: degrade gracefully instead of hammering a dead adapter.
            if not snapshot.critical:
                self.store.record_failure(
                    task_id,
                    snapshot.step_id,
                    worker=self.worker_id,
                    error=f"agent {entry.spec.name!r} circuit breaker is open",
                    will_retry=False,
                    dead_letter_reason="circuit open; step is non-critical",
                )
                return _Outcome(
                    snapshot.step_id,
                    entry.spec.name,
                    "unavailable",
                    None,
                    None,
                    "circuit open; non-critical step parked",
                    (time.perf_counter() - started) * 1000.0,
                )
            error = f"agent {entry.spec.name!r} circuit breaker is open"
            self.store.record_failure(
                task_id,
                snapshot.step_id,
                worker=self.worker_id,
                error=error,
                will_retry=False,
                dead_letter_reason="circuit open on a critical step",
            )
            return _Outcome(
                snapshot.step_id,
                entry.spec.name,
                "unavailable",
                None,
                None,
                error,
                (time.perf_counter() - started) * 1000.0,
            )

        # Attempt-indexed selection is the documented degradation rule:
        # attempt 1 uses the primary, attempt n uses the n-th live candidate.
        # That is how a *crashing* primary (whose breaker has not tripped yet)
        # is replaced by its declared fallback instead of being retried blindly.
        index = min(max(0, snapshot.attempts - 1), len(chain) - 1)
        entry = chain[index]
        fallback_note = "" if entry.spec.name == snapshot.agent else (
            f"degraded: {snapshot.agent!r} unavailable or failed, "
            f"using fallback {entry.spec.name!r} (attempt {snapshot.attempts})"
        )
        if fallback_note:
            self._emit(
                task_id, "step.degraded", f"{snapshot.step_id}: {fallback_note}", persist=True
            )
        acquired = entry.bulkhead.acquire(timeout=0.05) if entry.bulkhead else True
        if not acquired:
            self.store.record_failure(
                task_id,
                snapshot.step_id,
                worker=self.worker_id,
                error="bulkhead saturated",
                will_retry=True,
                backoff_s=self.config.backoff_base_s,
            )
            return _Outcome(
                snapshot.step_id,
                entry.spec.name,
                "failed",
                None,
                None,
                "bulkhead saturated; retry scheduled",
                (time.perf_counter() - started) * 1000.0,
            )
        try:
            return self._execute_entry(task_id, spec, snapshot, step, entry, key, started)
        finally:
            if entry.bulkhead:
                entry.bulkhead.release()

    def _execute_entry(  # noqa: PLR0913 - explicit is better than a bag of fields
        self,
        task_id: str,
        spec: TaskSpec,
        snapshot: StepSnapshot,
        step: PlanStep,
        entry: AgentEntry,
        key: str,
        started: float,
    ) -> _Outcome:
        """Invoke the handler with heartbeats, then verify and commit."""
        timeout = step.timeout_s or entry.spec.timeout_s or self.config.default_step_timeout_s
        deadline = time.monotonic() + timeout
        heartbeat = self._start_heartbeat(task_id, snapshot.step_id)
        ctx = HandlerContext(
            task_id=task_id,
            step_id=snapshot.step_id,
            action=step.action,
            goal=spec.goal,
            agent=entry.spec,
            attempt=snapshot.attempts,
            workspace_root=self.workspace_root,
            idempotency_key=key,
            inputs=dict(step.inputs),
            store=self.store,
            deadline_ts=deadline,
            scratch_dir=self.scratch_root / task_id / snapshot.step_id,
        )
        try:
            result = self._call_with_timeout(entry, ctx, timeout)
        except TimeoutError:
            duration = (time.perf_counter() - started) * 1000.0
            entry.breaker.record_failure(time.monotonic())
            self._handle_failure(
                task_id, snapshot, f"timed out after {timeout:.1f}s", started
            )
            return _Outcome(
                snapshot.step_id, entry.spec.name, "timeout", None, None,
                f"timed out after {timeout:.1f}s", duration,
            )
        except Exception as exc:  # noqa: BLE001 - agent bugs are data, not crashes
            duration = (time.perf_counter() - started) * 1000.0
            # A *declared* StepFailure is a normal, retryable business failure
            # (a flaky API, a cold cache). Anything else is an agent bug: it is
            # recorded, retried within the budget, and then dead-lettered — it
            # must never take the fleet down.
            if isinstance(exc, StepFailure):
                detail = str(exc)
                self._handle_failure(task_id, snapshot, detail, started)
                return _Outcome(
                    snapshot.step_id, entry.spec.name, "failed", None, None, detail, duration
                )
            entry.breaker.record_failure(time.monotonic())
            detail = f"{type(exc).__name__}: {exc}"
            self._emit(task_id, "step.crashed", f"{snapshot.step_id}: {detail}")
            self._handle_failure(task_id, snapshot, detail, started, crashed=True)
            return _Outcome(
                snapshot.step_id, entry.spec.name, "crashed", None, None, detail, duration
            )
        finally:
            heartbeat.set()

        duration = (time.perf_counter() - started) * 1000.0
        if not result.ok:
            entry.breaker.record_failure(time.monotonic())
            self._handle_failure(task_id, snapshot, result.error or "handler reported failure",
                                 started)
            return _Outcome(
                snapshot.step_id, entry.spec.name, "failed", result, None,
                result.error or "handler reported failure", duration,
            )

        verification = verify_step(
            step,
            result,
            workspace_root=self.workspace_root,
            reviewer=self.reviewer,
            pending_compensations=self._pending_compensations(task_id),
            policy=self.config.verification_policy,
        )
        snapshot_artifacts = tuple(result.artifacts)
        if not verification.ok:
            entry.breaker.record_failure(time.monotonic())
            reason = "verification failed: " + "; ".join(verification.failures[:4])
            self._handle_failure(task_id, snapshot, reason, started, verification=verification,
                                 artifacts=snapshot_artifacts)
            return _Outcome(
                snapshot.step_id, entry.spec.name, "failed", result, verification, reason, duration
            )
        entry.breaker.record_success()
        persisted = self.store.record_success(
            task_id,
            snapshot.step_id,
            worker=self.worker_id,
            result=result,
            verification=verification,
        )
        self._emit(
            task_id,
            "step.succeeded" if persisted else "step.commit_refused",
            f"{snapshot.step_id} → {entry.spec.name} in {duration:.0f} ms",
        )
        return _Outcome(
            snapshot.step_id,
            entry.spec.name,
            "succeeded" if persisted else "failed",
            result,
            verification,
            None if persisted else "lease was lost before commit",
            duration,
        )

    def _call_with_timeout(
        self, entry: AgentEntry, ctx: HandlerContext, timeout: float
    ) -> StepResult:
        """Run the handler in a thread and refuse to wait past ``timeout``."""
        box: dict[str, Any] = {}

        def target() -> None:
            try:
                box["result"] = entry.handler(ctx)
            except BaseException as exc:  # noqa: BLE001 - forwarded to the caller
                box["error"] = exc
                box["trace"] = traceback.format_exc()

        thread = threading.Thread(target=target, daemon=True, name=f"agent-{entry.spec.name}")
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            raise TimeoutError(f"agent {entry.spec.name} exceeded {timeout:.1f}s")
        if "error" in box:
            raise box["error"]
        result = box.get("result")
        if not isinstance(result, StepResult):
            raise FleetError(
                f"agent {entry.spec.name} returned {type(result).__name__}, not StepResult"
            )
        return result

    def _handle_failure(
        self,
        task_id: str,
        snapshot: StepSnapshot,
        error: str,
        started: float,
        *,
        crashed: bool = False,
        verification: VerificationReport | None = None,
        artifacts: Sequence[Any] = (),
    ) -> None:
        """Decide retry vs dead-letter using the persisted attempt budget."""
        del crashed, verification, artifacts, started
        attempts_left = snapshot.attempts < snapshot.max_attempts
        backoff = min(
            self.config.backoff_cap_s,
            self.config.backoff_base_s * (2 ** max(0, snapshot.attempts - 1)),
        )
        self.store.record_failure(
            task_id,
            snapshot.step_id,
            worker=self.worker_id,
            error=error,
            will_retry=attempts_left,
            backoff_s=backoff,
            dead_letter_reason=(
                f"exhausted {snapshot.max_attempts} attempt(s): {error[:300]}"
                if not attempts_left
                else None
            ),
        )

    def _pending_compensations(self, task_id: str) -> int:
        """Count rollbacks still recorded as pending for a task."""
        try:
            events = self.store.events(task_id, limit=2000)
        except Exception:  # pragma: no cover - events are best effort
            return 0
        return sum(1 for e in events if e["kind"] == "compensation.pending")

    def _start_heartbeat(self, task_id: str, step_id: str) -> threading.Event:
        """Heartbeat from a *separate* thread so a slow handler keeps its lease."""
        stop = threading.Event()
        interval = self.config.heartbeat_interval_s

        def beat() -> None:
            while not stop.wait(interval):
                alive = self.store.heartbeat(
                    task_id, step_id, worker=self.worker_id,
                    lease_seconds=self.config.lease_seconds, now_ts=time.time(),
                )
                if not alive:
                    return

        threading.Thread(target=beat, daemon=True, name="fleet-heartbeat").start()
        return stop

    def _emit(self, task_id: str, kind: str, message: str, *, persist: bool = False) -> None:
        """Push an observability event to the sink, and persist it when it matters.

        ``persist=True`` is used for events that change how the result should be
        read — a fallback substitution, a degraded run, an incomplete stop. A
        streamed log dies with the process; these must not.
        """
        if persist:
            try:
                self.store.note_event(task_id, kind, message)
            except Exception:  # pragma: no cover - never let telemetry kill work
                logger.exception("failed to persist event %s for %s", kind, task_id)
        if self.config.on_event is None:
            return
        try:
            self.config.on_event(
                {"at": utc_now(), "task_id": task_id, "kind": kind, "message": message}
            )
        except Exception:  # pragma: no cover - a broken sink must not kill a task
            logger.warning("event sink raised for %s/%s; the run continues", task_id, kind,
                           exc_info=True)


def summarize_snapshot(snapshot: TaskSnapshot) -> str:
    """One-line honest status used by the CLI."""
    counts = ", ".join(f"{k}={v}" for k, v in sorted(snapshot.counts().items()))
    return (
        f"{snapshot.task_id}: {snapshot.status.value}"
        f"{' (degraded)' if snapshot.degraded else ''} · {counts}"
    )


def ensure_runnable(spec: TaskSpec, registry: AgentRegistry) -> dict[str, str]:
    """Fail fast when a plan references agents nobody registered."""
    mapping: dict[str, str] = {}
    for step in spec.steps:
        entry = registry.handler_for(step.agent)
        mapping[step.step_id] = entry.spec.name
    return mapping


def assert_cost_free(registry: AgentRegistry) -> None:
    """Refuse to run a fleet with no local/free-tier agent available."""
    from .registry import cost_guard

    cost_guard(registry.specs())


def task_from_steps(
    task_id: str, goal: str, steps: Sequence[PlanStep], *, created_by: str = "cli"
) -> TaskSpec:
    """Small constructor used by tests and examples."""
    if not steps:
        raise ContractError("a task needs at least one step")
    return TaskSpec(task_id=task_id, goal=goal, steps=tuple(steps), created_by=created_by)
