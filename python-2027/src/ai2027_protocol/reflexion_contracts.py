"""Contracts, sliding-window memory, AST test filter, and evaluators for Reflexion.

Implements the data models, replaceable protocols, bounded episodic buffer
(``Omega``), §4.1 trajectory pathology detector, §4.3 AST self-test filter, and
SHA-256 trial audit chain for Shinn et al. (arXiv:2303.11366v4) under AI-2027
governance boundaries.
"""

from __future__ import annotations

import ast
import hashlib
import json
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from pydantic import Field

from .contracts import StrictModel
from .experience_store import ExperienceRecord
from .meta_router import VerificationResult

#: Canonical source paper identifier for audit and Research Radar traceability.
REFLEXION_ARXIV_ID: str = "2303.11366v4"

#: Governance policy trace identifier matching Memory P0 audit logs.
DEFAULT_REFLEXION_POLICY_ID: str = "POL-BOUNDED-REFLEXION-CAP"

#: Correction choices defined by ``prompts/agent/REFLECTION.md``.
CorrectionKind = Literal["re-run", "fix", "replan", "stop"]

#: Confidence tiers defined by ``prompts/agent/REFLECTION.md``.
ConfidenceTier = Literal["low", "medium", "high"]

#: Terminal outcome of a bounded Reflexion run.
ReflexionOutcome = Literal[
    "verified",
    "halted_max_retries",
    "halted_stagnation",
    "halted_stop",
]

#: Modules disallowed inside AST-filtered self-generated unit tests (§4.3 & §5).
_FORBIDDEN_TEST_MODULES: frozenset[str] = frozenset(
    {
        "ctypes",
        "http",
        "importlib",
        "multiprocessing",
        "os",
        "pathlib",
        "pty",
        "shutil",
        "signal",
        "socket",
        "subprocess",
        "sys",
        "tempfile",
        "threading",
        "urllib",
        "webbrowser",
    }
)

#: Builtin call names disallowed inside AST-filtered self-generated unit tests.
_FORBIDDEN_TEST_CALLS: frozenset[str] = frozenset(
    {"__import__", "breakpoint", "compile", "eval", "exec", "input", "open"}
)


class ReflexionPolicy(StrictModel):
    """Hard execution bounds for a governed Reflexion loop."""

    policy_id: str = Field(default=DEFAULT_REFLEXION_POLICY_ID, min_length=1, max_length=64)
    #: Maximum trials ``T_max`` (trial 0 plus at most ``max_trials - 1`` retries).
    max_trials: int = Field(default=3, ge=1, le=12)
    #: Sliding-window capacity ``Omega`` for episodic verbal cues (1..5; §3).
    memory_window: int = Field(default=3, ge=1, le=5)
    #: Consecutive identical ``(action, observation)`` steps signalling a loop (§4.1).
    max_consecutive_identical_actions: int = Field(default=3, ge=2, le=10)
    #: Trajectory step ceiling flagging inefficient planning (§4.1).
    max_trajectory_steps: int = Field(default=30, ge=1, le=100)
    #: Maximum AST-validated unit tests retained per programming check (§4.3).
    max_self_tests: int = Field(default=6, ge=1, le=20)
    #: Require independent verification even when a heuristic/self-evaluator passes.
    require_independent_verification: bool = True


class TrajectoryStep(StrictModel):
    """One short-term action-observation step inside a single trial trajectory."""

    step_index: int = Field(ge=0)
    action: str = Field(min_length=1, max_length=2_000)
    observation: str = Field(default="", max_length=4_000)


class TrialTrajectory(StrictModel):
    """Short-term memory trace ``tau_t`` produced by the Actor in trial ``t``."""

    trial: int = Field(ge=0)
    prompt_sha256: str = Field(min_length=64, max_length=64)
    output: str = Field(default="", max_length=32_000)
    steps: tuple[TrajectoryStep, ...] = ()
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=160)
    latency_ms: float = Field(default=0.0, ge=0.0)
    attempts: int = Field(default=1, ge=1)
    router_failed: bool = False
    cost_usd: float = Field(default=0.0, ge=0.0)
    billing_tier: str = Field(default="local", min_length=1, max_length=32)
    cost_status: str = Field(default="proven_zero_local", min_length=1, max_length=48)


class EvaluationSignal(StrictModel):
    """Evaluator verdict ``r_t = M_e(tau_t)`` for a single trial trajectory."""

    passed: bool
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = Field(default="", max_length=1_000)
    #: True only when produced by an independent, non-self-generated check.
    independent: bool = False
    #: Non-empty when a §4.1 trajectory pathology was detected.
    pathology: str = Field(default="", max_length=64)


class VerbalReflection(StrictModel):
    """Structured verbal reinforcement cue ``sr_t`` matching ``REFLECTION.md``."""

    trial: int = Field(ge=0)
    claim: str = Field(min_length=1, max_length=500)
    observation: str = Field(min_length=1, max_length=1_000)
    gap: str = Field(min_length=1, max_length=1_000)
    next_action: str = Field(min_length=1, max_length=1_000)
    correction: CorrectionKind = "fix"
    confidence: ConfidenceTier = "low"

    def normalized_signature(self) -> str:
        """Return a canonical signature used to detect repeated local-minima cues."""
        collapsed_gap = " ".join(self.gap.lower().split())
        collapsed_next = " ".join(self.next_action.lower().split())
        return f"{self.correction}|{collapsed_gap}|{collapsed_next}"

    def render_cue(self) -> str:
        """Format this reflection as a concise first-person lesson for the Actor."""
        return (
            f"[Trial {self.trial} | {self.correction}] "
            f"Observation: {self.observation} | "
            f"Gap: {self.gap} | "
            f"Next action: {self.next_action}"
        )


class ReflexionAuditEntry(StrictModel):
    """Hash-chained audit record binding one Reflexion trial and its verdict."""

    trial: int = Field(ge=0)
    policy_trace_id: str = Field(min_length=1, max_length=64)
    outcome: str = Field(min_length=1, max_length=48)
    output_sha256: str = Field(min_length=64, max_length=64)
    reflection_sha256: str = Field(default="", max_length=64)
    prev_hash: str = Field(default="", max_length=64)
    self_hash: str = Field(min_length=64, max_length=64)


class ReflexionRunResult(StrictModel):
    """Complete, auditable outcome of a bounded Reflexion execution."""

    outcome: ReflexionOutcome
    policy_trace_id: str = Field(min_length=1, max_length=64)
    paper_ref: str = Field(default=REFLEXION_ARXIV_ID, min_length=1, max_length=32)
    trials_used: int = Field(ge=1)
    memory_window: int = Field(ge=1, le=5)
    evicted_reflections: int = Field(default=0, ge=0)
    false_positives_caught: int = Field(default=0, ge=0)
    trajectories: tuple[TrialTrajectory, ...]
    evaluations: tuple[EvaluationSignal, ...]
    reflections: tuple[VerbalReflection, ...] = ()
    active_memory: tuple[VerbalReflection, ...] = ()
    audit_chain: tuple[ReflexionAuditEntry, ...]
    episode: ExperienceRecord


@runtime_checkable
class EvaluatorProtocol(Protocol):
    """Replaceable contract for the Reflexion Evaluator ``M_e``."""

    def evaluate(self, task: str, trajectory: TrialTrajectory) -> EvaluationSignal:
        """Score ``trajectory`` against ``task`` and return a structured verdict."""


@runtime_checkable
class SelfReflectorProtocol(Protocol):
    """Replaceable contract for the Reflexion Self-Reflection generator ``M_sr``."""

    async def reflect(
        self,
        task: str,
        trajectory: TrialTrajectory,
        evaluation: EvaluationSignal,
        memory: tuple[VerbalReflection, ...],
    ) -> VerbalReflection:
        """Amplify a failed trial and evaluation signal into a verbal cue."""


def canonical_audit_sha256(payload: dict[str, object]) -> str:
    """Return deterministic UTF-8 JSON SHA-256 hex digest for audit chaining."""
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def detect_trajectory_pathology(
    steps: Sequence[TrajectoryStep],
    policy: ReflexionPolicy,
) -> str:
    """Detect §4.1 hallucination loops or inefficient-planning step overflows."""
    if len(steps) > policy.max_trajectory_steps:
        return "step_cap_exceeded"
    streak = 1
    for prev, curr in zip(steps, steps[1:], strict=False):
        if prev.action.strip() == curr.action.strip() and (
            prev.observation.strip() == curr.observation.strip()
        ):
            streak += 1
            if streak >= policy.max_consecutive_identical_actions:
                return "repeated_action_cycle"
        else:
            streak = 1
    return ""


def filter_valid_python_tests(
    candidates: Sequence[str],
    *,
    max_tests: int = 6,
) -> tuple[str, ...]:
    """Filter candidate Python unit tests via ``ast.parse`` (§4.3, n <= max_tests)."""
    if max_tests < 1:
        raise ValueError("max_tests must be >= 1")
    accepted: list[str] = []
    seen_ast: set[str] = set()
    for raw in candidates:
        snippet = raw.strip()
        if not snippet:
            continue
        try:
            tree = ast.parse(snippet, mode="exec")
        except SyntaxError:
            continue
        if not tree.body or not _is_safe_test_ast(tree):
            continue
        ast_key = ast.dump(tree, annotate_fields=True, include_attributes=False)
        if ast_key in seen_ast:
            continue
        seen_ast.add(ast_key)
        accepted.append(snippet)
        if len(accepted) >= max_tests:
            break
    return tuple(accepted)


def _is_safe_test_ast(tree: ast.Module) -> bool:
    """Return True when ``tree`` contains assertions/tests and no unsafe calls."""
    has_assertion = False
    for stmt in tree.body:
        if isinstance(stmt, ast.Assert):
            has_assertion = True
        elif isinstance(stmt, ast.FunctionDef) and stmt.name.startswith("test_"):
            if any(isinstance(sub, ast.Assert) for sub in ast.walk(stmt)):
                has_assertion = True
        else:
            return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] in _FORBIDDEN_TEST_MODULES for alias in node.names):
                return False
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in _FORBIDDEN_TEST_MODULES:
                return False
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_TEST_CALLS:
                return False
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                if node.func.value.id in _FORBIDDEN_TEST_MODULES:
                    return False
    return has_assertion


class ReflexionMemoryBuffer:
    """Bounded sliding-window episodic memory ``mem`` of capacity ``Omega`` (§3)."""

    def __init__(self, window_size: int = 3) -> None:
        """Initialize an empty sliding window of capacity ``1 <= window_size <= 5``."""
        if not 1 <= window_size <= 5:
            raise ValueError(f"window_size must be within 1..5, got {window_size}")
        self.window_size = window_size
        self._items: deque[VerbalReflection] = deque()
        self.evicted_count = 0

    def snapshot(self) -> tuple[VerbalReflection, ...]:
        """Return the active sliding-window reflections in chronological order."""
        return tuple(self._items)

    def append(self, reflection: VerbalReflection) -> bool:
        """Append ``reflection``, evicting oldest if needed; return True if stagnated."""
        stagnated = bool(
            self._items
            and self._items[-1].normalized_signature() == reflection.normalized_signature()
        )
        if len(self._items) >= self.window_size:
            self._items.popleft()
            self.evicted_count += 1
        self._items.append(reflection)
        return stagnated

    def format_for_prompt(self) -> str:
        """Render the bounded episodic cues for conditioning the next Actor trial."""
        if not self._items:
            return ""
        lines = ["Prior self-reflections (apply these lessons, do not repeat prior mistakes):"]
        for item in self._items:
            lines.append(f"- {item.render_cue()}")
        return "\n".join(lines)


@dataclass(frozen=True)
class VerifierEvaluator:
    """Independent Evaluator ``M_e`` backed by a deterministic check + §4.1 guards."""

    verifier: Callable[[str], VerificationResult]
    policy: ReflexionPolicy = ReflexionPolicy()
    independent: bool = True

    def evaluate(self, task: str, trajectory: TrialTrajectory) -> EvaluationSignal:
        """Evaluate ``trajectory`` for router failure, pathology, and verifier pass."""
        del task
        if trajectory.router_failed:
            return EvaluationSignal(
                passed=False,
                score=0.0,
                reason="all free providers failed during Actor generation",
                independent=self.independent,
                pathology="router_failure",
            )
        pathology = detect_trajectory_pathology(trajectory.steps, self.policy)
        if pathology:
            return EvaluationSignal(
                passed=False,
                score=0.0,
                reason=f"trajectory pathology detected: {pathology}",
                independent=self.independent,
                pathology=pathology,
            )
        verdict = self.verifier(trajectory.output)
        return EvaluationSignal(
            passed=verdict.passed,
            score=1.0 if verdict.passed else 0.0,
            reason=verdict.detail or ("verified" if verdict.passed else "verification failed"),
            independent=self.independent,
        )


@dataclass(frozen=True)
class ExactMatchEvaluator:
    """Exact-match Evaluator ``M_e`` for closed-form reasoning answers (§4.2)."""

    expected_answer: str
    independent: bool = True

    def evaluate(self, task: str, trajectory: TrialTrajectory) -> EvaluationSignal:
        """Return passed=True iff normalized ``trajectory.output`` matches expected."""
        del task
        got = " ".join(trajectory.output.strip().lower().split())
        want = " ".join(self.expected_answer.strip().lower().split())
        matched = bool(want) and got == want
        return EvaluationSignal(
            passed=matched,
            score=1.0 if matched else 0.0,
            reason="exact match" if matched else "output did not match expected answer",
            independent=self.independent,
        )


def verify_reflexion_chain(entries: Sequence[ReflexionAuditEntry]) -> list[str]:
    """Verify ``prev_hash -> self_hash`` linkage and canonical digests across trials."""
    if not entries:
        return ["reflexion audit chain is empty"]
    problems: list[str] = []
    prev_hash = ""
    for idx, entry in enumerate(entries):
        if entry.trial != idx:
            problems.append(f"entry {idx}: expected trial={idx}, got {entry.trial}")
        if entry.prev_hash != prev_hash:
            problems.append(f"entry {idx}: prev_hash mismatch")
        expected = canonical_audit_sha256(
            {
                "outcome": entry.outcome,
                "output_sha256": entry.output_sha256,
                "policy_trace_id": entry.policy_trace_id,
                "prev_hash": entry.prev_hash,
                "reflection_sha256": entry.reflection_sha256,
                "trial": entry.trial,
            }
        )
        if entry.self_hash != expected:
            problems.append(f"entry {idx}: self_hash mismatch")
        prev_hash = entry.self_hash
    return problems
