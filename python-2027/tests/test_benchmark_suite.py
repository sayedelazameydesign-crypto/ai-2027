"""Tests for the benchmark suite (``ai2027_protocol.benchmark_suite``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai2027_protocol.ai_providers import DeterministicFallbackProvider
from ai2027_protocol.benchmark_suite import (
    BENCHMARK_CATEGORIES,
    BenchmarkCase,
    BenchmarkReport,
    BenchmarkSuite,
    default_cases,
)
from ai2027_protocol.budget import BudgetPolicy
from ai2027_protocol.meta_router import MetaRouter
from ai2027_protocol.router import FreeFirstRouter, RouterConfig

#: A fast, offline config: no retries, no backoff, short per-provider timeout.
_FAST_CONFIG = RouterConfig(
    per_provider_timeout_s=0.05, max_retries_per_provider=0, base_backoff_s=0.0
)


def _meta_router() -> MetaRouter:
    router = FreeFirstRouter(
        providers=[DeterministicFallbackProvider()],
        config=_FAST_CONFIG,
        budget_policy=BudgetPolicy(max_spend_usd=0.0),
    )
    return MetaRouter(router, registry=None, scorecard=None)


class TestCase:
    def test_default_cases_cover_every_category(self) -> None:
        cases = default_cases()
        assert cases
        seen = {case.category for case in cases}
        assert seen == set(BENCHMARK_CATEGORIES)

    def test_case_rejects_an_unknown_category(self) -> None:
        with pytest.raises(ValueError):
            BenchmarkCase("not_a_category", "task", "summary")

    def test_report_defaults_are_valid(self) -> None:
        report = BenchmarkReport()
        assert report.overall_score == 0.0
        assert report.categories == {}


class TestRun:
    async def test_run_scores_every_category(self) -> None:
        suite = BenchmarkSuite(_meta_router())
        report, episodes = await suite.run()
        assert len(episodes) == len(suite.cases)
        assert set(report.categories) == set(BENCHMARK_CATEGORIES)
        assert 0.0 <= report.overall_score <= 1.0

    async def test_deterministic_run_passes_all(self) -> None:
        suite = BenchmarkSuite(_meta_router())
        report, episodes = await suite.run()
        assert report.overall_score == pytest.approx(1.0)
        assert all(episode.verified for episode in episodes)

    async def test_run_episodes_are_real_records(self) -> None:
        suite = BenchmarkSuite(_meta_router())
        _, episodes = await suite.run()
        assert all(episode.task_type for episode in episodes)
        assert len(episodes[0].episode_id) > 0


class TestBaseline:
    async def test_save_and_load_baseline(self, tmp_path: Path) -> None:
        baseline = tmp_path / "baseline.json"
        suite = BenchmarkSuite(_meta_router(), baseline_path=baseline)
        report, _ = await suite.run()
        written = suite.save_baseline(report)
        assert written == baseline
        assert baseline.is_file()
        loaded = suite.load_baseline()
        assert loaded is not None
        assert loaded.overall_score == report.overall_score

    async def test_load_baseline_returns_none_when_absent(self, tmp_path: Path) -> None:
        suite = BenchmarkSuite(_meta_router(), baseline_path=tmp_path / "missing.json")
        assert suite.load_baseline() is None

    async def test_compare_reports_no_regression_when_equal(self, tmp_path: Path) -> None:
        baseline = tmp_path / "baseline.json"
        suite = BenchmarkSuite(_meta_router(), baseline_path=baseline)
        report, _ = await suite.run()
        suite.save_baseline(report)
        fresh, _ = await suite.run()
        assert suite.compare(fresh) == []

    async def test_compare_detects_a_regression(self, tmp_path: Path) -> None:
        baseline = tmp_path / "baseline.json"
        # Write a perfect baseline, then force a worse fresh report.
        suite = BenchmarkSuite(_meta_router(), baseline_path=baseline)
        report, _ = await suite.run()
        suite.save_baseline(report)
        worse = BenchmarkReport(
            created_at="2026-11-25T00:00:00Z",
            overall_score=0.0,
            categories={category: 0.0 for category in BENCHMARK_CATEGORIES},
        )
        regressions = suite.compare(worse)
        assert len(regressions) == len(BENCHMARK_CATEGORIES)

    async def test_compare_returns_empty_without_baseline(self, tmp_path: Path) -> None:
        suite = BenchmarkSuite(_meta_router(), baseline_path=tmp_path / "none.json")
        report, _ = await suite.run()
        assert suite.compare(report) == []

    def test_save_baseline_requires_a_path(self) -> None:
        suite = BenchmarkSuite(_meta_router())
        with pytest.raises(ValueError):
            suite.save_baseline(BenchmarkReport())

    def test_baseline_file_is_json(self, tmp_path: Path) -> None:
        baseline = tmp_path / "b.json"
        suite = BenchmarkSuite(_meta_router(), baseline_path=baseline)
        suite.save_baseline(BenchmarkReport(overall_score=0.5))
        payload = json.loads(baseline.read_text(encoding="utf-8"))
        assert payload["overall_score"] == 0.5
