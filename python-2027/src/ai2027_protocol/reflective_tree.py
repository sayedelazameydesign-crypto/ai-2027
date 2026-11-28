"""Bounded reflective tree search (ReTreVal / ToT + Reflexion hybrid).

Overcomes the linear single-trajectory trap of ReAct and local-minimum cycles in
sequential Reflexion by exploring a strictly bounded tree of candidate steps
while sharing structured :class:`~.reflexion_contracts.VerbalReflection` cues
across sibling and child branches.

Governance guarantees
---------------------
* **Hard combinatorial bounds:** ``max_branching <= 3``, ``max_depth <= 3``, and
  ``max_expansions <= 9`` prevent combinatorial blow-up on free tiers.
* **MAX_SPEND = 0:** :meth:`~.meta_router.MetaRouter.assert_free_first` runs
  before every expansion and every LLM-backed reflection.
* **Independent verification & false-positive interception:** a branch that
  passes a non-independent heuristic evaluator (``independent=False``) is
  cross-checked by ``meta_router.verifier`` when
  ``require_independent_verification=True``; unverified runs emit
  ``ExperienceRecord(verified=False)`` so :class:`~.experience_store.ExperienceStore`
  refuses them into ``refusals``.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Sequence
from typing import Literal

from pydantic import Field

from ._kernel_path import ensure_kernel_importable
from .contracts import CompletionResponse, PromptRequest, ProviderKind, StrictModel
from .experience_store import ExperienceRecord, fingerprint_text
from .meta_router import MetaRouter
from .reflexion import DeterministicSelfReflector
from .reflexion_contracts import (
    EvaluationSignal,
    EvaluatorProtocol,
    ReflexionPolicy,
    SelfReflectorProtocol,
    TrajectoryStep,
    TrialTrajectory,
    VerbalReflection,
    VerifierEvaluator,
    detect_trajectory_pathology,
)

ensure_kernel_importable()
from kernel2027 import canonical_json, sha256_text  # noqa: E402

DEFAULT_TREE_POLICY_ID = "POL-BOUNDED-REFLECTIVE-TREE"
TreeSearchOutcome = Literal["verified_pass", "exhausted_budget", "halted_no_candidates"]


class TreeSearchPolicy(StrictModel):
    """Hard governance caps for bounded reflective tree search."""

    policy_id: str = Field(default=DEFAULT_TREE_POLICY_ID, min_length=3, max_length=80)
    max_branching: int = Field(default=2, ge=1, le=3)
    max_depth: int = Field(default=2, ge=1, le=3)
    max_expansions: int = Field(default=6, ge=1, le=9)
    require_independent_verification: bool = True
    max_trajectory_steps: int = Field(default=30, ge=3, le=100)


class ReflectiveTreeNode(StrictModel):
    """Immutable search-tree node recording trajectory, evaluation, and reflection."""

    node_id: str = Field(min_length=1, max_length=64)
    parent_id: str | None = None
    depth: int = Field(ge=1, le=3)
    branch_index: int = Field(ge=0, le=2)
    output: str = Field(default="", max_length=32_000)
    provider: str = Field(default="none", min_length=1, max_length=64)
    model: str = Field(default="none", min_length=1, max_length=160)
    latency_ms: float = Field(default=0.0, ge=0.0)
    evaluation: EvaluationSignal
    independently_verified: bool = False
    reflection: VerbalReflection | None = None


class TreeAuditEntry(StrictModel):
    """Hash-chained audit entry for one node expansion in ``BoundedReflectiveTreeSearch``."""

    expansion_index: int = Field(ge=1)
    node_id: str = Field(min_length=1, max_length=64)
    parent_id: str | None = None
    depth: int = Field(ge=1, le=3)
    score: float = Field(ge=0.0, le=1.0)
    passed: bool
    independently_verified: bool
    output_sha256: str = Field(min_length=64, max_length=64)
    reflection_signature: str = Field(default="", max_length=256)
    prev_hash: str = Field(default="", max_length=64)
    self_hash: str = Field(min_length=64, max_length=64)


class TreeSearchResult(StrictModel):
    """Aggregate result of a bounded reflective tree search run."""

    run_id: str = Field(min_length=3, max_length=80)
    policy_id: str = Field(default=DEFAULT_TREE_POLICY_ID, min_length=3, max_length=80)
    outcome: TreeSearchOutcome
    verified: bool
    winning_node_id: str | None = None
    final_output: str = ""
    expansions_used: int = Field(ge=0)
    nodes: tuple[ReflectiveTreeNode, ...] = ()
    shared_reflections: tuple[VerbalReflection, ...] = ()
    audit_chain: tuple[TreeAuditEntry, ...] = ()
    episode: ExperienceRecord


def _compute_tree_audit_hash(
    *,
    expansion_index: int,
    node_id: str,
    parent_id: str | None,
    depth: int,
    score: float,
    passed: bool,
    independently_verified: bool,
    output_sha256: str,
    reflection_signature: str,
    prev_hash: str,
) -> str:
    payload = {
        "depth": depth,
        "expansion_index": expansion_index,
        "independently_verified": independently_verified,
        "node_id": node_id,
        "output_sha256": output_sha256,
        "parent_id": parent_id or "",
        "passed": passed,
        "prev_hash": prev_hash,
        "reflection_signature": reflection_signature,
        "score": round(score, 6),
    }
    return sha256_text(canonical_json(payload))


def verify_tree_audit_chain(entries: Sequence[TreeAuditEntry]) -> list[str]:
    """Verify hash continuity and digest integrity across ``entries``."""
    errors: list[str] = []
    expected_prev = ""
    for idx, entry in enumerate(entries, start=1):
        if entry.expansion_index != idx:
            errors.append(
                f"entry {idx}: expected expansion_index={idx}, got {entry.expansion_index}"
            )
        if entry.prev_hash != expected_prev:
            errors.append(f"entry {idx}: prev_hash mismatch")
        recomputed = _compute_tree_audit_hash(
            expansion_index=entry.expansion_index,
            node_id=entry.node_id,
            parent_id=entry.parent_id,
            depth=entry.depth,
            score=entry.score,
            passed=entry.passed,
            independently_verified=entry.independently_verified,
            output_sha256=entry.output_sha256,
            reflection_signature=entry.reflection_signature,
            prev_hash=entry.prev_hash,
        )
        if entry.self_hash != recomputed:
            errors.append(f"entry {idx}: self_hash mismatch")
        expected_prev = entry.self_hash
    return errors


def _compose_branch_prompt(
    task: str,
    *,
    parent_output: str | None,
    branch_index: int,
    depth: int,
    shared_reflections: Sequence[VerbalReflection],
) -> str:
    """Build a branch-specific prompt conditioned on ancestor output and sibling critiques."""
    sections: list[str] = [task.strip()]
    if parent_output:
        sections.append(f"[Parent Candidate (depth={depth - 1})]\n{parent_output.strip()[:1_500]}")
    if shared_reflections:
        cues = "\n".join(f"- {r.render_cue()}" for r in shared_reflections[-4:])
        sections.append(
            "[Cross-Branch Reflections — Do NOT repeat these failed assumptions]\n" + cues
        )
    sections.append(
        f"[Branch Directive: depth={depth}, alternative={branch_index + 1} — "
        "produce a distinct, complete, verifiable solution]"
    )
    return "\n\n".join(sections)


class BoundedReflectiveTreeSearch:
    """Best-first bounded tree search sharing verbal reflections across branches."""

    def __init__(
        self,
        meta_router: MetaRouter,
        *,
        policy: TreeSearchPolicy | None = None,
        evaluator: EvaluatorProtocol | None = None,
        reflector: SelfReflectorProtocol | None = None,
        step_extractor: Callable[[str], Sequence[TrajectoryStep]] | None = None,
    ) -> None:
        """Bind ``meta_router`` (MAX_SPEND=0), ``policy``, ``evaluator``, and ``reflector``."""
        self.meta_router = meta_router
        self.policy = policy or TreeSearchPolicy()
        self.evaluator: EvaluatorProtocol = evaluator or VerifierEvaluator(meta_router.verifier)
        self.reflector: SelfReflectorProtocol = reflector or DeterministicSelfReflector()
        self.step_extractor = step_extractor

    def _build_branch_request(
        self,
        prompt: str,
        *,
        base_temperature: float,
        branch_index: int,
        max_tokens: int,
    ) -> PromptRequest:
        default_kinds = self.meta_router.registry.default_profile.preferred_providers
        permitted = [
            kind for kind in default_kinds if self.meta_router.privacy.permits(ProviderKind(kind))
        ]
        allow_remote = self.meta_router.privacy.allow_remote and bool(permitted)
        bumped_temp = min(1.0, round(base_temperature + 0.08 * branch_index, 3))
        return PromptRequest(
            prompt=prompt,
            temperature=bumped_temp,
            max_tokens=max_tokens,
            allow_remote=allow_remote,
            preferred_providers=tuple(ProviderKind(k) for k in permitted) or None,
        )

    async def _expand_single_branch(
        self,
        *,
        task: str,
        expansion_num: int,
        parent: ReflectiveTreeNode | None,
        depth: int,
        branch_index: int,
        base_temperature: float,
        max_tokens: int,
        shared_reflections: tuple[VerbalReflection, ...],
    ) -> tuple[ReflectiveTreeNode, VerbalReflection | None]:
        self.meta_router.assert_free_first()
        prompt = _compose_branch_prompt(
            task,
            parent_output=parent.output if parent is not None else None,
            branch_index=branch_index,
            depth=depth,
            shared_reflections=shared_reflections,
        )
        request = self._build_branch_request(
            prompt,
            base_temperature=base_temperature,
            branch_index=branch_index,
            max_tokens=max_tokens,
        )
        t0 = time.perf_counter()
        router_failed = False
        try:
            response: CompletionResponse = await self.meta_router.router.route(request)
            output_text = response.text
            provider_name = response.provider.value
            model_name = response.model
            latency_ms = response.latency_ms
        except Exception:
            router_failed = True
            output_text = ""
            provider_name = "none"
            model_name = "router-error"
            latency_ms = round((time.perf_counter() - t0) * 1000.0, 3)

        steps = tuple(self.step_extractor(output_text)) if self.step_extractor else ()
        trajectory = TrialTrajectory(
            trial=expansion_num,
            prompt_sha256=fingerprint_text(prompt),
            output=output_text[:32_000],
            steps=steps,
            provider=provider_name,
            model=model_name,
            latency_ms=latency_ms,
            router_failed=router_failed,
        )

        pathology = detect_trajectory_pathology(
            steps,
            ReflexionPolicy(max_trajectory_steps=self.policy.max_trajectory_steps),
        )
        if router_failed:
            evaluation = EvaluationSignal(
                passed=False,
                score=0.0,
                reason="all free-first providers failed for branch",
                independent=True,
                pathology="router_failure",
            )
        elif pathology:
            evaluation = EvaluationSignal(
                passed=False,
                score=0.0,
                reason=f"trajectory pathology detected: {pathology}",
                independent=True,
                pathology=pathology,
            )
        else:
            evaluation = self.evaluator.evaluate(task, trajectory)

        independently_verified = False
        if evaluation.passed:
            if evaluation.independent or not self.policy.require_independent_verification:
                independently_verified = True
            else:
                indep_check = self.meta_router.verifier(output_text)
                if indep_check.passed:
                    independently_verified = True
                else:
                    evaluation = EvaluationSignal(
                        passed=False,
                        score=min(evaluation.score, 0.49),
                        reason=(
                            "self-evaluation false positive intercepted by independent verifier: "
                            f"{indep_check.detail or 'verification failed'}"
                        ),
                        independent=True,
                        pathology="self_eval_false_positive",
                    )

        reflection: VerbalReflection | None = None
        if not independently_verified:
            reflection = await self.reflector.reflect(
                task,
                trajectory,
                evaluation,
                shared_reflections[-3:],
            )

        node = ReflectiveTreeNode(
            node_id=f"node_{expansion_num:02d}_d{depth}_b{branch_index}",
            parent_id=parent.node_id if parent is not None else None,
            depth=depth,
            branch_index=branch_index,
            output=output_text[:32_000],
            provider=provider_name,
            model=model_name,
            latency_ms=latency_ms,
            evaluation=evaluation,
            independently_verified=independently_verified,
            reflection=reflection,
        )
        return node, reflection

    async def search(
        self,
        task: str,
        *,
        task_type: str | None = None,
        summary: str | None = None,
    ) -> TreeSearchResult:
        """Explore up to ``max_expansions`` branches best-first under MAX_SPEND=0."""
        self.meta_router.assert_free_first()
        decision = (
            self.meta_router.classify(task)
            if task_type is None
            else self.meta_router.registry.select(task_type)
        )
        profile = decision.profile
        run_id = f"rtree_{uuid.uuid4().hex[:12]}"

        frontier: list[tuple[float, int, ReflectiveTreeNode | None]] = [(1.0, 0, None)]
        nodes: list[ReflectiveTreeNode] = []
        shared_reflections: list[VerbalReflection] = []
        seen_reflection_sigs: set[str] = set()
        audit_chain: list[TreeAuditEntry] = []
        prev_hash = ""
        winning_node: ReflectiveTreeNode | None = None

        while frontier and len(nodes) < self.policy.max_expansions:
            frontier.sort(key=lambda item: (item[0], -item[1]), reverse=True)
            _prio, current_depth, parent_node = frontier.pop(0)
            next_depth = current_depth + 1
            if next_depth > self.policy.max_depth:
                continue

            for branch_idx in range(self.policy.max_branching):
                if len(nodes) >= self.policy.max_expansions:
                    break
                expansion_num = len(nodes) + 1
                node, reflection = await self._expand_single_branch(
                    task=task,
                    expansion_num=expansion_num,
                    parent=parent_node,
                    depth=next_depth,
                    branch_index=branch_idx,
                    base_temperature=profile.temperature,
                    max_tokens=profile.max_tokens,
                    shared_reflections=tuple(shared_reflections),
                )
                nodes.append(node)

                raw_sig = reflection.normalized_signature() if reflection is not None else ""
                if reflection is not None and raw_sig not in seen_reflection_sigs:
                    seen_reflection_sigs.add(raw_sig)
                    shared_reflections.append(reflection)
                refl_sig = sha256_text(raw_sig) if raw_sig else ""

                out_sha = fingerprint_text(node.output)
                self_hash = _compute_tree_audit_hash(
                    expansion_index=expansion_num,
                    node_id=node.node_id,
                    parent_id=node.parent_id,
                    depth=node.depth,
                    score=node.evaluation.score,
                    passed=node.evaluation.passed,
                    independently_verified=node.independently_verified,
                    output_sha256=out_sha,
                    reflection_signature=refl_sig,
                    prev_hash=prev_hash,
                )
                audit_chain.append(
                    TreeAuditEntry(
                        expansion_index=expansion_num,
                        node_id=node.node_id,
                        parent_id=node.parent_id,
                        depth=node.depth,
                        score=node.evaluation.score,
                        passed=node.evaluation.passed,
                        independently_verified=node.independently_verified,
                        output_sha256=out_sha,
                        reflection_signature=refl_sig,
                        prev_hash=prev_hash,
                        self_hash=self_hash,
                    )
                )
                prev_hash = self_hash

                if node.independently_verified:
                    winning_node = node
                    break

                if next_depth < self.policy.max_depth and node.provider != "none":
                    frontier.append((node.evaluation.score, next_depth, node))

            if winning_node is not None:
                break

        best_fallback = max(nodes, key=lambda n: n.evaluation.score) if nodes else None
        chosen = winning_node or best_fallback
        verified = winning_node is not None
        outcome: TreeSearchOutcome = (
            "verified_pass"
            if verified
            else ("exhausted_budget" if nodes else "halted_no_candidates")
        )
        final_output = chosen.output if chosen is not None else ""
        total_latency = round(sum(n.latency_ms for n in nodes), 3)
        episode = ExperienceRecord(
            episode_id=f"ep_{run_id}",
            task_type=decision.task_type,
            task_summary=(summary if summary is not None else task)[:200],
            provider=chosen.provider if chosen is not None else "none",
            model=chosen.model if chosen is not None else "none",
            latency_ms=total_latency,
            attempts=max(1, len(nodes)),
            verified=verified,
            test_result="passed" if verified else "failed",
            quality_score=chosen.evaluation.score if (verified and chosen is not None) else 0.0,
            accepted=None,
            output_fingerprint=fingerprint_text(final_output) if verified else "",
            cost_usd=0.0,
            billing_tier="local",
            cost_status="proven_zero_local",
        )
        return TreeSearchResult(
            run_id=run_id,
            policy_id=self.policy.policy_id,
            outcome=outcome,
            verified=verified,
            winning_node_id=winning_node.node_id if winning_node is not None else None,
            final_output=final_output,
            expansions_used=len(nodes),
            nodes=tuple(nodes),
            shared_reflections=tuple(shared_reflections),
            audit_chain=tuple(audit_chain),
            episode=episode,
        )


__all__ = [
    "DEFAULT_TREE_POLICY_ID",
    "BoundedReflectiveTreeSearch",
    "ReflectiveTreeNode",
    "TreeAuditEntry",
    "TreeSearchOutcome",
    "TreeSearchPolicy",
    "TreeSearchResult",
    "verify_tree_audit_chain",
]
