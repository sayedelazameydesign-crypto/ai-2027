"""Continuous evaluation: a fixed benchmark that measures progress and detects regressions.

The measured problem
--------------------
A system that "gets better" needs a fixed, reproducible yardstick. Without one,
the only comparison available is a narrative claim, and a regression can ship
unnoticed. This module provides a benchmark of fixed cases across the six
capabilities the platform targets, runs them through the learning engine's
:class:`~.meta_router.MetaRouter`, and produces a score that can be compared to
a stored baseline.

The six capabilities
--------------------
``python_engineering``, ``architecture``, ``reasoning``, ``research``,
``agent_execution``, ``long_term_memory`` — each a small set of fixed tasks.

What this module provides
-------------------------
* :class:`BenchmarkCase` — one fixed task, tagged with a capability category;
* :class:`BenchmarkReport` — per-category and overall scores plus episode ids;
* :class:`BenchmarkSuite` — runs cases, scores them, persists a baseline, and
  compares a fresh run against it to surface regressions.

The benchmark is **hermetic and reproducible**: it runs through whatever router
the caller supplies (the tests use the deterministic offline provider), so no
live model, no network, and no credential is involved. A category score is the
fraction of its cases whose episode was independently verified. Regressions are
detected when a fresh run scores strictly below the stored baseline for a
category.

What it does not do
-------------------
It does not pick models, does not spend money, and does not store experiences
directly — it drives :class:`~.meta_router.MetaRouter`, which owns execution and
recording. The baseline is a JSON file next to the experience store; it is a
measurement snapshot, not a source of truth.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field

from .contracts import StrictModel
from .experience_store import ExperienceRecord
from .meta_router import MetaRouter

#: The six capability categories of the AI-2027 intelligence benchmark.
BENCHMARK_CATEGORIES: tuple[str, ...] = (
    "python_engineering",
    "architecture",
    "reasoning",
    "research",
    "agent_execution",
    "long_term_memory",
)


@dataclass(frozen=True)
class BenchmarkCase:
    """One fixed benchmark task, tagged with the capability category it exercises."""

    category: str
    task: str
    summary: str

    def __post_init__(self) -> None:
        """Reject a case whose category is not one of the six benchmark categories."""
        if self.category not in BENCHMARK_CATEGORIES:
            raise ValueError(
                f"unknown benchmark category {self.category!r}; "
                f"expected one of {BENCHMARK_CATEGORIES}"
            )


def default_cases() -> tuple[BenchmarkCase, ...]:
    """Return the fixed benchmark case set, one or more per capability category."""
    return (
        BenchmarkCase(
            "python_engineering",
            "Fix the failing test in this module and add a regression test.",
            "fix a failing test and add a regression test",
        ),
        BenchmarkCase(
            "python_engineering",
            "Refactor this function to remove duplication while keeping behaviour.",
            "refactor a function without changing behaviour",
        ),
        BenchmarkCase(
            "architecture",
            "Design a layered architecture and document each layer's contract.",
            "design a layered architecture and document contracts",
        ),
        BenchmarkCase(
            "architecture",
            "Decide how to split responsibilities between the kernel and runtime.",
            "decide responsibility boundaries between kernel and runtime",
        ),
        BenchmarkCase(
            "reasoning",
            "Diagnose the root cause of this failure from the observed evidence.",
            "diagnose the root cause from observed evidence",
        ),
        BenchmarkCase(
            "reasoning",
            "Compare the two approaches and recommend one with reasons.",
            "compare two approaches and recommend one",
        ),
        BenchmarkCase(
            "research",
            "Verify whether this claim is supported by the cited sources.",
            "verify a claim against cited sources",
        ),
        BenchmarkCase(
            "research",
            "Summarise the findings of this paper and list its limitations.",
            "summarise a paper and list its limitations",
        ),
        BenchmarkCase(
            "agent_execution",
            "Execute this task step by step, verifying each step before moving on.",
            "execute a task step by step with verification",
        ),
        BenchmarkCase(
            "agent_execution",
            "Replan this task after the last step's verification failed.",
            "replan a task after a failed verification",
        ),
        BenchmarkCase(
            "long_term_memory",
            "Recall the most relevant past experience for this goal and apply it.",
            "recall and apply the most relevant past experience",
        ),
        BenchmarkCase(
            "long_term_memory",
            "Store this verified outcome so it survives a restart.",
            "store a verified outcome durably",
        ),
    )


class BenchmarkReport(StrictModel):
    """Serializable scorecard of one benchmark run."""

    created_at: str = ""
    overall_score: float = Field(default=0.0, ge=0.0, le=1.0)
    categories: dict[str, float] = {}
    episodes: tuple[str, ...] = ()


class BenchmarkSuite:
    """Run the fixed benchmark, score it, and compare against a stored baseline."""

    def __init__(
        self,
        meta_router: MetaRouter,
        *,
        cases: tuple[BenchmarkCase, ...] | None = None,
        baseline_path: Path | str | None = None,
    ) -> None:
        """Bind a meta-router, the case set, and an optional baseline file path."""
        self.meta_router = meta_router
        self.cases = cases if cases is not None else default_cases()
        self.baseline_path = Path(baseline_path) if baseline_path is not None else None

    async def run(self) -> tuple[BenchmarkReport, list[ExperienceRecord]]:
        """Execute every case and return the report together with the episodes."""
        episodes: list[ExperienceRecord] = []
        category_totals: dict[str, int] = {category: 0 for category in BENCHMARK_CATEGORIES}
        category_passed: dict[str, int] = {category: 0 for category in BENCHMARK_CATEGORIES}
        for case in self.cases:
            episode = await self.meta_router.execute(case.task, summary=case.summary)
            episodes.append(episode)
            category_totals[case.category] += 1
            if episode.verified:
                category_passed[case.category] += 1
        categories = {
            category: (category_passed[category] / category_totals[category])
            if category_totals[category]
            else 0.0
            for category in BENCHMARK_CATEGORIES
        }
        overall = sum(categories.values()) / len(categories) if categories else 0.0
        report = BenchmarkReport(
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            overall_score=round(overall, 4),
            categories={category: round(score, 4) for category, score in categories.items()},
            episodes=tuple(episode.episode_id for episode in episodes),
        )
        return report, episodes

    def save_baseline(self, report: BenchmarkReport) -> Path:
        """Persist ``report`` as the baseline JSON; return the path written."""
        if self.baseline_path is None:
            raise ValueError("no baseline_path configured for this suite")
        self.baseline_path.parent.mkdir(parents=True, exist_ok=True)
        self.baseline_path.write_text(
            json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return self.baseline_path

    def load_baseline(self) -> BenchmarkReport | None:
        """Load the stored baseline, or return ``None`` if none exists yet."""
        if self.baseline_path is None or not self.baseline_path.is_file():
            return None
        payload = json.loads(self.baseline_path.read_text(encoding="utf-8"))
        return BenchmarkReport.model_validate(payload)

    def compare(self, report: BenchmarkReport) -> list[str]:
        """Return the categories where ``report`` regressed below the baseline.

        A regression is a category whose fresh score is strictly below the
        baseline score. An empty list means no regression detected (or no
        baseline to compare against).
        """
        baseline = self.load_baseline()
        if baseline is None:
            return []
        regressions: list[str] = []
        for category, score in report.categories.items():
            baseline_score = baseline.categories.get(category)
            if baseline_score is not None and score < baseline_score:
                regressions.append(f"{category}: {score:.3f} < baseline {baseline_score:.3f}")
        return regressions


__all__ = [
    "BENCHMARK_CATEGORIES",
    "BenchmarkCase",
    "BenchmarkReport",
    "BenchmarkSuite",
    "default_cases",
]
