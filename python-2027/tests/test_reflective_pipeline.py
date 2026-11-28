"""End-to-end integration tests for ReflectiveCognitivePipeline."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from ai2027_protocol.budget import BudgetPolicy
from ai2027_protocol.contracts import AIProvider, PromptRequest, ProviderKind
from ai2027_protocol.meta_router import MetaRouter, VerificationResult
from ai2027_protocol.reflective_graph_memory import ReflectiveGraphMemory
from ai2027_protocol.reflective_pipeline import ReflectiveCognitivePipeline
from ai2027_protocol.reflective_tree import TreeSearchPolicy
from ai2027_protocol.reflexion_contracts import (
    EvaluationSignal,
    ReflexionPolicy,
    TrialTrajectory,
)
from ai2027_protocol.router import FreeFirstRouter, RouterConfig

_FAST_CFG = RouterConfig(
    per_provider_timeout_s=0.1,
    max_retries_per_provider=0,
    base_backoff_s=0.0,
)


class PipelineProvider:
    """Deterministic local provider recording prompts and returning scripted outputs."""

    kind: ProviderKind = ProviderKind.DETERMINISTIC
    model: str = "pipeline-local-v1"
    is_remote: bool = False

    def __init__(self, outputs: Sequence[str]) -> None:
        self._outputs = list(outputs)
        self.prompts_seen: list[str] = []
        self._idx = 0

    def is_configured(self) -> bool:
        return True

    async def complete(self, request: PromptRequest, *, timeout_s: float) -> str:
        del timeout_s
        call_no = self._idx
        self._idx += 1
        self.prompts_seen.append(request.prompt)
        return self._outputs[min(call_no, len(self._outputs) - 1)]


class ExactTokenEvaluator:
    """Independent evaluator passing only when ``VERIFIED_WIN`` is present."""

    def evaluate(self, task: str, trajectory: TrialTrajectory) -> EvaluationSignal:
        del task
        if "VERIFIED_WIN" in trajectory.output:
            return EvaluationSignal(
                passed=True,
                score=1.0,
                reason="independent check passed",
                independent=True,
            )
        return EvaluationSignal(
            passed=False,
            score=0.2,
            reason="missing boundary validation",
            independent=True,
        )


def _make_meta(
    providers: Sequence[AIProvider],
    *,
    verifier_fn: Callable[[str], VerificationResult] | None = None,
) -> MetaRouter:
    router = FreeFirstRouter(
        providers=list(providers),
        config=_FAST_CFG,
        budget_policy=BudgetPolicy(max_spend_usd=0.0),
    )
    return MetaRouter(router, verifier=verifier_fn)


async def test_pipeline_tree_and_reflexion_close_rmm_utility_and_graph_loop(
    tmp_path: Path,
) -> None:
    """Verified tree run indexes failure_mode nodes; second run retrieves & updates RMM utility."""
    db_file = tmp_path / "pipeline.sqlite3"
    provider = PipelineProvider(
        [
            "FIRST_BRANCH_FAIL",
            "VERIFIED_WIN :: sqlite wal lock fix in store.py",
            "VERIFIED_WIN :: second task reuses prior lesson",
            "NEVER_PASSES_1",
            "NEVER_PASSES_2",
        ]
    )
    meta = _make_meta([provider])

    with pytest.raises(ValueError, match="k_lessons"):
        with ReflectiveGraphMemory(db_file) as bad_mem:
            ReflectiveCognitivePipeline(meta, bad_mem, k_lessons=6)

    with ReflectiveGraphMemory(db_file) as mem:
        pipeline = ReflectiveCognitivePipeline(
            meta,
            mem,
            reflexion_policy=ReflexionPolicy(max_trials=2, memory_window=2),
            tree_policy=TreeSearchPolicy(max_branching=2, max_depth=2, max_expansions=4),
            evaluator=ExactTokenEvaluator(),
            k_lessons=2,
        )

        # 1. Auto mode with branching_hint=True selects BoundedReflectiveTreeSearch ("tree").
        first = await pipeline.execute(
            "Implement sqlite wal lock fix in store.py",
            mode="auto",
            branching_hint=True,
            task_type="coding",
        )
        assert first.strategy_used == "tree"
        assert first.verified is True
        assert first.admitted_to_graph is True
        assert first.retrieved_episode_ids == ()
        assert first.overcome_failure_nodes == ("failure_mode:missing boundary validation",)
        assert mem.get_node("failure_mode:missing boundary validation") is not None

        # 2. Second task retrieves episode #1, injects Verified Prior Lessons, and boosts utility!
        second = await pipeline.execute(
            "Extend sqlite wal lock fix in store.py for readers",
            mode="auto",
            branching_hint=False,
            task_type="coding",
        )
        assert second.strategy_used == "reflexion"
        assert second.verified is True
        assert second.admitted_to_graph is True
        assert first.episode.episode_id in second.retrieved_episode_ids
        assert second.utility_rows_updated == 1
        assert "Verified Prior Lessons" in provider.prompts_seen[2]

        retrieved_cnt, helpful_cnt, util = mem.utility_stats(first.episode.episode_id)
        assert retrieved_cnt == 1
        assert helpful_cnt == 1
        assert util > 0.5

        # 3. Third task fails verification -> refused into refusals, RMM records non-helpful!
        third = await pipeline.execute(
            "Broken sqlite wal migration in store.py",
            mode="reflexion",
            task_type="coding",
        )
        assert third.verified is False
        assert third.admitted_to_graph is False
        assert third.overcome_failure_nodes == ()
        stats = mem.stats()
        assert stats["stored"] == 2
        assert stats["refused"] == 1
        # Episode #1 was retrieved in failed task #3: helpful stayed 1, retrieved became 2.
        r_after, h_after, _ = mem.utility_stats(first.episode.episode_id)
        assert r_after == 2
        assert h_after == 1
        assert mem.verify_durability() == []
