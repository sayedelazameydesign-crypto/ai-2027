"""End-to-end runner tests: parallelism, failover, verification, cancel, resume.

Every assertion here is about *observable state*, not about a log line saying
the work happened.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from fleet2027 import (  # noqa: E402
    AgentRegistry,
    AgentSpec,
    ArtifactRef,
    CostTier,
    DurableTaskStore,
    EvidenceKind,
    FleetRunner,
    PlanStep,
    RunnerConfig,
    StepFailure,
    StepResult,
    StepStatus,
    TaskSpec,
    TaskStatus,
    build_reviewer,
    examples,
)


class RunnerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.ws = base / "ws"
        self.ws.mkdir()
        self.store = DurableTaskStore(str(base / "fleet.db"))
        self.registry = AgentRegistry()

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def register(self, name: str, handler, **kwargs) -> AgentSpec:
        spec = AgentSpec(name=name, role="test agent", cost_tier=CostTier.LOCAL, **kwargs)
        return self.registry.register(spec, handler)

    def runner(self, **config) -> FleetRunner:
        return FleetRunner(
            self.store, self.registry, workspace_root=self.ws, config=RunnerConfig(**config)
        )

    def write_artifact(self, ctx, relative: str, text: str) -> ArtifactRef:
        path = self.ws / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return ArtifactRef.from_file(str(path), record_path=relative)


class HappyPathTests(RunnerTestCase):
    def test_three_steps_run_in_dependency_order_and_verify(self) -> None:
        def first(ctx):
            return StepResult(
                ok=True, summary="wrote", output="x",
                artifacts=(self.write_artifact(ctx, "a.txt", "a"),),
            )

        def second(ctx):
            return StepResult(
                ok=True, summary="wrote", output="y",
                artifacts=(self.write_artifact(ctx, "b.txt", "b"),),
            )

        self.register("first", first, requires_evidence=(EvidenceKind.ARTIFACT_SHA256.value,))
        self.register("second", second, requires_evidence=(EvidenceKind.ARTIFACT_SHA256.value,))
        spec = TaskSpec(
            task_id="t-happy",
            goal="two files",
            steps=(
                PlanStep(step_id="a", agent="first", action="write a",
                         verification=(EvidenceKind.ARTIFACT_SHA256.value,)),
                PlanStep(step_id="b", agent="second", action="write b", depends_on=("a",),
                         verification=(EvidenceKind.ARTIFACT_SHA256.value,)),
            ),
        )
        report = self.runner().submit_and_run(spec)
        self.assertEqual(report.status, TaskStatus.SUCCEEDED)
        self.assertEqual(report.verified, 2)
        self.assertEqual(report.failed, 0)
        self.assertEqual(
            sorted(p.name for p in self.ws.iterdir()), ["a.txt", "b.txt"]
        )
        snapshots = self.store.get_task("t-happy")
        self.assertFalse(snapshots.degraded)
        for step in snapshots.steps:
            self.assertEqual(step.status, StepStatus.SUCCEEDED)
            self.assertEqual(step.attempts, 1)

    def test_independent_steps_of_one_wave_actually_overlap(self) -> None:
        active = 0
        peak = 0
        lock = threading.Lock()

        def slow(ctx):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.15)
            with lock:
                active -= 1
            return StepResult(ok=True, summary="done", output="x")

        self.register("slow", slow, max_parallel=4)
        spec = TaskSpec(
            task_id="t-parallel",
            goal="three independent steps",
            steps=tuple(
                PlanStep(step_id=f"s{i}", agent="slow", action="work",
                         verification=(EvidenceKind.OUTPUT_NON_EMPTY.value,))
                for i in range(3)
            ),
        )
        report = self.runner(max_worker_threads=3).submit_and_run(spec)
        self.assertEqual(report.status, TaskStatus.SUCCEEDED)
        self.assertEqual(report.waves, 1, "independent steps belong to one wave")
        self.assertEqual(peak, 3, f"expected 3 concurrent handlers, saw {peak}")

    def test_deterministic_demo_fleet_end_to_end(self) -> None:
        registry = examples.build_demo_registry()
        runner = FleetRunner(
            self.store,
            registry,
            workspace_root=self.ws,
            reviewer=build_reviewer(registry, reviewer_agent="reviewer"),
            config=RunnerConfig(backoff_base_s=0.01),
        )
        report = runner.submit_and_run(examples.build_demo_task("demo-test"))
        self.assertEqual(report.status, TaskStatus.SUCCEEDED)
        self.assertTrue(report.degraded, "the optional metrics step fails by design")
        snapshot = self.store.get_task("demo-test")
        states = {step.step_id: step for step in snapshot.steps}
        self.assertEqual(states["docs"].attempts, 2, "the writer retries once by design")
        self.assertEqual(states["metrics"].status, StepStatus.DEAD_LETTER)
        self.assertEqual(states["qa"].status, StepStatus.SUCCEEDED)
        qa = self.store.step_result("demo-test", "qa")
        self.assertTrue(qa["verification"]["ok"])
        self.assertEqual(qa["evidence"]["json"]["sum"], 5)
        self.assertTrue((self.ws / "demo" / "demo-test" / "app.py").is_file())


class VerificationGateTests(RunnerTestCase):
    def test_a_fabricated_digest_fails_the_step_even_though_the_agent_claimed_success(self) -> None:
        def liar(ctx):
            return StepResult(
                ok=True,
                summary="definitely wrote it",
                output="yes",
                artifacts=(ArtifactRef(path="ghost.txt", sha256="0" * 64),),
            )

        self.register("liar", liar)
        spec = TaskSpec(
            task_id="t-liar",
            goal="claim without evidence",
            steps=(
                PlanStep(step_id="s", agent="liar", action="claim",
                         verification=(EvidenceKind.ARTIFACT_SHA256.value,)),
            ),
        )
        report = self.runner().submit_and_run(spec)
        self.assertEqual(report.status, TaskStatus.DEAD_LETTER)
        self.assertEqual(report.verified, 0)
        step = self.store.get_task("t-liar").steps[0]
        self.assertEqual(step.status, StepStatus.DEAD_LETTER)
        self.assertIn("verification failed", step.error or "")

    def test_verification_failure_is_retried_within_the_budget(self) -> None:
        attempts: list[int] = []

        def flaky(ctx):
            attempts.append(ctx.attempt)
            if ctx.attempt < 2:
                return StepResult(ok=True, summary="faked it", output="x",
                                  artifacts=(ArtifactRef(path="missing.txt", sha256="1" * 64),))
            return StepResult(ok=True, summary="really wrote it", output="x",
                              artifacts=(self.write_artifact(ctx, "ok.txt", "real"),))

        self.register("flaky", flaky)
        spec = TaskSpec(
            task_id="t-retry",
            goal="retry after a bad claim",
            steps=(
                PlanStep(step_id="s", agent="flaky", action="write", max_attempts=3,
                         verification=(EvidenceKind.ARTIFACT_SHA256.value,)),
            ),
        )
        report = self.runner(backoff_base_s=0.01).submit_and_run(spec)
        self.assertEqual(report.status, TaskStatus.SUCCEEDED)
        self.assertEqual(attempts, [1, 2])

    def test_an_unverified_step_with_no_policy_is_refused(self) -> None:
        self.register("quiet", lambda ctx: StepResult(ok=True, summary="", output=""))
        spec = TaskSpec(
            task_id="t-quiet", goal="say nothing",
            steps=(PlanStep(step_id="s", agent="quiet", action="nothing"),),
        )
        report = self.runner().submit_and_run(spec)
        self.assertNotEqual(report.status, TaskStatus.SUCCEEDED)
        self.assertEqual(report.verified, 0)


class FailureHandlingTests(RunnerTestCase):
    def test_a_crashing_agent_is_contained_and_reported(self) -> None:
        def crash(ctx):
            raise ZeroDivisionError("agent bug")

        self.register("crash", crash, max_attempts=1)
        spec = TaskSpec(task_id="t-crash", goal="crash",
                        steps=(PlanStep(step_id="s", agent="crash", action="x"),))
        report = self.runner().submit_and_run(spec)
        self.assertEqual(report.status, TaskStatus.DEAD_LETTER)
        step = self.store.get_task("t-crash").steps[0]
        self.assertIn("ZeroDivisionError", step.error or "")

    def test_a_declared_step_failure_is_retried_without_tripping_the_breaker(self) -> None:
        calls = {"n": 0}

        def flaky(ctx):
            calls["n"] += 1
            if calls["n"] == 1:
                raise StepFailure("provider returned 503")
            return StepResult(ok=True, summary="recovered", output="ok")

        self.register("flaky", flaky, max_attempts=3)
        spec = TaskSpec(task_id="t-503", goal="transient",
                        steps=(PlanStep(step_id="s", agent="flaky", action="call",
                                        verification=(EvidenceKind.OUTPUT_NON_EMPTY.value,)),))
        report = self.runner(backoff_base_s=0.01).submit_and_run(spec)
        self.assertEqual(report.status, TaskStatus.SUCCEEDED)
        entry = self.registry.handler_for("flaky")
        self.assertEqual(entry.breaker.failures, 0, "a business failure must not open the breaker")

    def test_a_timeout_is_enforced_and_recorded(self) -> None:
        self.register("sleepy", lambda ctx: (time.sleep(2), StepResult(ok=True, output="x"))[1],
                      timeout_s=5.0, max_attempts=1)
        spec = TaskSpec(task_id="t-timeout", goal="timeout",
                        steps=(PlanStep(step_id="s", agent="sleepy", action="x",
                                        timeout_s=0.2),))
        report = self.runner().submit_and_run(spec)
        self.assertEqual(report.status, TaskStatus.DEAD_LETTER)
        step = self.store.get_task("t-timeout").steps[0]
        self.assertIn("timed out", step.error or "")

    def test_a_dead_agent_is_replaced_by_its_declared_fallback(self) -> None:
        def dead(ctx):
            raise RuntimeError("primary is down")

        def backup(ctx):
            return StepResult(ok=True, summary="backup served", output="ok")

        self.register("primary", dead, max_attempts=2, fallbacks=("backup",))
        self.register("backup", backup)
        spec = TaskSpec(task_id="t-fo", goal="fail over",
                        steps=(PlanStep(step_id="s", agent="primary", action="x",
                                        verification=(EvidenceKind.OUTPUT_NON_EMPTY.value,)),))
        report = self.runner().submit_and_run(spec)
        self.assertEqual(report.status, TaskStatus.SUCCEEDED)
        self.assertIn("backup", report.agents_used)
        self.assertEqual(report.verified, 1)

    def test_an_unregistered_agent_is_dead_lettered_not_guessed(self) -> None:
        spec = TaskSpec(task_id="t-ghost", goal="nobody",
                        steps=(PlanStep(step_id="s", agent="ghost", action="x"),))
        report = self.runner().submit_and_run(spec)
        self.assertEqual(report.status, TaskStatus.DEAD_LETTER)
        self.assertIn("unroutable", self.store.get_task("t-ghost").steps[0].error or "")


class ControlFlowTests(RunnerTestCase):
    def test_cancel_stops_the_run_and_late_writes_are_refused(self) -> None:
        started = threading.Event()

        def slow(ctx):
            started.set()
            time.sleep(0.3)
            return StepResult(ok=True, output="x")

        self.register("slow", slow)
        spec = TaskSpec(
            task_id="t-cancel", goal="cancel me",
            steps=(
                PlanStep(step_id="a", agent="slow", action="x",
                         verification=(EvidenceKind.OUTPUT_NON_EMPTY.value,)),
                PlanStep(step_id="b", agent="slow", action="y", depends_on=("a",)),
            ),
        )
        self.store.submit(spec)
        runner = self.runner()
        thread = threading.Thread(target=runner.run, args=("t-cancel",))
        thread.start()
        self.assertTrue(started.wait(2))
        snapshot = self.store.cancel_task("t-cancel", reason="test cancel")
        thread.join(5)
        self.assertEqual(snapshot.status, TaskStatus.CANCELLED)
        final = self.store.get_task("t-cancel")
        self.assertEqual(final.status, TaskStatus.CANCELLED)
        self.assertNotEqual(final.steps[1].status, StepStatus.SUCCEEDED)

    def test_resume_continues_after_a_simulated_crash(self) -> None:
        state = {"calls": 0}

        def first(ctx):
            state["calls"] += 1
            return StepResult(ok=True, summary="first done", output="x",
                              artifacts=(self.write_artifact(ctx, "first.txt", "1"),))

        def second(ctx):
            return StepResult(ok=True, summary="second done", output="y",
                              artifacts=(self.write_artifact(ctx, "second.txt", "2"),))

        self.register("first", first, requires_evidence=(EvidenceKind.ARTIFACT_SHA256.value,))
        self.register("second", second, requires_evidence=(EvidenceKind.ARTIFACT_SHA256.value,))
        spec = TaskSpec(
            task_id="t-resume", goal="survive",
            steps=(
                PlanStep(step_id="a", agent="first", action="x",
                         verification=(EvidenceKind.ARTIFACT_SHA256.value,)),
                PlanStep(step_id="b", agent="second", action="y", depends_on=("a",),
                         verification=(EvidenceKind.ARTIFACT_SHA256.value,)),
            ),
        )
        self.store.submit(spec)
        # Simulate a worker that claimed step b and died without committing.
        runner = self.runner()
        runner.run("t-resume")  # completes everything legitimately first
        # Now build a second task whose lease we deliberately strand.
        spec2 = TaskSpec(
            task_id="t-stranded", goal="stranded",
            steps=(
                PlanStep(step_id="a", agent="first", action="x",
                         verification=(EvidenceKind.ARTIFACT_SHA256.value,)),
            ),
        )
        self.store.submit(spec2)
        self.store.claim_ready_steps("t-stranded", worker="dead-worker", lease_seconds=1)
        recovered = self.store.recover_expired_leases(lease_seconds=1, now_ts=time.time() + 10)
        self.assertEqual([item["step_id"] for item in recovered], ["a"])
        report = self.runner().run("t-stranded")
        self.assertEqual(report.status, TaskStatus.SUCCEEDED)
        self.assertEqual(self.store.get_task("t-stranded").steps[0].attempts, 2)

    def test_completed_work_is_never_recomputed_on_resume(self) -> None:
        calls: list[str] = []

        def once(ctx):
            calls.append(ctx.step_id)
            return StepResult(ok=True, summary="done", output="x",
                              artifacts=(self.write_artifact(ctx, f"{ctx.step_id}.txt", "1"),))

        self.register("once", once, requires_evidence=(EvidenceKind.ARTIFACT_SHA256.value,))
        spec = TaskSpec(
            task_id="t-once", goal="run each step once",
            steps=(
                PlanStep(step_id="a", agent="once", action="x",
                         verification=(EvidenceKind.ARTIFACT_SHA256.value,)),
                PlanStep(step_id="b", agent="once", action="y", depends_on=("a",),
                         verification=(EvidenceKind.ARTIFACT_SHA256.value,)),
            ),
        )
        runner = self.runner()
        runner.submit_and_run(spec)
        self.assertEqual(calls, ["a", "b"])
        # A second run of a finished task must do nothing at all.
        report = runner.run("t-once")
        self.assertEqual(report.executed, 0)
        self.assertEqual(calls, ["a", "b"])


class IdempotencyTests(RunnerTestCase):
    def test_the_idempotency_ledger_records_one_effect_per_step(self) -> None:
        def effectful(ctx):
            fresh = ctx.store.record_effect(
                ctx.idempotency_key, task_id=ctx.task_id, step_id=ctx.step_id
            )
            return StepResult(ok=True, summary=f"effect fresh={fresh}", output="x")

        self.register("effectful", effectful)
        spec = TaskSpec(task_id="t-effect", goal="once",
                        steps=(PlanStep(step_id="a", agent="effectful", action="x",
                                        verification=(EvidenceKind.OUTPUT_NON_EMPTY.value,)),))
        self.runner().submit_and_run(spec)
        self.assertEqual(self.store.stats()["effects"], 1)
        key = self.store.idempotency_key("t-effect", "a")
        self.assertIsNotNone(self.store.effect(key))


if __name__ == "__main__":
    unittest.main(verbosity=2)
