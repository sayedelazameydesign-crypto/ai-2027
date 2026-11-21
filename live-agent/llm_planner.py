"""Dependency-free adapter from a structured plan provider to the AGI Kernel.

This module deliberately contains no HTTP client, API key handling, executor,
or filesystem access. A provider can only propose JSON plans; the Kernel still
owns policy, execution, verification, evidence, and its append-only ledger.
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from core.errors import PlanningError
from core.models import Action, Goal, Plan
from core.planner import Planner, SequentialPlanner
from core.policy import DEFAULT_ALLOWLIST

READ_ONLY_CAPABILITIES = frozenset({"filesystem.read", "filesystem.list"})
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
MAX_PLANS = 3
MAX_ACTIONS_IN_RESPONSE = 64
MAX_PARAM_STRING_CHARS = 4096


class PlanProvider(Protocol):
    """Synchronous transport boundary for a provider returning structured JSON.

    Implementations must honor ``timeout_seconds``. The provider receives no
    Kernel, PolicyEngine, Executor, filesystem handle, or environment mapping.
    """

    def propose(self, request: dict[str, Any], *, timeout_seconds: float) -> str:
        """Return JSON with a ``plans`` array; do not execute any action."""
        ...


class BudgetGate(Protocol):
    """Out-of-model preflight gate; it must allow a call before the provider runs."""

    def allows(self, request: dict[str, Any]) -> bool:
        """Return True only when the provider call is within the configured budget."""
        ...


class ApprovalGate(Protocol):
    """Out-of-model approval boundary for plans that can change state."""

    def approve(self, goal: Goal, plan: Plan) -> bool:
        """Return True only after an external user-approval step has succeeded."""
        ...


ContextProvider = Callable[[Goal], Mapping[str, Any]]


class LLMPlanner(Planner):
    """Convert provider JSON into Kernel ``Plan`` objects, never execute them.

    Until a real provider and user-facing approval flow are added, this adapter
    is opt-in and the Live Agent continues to use ``SequentialPlanner`` by
    default. Budget rejection, provider failure, or invalid output fails closed
    without a fallback plan. Mutating plans require an external approval gate.
    """

    def __init__(
        self,
        provider: PlanProvider,
        *,
        budget_gate: BudgetGate,
        max_actions: int = 32,
        timeout_seconds: float = 8.0,
        allowed_capabilities: tuple[str, ...] | None = None,
        context_provider: ContextProvider | None = None,
        approval_gate: ApprovalGate | None = None,
    ) -> None:
        if (
            isinstance(max_actions, bool)
            or not isinstance(max_actions, int)
            or not 1 <= max_actions <= MAX_ACTIONS_IN_RESPONSE
        ):
            raise ValueError(f"max_actions must be between 1 and {MAX_ACTIONS_IN_RESPONSE}")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int | float)
            or not 0 < timeout_seconds <= 60
            or not math.isfinite(timeout_seconds)
        ):
            raise ValueError("timeout_seconds must be finite and between 0 and 60")
        if allowed_capabilities is None:
            capabilities = tuple(sorted(DEFAULT_ALLOWLIST))
        elif isinstance(allowed_capabilities, (str, bytes)) or not isinstance(
            allowed_capabilities, Sequence
        ):
            raise ValueError("allowed_capabilities must be a sequence of capability names")
        else:
            capabilities = tuple(allowed_capabilities)
        if not capabilities or any(not isinstance(item, str) or not item for item in capabilities):
            raise ValueError("allowed_capabilities must contain non-empty strings")
        self._provider = provider
        self._budget_gate = budget_gate
        self._max_actions = max_actions
        self._timeout_seconds = timeout_seconds
        self._allowed_capabilities = tuple(sorted(set(capabilities)))
        self._context_provider = context_provider or (lambda _goal: {})
        self._approval_gate = approval_gate

    def propose(self, goal: Goal) -> list[Plan]:
        """Ask for bounded proposals; failures stop before any plan is executed."""
        request = self._build_request(goal)
        try:
            permitted = self._budget_gate.allows(request)
        except Exception as exc:
            raise PlanningError(
                "LLM provider budget check failed "
                f"({type(exc).__name__}); no provider call or plan execution occurred"
            ) from exc
        if permitted is not True:
            raise PlanningError(
                "LLM provider call denied by budget gate; "
                "no plan was proposed or executed"
            )

        try:
            raw_response = self._provider.propose(
                request, timeout_seconds=self._timeout_seconds
            )
        except Exception as exc:
            raise PlanningError(
                f"LLM provider request failed ({type(exc).__name__}); no plan was executed"
            ) from exc

        try:
            plans = self._parse_response(raw_response, goal)
        except Exception as exc:
            raise PlanningError(
                "LLM provider returned an invalid plan response "
                f"({type(exc).__name__}); no plan was executed"
            ) from exc

        approved: list[Plan] = []
        for plan in plans:
            if self._requires_approval(plan):
                if self._approval_gate is None:
                    continue
                try:
                    if self._approval_gate.approve(goal, plan) is not True:
                        continue
                except Exception:
                    continue
            approved.append(plan)

        if not approved:
            raise PlanningError(
                "no provider plan passed the explicit external approval gate; "
                "no action was executed"
            )
        return approved

    def _build_request(self, goal: Goal) -> dict[str, Any]:
        """Build a minimal JSON-safe request with task context and hard constraints."""
        try:
            context = self._context_provider(goal)
            if not isinstance(context, Mapping):
                raise TypeError("planning context must be a mapping")
            request = {
                "goal": goal.to_dict(),
                "context": dict(context),
                "constraints": {
                    "allowed_capabilities": list(self._allowed_capabilities),
                    "read_only_capabilities": sorted(READ_ONLY_CAPABILITIES),
                    "max_actions_per_plan": self._max_actions,
                    "max_plans": MAX_PLANS,
                    "relative_paths_only": True,
                    "mutating_actions_require_approval": True,
                },
            }
            encoded = json.dumps(
                request, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ).encode("utf-8")
        except (TypeError, ValueError, RecursionError) as exc:
            raise PlanningError("planning request or context is not valid JSON") from exc
        if len(encoded) > MAX_REQUEST_BYTES:
            raise PlanningError(f"planning request exceeds {MAX_REQUEST_BYTES} bytes")
        return json.loads(encoded.decode("utf-8"))

    def _parse_response(self, raw_response: str, goal: Goal) -> list[Plan]:
        """Strictly parse bounded JSON; ignore model-supplied IDs, risk, and cost."""
        if not isinstance(raw_response, str):
            raise ValueError("provider response must be text")
        try:
            encoded = raw_response.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("provider response is not valid UTF-8") from exc
        if len(encoded) > MAX_RESPONSE_BYTES:
            raise ValueError(f"provider response exceeds {MAX_RESPONSE_BYTES} bytes")
        parsed = json.loads(
            raw_response,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
        if not isinstance(parsed, dict) or set(parsed) != {"plans"}:
            raise ValueError("provider response must contain only a plans array")
        candidates = parsed["plans"]
        if not isinstance(candidates, list) or not 1 <= len(candidates) <= MAX_PLANS:
            raise ValueError(f"provider must return 1 to {MAX_PLANS} plans")

        plans: list[Plan] = []
        for rank, candidate in enumerate(candidates):
            if not isinstance(candidate, dict) or set(candidate) - {"rationale", "actions"}:
                raise ValueError("plan contains unknown fields")
            rationale = candidate.get("rationale", "provider-proposed plan")
            if not isinstance(rationale, str) or len(rationale) > 2048:
                raise ValueError("plan rationale must be text of at most 2048 characters")
            raw_actions = candidate.get("actions")
            if (
                not isinstance(raw_actions, list)
                or not raw_actions
                or len(raw_actions) > self._max_actions
            ):
                raise ValueError(
                    f"plan actions must contain between 1 and {self._max_actions} items"
                )

            actions: list[Action] = []
            for item in raw_actions:
                if (
                    not isinstance(item, dict)
                    or set(item) - {"capability", "params", "rationale"}
                    or not {"capability", "params"}.issubset(item)
                ):
                    raise ValueError("action has an invalid shape")
                capability = item["capability"]
                params = item["params"]
                action_rationale = item.get("rationale", "provider-proposed action")
                if not isinstance(capability, str) or not capability or len(capability) > 128:
                    raise ValueError("action capability must be a non-empty short string")
                if capability not in self._allowed_capabilities:
                    raise ValueError("action capability is outside the configured allowlist")
                if not isinstance(params, dict) or not _valid_params(params):
                    raise ValueError("action params must contain bounded scalar values")
                if not isinstance(action_rationale, str) or len(action_rationale) > 1024:
                    raise ValueError("action rationale must be text of at most 1024 characters")
                # Risk is a local hint only. Kernel PolicyEngine recomputes the
                # authoritative risk and allows/denies every action independently.
                risk = SequentialPlanner._predict_risk(capability, params)
                actions.append(
                    Action(
                        capability=capability,
                        params=dict(params),
                        rationale=action_rationale,
                        predicted_risk=risk,
                    )
                )
            estimated_cost = round(
                len(actions) + rank * 0.1 + sum(action.predicted_risk for action in actions),
                4,
            )
            plans.append(
                Plan(
                    goal_id=goal.goal_id,
                    actions=actions,
                    rationale=rationale,
                    estimated_cost=estimated_cost,
                )
            )
        return plans

    @staticmethod
    def _requires_approval(plan: Plan) -> bool:
        """Fail closed: every capability other than read/list needs approval."""
        return any(action.capability not in READ_ONLY_CAPABILITIES for action in plan.actions)


def _valid_params(params: dict[str, Any]) -> bool:
    for key, value in params.items():
        if not isinstance(key, str) or len(key) > 64:
            return False
        if isinstance(value, str):
            if len(value) > MAX_PARAM_STRING_CHARS:
                return False
        elif isinstance(value, float):
            if not math.isfinite(value):
                return False
        elif not isinstance(value, (int, bool)) and value is not None:
            return False
    return True


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


__all__ = [
    "ApprovalGate",
    "BudgetGate",
    "LLMPlanner",
    "PlanProvider",
    "READ_ONLY_CAPABILITIES",
]
