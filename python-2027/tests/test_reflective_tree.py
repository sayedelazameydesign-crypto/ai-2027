"""Tests and Hypothesis properties for BoundedReflectiveTreeSearch (ReTreVal)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from ai2027_protocol.budget import BudgetPolicy
from ai2027_protocol.contracts import AIProvider, PromptRequest, ProviderKind
from ai2027_protocol.experience_store import ExperienceStore
from ai2027_protocol.meta_router import FreeFirstPolicyError, MetaRouter, VerificationResult
from ai2027_protocol.reflective_tree import (
    BoundedReflectiveTreeSearch,
    TreeSearchPolicy,
    verify_tree_audit_chain,
)
from ai2027_protocol.reflexion_contracts import (
    EvaluationSignal,
    TrajectoryStep,
    TrialTrajectory,
)
from ai2027_protocol.router import FreeFirstRouter, RouterConfig

_FAST_CFG = RouterConfig(
    per_provider_timeout_s=0.1,
    max_retries_per_provider=0,
    base_backoff_s=0.0,
)


class BranchAwareProvider:
    """Local deterministic provider emitting scripted responses and recording prompts."""

    kind: ProviderKind = ProviderKind.DETERMINISTIC
    model: str = "tree-local-v1"
    is_remote: bool = False

    def __init__(self, outputs: Sequence[str], *, fail_indices: set[int] | None = None) -> None:
        self._outputs = list(outputs)
        self._fail_indices = fail_indices or set()
        self.prompts_seen: list[str] = []
        self._idx = 0

    def is_configured(self) -> bool:
        return True

    async def complete(self, request: PromptRequest, *, timeout_s: float) -> str:
        del timeout_s
        call_no = self._idx
        self._idx += 1
        self.prompts_seen.append(request.prompt)
        if call_no in self._fail_indices:
            raise RuntimeError("simulated provider failure")
        return self._outputs[min(call_no, len(self._outputs) - 1)]


class ScoredKeywordEvaluator:
    """Objective test evaluator scoring partial progress vs full satisfaction."""

    def __init__(self, *, independent: bool = True) -> None:
        self.independent = independent

    def evaluate(self, task: str, trajectory: TrialTrajectory) -> EvaluationSignal:
        del task
        text = trajectory.output
        if "VERIFIED_SOLUTION_FINAL" in text:
            return EvaluationSignal(
                passed=True,
                score=1.0,
                reason="complete verified solution",
                independent=self.independent,
            )
        if "PROMISING_PARTIAL_STEP" in text:
            return EvaluationSignal(
                passed=False,
                score=0.75,
                reason="missing edge-case guard on empty input",
                independent=self.independent,
            )
        return EvaluationSignal(
            passed=False,
            score=0.15,
            reason="wrong algorithm choice",
            independent=self.independent,
        )


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


async def test_tree_search_passes_sibling_reflections_and_finds_verified_child(
    tmp_path: Path,
) -> None:
    """Failed sibling reflections are injected into later branches; winning path is verified."""
    provider = BranchAwareProvider(
        [
            "DEAD_END_BRANCH_0",
            "PROMISING_PARTIAL_STEP",
            "VERIFIED_SOLUTION_FINAL with edge-case guard",
        ]
    )
    meta = _make_meta([provider])
    policy = TreeSearchPolicy(max_branching=2, max_depth=2, max_expansions=5)
    engine = BoundedReflectiveTreeSearch(
        meta,
        policy=policy,
        evaluator=ScoredKeywordEvaluator(independent=True),
    )

    result = await engine.search("Design a deterministic parser with empty-input guard")
    assert result.outcome == "verified_pass"
    assert result.verified is True
    assert result.expansions_used == 3
    assert result.winning_node_id == "node_03_d2_b0"
    assert verify_tree_audit_chain(result.audit_chain) == []

    # Branch 1 (index 1) must receive the verbal reflection from failed Branch 0!
    assert "Cross-Branch Reflections" in provider.prompts_seen[1]
    assert "wrong algorithm choice" in provider.prompts_seen[1]
    # Depth-2 child (index 2) must be conditioned on the higher-scoring parent (score 0.75 > 0.15)!
    assert "Parent Candidate (depth=1)" in provider.prompts_seen[2]
    assert "PROMISING_PARTIAL_STEP" in provider.prompts_seen[2]

    with ExperienceStore(tmp_path / "tree_ok.sqlite3") as store:
        assert store.record(result.episode) is True
        assert store.stats() == {"stored": 1, "refused": 0}


async def test_tree_search_intercepts_self_eval_false_positive_and_refuses_unverified(
    tmp_path: Path,
) -> None:
    """Non-independent passing branch rejected by independent verifier never enters memory."""
    provider = BranchAwareProvider(
        [
            "VERIFIED_SOLUTION_FINAL {{unresolved_placeholder}}",
            "DEAD_END_2",
            "DEAD_END_3",
        ]
    )
    meta = _make_meta([provider])
    policy = TreeSearchPolicy(max_branching=2, max_depth=2, max_expansions=3)
    engine = BoundedReflectiveTreeSearch(
        meta,
        policy=policy,
        evaluator=ScoredKeywordEvaluator(independent=False),
    )

    result = await engine.search("Generate production configuration", task_type="coding")
    assert result.verified is False
    assert result.outcome == "exhausted_budget"
    assert result.expansions_used == 3
    assert result.nodes[0].evaluation.pathology == "self_eval_false_positive"
    assert verify_tree_audit_chain(result.audit_chain) == []

    with ExperienceStore(tmp_path / "tree_fp.sqlite3") as store:
        assert store.record(result.episode) is False
        assert store.stats() == {"stored": 0, "refused": 1}


async def test_tree_search_handles_router_failure_and_pathology_and_budget_guard() -> None:
    """Router failures, action-cycle pathologies, and non-zero spend policies are handled."""
    paid_meta = _make_meta(
        [BranchAwareProvider(["VERIFIED_SOLUTION_FINAL"])],
        max_spend_usd=1.0,
    )
    with pytest.raises(FreeFirstPolicyError):
        await BoundedReflectiveTreeSearch(paid_meta).search("Blocked paid run")

    provider = BranchAwareProvider(
        [
            "ignored-router-fail",
            "LOOPING_TRACE",
            "CLEAN_FINAL_ANSWER",
        ],
        fail_indices={0},
    )

    def _extract_steps(text: str) -> Sequence[TrajectoryStep]:
        if "LOOPING_TRACE" in text:
            return tuple(
                TrajectoryStep(step_index=i, action="search[x]", observation="none")
                for i in range(4)
            )
        return (TrajectoryStep(step_index=0, action="finish", observation="ok"),)

    meta = _make_meta([provider])
    engine = BoundedReflectiveTreeSearch(
        meta,
        policy=TreeSearchPolicy(
            max_branching=3,
            max_depth=1,
            max_expansions=3,
            require_independent_verification=False,
        ),
        step_extractor=_extract_steps,
    )
    result = await engine.search("Recover from provider error and loop", summary="custom summary")
    assert result.verified is True
    assert result.nodes[0].evaluation.pathology == "router_failure"
    assert result.nodes[1].evaluation.pathology == "repeated_action_cycle"
    assert result.nodes[2].independently_verified is True

    # Tampered audit chain is detected cleanly.
    bad_first = result.audit_chain[0].model_copy(
        update={"expansion_index": 9, "self_hash": "0" * 64}
    )
    tampered_chain = (bad_first, *result.audit_chain[1:])
    chain_errors = verify_tree_audit_chain(tampered_chain)
    assert len(chain_errors) >= 2


def test_tree_policy_enforces_strict_upper_bounds() -> None:
    """TreeSearchPolicy rejects branching > 3, depth > 3, or expansions > 9."""
    with pytest.raises(ValidationError):
        TreeSearchPolicy(max_branching=4)
    with pytest.raises(ValidationError):
        TreeSearchPolicy(max_depth=4)
    with pytest.raises(ValidationError):
        TreeSearchPolicy(max_expansions=10)


@settings(max_examples=25, deadline=None)
@given(
    branching=st.integers(min_value=1, max_value=3),
    depth=st.integers(min_value=1, max_value=3),
    expansions=st.integers(min_value=1, max_value=9),
)
def test_property_tree_expansions_never_exceed_configured_cap(
    branching: int,
    depth: int,
    expansions: int,
) -> None:
    """Across all valid (branching, depth, expansions) triples, node count <= max_expansions."""
    import asyncio

    provider = BranchAwareProvider(["STILL_PARTIAL"] * 12)
    meta = _make_meta(
        [provider],
        verifier_fn=lambda _txt: VerificationResult(False, "never passes"),
    )
    policy = TreeSearchPolicy(
        max_branching=branching,
        max_depth=depth,
        max_expansions=expansions,
    )
    result = asyncio.run(BoundedReflectiveTreeSearch(meta, policy=policy).search("Bounded check"))
    assert result.expansions_used <= expansions
    assert all(node.depth <= depth for node in result.nodes)
    assert verify_tree_audit_chain(result.audit_chain) == []
