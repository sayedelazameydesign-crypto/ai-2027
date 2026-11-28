"""Tests and Hypothesis properties for the governed Reflexion loop (arXiv:2303.11366v4)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ai2027_protocol.ai_providers import DeterministicFallbackProvider
from ai2027_protocol.budget import BudgetPolicy
from ai2027_protocol.contracts import (
    AIProvider,
    PromptRequest,
    ProviderKind,
)
from ai2027_protocol.experience_store import ExperienceStore
from ai2027_protocol.feedback_engine import Feedback, FeedbackEngine, FeedbackError
from ai2027_protocol.meta_router import FreeFirstPolicyError, MetaRouter, VerificationResult
from ai2027_protocol.provider_scoring import ProviderScorecard
from ai2027_protocol.reflexion import (
    DEFAULT_REFLEXION_POLICY_ID,
    REFLEXION_ARXIV_ID,
    DeterministicSelfReflector,
    EvaluationSignal,
    EvaluatorProtocol,
    ExactMatchEvaluator,
    GovernedReflexionLoop,
    ReflexionAuditEntry,
    ReflexionMemoryBuffer,
    ReflexionPolicy,
    RouterSelfReflector,
    SelfReflectorProtocol,
    TrajectoryStep,
    TrialTrajectory,
    VerbalReflection,
    VerifierEvaluator,
    detect_trajectory_pathology,
    filter_valid_python_tests,
    verify_reflexion_chain,
)
from ai2027_protocol.router import FreeFirstRouter, RouterConfig

_FAST_CFG = RouterConfig(
    per_provider_timeout_s=0.1,
    max_retries_per_provider=0,
    base_backoff_s=0.0,
)


class ScriptedFreeProvider:
    """Deterministic local test provider emitting scripted responses per call."""

    kind: ProviderKind = ProviderKind.DETERMINISTIC
    model: str = "scripted-free-v1"
    is_remote: bool = False

    def __init__(self, outputs: Sequence[str]) -> None:
        self._outputs = list(outputs)
        self.prompts_seen: list[str] = []
        self._idx = 0

    def is_configured(self) -> bool:
        return True

    async def complete(self, request: PromptRequest, *, timeout_s: float) -> str:
        del timeout_s
        self.prompts_seen.append(request.prompt)
        text = self._outputs[min(self._idx, len(self._outputs) - 1)]
        self._idx += 1
        return text


def _make_meta(
    providers: Sequence[AIProvider],
    *,
    verifier_fn: Callable[[str], VerificationResult] | None = None,
    max_spend_usd: float = 0.0,
) -> MetaRouter:
    router = FreeFirstRouter(
        providers=list(providers),
        config=_FAST_CFG,
        budget_policy=BudgetPolicy(max_spend_usd=max_spend_usd),
    )
    return MetaRouter(router, verifier=verifier_fn)


async def test_reflexion_recovers_on_second_trial_and_persists_only_verified_episode(
    tmp_path: Path,
) -> None:
    actor = ScriptedFreeProvider(
        [
            "draft answer with {{ unresolved }} marker",
            "clean verified implementation ready",
        ]
    )
    meta = _make_meta([actor])
    loop = GovernedReflexionLoop(
        meta,
        policy=ReflexionPolicy(max_trials=3, memory_window=2),
    )
    assert isinstance(loop.evaluator, EvaluatorProtocol)
    assert isinstance(loop.reflector, SelfReflectorProtocol)

    result = await loop.run("implement bounded parser", task_type="coding")
    assert result.outcome == "verified"
    assert result.paper_ref == REFLEXION_ARXIV_ID
    assert result.policy_trace_id == DEFAULT_REFLEXION_POLICY_ID
    assert result.trials_used == 2
    assert len(result.reflections) == 1
    assert "Prior self-reflections" in actor.prompts_seen[1]
    assert verify_reflexion_chain(result.audit_chain) == []

    # Durable memory admits the verified final episode, never the failed draft.
    store = ExperienceStore(tmp_path / "exp.sqlite")
    scorecard = ProviderScorecard()
    feedback = FeedbackEngine(scorecard)
    assert store.record(result.episode) is True
    assert store.stats() == {"stored": 1, "refused": 0}
    stored = store.get(result.episode.episode_id)
    assert stored is not None
    assert stored.attempts == 2
    assert feedback.apply(result.episode, Feedback.accept()) is True
    assert store.verify_durability() == []
    store.close()


async def test_unverified_max_retries_halts_and_never_poisons_experience_store(
    tmp_path: Path,
) -> None:
    actor = ScriptedFreeProvider(
        [
            "draft 1 TODO",
            "draft 2 TODO",
            "draft 3 {{ missing }}",
        ]
    )
    meta = _make_meta([actor])
    loop = GovernedReflexionLoop(
        meta,
        policy=ReflexionPolicy(max_trials=3, memory_window=2),
    )

    result = await loop.run("solve hard constraint", summary="hard constraint")
    assert result.outcome == "halted_max_retries"
    assert result.trials_used == 3
    assert len(result.reflections) == 2
    assert result.reflections[0].correction == "fix"
    assert result.reflections[1].correction == "replan"
    assert result.episode.verified is False
    assert result.episode.test_result == "failed"
    assert result.episode.output_fingerprint == ""
    assert verify_reflexion_chain(result.audit_chain) == []

    store = ExperienceStore(tmp_path / "exp.sqlite")
    assert store.record(result.episode) is False
    assert store.get(result.episode.episode_id) is None
    assert store.stats()["refused"] == 1
    with pytest.raises(FeedbackError):
        FeedbackEngine(ProviderScorecard()).apply(result.episode, Feedback.accept())
    store.close()


async def test_false_positive_self_evaluation_is_intercepted_by_independent_verifier() -> None:
    class FlakySelfEvaluator:
        """Simulates a self-generated unit-test suite that passes on wrong code (§4.3)."""

        def evaluate(self, task: str, trajectory: TrialTrajectory) -> EvaluationSignal:
            del task, trajectory
            return EvaluationSignal(
                passed=True,
                score=1.0,
                reason="flaky self-test passed",
                independent=False,
            )

    actor = ScriptedFreeProvider(
        [
            "def add(a, b): return 0  # TODO wrong",
            "def add(a, b): return a + b",
        ]
    )
    meta = _make_meta(
        [actor],
        verifier_fn=lambda text: VerificationResult(
            passed=("a + b" in text and "todo" not in text.lower()),
            detail="independent oracle check",
        ),
    )
    loop = GovernedReflexionLoop(
        meta,
        policy=ReflexionPolicy(
            max_trials=3, memory_window=1, require_independent_verification=True
        ),
        evaluator=FlakySelfEvaluator(),
    )

    result = await loop.run("write add(a, b)", task_type="coding")
    assert result.false_positives_caught == 1
    assert result.trials_used == 2
    assert result.outcome == "verified"
    assert result.evaluations[0].pathology == "self_eval_false_positive"
    assert result.episode.is_admissible is True


async def test_non_independent_pass_allowed_only_when_explicitly_disabled() -> None:
    class HeuristicEvaluator:
        def evaluate(self, task: str, trajectory: TrialTrajectory) -> EvaluationSignal:
            del task
            return EvaluationSignal(passed=bool(trajectory.output), score=1.0, independent=False)

    meta = _make_meta([ScriptedFreeProvider(["ok"])])
    loop = GovernedReflexionLoop(
        meta,
        policy=ReflexionPolicy(max_trials=2, require_independent_verification=False),
        evaluator=HeuristicEvaluator(),
    )
    result = await loop.run("quick heuristic task")
    assert result.outcome == "verified"
    assert result.false_positives_caught == 0


async def test_stagnation_and_stop_signals_halt_early_without_burning_trials() -> None:
    # 1. Identical consecutive reflections trigger halted_stagnation before trial 3.
    class ConstantReflector:
        async def reflect(
            self,
            task: str,
            trajectory: TrialTrajectory,
            evaluation: EvaluationSignal,
            memory: tuple[VerbalReflection, ...],
        ) -> VerbalReflection:
            del task, evaluation, memory
            return VerbalReflection(
                trial=trajectory.trial,
                claim="same claim",
                observation="same obs",
                gap="identical gap",
                next_action="identical action",
                correction="fix",
            )

    actor = ScriptedFreeProvider(["bad 1", "bad 2", "bad 3", "bad 4"])
    meta = _make_meta([actor], verifier_fn=lambda _: VerificationResult(False, "still wrong"))
    stagnant_loop = GovernedReflexionLoop(
        meta,
        policy=ReflexionPolicy(max_trials=5, memory_window=3),
        reflector=ConstantReflector(),
    )
    stag_res = await stagnant_loop.run("ambiguous webshop query")
    assert stag_res.outcome == "halted_stagnation"
    assert stag_res.trials_used == 2

    # 2. Explicit 'stop' correction halts after trial 0.
    class StopReflector:
        async def reflect(
            self,
            task: str,
            trajectory: TrialTrajectory,
            evaluation: EvaluationSignal,
            memory: tuple[VerbalReflection, ...],
        ) -> VerbalReflection:
            del task, evaluation, memory
            return VerbalReflection(
                trial=trajectory.trial,
                claim="boundary",
                observation="denied",
                gap="missing approval boundary",
                next_action="stop and report",
                correction="stop",
            )

    stop_loop = GovernedReflexionLoop(
        _make_meta(
            [ScriptedFreeProvider(["denied", "unused"])],
            verifier_fn=lambda _: VerificationResult(False, "denied"),
        ),
        policy=ReflexionPolicy(max_trials=4, memory_window=2),
        reflector=StopReflector(),
    )
    stop_res = await stop_loop.run("restricted task")
    assert stop_res.outcome == "halted_stop"
    assert stop_res.trials_used == 1


async def test_max_spend_zero_and_total_router_failure_are_enforced() -> None:
    paid_meta = _make_meta([DeterministicFallbackProvider()], max_spend_usd=1.0)
    with pytest.raises(FreeFirstPolicyError):
        await GovernedReflexionLoop(paid_meta).run("any goal")

    empty_meta = _make_meta([])
    res = await GovernedReflexionLoop(
        empty_meta,
        policy=ReflexionPolicy(max_trials=2, memory_window=2),
    ).run("offline goal")
    assert res.outcome == "halted_max_retries"
    assert res.episode.verified is False
    assert res.episode.test_result == "unverified"
    assert res.evaluations[0].pathology == "router_failure"


async def test_router_self_reflector_parses_structured_lines_and_falls_back() -> None:
    llm = ScriptedFreeProvider(
        [
            "OBSERVATION: saw off-by-one\nGAP: boundary index wrong\n"
            "NEXT_ACTION: use len(xs) - 1\nCORRECTION: replan\n",
            "unstructured prose with no colons",
        ]
    )
    meta = _make_meta([llm])
    reflector = RouterSelfReflector(meta)
    traj = TrialTrajectory(
        trial=0,
        prompt_sha256="a" * 64,
        output="draft",
        provider="deterministic",
        model="m",
    )
    sig = EvaluationSignal(passed=False, score=0.0, reason="index out of range")
    parsed = await reflector.reflect("binary search", traj, sig, ())
    assert parsed.gap == "boundary index wrong"
    assert parsed.next_action == "use len(xs) - 1"
    assert parsed.correction == "replan"

    unparsed = await reflector.reflect("binary search", traj, sig, (parsed,))
    assert unparsed.gap == "index out of range"
    assert unparsed.correction in {"fix", "replan"}

    failing_reflector = RouterSelfReflector(_make_meta([]))
    fb = await failing_reflector.reflect("binary search", traj, sig, ())
    assert fb.gap == "index out of range"


def test_trajectory_pathology_and_exact_match_evaluator() -> None:
    policy = ReflexionPolicy(max_consecutive_identical_actions=3, max_trajectory_steps=4)
    loop_steps = [
        TrajectoryStep(step_index=i, action="open drawer 1", observation="Nothing happens.")
        for i in range(3)
    ]
    assert detect_trajectory_pathology(loop_steps, policy) == "repeated_action_cycle"

    varied_steps = [
        TrajectoryStep(step_index=i, action=f"act-{i}", observation="ok") for i in range(5)
    ]
    assert detect_trajectory_pathology(varied_steps, policy) == "step_cap_exceeded"
    assert detect_trajectory_pathology(varied_steps[:3], policy) == ""

    ver_eval = VerifierEvaluator(lambda _: VerificationResult(True, "ok"), policy=policy)
    bad_traj = TrialTrajectory(
        trial=0,
        prompt_sha256="b" * 64,
        output="valid",
        steps=tuple(loop_steps),
        provider="deterministic",
        model="m",
    )
    assert ver_eval.evaluate("task", bad_traj).pathology == "repeated_action_cycle"

    det = DeterministicSelfReflector()
    import asyncio

    replan_cue = asyncio.run(
        det.reflect("alfworld", bad_traj, ver_eval.evaluate("task", bad_traj), ())
    )
    assert replan_cue.correction == "replan"

    em = ExactMatchEvaluator("Cairo, Egypt")
    good_traj = TrialTrajectory(
        trial=0, prompt_sha256="c" * 64, output="  cairo,   egypt \n", provider="d", model="m"
    )
    assert em.evaluate("capital", good_traj).passed is True
    assert ExactMatchEvaluator("Giza").evaluate("capital", good_traj).passed is False


def test_ast_unit_test_filter_accepts_clean_asserts_and_blocks_unsafe_or_broken_code() -> None:
    with pytest.raises(ValueError):
        filter_valid_python_tests(["assert 1 == 1"], max_tests=0)

    candidates = [
        "",
        "def broken(:",
        "x = 42",
        "def test_imp():\n    import os\n    assert os.getcwd() != ''",
        "def test_from():\n    from subprocess import run\n    assert run is not None",
        "assert open('/etc/passwd') is not None",
        "assert eval('1+1') == 2",
        "assert add(2, 3) == 5",
        "assert add(2, 3) == 5  # duplicate AST",
        "def test_zero():\n    assert add(0, 0) == 0",
        "def test_no_assert():\n    return 1",
        "def test_attr_call():\n    assert os.system('id') == 0",
        "assert add(-1, 1) == 0",
    ]
    filtered = filter_valid_python_tests(candidates, max_tests=2)
    assert filtered == (
        "assert add(2, 3) == 5",
        "def test_zero():\n    assert add(0, 0) == 0",
    )


def test_audit_chain_verifier_detects_empty_and_tampered_chains() -> None:
    assert verify_reflexion_chain(()) == ["reflexion audit chain is empty"]
    tampered = ReflexionAuditEntry(
        trial=1,
        policy_trace_id=DEFAULT_REFLEXION_POLICY_ID,
        outcome="verified",
        output_sha256="0" * 64,
        prev_hash="f" * 64,
        self_hash="e" * 64,
    )
    problems = verify_reflexion_chain((tampered,))
    assert len(problems) == 3


def test_memory_buffer_rejects_out_of_range_window() -> None:
    with pytest.raises(ValueError):
        ReflexionMemoryBuffer(window_size=0)
    with pytest.raises(ValueError):
        ReflexionMemoryBuffer(window_size=6)
    empty = ReflexionMemoryBuffer(window_size=2)
    assert empty.format_for_prompt() == ""


@given(
    window=st.integers(min_value=1, max_value=5),
    total_cues=st.integers(min_value=1, max_value=20),
)
@settings(max_examples=40)
def test_property_sliding_window_never_exceeds_omega(window: int, total_cues: int) -> None:
    buf = ReflexionMemoryBuffer(window_size=window)
    for idx in range(total_cues):
        cue = VerbalReflection(
            trial=idx,
            claim=f"claim {idx}",
            observation=f"obs {idx}",
            gap=f"distinct gap {idx}",
            next_action=f"distinct action {idx}",
        )
        stagnated = buf.append(cue)
        assert stagnated is False
        assert len(buf.snapshot()) <= window

    expected_kept = min(window, total_cues)
    assert len(buf.snapshot()) == expected_kept
    assert buf.evicted_count == max(0, total_cues - window)
    assert buf.snapshot()[-1].trial == total_cues - 1
