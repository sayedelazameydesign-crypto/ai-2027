"""Learn from user feedback and test evaluations — but only from verified episodes.

The measured problem
--------------------
A learning loop that accepts feedback on any outcome will happily learn from
results nobody verified. If a provider produces a plausible-but-wrong answer and
the user never checks it, treating that as a success trains the router toward a
provider that is reliably wrong. The feedback signal must be gated the same way
the memory is.

What this module provides
-------------------------
:class:`FeedbackEngine` turns two kinds of signal into scorecard updates:

* **user feedback** — an explicit accept / reject / quality rating for an
  episode;
* **test evaluation** — the outcome of the independent verification step
  (passed / failed / unverified).

The engine applies a signal to a :class:`~.experience_store.ExperienceRecord`
only when the episode was **verified**. Feedback on an unverified episode is
refused and reported, never silently applied. This is the operational form of
the rule *no verified result, no learning*.

It writes through a :class:`~.provider_scoring.ProviderScorecard`, so the
quality score and provider recommendation for a capability move only on evidence
that was checked.

What it does not do
-------------------
It does not call models, does not store experiences, and does not decide which
provider to call. It is the translation layer between a verified outcome and the
routing policy.
"""

from __future__ import annotations

from dataclasses import dataclass

from .experience_store import ExperienceRecord
from .provider_scoring import ProviderScore, ProviderScorecard

#: A quality rating in ``[0, 1]``; ratings at or above this threshold count as
#: an implicit accept, below it as an implicit reject.
ACCEPT_THRESHOLD = 0.6


class FeedbackError(RuntimeError):
    """Raised when feedback is applied to an episode that cannot support it."""


@dataclass(frozen=True)
class Feedback:
    """A user's judgement on a single episode, or ``None`` for "no feedback yet"."""

    accepted: bool
    rating: float | None = None
    note: str = ""

    @classmethod
    def accept(cls, rating: float = 1.0, note: str = "") -> Feedback:
        """Return an accepting feedback signal."""
        return cls(accepted=True, rating=rating, note=note)

    @classmethod
    def reject(cls, rating: float = 0.0, note: str = "") -> Feedback:
        """Return a rejecting feedback signal."""
        return cls(accepted=False, rating=rating, note=note)

    @classmethod
    def from_rating(cls, rating: float, note: str = "") -> Feedback:
        """Map a ``[0, 1]`` quality rating to an accept/reject signal."""
        return cls(accepted=rating >= ACCEPT_THRESHOLD, rating=rating, note=note)


class FeedbackEngine:
    """Gate feedback on verified episodes and fold it into the provider scorecard."""

    def __init__(self, scorecard: ProviderScorecard) -> None:
        """Bind the engine to a scorecard it writes through."""
        self.scorecard = scorecard

    def apply(self, record: ExperienceRecord, feedback: Feedback) -> bool:
        """Apply ``feedback`` to a verified ``record``; return True when applied.

        The episode must be verified for the feedback to count. A rejected
        episode is still admissible for learning *about failure* — a rejection is
        a real, checked outcome — so it is folded in; an unverified episode is
        refused outright, because there is nothing verified to learn from.
        """
        if not record.verified:
            raise FeedbackError(
                f"feedback refused for episode {record.episode_id}: "
                "the outcome was not independently verified; no learning from "
                "unverified results"
            )
        # A rejection is a checked outcome, so it updates the scorecard. An
        # acceptance on a verified episode is folded in as a success.
        self.scorecard.update([record])
        # Re-fold explicitly with the user's accept/reject so the preference
        # signal reflects the judgement, not just the verification verdict.
        judged = ExperienceRecord(
            episode_id=record.episode_id,
            task_type=record.task_type,
            complexity=record.complexity,
            task_summary=record.task_summary,
            provider=record.provider,
            model=record.model,
            latency_ms=record.latency_ms,
            attempts=record.attempts,
            verified=record.verified,
            test_result=record.test_result,
            quality_score=record.quality_score,
            accepted=feedback.accepted,
            output_fingerprint=record.output_fingerprint,
            cost_usd=record.cost_usd,
            billing_tier=record.billing_tier,
            cost_status=record.cost_status,
            created_at=record.created_at,
        )
        self.scorecard.update([judged])
        return True

    def apply_test_result(self, record: ExperienceRecord, test_result: str) -> bool:
        """Apply an independent test verdict to a verified ``record``.

        Only ``"passed"`` counts as a verified success. ``"failed"`` and
        ``"unverified"`` are recorded as a non-success, so they lower the score
        rather than being ignored.
        """
        judged = ExperienceRecord(
            episode_id=record.episode_id,
            task_type=record.task_type,
            complexity=record.complexity,
            task_summary=record.task_summary,
            provider=record.provider,
            model=record.model,
            latency_ms=record.latency_ms,
            attempts=record.attempts,
            verified=test_result == "passed",
            test_result=test_result,
            quality_score=record.quality_score,
            accepted=record.accepted,
            output_fingerprint=record.output_fingerprint,
            cost_usd=record.cost_usd,
            billing_tier=record.billing_tier,
            cost_status=record.cost_status,
            created_at=record.created_at,
        )
        self.scorecard.update([judged])
        return judged.verified

    def recommend(self, capability: str, *, min_attempts: int = 1) -> ProviderScore | None:
        """Return the scorecard's best provider for ``capability``.

        Delegates to :meth:`ProviderScorecard.recommend`; present here so callers
        can ask the engine for a recommendation without touching the scorecard.
        """
        return self.scorecard.recommend(capability, min_attempts=min_attempts)


__all__ = ["ACCEPT_THRESHOLD", "Feedback", "FeedbackEngine", "FeedbackError"]
