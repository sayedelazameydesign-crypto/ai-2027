"""Governed verbal reinforcement loop (Reflexion, arXiv:2303.11366v4).

Adapts the three-role Reflexion loop of Shinn et al. (2023) — Actor (``M_a``),
Evaluator (``M_e``), and Self-Reflection (``M_sr``) over a bounded sliding
window (``Omega``) — to AI-2027's non-negotiable rules:

* **MAX_SPEND = 0** enforced via :meth:`~.meta_router.MetaRouter.assert_free_first`.
* **POL-BOUNDED-REFLEXION-CAP** enforcing ``max_trials``, sliding window ``Omega``,
  and early halt on stagnation or explicit ``stop``.
* **NO VERIFIED RESULT = NO LEARNING** plus §4.3 false-positive interception:
  non-independent passing self-evaluations are cross-checked by the independent
  verifier, and unverified runs emit ``ExperienceRecord(verified=False)`` so
  :class:`~.experience_store.ExperienceStore` refuses them into ``refusals``.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Sequence

from .experience_store import ExperienceRecord, fingerprint_text
from .meta_router import MetaRouter
from .reflexion_contracts import (
    DEFAULT_REFLEXION_POLICY_ID,
    REFLEXION_ARXIV_ID,
    ConfidenceTier,
    CorrectionKind,
    EvaluationSignal,
    EvaluatorProtocol,
    ExactMatchEvaluator,
    ReflexionAuditEntry,
    ReflexionMemoryBuffer,
    ReflexionOutcome,
    ReflexionPolicy,
    ReflexionRunResult,
    SelfReflectorProtocol,
    TrajectoryStep,
    TrialTrajectory,
    VerbalReflection,
    VerifierEvaluator,
    canonical_audit_sha256,
    detect_trajectory_pathology,
    filter_valid_python_tests,
    verify_reflexion_chain,
)


class DeterministicSelfReflector:
    """Zero-cost deterministic Self-Reflection generator ``M_sr``."""

    async def reflect(
        self,
        task: str,
        trajectory: TrialTrajectory,
        evaluation: EvaluationSignal,
        memory: tuple[VerbalReflection, ...],
    ) -> VerbalReflection:
        """Synthesize a structured verbal cue from ``trajectory`` and ``evaluation``."""
        reason = evaluation.reason.strip() or "verification check failed"
        prior_gaps = {item.gap for item in memory}
        if evaluation.pathology == "router_failure":
            correction: CorrectionKind = "re-run"
            next_action = (
                "Retry concisely within free-tier provider limits without expanding scope."
            )
        elif evaluation.pathology in {"repeated_action_cycle", "step_cap_exceeded"}:
            correction = "replan"
            next_action = (
                "Abandon the repeated action sequence and select a shorter, direct plan "
                f"for task: {task[:120]}"
            )
        elif reason in prior_gaps:
            correction = "replan"
            next_action = (
                f"Previous fix did not resolve ({reason}); restructure the approach and "
                "eliminate every unresolved placeholder or missing constraint."
            )
        else:
            correction = "fix"
            next_action = (
                f"Address failure ({reason}) directly and satisfy all task constraints "
                "with concrete, verifiable output."
            )
        snippet = " ".join(trajectory.output.strip().split())[:160] or "<empty output>"
        return VerbalReflection(
            trial=trajectory.trial,
            claim=f"Trial {trajectory.trial} attempted to satisfy: {task[:120]}",
            observation=f"Evaluator reported score={evaluation.score:.2f} ({reason}) on {snippet}",
            gap=reason,
            next_action=next_action,
            correction=correction,
            confidence="low",
        )


class RouterSelfReflector:
    """Free-first LLM Self-Reflection generator ``M_sr`` with deterministic fallback."""

    def __init__(
        self,
        meta_router: MetaRouter,
        *,
        fallback: DeterministicSelfReflector | None = None,
    ) -> None:
        """Bind ``meta_router`` (MAX_SPEND=0) and an offline deterministic fallback."""
        self.meta_router = meta_router
        self.fallback = fallback or DeterministicSelfReflector()

    async def reflect(
        self,
        task: str,
        trajectory: TrialTrajectory,
        evaluation: EvaluationSignal,
        memory: tuple[VerbalReflection, ...],
    ) -> VerbalReflection:
        """Generate structured verbal feedback via the free-first router."""
        self.meta_router.assert_free_first()
        base = await self.fallback.reflect(task, trajectory, evaluation, memory)
        prior_text = "\n".join(item.render_cue() for item in memory) or "none"
        prompt = (
            "Produce a 4-line self-reflection (GAP: ... / NEXT_ACTION: ... / "
            "CORRECTION: fix|replan|re-run|stop).\n"
            f"Task: {task[:300]}\n"
            f"Draft output: {trajectory.output[:400]}\n"
            f"Evaluator verdict: {evaluation.reason}\n"
            f"Prior reflections: {prior_text}\n"
        )
        profile = self.meta_router.registry.select("reason").profile
        request = self.meta_router._build_request(
            prompt,
            temperature=profile.temperature,
            max_tokens=min(profile.max_tokens, 256),
        )
        try:
            response = await self.meta_router.router.route(request)
        except Exception:
            return base
        return _parse_reflection_text(response.text, fallback=base)


def _parse_reflection_text(raw: str, *, fallback: VerbalReflection) -> VerbalReflection:
    """Parse ``GAP:`` / ``NEXT_ACTION:`` / ``CORRECTION:`` lines or keep ``fallback``."""
    fields: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        head, val = line.split(":", 1)
        norm_head = head.strip().upper().replace(" ", "_")
        if norm_head in {"GAP", "NEXT_ACTION", "CORRECTION", "OBSERVATION"} and val.strip():
            fields[norm_head] = val.strip()
    if "GAP" not in fields and "NEXT_ACTION" not in fields:
        return fallback
    raw_corr = fields.get("CORRECTION", fallback.correction).lower().strip()
    correction: CorrectionKind = (
        raw_corr  # type: ignore[assignment]
        if raw_corr in {"re-run", "fix", "replan", "stop"}
        else fallback.correction
    )
    return VerbalReflection(
        trial=fallback.trial,
        claim=fallback.claim,
        observation=fields.get("OBSERVATION", fallback.observation)[:1_000],
        gap=fields.get("GAP", fallback.gap)[:1_000],
        next_action=fields.get("NEXT_ACTION", fallback.next_action)[:1_000],
        correction=correction,
        confidence=fallback.confidence,
    )


class GovernedReflexionLoop:
    """Execute Shinn et al. Algorithm 1 under AI-2027 zero-cost and memory gates."""

    def __init__(
        self,
        meta_router: MetaRouter,
        *,
        policy: ReflexionPolicy | None = None,
        evaluator: EvaluatorProtocol | None = None,
        reflector: SelfReflectorProtocol | None = None,
    ) -> None:
        """Wire Actor (``meta_router``), Evaluator ``M_e``, and Reflector ``M_sr``."""
        self.meta_router = meta_router
        self.policy = policy or ReflexionPolicy()
        self.evaluator: EvaluatorProtocol = evaluator or VerifierEvaluator(
            meta_router.verifier, policy=self.policy, independent=True
        )
        self.reflector: SelfReflectorProtocol = reflector or DeterministicSelfReflector()

    def _compose_actor_prompt(self, task: str, memory: ReflexionMemoryBuffer) -> str:
        """Combine ``task`` with the bounded sliding-window verbal memory ``mem``."""
        cues = memory.format_for_prompt()
        if not cues:
            return task
        return f"{task}\n\n{cues}"

    async def _run_actor_trial(
        self,
        prompt: str,
        *,
        trial: int,
        task_type: str | None,
        step_extractor: Callable[[str], Sequence[TrajectoryStep]] | None,
    ) -> tuple[TrialTrajectory, str]:
        """Invoke the Actor once via ``MetaRouter`` under ``MAX_SPEND=0``."""
        self.meta_router.assert_free_first()
        decision = (
            self.meta_router.classify(prompt)
            if task_type is None
            else self.meta_router.registry.select(task_type)
        )
        profile = decision.profile
        request = self.meta_router._build_request(
            prompt,
            temperature=profile.temperature,
            max_tokens=profile.max_tokens,
        )
        prompt_hash = fingerprint_text(prompt)
        started = time.perf_counter()
        try:
            response = await self.meta_router.router.route(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            return (
                TrialTrajectory(
                    trial=trial,
                    prompt_sha256=prompt_hash,
                    output="",
                    steps=(),
                    provider="none",
                    model="router-error",
                    latency_ms=elapsed_ms,
                    attempts=1,
                    router_failed=True,
                ),
                decision.task_type,
            )
        steps = tuple(step_extractor(response.text)) if step_extractor is not None else ()
        return (
            TrialTrajectory(
                trial=trial,
                prompt_sha256=prompt_hash,
                output=response.text,
                steps=steps,
                provider=response.provider.value,
                model=response.model,
                latency_ms=response.latency_ms,
                attempts=len(response.attempts),
                router_failed=False,
                cost_usd=response.cost_usd or 0.0,
                billing_tier=response.billing_tier.value,
                cost_status=response.cost_status.value,
            ),
            decision.task_type,
        )

    async def run(
        self,
        task: str,
        *,
        task_type: str | None = None,
        summary: str | None = None,
        step_extractor: Callable[[str], Sequence[TrajectoryStep]] | None = None,
    ) -> ReflexionRunResult:
        """Run bounded verbal reinforcement on ``task`` and emit an auditable result."""
        self.meta_router.assert_free_first()
        memory = ReflexionMemoryBuffer(window_size=self.policy.memory_window)
        trajectories: list[TrialTrajectory] = []
        evaluations: list[EvaluationSignal] = []
        reflections: list[VerbalReflection] = []
        audit_chain: list[ReflexionAuditEntry] = []
        prev_hash = ""
        false_positives_caught = 0
        resolved_task_type = task_type or self.meta_router.classify(task).task_type
        outcome: ReflexionOutcome = "halted_max_retries"

        for trial in range(self.policy.max_trials):
            actor_prompt = self._compose_actor_prompt(task, memory)
            trajectory, resolved_task_type = await self._run_actor_trial(
                actor_prompt,
                trial=trial,
                task_type=task_type,
                step_extractor=step_extractor,
            )
            trajectories.append(trajectory)

            eval_signal = self.evaluator.evaluate(task, trajectory)
            if (
                eval_signal.passed
                and self.policy.require_independent_verification
                and not eval_signal.independent
            ):
                indep_verdict = self.meta_router.verifier(trajectory.output)
                if indep_verdict.passed:
                    eval_signal = EvaluationSignal(
                        passed=True,
                        score=eval_signal.score,
                        reason=indep_verdict.detail or "independently verified",
                        independent=True,
                    )
                else:
                    false_positives_caught += 1
                    eval_signal = EvaluationSignal(
                        passed=False,
                        score=0.0,
                        reason=(
                            "self-evaluation false positive intercepted by independent verifier: "
                            f"{indep_verdict.detail}"
                        ),
                        independent=True,
                        pathology="self_eval_false_positive",
                    )
            evaluations.append(eval_signal)

            if eval_signal.passed:
                outcome = "verified"
                audit_chain.append(
                    _build_audit_entry(
                        trial=trial,
                        policy_trace_id=self.policy.policy_id,
                        outcome="verified",
                        output_sha256=fingerprint_text(trajectory.output),
                        reflection_sha256="",
                        prev_hash=prev_hash,
                    )
                )
                break

            if trial + 1 >= self.policy.max_trials:
                outcome = "halted_max_retries"
                audit_chain.append(
                    _build_audit_entry(
                        trial=trial,
                        policy_trace_id=self.policy.policy_id,
                        outcome=outcome,
                        output_sha256=fingerprint_text(trajectory.output),
                        reflection_sha256="",
                        prev_hash=prev_hash,
                    )
                )
                break

            cue = await self.reflector.reflect(task, trajectory, eval_signal, memory.snapshot())
            reflections.append(cue)
            stagnated = memory.append(cue)
            cue_hash = fingerprint_text(cue.render_cue())

            if cue.correction == "stop":
                outcome = "halted_stop"
            elif stagnated:
                outcome = "halted_stagnation"
            else:
                outcome = "halted_max_retries"

            trial_outcome = outcome if outcome in {"halted_stop", "halted_stagnation"} else "retry"
            entry = _build_audit_entry(
                trial=trial,
                policy_trace_id=self.policy.policy_id,
                outcome=trial_outcome,
                output_sha256=fingerprint_text(trajectory.output),
                reflection_sha256=cue_hash,
                prev_hash=prev_hash,
            )
            audit_chain.append(entry)
            prev_hash = entry.self_hash

            if trial_outcome != "retry":
                break

        episode = _build_episode_record(
            task=task,
            summary=summary,
            task_type=resolved_task_type,
            outcome=outcome,
            trajectories=trajectories,
        )
        return ReflexionRunResult(
            outcome=outcome,
            policy_trace_id=self.policy.policy_id,
            trials_used=len(trajectories),
            memory_window=self.policy.memory_window,
            evicted_reflections=memory.evicted_count,
            false_positives_caught=false_positives_caught,
            trajectories=tuple(trajectories),
            evaluations=tuple(evaluations),
            reflections=tuple(reflections),
            active_memory=memory.snapshot(),
            audit_chain=tuple(audit_chain),
            episode=episode,
        )


def _build_audit_entry(
    *,
    trial: int,
    policy_trace_id: str,
    outcome: str,
    output_sha256: str,
    reflection_sha256: str,
    prev_hash: str,
) -> ReflexionAuditEntry:
    """Create one SHA-256 chained ``ReflexionAuditEntry``."""
    self_hash = canonical_audit_sha256(
        {
            "outcome": outcome,
            "output_sha256": output_sha256,
            "policy_trace_id": policy_trace_id,
            "prev_hash": prev_hash,
            "reflection_sha256": reflection_sha256,
            "trial": trial,
        }
    )
    return ReflexionAuditEntry(
        trial=trial,
        policy_trace_id=policy_trace_id,
        outcome=outcome,
        output_sha256=output_sha256,
        reflection_sha256=reflection_sha256,
        prev_hash=prev_hash,
        self_hash=self_hash,
    )


def _build_episode_record(
    *,
    task: str,
    summary: str | None,
    task_type: str,
    outcome: ReflexionOutcome,
    trajectories: Sequence[TrialTrajectory],
) -> ExperienceRecord:
    """Construct the final ``ExperienceRecord`` gated by independent verification."""
    final = trajectories[-1]
    total_latency = sum(item.latency_ms for item in trajectories)
    total_attempts = sum(item.attempts for item in trajectories)
    total_cost = sum(item.cost_usd for item in trajectories)
    all_router_failed = all(item.router_failed for item in trajectories)
    verified = outcome == "verified"
    if verified:
        test_result = "passed"
    elif all_router_failed:
        test_result = "unverified"
    else:
        test_result = "failed"
    return ExperienceRecord(
        episode_id=f"ep_{uuid.uuid4().hex[:12]}",
        task_type=task_type,
        task_summary=(summary if summary is not None else task[:200]),
        provider=final.provider,
        model=final.model,
        latency_ms=total_latency,
        attempts=max(1, total_attempts),
        verified=verified,
        test_result=test_result,
        quality_score=0.0,
        accepted=None,
        output_fingerprint=fingerprint_text(final.output) if verified else "",
        cost_usd=total_cost,
        billing_tier=final.billing_tier,
        cost_status=final.cost_status,
    )


__all__ = [
    "ConfidenceTier",
    "CorrectionKind",
    "DEFAULT_REFLEXION_POLICY_ID",
    "DeterministicSelfReflector",
    "EvaluationSignal",
    "EvaluatorProtocol",
    "ExactMatchEvaluator",
    "GovernedReflexionLoop",
    "REFLEXION_ARXIV_ID",
    "ReflexionAuditEntry",
    "ReflexionMemoryBuffer",
    "ReflexionOutcome",
    "ReflexionPolicy",
    "ReflexionRunResult",
    "RouterSelfReflector",
    "SelfReflectorProtocol",
    "TrajectoryStep",
    "TrialTrajectory",
    "VerbalReflection",
    "VerifierEvaluator",
    "canonical_audit_sha256",
    "detect_trajectory_pathology",
    "filter_valid_python_tests",
    "verify_reflexion_chain",
]
