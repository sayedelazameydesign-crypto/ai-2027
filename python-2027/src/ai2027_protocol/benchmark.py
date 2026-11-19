"""Reproducible performance measurement harness (measure before optimizing)."""

from __future__ import annotations

import asyncio
import math
import platform
import re
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from .ai_providers import DeterministicFallbackProvider
from .contracts import (
    AIProvider,
    BenchmarkReport,
    ComparisonResult,
    PromptRequest,
    ProviderKind,
    TelemetrySink,
    TimingStats,
)
from .router import FreeFirstRouter, RouterConfig
from .telemetry import ObservabilityRuntime

_MODULE_DIR = Path(__file__).resolve().parent


class _SimulatedLatencyProvider:
    """Test-only async provider simulating non-blocking network latency."""

    kind = ProviderKind.GEMINI
    is_remote = True

    def __init__(self, delay_s: float = 0.005) -> None:
        self._delay_s = delay_s
        self.model = "simulated-latency-probe"

    def is_configured(self) -> bool:
        return True

    async def complete(self, request: PromptRequest, *, timeout_s: float) -> str:
        async with asyncio.timeout(timeout_s):
            await asyncio.sleep(self._delay_s)
        return f"ok:{len(request.prompt)}"


def summarize_nanoseconds(name: str, samples_ns: Sequence[int]) -> TimingStats:
    """Compute exact min, p50, p95, max, spread, and ops/sec from nanosecond samples."""
    if not samples_ns:
        raise ValueError("samples_ns must contain at least one measurement.")
    ordered = sorted(max(1, int(ns)) for ns in samples_ns)
    n = len(ordered)
    p50_idx = int(0.50 * (n - 1))
    p95_idx = max(p50_idx, min(n - 1, math.ceil(0.95 * (n - 1))))
    min_ms = round(ordered[0] / 1e6, 3)
    p50_ms = round(ordered[p50_idx] / 1e6, 3)
    p95_ms = round(ordered[p95_idx] / 1e6, 3)
    max_ms = round(ordered[-1] / 1e6, 3)
    spread_pct = round(((max_ms - min_ms) / p50_ms) * 100.0, 1) if p50_ms > 0 else 0.0
    total_s = sum(ordered) / 1e9
    ops_per_sec = round(n / total_s, 1) if total_s > 0 else 0.0
    return TimingStats(
        name=name,
        n=n,
        min_ms=min_ms,
        p50_ms=p50_ms,
        p95_ms=p95_ms,
        max_ms=max_ms,
        spread_pct=spread_pct,
        ops_per_sec=ops_per_sec,
    )


async def measure_async(
    name: str,
    iterations: int,
    coro_factory: Callable[[], Awaitable[object]],
) -> TimingStats:
    """Measure an async operation over N iterations using monotonic perf_counter_ns."""
    samples: list[int] = []
    for _ in range(iterations):
        start = time.perf_counter_ns()
        await coro_factory()
        samples.append(time.perf_counter_ns() - start)
    return summarize_nanoseconds(name, samples)


def measure_sync(
    name: str,
    iterations: int,
    fn: Callable[[], object],
) -> TimingStats:
    """Measure a synchronous operation over N iterations using perf_counter_ns."""
    samples: list[int] = []
    for _ in range(iterations):
        start = time.perf_counter_ns()
        fn()
        samples.append(time.perf_counter_ns() - start)
    return summarize_nanoseconds(name, samples)


def _read_sample_texts(src_dir: Path) -> list[str]:
    return [
        p.read_text(encoding="utf-8", errors="ignore") for p in sorted(src_dir.glob("*.py"))[:12]
    ]


async def run_benchmark_suite(
    *,
    iterations: int = 20,
    scan_dir: Path | None = None,
    telemetry: TelemetrySink | None = None,
) -> BenchmarkReport:
    """Run reproducible baseline-vs-optimized benchmarks and emit a BenchmarkReport."""
    runtime: TelemetrySink = telemetry or ObservabilityRuntime()
    src_dir = scan_dir or _MODULE_DIR
    sample_texts = await asyncio.to_thread(_read_sample_texts, src_dir)

    with runtime.start_span("bench.run", {"iterations": iterations}):
        trace_id = runtime.current_trace_id()
        # 1. Sequential vs asyncio.TaskGroup concurrent batch routing
        probe_provider: AIProvider = _SimulatedLatencyProvider(delay_s=0.004)
        router = FreeFirstRouter(
            providers=[probe_provider, DeterministicFallbackProvider()],
            config=RouterConfig(base_backoff_s=0.0),
            telemetry=ObservabilityRuntime(),
        )
        batch = [PromptRequest(prompt=f"Measure batch item {i}") for i in range(6)]

        async def _run_sequential() -> None:
            for item in batch:
                await router.route(item)

        async def _run_taskgroup() -> None:
            await router.route_batch(batch, max_concurrency=6)

        batch_iters = max(3, min(iterations, 15))
        seq_stats = await measure_async(
            "route_batch_sequential(6x4ms)", batch_iters, _run_sequential
        )
        tg_stats = await measure_async("route_batch_taskgroup(6x4ms)", batch_iters, _run_taskgroup)
        batch_speedup = round(seq_stats.p50_ms / tg_stats.p50_ms, 2) if tg_stats.p50_ms > 0 else 0.0

        # 2. Naive uncached regex compile per scan vs precompiled regex patterns
        raw_patterns = (
            r"AIza[0-9A-Za-z_\-]{20,}",
            r"sk-(?:proj-)?[0-9A-Za-z_\-]{20,}",
            r"gh[pousr]_[0-9A-Za-z]{20,}",
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        )
        compiled = tuple(re.compile(pat) for pat in raw_patterns)

        def _naive_scan() -> int:
            hits = 0
            for text in sample_texts:
                for pat in raw_patterns:
                    if re.search(pat, text):
                        hits += 1
            return hits

        def _precompiled_scan() -> int:
            hits = 0
            for text in sample_texts:
                for rx in compiled:
                    if rx.search(text):
                        hits += 1
            return hits

        scan_iters = max(10, iterations)
        naive_stats = measure_sync("regex_scan_uncached", scan_iters, _naive_scan)
        fast_stats = measure_sync("regex_scan_precompiled", scan_iters, _precompiled_scan)
        scan_speedup = (
            round(naive_stats.p50_ms / fast_stats.p50_ms, 2) if fast_stats.p50_ms > 0 else 0.0
        )

        # 3. Pydantic V2 contract validation throughput
        raw_payload = {
            "prompt": "Verify Pydantic V2 strict schema throughput",
            "max_tokens": 128,
        }
        pydantic_stats = measure_sync(
            "pydantic_v2_PromptRequest_validate",
            max(50, iterations * 5),
            lambda: PromptRequest.model_validate(raw_payload),
        )

    runtime.log(
        "info",
        "benchmark_suite_completed",
        batch_speedup_x=batch_speedup,
        scan_speedup_x=scan_speedup,
        pydantic_p50_ms=pydantic_stats.p50_ms,
    )
    return BenchmarkReport(
        python_version=sys.version.split()[0],
        platform=platform.platform(),
        comparisons=(
            ComparisonResult(
                baseline=seq_stats,
                optimized=tg_stats,
                speedup_x=batch_speedup,
                measured_note="asyncio.TaskGroup parallelizes independent async provider I/O.",
            ),
            ComparisonResult(
                baseline=naive_stats,
                optimized=fast_stats,
                speedup_x=scan_speedup,
                measured_note=(
                    "Precompiling regex patterns once eliminates per-file compile overhead."
                ),
            ),
        ),
        standalone=(pydantic_stats,),
        trace_id=trace_id,
    )
