"""Circuit breaker — time-based, so a restart cannot inherit a stale "open".

This exact reasoning already existed in ``fleet2027.registry``; promoting it
means the other engines stop growing their own subtly-different versions.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TypeVar

from .contracts import KernelError

T = TypeVar("T")


class CircuitOpenError(KernelError):
    """Raised when a call is refused because the breaker is open."""


class BreakerState(str, Enum):
    """The three states, named explicitly so logs are unambiguous."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Per-target breaker: stop hammering something that is clearly down.

    Time-based rather than counter-reset on restart: a process that dies mid-outage
    must not come back believing the dependency is healthy.
    """

    failure_threshold: int = 3
    cooldown_s: float = 30.0
    failures: int = 0
    opened_at: float | None = None
    successes: int = 0
    clock: Callable[[], float] = time.monotonic
    _now: float | None = None

    def __post_init__(self) -> None:
        """Refuse a breaker that can never open or never close."""
        if self.failure_threshold < 1:
            raise KernelError("failure_threshold must be >= 1")
        if self.cooldown_s < 0:
            raise KernelError("cooldown_s must be >= 0")

    def now(self, explicit: float | None = None) -> float:
        """Current instant: an explicit value wins, else the last one, else the clock.

        Remembering the last explicit instant keeps a caller-driven breaker (the
        fleet runner passes its own ``now``) self-consistent: a state query right
        after ``record_failure(now=100.0)`` must not consult the wall clock.
        """
        if explicit is not None:
            self._now = explicit
            return explicit
        return self._now if self._now is not None else self.clock()

    def state_at(self, now: float | None = None) -> BreakerState:
        """State at ``now`` (or the last known instant), so queries are explicit."""
        if self.opened_at is None:
            return BreakerState.CLOSED
        if (self.now(now) - self.opened_at) >= self.cooldown_s:
            return BreakerState.HALF_OPEN
        return BreakerState.OPEN

    @property
    def breaker_state(self) -> BreakerState:
        """Current state as an enum, derived from the clock so it cannot go stale."""
        return self.state_at()

    @property
    def state(self) -> str:
        """Current state as a plain string — the shape dashboards and logs print."""
        return self.breaker_state.value

    def is_available(self, now: float | None = None) -> bool:
        """Whether a call may be attempted now (half-open allows a trial).

        ``now`` lets a caller pass its own monotonic timestamp — the pattern the
        fleet runner already used — instead of forcing a per-instance clock.
        """
        return self.state_at(now) is not BreakerState.OPEN

    def record_success(self) -> None:
        """Close the breaker: one proven success is enough."""
        self.failures = 0
        self.opened_at = None
        self.successes += 1

    def record_failure(self, now: float | None = None) -> None:
        """Count a failure and open the breaker at the threshold."""
        self.failures += 1
        if self.failures >= self.failure_threshold and self.opened_at is None:
            self.opened_at = self.now(now)

    def call(self, operation: Callable[[], T]) -> T:
        """Run ``operation`` through the breaker, refusing while open."""
        if not self.is_available():
            raise CircuitOpenError(
                f"circuit open: {self.failures} consecutive failures, "
                f"cooling down for {self.cooldown_s:.0f}s"
            )
        try:
            result = operation()
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result


@dataclass
class BreakerPool:
    """One breaker per named target — the shape most engines actually need."""

    failure_threshold: int = 3
    cooldown_s: float = 30.0
    _breakers: dict[str, CircuitBreaker] = field(default_factory=dict)

    def for_target(self, name: str) -> CircuitBreaker:
        """Return the breaker for ``name``, creating it on first use."""
        breaker = self._breakers.get(name)
        if breaker is None:
            breaker = CircuitBreaker(
                failure_threshold=self.failure_threshold, cooldown_s=self.cooldown_s
            )
            self._breakers[name] = breaker
        return breaker

    def snapshot(self) -> dict[str, dict[str, object]]:
        """Observable state of every breaker, for logs and health endpoints."""
        return {
            name: {
                "state": breaker.state,
                "failures": breaker.failures,
                "successes": breaker.successes,
            }
            for name, breaker in sorted(self._breakers.items())
        }
