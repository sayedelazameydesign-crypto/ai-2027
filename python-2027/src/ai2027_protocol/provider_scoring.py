"""Per-provider, per-capability quality scoring derived from verified outcomes.

The measured problem
--------------------
The router needs to answer "which free provider is best *for this kind of task*?"
and the answer must come from measured outcomes, not from a provider's name or
marketing. A provider that is excellent at code repair may be poor at research
summaries, and a naive global ranking would send the wrong model to the wrong
task.

What this module provides
-------------------------
A :class:`ProviderScorecard` that accumulates verified :class:`ExperienceRecord`
outcomes per ``(provider, model, capability)`` key and derives an explainable
quality score from four measured signals:

* **verification rate** — the fraction of attempts whose outcome was
  independently proven;
* **success rate** — the fraction of attempts that produced an accepted result;
* **preference** — the fraction of user feedback that accepted the outcome;
* **efficiency** — a penalty for high latency and repeated attempts, so a model
  that only wins by being slow and retrying is not rewarded.

These signals are combined into a single ``quality_score`` in ``[0, 1]`` with a
documented weighting, and :meth:`ProviderScorecard.recommend` returns the best
provider for a capability. Recommendations are **advisory**: the actual call
still goes through :class:`.router.FreeFirstRouter`, which enforces the free-first
budget. This module never calls a provider and never spends money.

The honesty rule
----------------
Only **verified** experiences update the scorecard. An unverified or rejected
outcome contributes to the failure/rejection counts (so it lowers the score) but
never to the success or verification signals. A provider is never scored as
"good" from an outcome nobody checked.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from .experience_store import ExperienceRecord

#: Weight of each signal in the composite quality score. They sum to 1.0.
SIGNAL_WEIGHTS: dict[str, float] = {
    "verification": 0.4,
    "success": 0.3,
    "preference": 0.2,
    "efficiency": 0.1,
}

#: Latency and retry reference points used to compute the efficiency signal.
_REFERENCE_LATENCY_MS = 5_000.0
_REFERENCE_ATTEMPTS = 3


def _efficiency_score(latency_ms: float, attempts: int) -> float:
    """Return a ``[0, 1]`` efficiency score penalising slow, high-retry runs."""
    latency_ratio = latency_ms / _REFERENCE_LATENCY_MS if _REFERENCE_LATENCY_MS else 0.0
    attempt_ratio = attempts / _REFERENCE_ATTEMPTS
    return max(0.0, 1.0 - min(1.0, latency_ratio * 0.5 + attempt_ratio * 0.5))


@dataclass(frozen=True)
class ProviderScore:
    """Accumulated, explainable quality metrics for one provider on one capability."""

    provider: str
    model: str
    capability: str
    attempts: int = 0
    verified: int = 0
    successes: int = 0
    failures: int = 0
    accepted: int = 0
    rejected: int = 0
    latency_ms_total: float = 0.0
    latency_ms_samples: int = 0

    @property
    def verification_rate(self) -> float:
        """Return the fraction of attempts whose outcome was independently verified."""
        return self.verified / self.attempts if self.attempts else 0.0

    @property
    def success_rate(self) -> float:
        """Return the fraction of attempts that produced a successful outcome."""
        return self.successes / self.attempts if self.attempts else 0.0

    @property
    def preference(self) -> float:
        """Return the fraction of user feedback that accepted the outcome."""
        feedback = self.accepted + self.rejected
        return self.accepted / feedback if feedback else 0.0

    @property
    def avg_latency_ms(self) -> float:
        """Return the mean latency across measured samples, or 0.0 if none."""
        return self.latency_ms_total / self.latency_ms_samples if self.latency_ms_samples else 0.0

    @property
    def quality_score(self) -> float:
        """Return the weighted composite quality score in ``[0, 1]``.

        With no data the score is ``0.0`` — a provider with no verified outcomes
        is not assumed good. ``success_rate`` and ``preference`` measure
        different things: success is the raw verified-success fraction, while
        preference captures explicit user accept/reject on the subset that
        received feedback.
        """
        if self.attempts == 0:
            return 0.0
        verification = self.verification_rate
        success = self.success_rate
        preference = self.preference
        efficiency = _efficiency_score(self.avg_latency_ms, max(1, self.attempts))
        score = (
            SIGNAL_WEIGHTS["verification"] * verification
            + SIGNAL_WEIGHTS["success"] * success
            + SIGNAL_WEIGHTS["preference"] * preference
            + SIGNAL_WEIGHTS["efficiency"] * efficiency
        )
        return round(min(1.0, max(0.0, score)), 4)

    def explain(self) -> dict[str, float]:
        """Return the individual signals behind :attr:`quality_score` for audit."""
        return {
            "verification": round(self.verification_rate, 4),
            "success": round(self.success_rate, 4),
            "preference": round(self.preference, 4),
            "efficiency": 0.0
            if self.latency_ms_samples == 0
            else round(_efficiency_score(self.avg_latency_ms, max(1, self.attempts)), 4),
            "quality_score": self.quality_score,
        }


@dataclass
class ProviderScorecard:
    """Mutable registry of :class:`ProviderScore` entries keyed by provider+capability."""

    scores: dict[tuple[str, str, str], ProviderScore] = field(default_factory=dict)

    def update(self, records: Iterable[ExperienceRecord]) -> int:
        """Fold experiences into the scorecard; return the number of verified ones folded.

        Only admissible (verified) records contribute to success/verification
        signals. Every record — including rejected ones — still updates the
        attempt and rejection counts, so a provider's failures lower its score.
        """
        count = 0
        for record in records:
            key = (record.provider, record.model, record.task_type)
            entry = self.scores.get(key)
            if entry is None:
                entry = ProviderScore(
                    provider=record.provider, model=record.model, capability=record.task_type
                )
            self.scores[key] = self._fold(entry, record)
            if record.is_admissible:
                count += 1
        return count

    @staticmethod
    def _fold(entry: ProviderScore, record: ExperienceRecord) -> ProviderScore:
        """Apply one experience to a score entry, returning an updated entry.

        :class:`ProviderScore` is frozen, so the updated counters are built into
        a fresh instance rather than mutated in place.
        """
        return ProviderScore(
            provider=entry.provider,
            model=entry.model,
            capability=entry.capability,
            attempts=entry.attempts + 1,
            verified=entry.verified + int(record.verified),
            successes=entry.successes + int(record.verified and not record.is_rejected),
            failures=entry.failures + int(not record.verified or record.is_rejected),
            accepted=entry.accepted + int(record.accepted is True),
            rejected=entry.rejected + int(record.accepted is False),
            latency_ms_total=entry.latency_ms_total + record.latency_ms,
            latency_ms_samples=entry.latency_ms_samples + 1,
        )

    def get(self, provider: str, model: str, capability: str) -> ProviderScore | None:
        """Return the score entry for a provider/model/capability, or ``None``."""
        return self.scores.get((provider, model, capability))

    def for_capability(self, capability: str) -> list[ProviderScore]:
        """Return every provider score for ``capability``, best quality first."""
        entries = [score for (_p, _m, cap), score in self.scores.items() if cap == capability]
        return sorted(entries, key=lambda score: score.quality_score, reverse=True)

    def recommend(self, capability: str, *, min_attempts: int = 1) -> ProviderScore | None:
        """Return the best-scoring provider for ``capability`` with enough data.

        A provider is only recommended once it has at least ``min_attempts``
        measured attempts, so a single lucky pass cannot dominate a ranking.
        """
        ranked = [
            score for score in self.for_capability(capability) if score.attempts >= min_attempts
        ]
        return ranked[0] if ranked else None

    def best_global(self) -> ProviderScore | None:
        """Return the highest-scoring provider across all capabilities, or ``None``."""
        if not self.scores:
            return None
        return max(self.scores.values(), key=lambda score: score.quality_score)

    def summary(self) -> dict[str, dict[str, object]]:
        """Return a serializable overview of all scores for reports and baselines."""
        return {
            f"{provider}/{model}": {
                "capability": capability,
                "attempts": score.attempts,
                "quality_score": score.quality_score,
                "signals": score.explain(),
            }
            for (provider, model, capability), score in self.scores.items()
        }


__all__ = ["SIGNAL_WEIGHTS", "ProviderScore", "ProviderScorecard"]
