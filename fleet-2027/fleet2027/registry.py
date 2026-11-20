"""Agent registry, capability routing, circuit breaking, and bulkhead limits.

This is the *adapter* layer of the architecture: agent kinds are replaceable,
while the task lifecycle, contracts, and verification stay in the core. An
agent is a :class:`AgentSpec` (declared capability) plus a handler callable;
nothing else about it is special, which is what lets a real LLM-backed agent
and a deterministic test agent be swapped without touching the runner.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .contracts import AgentSpec, ContractError, CostTier, StepResult
# The breaker comes from the shared kernel so a fix there reaches every engine.
# Imported under its original name: no call site changes.
from kernel2027.circuit import CircuitBreaker  # noqa: E402 - kernel is stdlib-only


class Bulkhead:
    """A bounded lease pool per agent so one slow agent cannot starve the fleet.

    Implemented with explicit tokens rather than a semaphore so a handler that
    overruns its timeout cannot leak capacity permanently: the runner gives the
    token back in a ``finally`` block, and the pool is sized from the spec.
    """

    def __init__(self, limit: int) -> None:
        """Create a bulkhead permitting ``limit`` concurrent calls."""
        if limit < 1:
            raise ContractError("Bulkhead limit must be >= 1")
        self.limit = limit
        self._cond = threading.Condition()
        self._in_flight = 0

    def acquire(self, timeout: float | None = None) -> bool:
        """Try to enter the bulkhead, waiting at most ``timeout`` seconds."""
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        with self._cond:
            while self._in_flight >= self.limit:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._cond.wait(remaining)
            self._in_flight += 1
            return True

    def release(self) -> None:
        """Leave the bulkhead."""
        with self._cond:
            if self._in_flight > 0:
                self._in_flight -= 1
            self._cond.notify()

    @property
    def in_flight(self) -> int:
        """Current occupancy — used by the observability report."""
        with self._cond:
            return self._in_flight

@dataclass(frozen=True)
class HandlerContext:
    """Everything a handler is allowed to know about the step it is running."""

    task_id: str
    step_id: str
    action: str
    goal: str
    agent: AgentSpec
    attempt: int
    workspace_root: Path
    idempotency_key: str
    inputs: Mapping[str, Any] = field(default_factory=dict)
    store: Any = None
    deadline_ts: float | None = None
    scratch_dir: Path | None = None


@runtime_checkable
class StepHandler(Protocol):
    """Contract every agent handler satisfies."""

    def __call__(self, ctx: HandlerContext) -> StepResult:
        """Execute one step and return a claimed result (verified separately)."""
        ...


Handler = Callable[[HandlerContext], StepResult]


class AgentRegistrationError(ContractError):
    """Raised when an agent would make routing ambiguous or is malformed."""


@dataclass
class AgentEntry:
    """One registered agent: its declaration, handler and runtime state."""

    spec: AgentSpec
    handler: Handler
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    bulkhead: Bulkhead | None = None

    def __post_init__(self) -> None:
        """Create the bulkhead once the spec is known."""
        if self.bulkhead is None:
            self.bulkhead = Bulkhead(self.spec.max_parallel)


class AgentRegistry:
    """Holds agent declarations and resolves a step to a runnable handler.

    Resolution rules (in order):

    1. exact agent name,
    2. declared capability tag,
    3. declared fallback chain of the resolved agent,
    4. otherwise: :class:`AgentRegistrationError` — a step never silently runs
       on an agent that was not declared for it.
    """

    def __init__(self, *, default_breaker: CircuitBreaker | None = None) -> None:
        """Create an empty registry."""
        self._agents: dict[str, AgentEntry] = {}
        self._capabilities: dict[str, str] = {}
        self._default_breaker = default_breaker or CircuitBreaker()

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #

    def register(
        self,
        spec: AgentSpec,
        handler: Handler,
        *,
        capabilities: Sequence[str] | None = None,
    ) -> AgentSpec:
        """Register one agent; capabilities must be unique across the fleet."""
        if spec.name in self._agents:
            raise AgentRegistrationError(f"agent {spec.name!r} is already registered")
        tags = tuple(capabilities if capabilities is not None else spec.capabilities)
        for tag in tags:
            owner = self._capabilities.get(tag)
            if owner is not None:
                raise AgentRegistrationError(
                    f"capability {tag!r} already provided by {owner!r}; "
                    "ambiguous routing is refused"
                )
        for tag in tags:
            self._capabilities[tag] = spec.name
        breaker = CircuitBreaker(
            failure_threshold=self._default_breaker.failure_threshold,
            cooldown_s=self._default_breaker.cooldown_s,
        )
        entry = AgentEntry(spec=spec, handler=handler, breaker=breaker)
        entry.spec = AgentSpec.from_dict(
            {**spec.to_dict(), "capabilities": list(tags)}
        )
        self._agents[spec.name] = entry
        return entry.spec

    def agent(self, spec: AgentSpec) -> Callable[[Handler], Handler]:
        """Decorator form: ``@registry.agent(AgentSpec(...))``."""

        def wrap(fn: Handler) -> Handler:
            self.register(spec, fn)
            return fn

        return wrap

    # ------------------------------------------------------------------ #
    # Resolution
    # ------------------------------------------------------------------ #

    @property
    def names(self) -> tuple[str, ...]:
        """Registered agent names in insertion order."""
        return tuple(self._agents)

    @property
    def capabilities(self) -> Mapping[str, str]:
        """Read-only capability → agent map."""
        return dict(self._capabilities)

    def spec(self, name: str) -> AgentSpec:
        """Return the declaration of ``name``."""
        return self._entry(name).spec

    def specs(self) -> tuple[AgentSpec, ...]:
        """Return all declarations."""
        return tuple(entry.spec for entry in self._agents.values())

    def _entry(self, name: str) -> AgentEntry:
        entry = self._agents.get(name)
        if entry is None:
            known = ", ".join(sorted(self._agents)) or "none"
            raise AgentRegistrationError(f"unknown agent {name!r} (registered: {known})")
        return entry

    def handler_for(self, name_or_capability: str) -> AgentEntry:
        """Resolve an agent name or capability tag to its entry."""
        if name_or_capability in self._agents:
            return self._agents[name_or_capability]
        owner = self._capabilities.get(name_or_capability)
        if owner is not None:
            return self._agents[owner]
        known = ", ".join(sorted(self._agents)) or "none"
        raise AgentRegistrationError(
            f"no agent provides {name_or_capability!r} (registered: {known})"
        )

    def candidates(self, name_or_capability: str, *, now: float | None = None) -> list[AgentEntry]:
        """Return the ordered, currently-available candidate chain for a step.

        The primary agent comes first; declared fallbacks follow. Agents whose
        circuit breaker is open are omitted — that is graceful degradation, and
        the runner records the degradation instead of hiding it.
        """
        now = time.monotonic() if now is None else now
        primary = self.handler_for(name_or_capability)
        chain: list[AgentEntry] = []
        seen: set[str] = set()
        for entry in (primary, *(self._agents.get(f) for f in primary.spec.fallbacks)):
            if entry is None or entry.spec.name in seen:
                continue
            seen.add(entry.spec.name)
            if entry.breaker.is_available(now):
                chain.append(entry)
        return chain

    def report(self, *, now: float | None = None) -> dict[str, Any]:
        """Return an observability snapshot of the whole fleet."""
        now = time.monotonic() if now is None else now
        return {
            "agents": [
                {
                    "name": entry.spec.name,
                    "role": entry.spec.role,
                    "capabilities": list(entry.spec.capabilities),
                    "cost_tier": entry.spec.cost_tier.value,
                    "breaker": entry.breaker.state,
                    "consecutive_failures": entry.breaker.failures,
                    "available": entry.breaker.is_available(now),
                }
                for entry in self._agents.values()
            ],
            "capabilities": dict(self._capabilities),
            "cost_tiers": self._tier_counts(),
        }

    def _tier_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self._agents.values():
            tier = entry.spec.cost_tier
            counts[tier.value] = counts.get(tier.value, 0) + 1
        return counts


# --------------------------------------------------------------------------- #
# Reviewer agents
# --------------------------------------------------------------------------- #


def build_reviewer(
    registry: AgentRegistry,
    *,
    reviewer_agent: str,
    produce_name: str | None = None,
) -> Callable[[Any, StepResult], tuple[bool, str]]:
    """Create an independent-review callback backed by a *different* agent.

    The reviewer is invoked as a real step handler so it can, for example, call
    a stronger model. If the reviewer is the same agent that produced the work,
    the callback refuses — a self-review is not independent by definition.
    """
    entry = registry.handler_for(reviewer_agent)

    def review(step: Any, result: StepResult) -> tuple[bool, str]:
        producer = produce_name or step.agent
        if entry.spec.name == producer:
            return False, "reviewer is the producing agent — not independent"
        ctx = HandlerContext(
            task_id="review",
            step_id=step.step_id,
            action=step.action,
            goal="independent review",
            agent=entry.spec,
            attempt=1,
            workspace_root=Path("."),
            idempotency_key="review",
            inputs={"review_target": result.summary, "step_id": step.step_id},
            store=None,
        )
        try:
            verdict = entry.handler(ctx)
        except Exception as exc:  # noqa: BLE001 - a reviewer crash is a failed check
            return False, f"reviewer crashed: {type(exc).__name__}: {exc}"
        note = str(verdict.evidence.get("verdict_note", verdict.summary))[:300]
        return verdict.ok, note

    return review


def cost_guard(agents: Sequence[AgentSpec]) -> dict[str, Any]:
    """Refuse a fleet whose declared cost tiers are all paid.

    Mirrors the repo's Free-First doctrine: a fleet must be runnable at zero
    spend, so at least one agent has to be local/free-tier.
    """
    tiers = {agent.cost_tier for agent in agents}
    free = {CostTier.LOCAL, CostTier.FREE_TIER}
    if not tiers & free:
        raise ContractError(
            "every registered agent declares a paid/unknown cost tier; "
            "the Free-First doctrine requires at least one local or free-tier agent"
        )
    return {"free_capable": True, "tiers": sorted(t.value for t in tiers)}
