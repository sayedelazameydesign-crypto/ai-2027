"""Retry policy with bounded, jittered exponential backoff.

Promoted from the runner's inline retry arithmetic. The *policy* is shared; the
clock and the sleeper are injected, because a library that sleeps on its own
cannot be tested honestly.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from .contracts import KernelError, clamp

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    """How many attempts, how long between them, and what counts as retryable."""

    max_attempts: int = 3
    base_delay_s: float = 0.05
    max_delay_s: float = 5.0
    jitter: float = 0.5

    def __post_init__(self) -> None:
        """Refuse a policy that cannot make sense."""
        if self.max_attempts < 1:
            raise KernelError("RetryPolicy.max_attempts must be >= 1")
        if self.base_delay_s < 0 or self.max_delay_s < 0:
            raise KernelError("delays must be >= 0")
        if not 0.0 <= self.jitter <= 1.0:
            raise KernelError("jitter must be within 0..1")


def backoff_delay(
    attempt: int,
    policy: RetryPolicy,
    *,
    rng: Callable[[], float] = random.random,
) -> float:
    """Seconds to wait after failed ``attempt`` (1-based), jittered and capped.

    Full jitter over ``[d*(1-jitter), d]`` keeps a fleet of workers from
    retrying in lockstep after a shared outage — the reason the per-engine
    copies that used a fixed sleep caused thundering herds.
    """
    if attempt < 1:
        raise KernelError("attempt is 1-based")
    raw = policy.base_delay_s * (2 ** (attempt - 1))
    capped = min(raw, policy.max_delay_s)
    low = capped * (1.0 - policy.jitter)
    return clamp(low + (capped - low) * rng(), 0.0, policy.max_delay_s)


def with_retry(
    operation: Callable[[], T],
    *,
    policy: RetryPolicy | None = None,
    is_retryable: Callable[[BaseException], bool] = lambda exc: True,
    on_attempt_failed: Callable[[int, BaseException], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    rng: Callable[[], float] = random.random,
) -> T:
    """Run ``operation`` until it succeeds, the policy runs out, or it is fatal.

    Returns the value on success and re-raises the **last** exception on
    exhaustion — never a bare ``RuntimeError("retries exhausted")`` that hides
    the real cause, which is the usual bug in hand-rolled retry loops.
    """
    active = policy or RetryPolicy()
    last: BaseException | None = None
    for attempt in range(1, active.max_attempts + 1):
        try:
            return operation()
        except BaseException as exc:
            last = exc
            if on_attempt_failed is not None:
                on_attempt_failed(attempt, exc)
            if not is_retryable(exc) or attempt >= active.max_attempts:
                raise
            sleep(backoff_delay(attempt, active, rng=rng))
    if last is not None:  # pragma: no cover - loop always returns or raises
        raise last
    raise KernelError("with_retry reached an impossible state")  # pragma: no cover


def attempt_ledger(
    attempts: list[dict[str, Any]],
) -> dict[str, int]:
    """Summarise a ledger of attempts: how many, how many failed."""
    failed = sum(1 for row in attempts if not row.get("ok"))
    return {"total": len(attempts), "failed": failed, "succeeded": len(attempts) - failed}
