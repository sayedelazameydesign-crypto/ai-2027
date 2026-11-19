"""A2 — route fleet steps through ``python-2027``'s ``FreeFirstRouter``.

The fleet already had two halves of "thinking": the router in ``python-2027``
(knows *which* free provider answers, enforces zero spend, breaks circuits,
retries with backoff) and the runner here (knows *what* must be done and
verifies it). A2 joins them, and the rule is the same as everywhere else in this
package: **the router's success is a claim, not a result.**

What this module adds, concretely:

* ``LLMRequest`` → router request, without importing pydantic. The caller
  injects two tiny factories (they live in the optional adapter), so
  ``fleet2027`` keeps its zero-dependency promise — ``fleet_guard.py`` proves it.
* A **complete failover trail** from ``CompletionResponse.attempts``: which
  provider was tried, which failed and *why*, and which one finally answered.
  That trail becomes step evidence, a persisted event, and — when verification
  passes — an audit entry.
* ``AllProvidersFailedError`` is translated into :class:`RoutingExhausted`, a
  ``StepFailure`` subclass, so an exhausted router is a *retryable business
  failure* with a reason instead of a crash with a stack trace.
* ``DeterministicRouterBackend``: the honest labelled fallback. If the router
  answers with its deterministic/simulated provider, the step is marked so no
  downstream reader can mistake simulation for a real model.

Dishonesty is refused here as everywhere: a routed answer whose text is empty,
or a "success" that claims a model which never ran, is an error — not a pass.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .contracts import ContractError
from .llm import (
    LLMBackend,
    LLMError,
    LLMRequest,
    LLMResponse,
    StepFailure,
    call_async_sync,
)

#: Reasons a single provider attempt can fail, mapped to human phrasing. Anything
#: unknown is passed through verbatim rather than hidden behind "error".
_FAILURE_HINTS: dict[str, str] = {
    "timeout": "never answered before its timeout",
    "budget_blocked": "was blocked by the zero-spend budget guard",
    "circuit_open": "was skipped while its circuit breaker cooled down",
    "not_configured": "has no credentials in this environment",
    "model_retired": "points at a retired model",
    "http_error": "returned an HTTP error",
    "upstream_error": "returned an upstream error",
}


class RoutingError(LLMError):
    """Base class for anything that goes wrong while routing a completion."""


class RoutingExhausted(StepFailure):
    """Every provider in the chain failed; the step may be retried later.

    Subclasses :class:`~fleet2027.llm.StepFailure` on purpose: an exhausted
    router is a normal, retryable business failure (all free tiers are rate
    limited sometimes), not an agent bug that should be dead-lettered at once.
    """

    def __init__(self, message: str, *, trail: tuple[dict[str, Any], ...] = ()) -> None:
        """Carry the full attempt trail with the failure."""
        super().__init__(message)
        self.trail = trail


# --------------------------------------------------------------------------- #
# The trail
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ProviderAttemptView:
    """One normalised provider attempt, independent of the caller's SDK types."""

    provider: str
    model: str
    attempt: int
    ok: bool
    latency_ms: float
    error_code: str | None = None
    detail: str | None = None
    billing_tier: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe mapping for evidence and logs."""
        return {
            "provider": self.provider,
            "model": self.model,
            "attempt": self.attempt,
            "ok": self.ok,
            "latency_ms": round(self.latency_ms, 2),
            "error_code": self.error_code,
            "detail": self.detail,
            "billing_tier": self.billing_tier,
        }

    def phrase(self) -> str:
        """One short, honest sentence a human can read in an event log."""
        if self.ok:
            return f"{self.provider}:{self.model} answered in {self.latency_ms:.0f} ms"
        hint = _FAILURE_HINTS.get(self.error_code or "", self.error_code or "failed")
        return f"{self.provider}:{self.model} {hint}"


@dataclass(frozen=True)
class RouteOutcome:
    """What one routed completion actually did — the whole story, not the tail."""

    text: str
    provider: str
    model: str
    simulated: bool
    latency_ms: float
    cost_usd: float
    trace_id: str
    attempts: tuple[ProviderAttemptView, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def failovers(self) -> tuple[ProviderAttemptView, ...]:
        """Failed attempts that happened *before* the provider which answered."""
        return tuple(attempt for attempt in self.attempts if not attempt.ok)

    @property
    def used_fallback(self) -> bool:
        """Whether the first choice failed and a later one saved the step."""
        return bool(self.failovers)

    def trail(self) -> tuple[dict[str, Any], ...]:
        """The attempt ledger, ready for evidence."""
        return tuple(attempt.to_dict() for attempt in self.attempts)

    def summary_line(self) -> str:
        """Human phrasing used in step summaries and ``step.failover`` events."""
        if not self.used_fallback:
            return f"{self.provider}:{self.model} on first attempt"
        failed = "; ".join(attempt.phrase() for attempt in self.failovers)
        return f"fell back after {failed} → {self.provider}:{self.model}"


def _as_str(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def read_attempts(attempts: Any) -> tuple[ProviderAttemptView, ...]:
    """Normalise the router's ``ProviderAttempt`` tuple without importing it.

    Duck-typed on purpose: the caller may pass the real pydantic models, or plain
    stand-ins in a test, and this must behave identically for both.
    """
    views: list[ProviderAttemptView] = []
    for item in attempts or ():
        kind = getattr(item, "provider", None)
        provider = getattr(kind, "value", kind)
        views.append(
            ProviderAttemptView(
                provider=_as_str(provider, "unknown"),
                model=_as_str(getattr(item, "model", None), "unknown"),
                attempt=int(getattr(item, "attempt", 1) or 1),
                ok=bool(getattr(item, "ok", False)),
                latency_ms=float(getattr(item, "latency_ms", 0.0) or 0.0),
                error_code=getattr(item, "error_code", None),
                detail=getattr(item, "detail", None),
                billing_tier=getattr(
                    getattr(item, "billing_tier", None), "value",
                    getattr(item, "billing_tier", None),
                ),
            )
        )
    return tuple(views)


# --------------------------------------------------------------------------- #
# The backend
# --------------------------------------------------------------------------- #
@dataclass
class RouterBackend:
    """``LLMBackend`` that delegates to a ``FreeFirstRouter``.

    Both factories are injected, so this module never imports pydantic or the
    protocol package:

    * ``request_factory(LLMRequest)`` builds the router's native request object;
    * ``describe(outcome)`` is optional and lets the adapter attach anything the
      native ``CompletionResponse`` carries beyond the fields read here.
    """

    router: Any
    request_factory: Callable[[LLMRequest], Any]
    name_hint: str = "free-first-router"
    describe: Callable[[Any], dict[str, Any]] | None = None
    calls: int = 0
    last_outcome: RouteOutcome | None = None
    trail: list[RouteOutcome] = field(default_factory=list)

    @property
    def name(self) -> str:
        """Backend identifier used in events and evidence."""
        return self.name_hint

    def is_configured(self) -> bool:
        """A router is usable when it exposes at least one configured provider."""
        providers = getattr(self.router, "providers", None)
        if providers is None:  # pragma: no cover - defensive
            return True
        try:
            return any(provider.is_configured() for provider in providers)
        except Exception:  # noqa: BLE001 - a broken provider is "not configured"
            return False

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Route one completion and return the *measured* result."""
        started = time.perf_counter()
        native = self.request_factory(request)
        try:
            response = call_async_sync(lambda: self.router.route(native))
        except Exception as exc:  # noqa: BLE001 - classified below
            trail = read_attempts(getattr(exc, "attempts", ()))
            self.calls += 1
            if trail or type(exc).__name__ == "AllProvidersFailedError":
                reasons = "; ".join(attempt.phrase() for attempt in trail) or "no provider configured"
                raise RoutingExhausted(
                    f"every provider failed ({len(trail)} attempt(s)): {reasons}",
                    trail=tuple(attempt.to_dict() for attempt in trail),
                ) from exc
            raise RoutingError(f"{type(exc).__name__}: {exc}") from exc

        latency_ms = (time.perf_counter() - started) * 1000.0
        attempts = read_attempts(getattr(response, "attempts", ()))
        text = _as_str(getattr(response, "text", None))
        provider = getattr(getattr(response, "provider", None), "value",
                           getattr(response, "provider", "unknown"))
        outcome = RouteOutcome(
            text=text,
            provider=_as_str(provider, "unknown"),
            model=_as_str(getattr(response, "model", None), "unknown"),
            simulated=bool(getattr(response, "simulated", False)),
            latency_ms=float(getattr(response, "latency_ms", 0.0) or latency_ms),
            cost_usd=float(getattr(response, "cost_usd", 0.0) or 0.0),
            trace_id=_as_str(getattr(response, "trace_id", None), ""),
            attempts=attempts,
            extra=self.describe(response) if self.describe else {},
        )
        if not text.strip():
            raise RoutingError("router returned an empty completion")
        self.calls += 1
        self.last_outcome = outcome
        self.trail.append(outcome)

        return LLMResponse(
            text=text,
            backend=f"{self.name}:{outcome.provider}",
            model=outcome.model,
            latency_ms=outcome.latency_ms,
            prompt_sha256="",
            raw_bytes=len(text.encode("utf-8")),
        )

    # -- reporting -------------------------------------------------------- #

    def routing_evidence(self) -> dict[str, Any]:
        """Evidence describing the last route, safe to persist in a step result."""
        outcome = self.last_outcome
        if outcome is None:
            return {"routed": False}
        return {
            "routed": True,
            "provider": outcome.provider,
            "model": outcome.model,
            "simulated": outcome.simulated,
            "failovers": len(outcome.failovers),
            "used_fallback": outcome.used_fallback,
            "attempts": [attempt.to_dict() for attempt in outcome.attempts],
            "trace_id": outcome.trace_id,
        }


# --------------------------------------------------------------------------- #
# The honest labelled fallback
# --------------------------------------------------------------------------- #
@dataclass
class DeterministicRouterBackend:
    """Last-resort backend that can only ever claim *simulation*.

    It exists so a fleet with nothing configured still finishes with a labelled
    answer instead of a crash — and so the label cannot be lost: every response
    carries ``simulated=True`` and ``backend="deterministic"``, and the agent
    copies that into step evidence and the persisted event.
    """

    agent_name: str = "llm-writer"
    calls: int = 0

    @property
    def name(self) -> str:
        """Fixed identifier the verifier and audit trail will show."""
        return "deterministic-simulated"

    def is_configured(self) -> bool:
        """Always available — that is the point, and the label is the price."""
        return True

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Return a clearly labelled, deterministic echo of the request."""
        self.calls += 1
        preview = " ".join(request.prompt.split())[:160]
        text = (
            "[deterministic-simulated | no model was called | cost=$0.00] "
            f"{preview}"
        )
        return LLMResponse(
            text=text,
            backend=self.name,
            model="deterministic-echo-v1",
            latency_ms=0.0,
            raw_bytes=len(text.encode("utf-8")),
        )


# --------------------------------------------------------------------------- #
# Guard rails on what the backend is allowed to be
# --------------------------------------------------------------------------- #
def require_configured(backend: LLMBackend, *, label: str = "LLM backend") -> None:
    """Refuse to start a step whose backend cannot possibly answer."""
    if not backend.is_configured():
        raise ContractError(f"{label} {backend.name!r} is not configured")


__all__ = [
    "DeterministicRouterBackend",
    "ProviderAttemptView",
    "RouteOutcome",
    "RouterBackend",
    "RoutingError",
    "RoutingExhausted",
    "read_attempts",
    "require_configured",
]
