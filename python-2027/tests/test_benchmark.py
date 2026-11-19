from __future__ import annotations

import pytest

from ai2027_protocol.benchmark import run_benchmark_suite, summarize_nanoseconds


def test_summarize_nanoseconds_rejects_empty() -> None:
    with pytest.raises(ValueError):
        summarize_nanoseconds("empty", [])


async def test_benchmark_suite_measures_taskgroup_speedup() -> None:
    report = await run_benchmark_suite(iterations=5)
    assert len(report.comparisons) == 2
    assert len(report.standalone) == 1
    batch_comp = report.comparisons[0]
    assert batch_comp.baseline.p50_ms > 0.0
    assert batch_comp.optimized.p50_ms > 0.0
    assert batch_comp.speedup_x > 1.5
    assert report.standalone[0].ops_per_sec > 0.0
