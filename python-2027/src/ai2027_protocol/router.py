"""Async Free-First AI router with timeouts, jittered retries, and TaskGroup batching."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from pydantic import Field

from .ai_providers import ProviderError, build_default_providers
from .contracts import (
    AIProvider,
    CompletionResponse,
    PromptRequest,
    ProviderAttempt,
    ProviderKind,
    StrictModel,
    TelemetrySink,
)
from .telemetry import ObservabilityRuntime


class RouterConfig(StrictModel):
    """Tuning parameters for Free-First failover, timeouts, retries, and circuit breaking."""

    per_provider_timeout_s: float = Field(default=8.0, ge=0.01, le=60.0)
    max_retries_per_provider: int = Field(default=1, ge=0, le=5)
    base_backoff_s: float = Field(default=0.05, ge=0.0, le=5.0)
    circuit_breaker_threshold: int = Field(default=3, ge=1, le=20)
    circuit_cooldown_s: float = Field(default=30.0, ge=0.0, le=600.0)


class AllProvidersFailedError(RuntimeError):
    """Raised when every configured provider and fallback fails."""

    def __init__(self, attempts: tuple[ProviderAttempt, ...]) -> None:
        """Capture every recorded provider attempt on total failure."""
        super().__init__(f"All {len(attempts)} provider attempt(s) failed.")
        self.attempts = attempts


@dataclass
class _CircuitState:
    consecutive_failures: int = 0
    opened_until_monotonic: float = 0.0


class FreeFirstRouter:
    """Resilient async router enforcing Free-First priority, fallback, and TaskGroup concurrency."""

    def __init__(
        self,
        providers: Sequence[AIProvider] | None = None,
        *,
        config: RouterConfig | None = None,
        telemetry: TelemetrySink | None = None,
        clock: Callable[[], float] = time.monotonic,
        jitter: Callable[[float], float] | None = None,
    ) -> None:
        """Initialize the router with replaceable providers, config, and telemetry sink."""
        self._providers: tuple[AIProvider, ...] = (
            tuple(providers) if providers is not None else build_default_providers()
        )
        self._config = config or RouterConfig()
        self._telemetry: TelemetrySink = telemetry or ObservabilityRuntime()
        self._clock = clock
        self._jitter = jitter or (lambda base: base * random.uniform(0.5, 1.0))  # noqa: S311
        self._circuits: dict[str, _CircuitState] = {}

    @property
    def providers(self) -> tuple[AIProvider, ...]:
        """Return ordered providers registered in this router."""
        return self._providers

    def _ordered_candidates(self, request: PromptRequest) -> list[AIProvider]:
        if not request.preferred_providers:
            return list(self._providers)
        rank = {kind: idx for idx, kind in enumerate(request.preferred_providers)}
        return sorted(
            self._providers,
            key=lambda p: (0, rank[p.kind]) if p.kind in rank else (1, 0),
        )

    def _is_circuit_open(self, provider: AIProvider) -> bool:
        state = self._circuits.get(f"{provider.kind.value}:{provider.model}")
        if state is None:
            return False
        return state.opened_until_monotonic > self._clock()

    def _record_success(self, provider: AIProvider) -> None:
        self._circuits[f"{provider.kind.value}:{provider.model}"] = _CircuitState()

    def _record_failure(self, provider: AIProvider) -> None:
        key = f"{provider.kind.value}:{provider.model}"
        state = self._circuits.setdefault(key, _CircuitState())
        state.consecutive_failures += 1
        if state.consecutive_failures >= self._config.circuit_breaker_threshold:
            state.opened_until_monotonic = self._clock() + self._config.circuit_cooldown_s

    async def route(self, request: PromptRequest) -> CompletionResponse:
        """Route one prompt across Free-First providers with timeout, retry, and fallback."""
        start_ns = time.perf_counter_ns()
        attempts: list[ProviderAttempt] = []

        with self._telemetry.start_span(
            "ai.route",
            {"allow_remote": request.allow_remote, "max_tokens": request.max_tokens},
        ):
            trace_id = self._telemetry.current_trace_id()
            for provider in self._ordered_candidates(request):
                if provider.is_remote and not request.allow_remote:
                    continue
                if not provider.is_configured():
                    continue
                if self._is_circuit_open(provider):
                    attempts.append(
                        ProviderAttempt(
                            provider=provider.kind,
                            model=provider.model,
                            attempt=1,
                            ok=False,
                            latency_ms=0.0,
                            error_code="circuit_open",
                            detail="Skipped while circuit breaker cooldown is active",
                        )
                    )
                    continue

                max_attempts = 1 + self._config.max_retries_per_provider
                for attempt_idx in range(1, max_attempts + 1):
                    attempt_start = time.perf_counter_ns()
                    with self._telemetry.start_span(
                        "ai.provider.attempt",
                        {
                            "provider": provider.kind.value,
                            "model": provider.model,
                            "attempt": attempt_idx,
                        },
                    ):
                        try:
                            async with asyncio.timeout(self._config.per_provider_timeout_s):
                                text = await provider.complete(
                                    request, timeout_s=self._config.per_provider_timeout_s
                                )
                        except (ProviderError, TimeoutError, Exception) as exc:
                            elapsed_ms = round((time.perf_counter_ns() - attempt_start) / 1e6, 3)
                            if isinstance(exc, ProviderError):
                                err_code, retryable, detail = (
                                    exc.code,
                                    exc.retryable,
                                    str(exc),
                                )
                            elif isinstance(exc, TimeoutError):
                                err_code, retryable, detail = (
                                    "timeout",
                                    True,
                                    "Provider attempt exceeded timeout",
                                )
                            else:
                                err_code, retryable, detail = (
                                    type(exc).__name__,
                                    False,
                                    str(exc),
                                )
                            attempts.append(
                                ProviderAttempt(
                                    provider=provider.kind,
                                    model=provider.model,
                                    attempt=attempt_idx,
                                    ok=False,
                                    latency_ms=elapsed_ms,
                                    error_code=err_code,
                                    detail=detail,
                                )
                            )
                            self._telemetry.log(
                                "warning",
                                "provider_attempt_failed",
                                provider=provider.kind.value,
                                model=provider.model,
                                attempt=attempt_idx,
                                error_code=err_code,
                                retryable=retryable,
                            )
                            if retryable and attempt_idx < max_attempts:
                                delay = self._jitter(
                                    self._config.base_backoff_s * (2 ** (attempt_idx - 1))
                                )
                                if delay > 0:
                                    await asyncio.sleep(delay)
                                continue
                            self._record_failure(provider)
                            break

                        elapsed_ms = round((time.perf_counter_ns() - attempt_start) / 1e6, 3)
                        self._record_success(provider)
                        attempts.append(
                            ProviderAttempt(
                                provider=provider.kind,
                                model=provider.model,
                                attempt=attempt_idx,
                                ok=True,
                                latency_ms=elapsed_ms,
                            )
                        )
                        total_ms = round((time.perf_counter_ns() - start_ns) / 1e6, 3)
                        self._telemetry.log(
                            "info",
                            "provider_route_succeeded",
                            provider=provider.kind.value,
                            model=provider.model,
                            attempt=attempt_idx,
                            latency_ms=total_ms,
                        )
                        return CompletionResponse(
                            provider=provider.kind,
                            model=provider.model,
                            text=text,
                            simulated=(provider.kind == ProviderKind.DETERMINISTIC),
                            cost_usd=0.0,
                            trace_id=trace_id,
                            attempts=tuple(attempts),
                            latency_ms=total_ms,
                        )

        self._telemetry.log("error", "all_providers_failed", attempts=len(attempts))
        raise AllProvidersFailedError(tuple(attempts))

    async def route_batch(
        self,
        requests: Sequence[PromptRequest],
        *,
        max_concurrency: int = 4,
    ) -> list[CompletionResponse]:
        """Execute multiple routing requests concurrently via asyncio.TaskGroup."""
        if not requests:
            return []
        semaphore = asyncio.Semaphore(max_concurrency)
        results: list[CompletionResponse | None] = [None] * len(requests)

        async def _worker(index: int, req: PromptRequest) -> None:
            async with semaphore:
                results[index] = await self.route(req)

        with self._telemetry.start_span(
            "ai.route_batch", {"batch_size": len(requests), "max_concurrency": max_concurrency}
        ):
            async with asyncio.TaskGroup() as group:
                for idx, req in enumerate(requests):
                    group.create_task(_worker(idx, req))

        return [item for item in results if item is not None]
