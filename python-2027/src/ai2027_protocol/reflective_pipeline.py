"""End-to-end reflective pipeline wiring Reflexion / ReTreVal to ReflectiveGraphMemory.

Closes the post-Reflexion learning loop under AI-2027's hard invariants:

1. **Retrieve (RMM utility-aware):** queries
   :class:`~.reflective_graph_memory.ReflectiveGraphMemory` for up to
   ``k_lessons`` verified prior episodes and formats them as bounded context.
2. **Execute ($0 free-first):** dispatches either
   :class:`~.reflective_tree.BoundedReflectiveTreeSearch` (``mode="tree"``) or
   :class:`~.reflexion.GovernedReflexionLoop` (``mode="reflexion"``;
   ``mode="auto"`` selects ``"tree"`` when ``branching_hint`` is True).
3. **Retrospective RMM update:** records whether the retrieved episodes contributed
   to an independently verified downstream outcome via
   :meth:`~.reflective_graph_memory.ReflectiveGraphMemory.record_retrieval_outcome`.
4. **Prospective graph admission:** indexes the resulting
   :class:`~.experience_store.ExperienceRecord` plus overcome ``failure_mode``
   graph nodes **if and only if** the run passed independent verification;
   unverified runs are logged as refusals.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import Field

from .contracts import StrictModel
from .experience_store import ExperienceRecord
from .meta_router import MetaRouter
from .reflective_graph_memory import (
    GraphEdgeSpec,
    GraphNodeSpec,
    RankedExperienceHit,
    ReflectiveGraphMemory,
)
from .reflective_tree import BoundedReflectiveTreeSearch, TreeSearchPolicy
from .reflexion import GovernedReflexionLoop
from .reflexion_contracts import (
    EvaluatorProtocol,
    ReflexionPolicy,
    SelfReflectorProtocol,
    VerbalReflection,
)

ExecutionStrategy = Literal["auto", "reflexion", "tree"]


class ReflectivePipelineResult(StrictModel):
    """Unified outcome of a retrieval-augmented reflective execution."""

    strategy_used: Literal["reflexion", "tree"]
    verified: bool
    admitted_to_graph: bool
    final_output: str
    retrieved_episode_ids: tuple[str, ...] = ()
    utility_rows_updated: int = Field(ge=0)
    overcome_failure_nodes: tuple[str, ...] = ()
    episode: ExperienceRecord


def _format_prior_lessons(hits: Sequence[RankedExperienceHit]) -> str:
    """Format verified prior episodes into a compact bounded context block."""
    if not hits:
        return ""
    lines = ["[Verified Prior Lessons — reuse proven patterns]"]
    for hit in hits:
        summary = " ".join(hit.record.task_summary.strip().split())[:180]
        lines.append(
            f"- ({hit.record.episode_id}, utility={hit.utility_score:.2f}, "
            f"provider={hit.record.provider}/{hit.record.model}): {summary}"
        )
    return "\n".join(lines)


def _extract_overcome_failure_graph(
    task_type: str,
    reflections: Sequence[VerbalReflection],
) -> tuple[tuple[GraphNodeSpec, ...], tuple[GraphEdgeSpec, ...]]:
    """Convert overcome verbal reflections into ``failure_mode`` graph nodes/edges."""
    cap_id = f"capability:{task_type.strip().lower()}"
    nodes: dict[str, GraphNodeSpec] = {}
    edges: list[GraphEdgeSpec] = []
    for item in reflections[:4]:
        key = " ".join(item.gap.strip().lower().split())[:120]
        if not key:
            continue
        spec = GraphNodeSpec(
            node_kind="failure_mode",
            key=key,
            label=item.gap.strip()[:160],
        )
        if spec.node_id in nodes:
            continue
        nodes[spec.node_id] = spec
        edges.append(
            GraphEdgeSpec(
                source_id=cap_id,
                target_id=spec.node_id,
                relation="overcame_failure_mode",
            )
        )
    return tuple(nodes.values()), tuple(edges)


class ReflectiveCognitivePipeline:
    """Orchestrates retrieval, bounded reflection/tree search, and graph memory updates."""

    def __init__(
        self,
        meta_router: MetaRouter,
        memory: ReflectiveGraphMemory,
        *,
        reflexion_policy: ReflexionPolicy | None = None,
        tree_policy: TreeSearchPolicy | None = None,
        evaluator: EvaluatorProtocol | None = None,
        reflector: SelfReflectorProtocol | None = None,
        k_lessons: int = 3,
    ) -> None:
        """Bind ``meta_router``, ``memory``, and optional execution policies."""
        if k_lessons < 0 or k_lessons > 5:
            raise ValueError("k_lessons must be between 0 and 5")
        self.meta_router = meta_router
        self.memory = memory
        self.k_lessons = k_lessons
        self.reflexion_loop = GovernedReflexionLoop(
            meta_router,
            policy=reflexion_policy,
            evaluator=evaluator,
            reflector=reflector,
        )
        self.tree_search = BoundedReflectiveTreeSearch(
            meta_router,
            policy=tree_policy,
            evaluator=evaluator,
            reflector=reflector,
        )

    async def execute(
        self,
        task: str,
        *,
        mode: ExecutionStrategy = "auto",
        branching_hint: bool = False,
        task_type: str | None = None,
        summary: str | None = None,
    ) -> ReflectivePipelineResult:
        """Run RMM retrieval -> governed execution -> RMM utility update -> graph index."""
        self.meta_router.assert_free_first()
        resolved_type = (
            self.meta_router.classify(task).task_type
            if task_type is None
            else self.meta_router.registry.select(task_type).task_type
        )

        hits: list[RankedExperienceHit] = (
            self.memory.search(
                task,
                k=self.k_lessons,
                task_type=resolved_type,
                rerank_by_utility=True,
            )
            if self.k_lessons > 0
            else []
        )

        prior_block = _format_prior_lessons(hits)
        conditioned_task = f"{task.strip()}\n\n{prior_block}" if prior_block else task

        chosen_strategy: Literal["reflexion", "tree"] = (
            ("tree" if branching_hint else "reflexion") if mode == "auto" else mode
        )
        clean_summary = (summary if summary is not None else task)[:200]

        reflections: tuple[VerbalReflection, ...]
        if chosen_strategy == "tree":
            tree_res = await self.tree_search.search(
                conditioned_task,
                task_type=resolved_type,
                summary=clean_summary,
            )
            verified = tree_res.verified
            final_output = tree_res.final_output
            episode = tree_res.episode
            reflections = tree_res.shared_reflections
        else:
            refl_res = await self.reflexion_loop.run(
                conditioned_task,
                task_type=resolved_type,
                summary=clean_summary,
            )
            verified = refl_res.episode.verified
            final_output = refl_res.trajectories[-1].output if refl_res.trajectories else ""
            episode = refl_res.episode
            reflections = refl_res.reflections

        retrieved_ids = tuple(h.record.episode_id for h in hits)
        utility_updated = (
            self.memory.record_retrieval_outcome(
                retrieved_ids,
                downstream_verified=verified,
            )
            if retrieved_ids
            else 0
        )

        extra_nodes, extra_edges = (
            _extract_overcome_failure_graph(resolved_type, reflections) if verified else ((), ())
        )
        admitted = self.memory.index_episode(
            episode,
            extra_nodes=extra_nodes,
            extra_edges=extra_edges,
        )
        return ReflectivePipelineResult(
            strategy_used=chosen_strategy,
            verified=verified,
            admitted_to_graph=admitted,
            final_output=final_output,
            retrieved_episode_ids=retrieved_ids,
            utility_rows_updated=utility_updated,
            overcome_failure_nodes=tuple(n.node_id for n in extra_nodes) if admitted else (),
            episode=episode,
        )


__all__ = [
    "ExecutionStrategy",
    "ReflectiveCognitivePipeline",
    "ReflectivePipelineResult",
]
