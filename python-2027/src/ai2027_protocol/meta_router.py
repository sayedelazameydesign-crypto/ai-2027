"""Task-aware meta-router: classify a goal, execute it free-first, record the episode.

The measured problem
--------------------
P1 added a capability *profile* layer and the operating-mind instructions, but
the runtime still treats every prompt identically: :class:`.router.FreeFirstRouter`
answers "which free provider can serve this call?" without knowing *what the
task is*. The learning loop in P2 needs a single entry point that takes a goal,
classifies it, executes it through the existing free-first router, independently
verifies the result, and emits an :class:`~.experience_store.ExperienceRecord`
that the rest of the learning engine can learn from.

What this module provides
-------------------------
:class:`MetaRouter` is the bridge between the operating mind and the runtime:

* **classify** the goal into a capability profile via the P1
  :class:`~.routing_profiles.RoutingRegistry` (reusing its text rules, not
  re-implementing them);
* **execute** the goal through the existing :class:`.router.FreeFirstRouter`,
  which owns budget, timeouts, retries and circuit breaking — this module does
  not replace it;
* **verify** the generated result with an injectable, independent verifier;
* **record** a verified :class:`ExperienceRecord` (or a refused one) for the
  learning memory.

The three hard rules
--------------------
* **MAX_SPEND=0** — :meth:`MetaRouter.assert_free_first` refuses to execute
  against a router whose budget policy is not free-first, before any call.
* **NO VERIFIED RESULT = NO LEARNING** — an episode is only marked verified when
  the independent verifier passes; the record carries the real verdict.
* **Privacy** — :class:`PrivacyPolicy` defaults to local-only, so repository or
  user content is never sent to a remote provider without an explicit opt-in.

What it does not do
-------------------
It does not re-rank providers for execution (that is P5, the adaptive router);
it uses the router's free-first order and exposes the scorecard's
recommendation for reporting. It does not call models directly — every call
goes through the router.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from .contracts import CompletionResponse, PromptRequest, ProviderKind
from .experience_store import ExperienceRecord, fingerprint_text
from .provider_scoring import ProviderScore, ProviderScorecard
from .router import FreeFirstRouter
from .routing_profiles import RoutingDecision, RoutingRegistry, load_routing_registry


class FreeFirstPolicyError(RuntimeError):
    """Raised when the router's budget policy is not a proven-zero, free-first policy."""


@dataclass(frozen=True)
class VerificationResult:
    """The verdict of an independent check on a generated result."""

    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class PrivacyPolicy:
    """Controls whether and to whom generated content may be sent remotely.

    The default is local-only: no remote provider receives any content unless
    the operator explicitly opts in. This is the privacy guard the learning
    engine applies before any outbound call.
    """

    allow_remote: bool = False
    #: Provider kinds permitted to receive content when ``allow_remote`` is True.
    allowed_remote_kinds: frozenset[str] = field(default_factory=frozenset)

    def permits(self, kind: ProviderKind) -> bool:
        """Return True when ``kind`` may receive content under this policy."""
        if not self.allow_remote:
            return False
        return kind.value in self.allowed_remote_kinds


def default_verifier(text: str) -> VerificationResult:
    """Return a structural verification: non-empty, with no leftover placeholders.

    This is a deliberately conservative, deterministic check suitable for the
    offline learning loop. Real verification of specific artifacts belongs to
    the caller-supplied verifier; this default only proves the model produced
    actual content rather than an empty or placeholder-laden response.
    """
    if not text or not text.strip():
        return VerificationResult(False, "empty output")
    lowered = text.lower()
    for marker in ("{{", "todo", "tbd", "placeholder"):
        if marker in lowered:
            return VerificationResult(False, f"output contains unresolved marker {marker!r}")
    return VerificationResult(True, "structural check passed")


class MetaRouter:
    """Classify, execute (free-first), verify, and record one learning episode."""

    def __init__(
        self,
        router: FreeFirstRouter,
        *,
        registry: RoutingRegistry | None = None,
        scorecard: ProviderScorecard | None = None,
        verifier: Callable[[str], VerificationResult] | None = None,
        privacy: PrivacyPolicy | None = None,
    ) -> None:
        """Bind a router and the optional collaborators of the learning loop."""
        self.router = router
        self.registry = registry or load_routing_registry()
        self.scorecard = scorecard
        self.verifier: Callable[[str], VerificationResult] = verifier or default_verifier
        self.privacy = privacy or PrivacyPolicy()

    # ------------------------------------------------------------------ policy

    def assert_free_first(self) -> None:
        """Raise :class:`FreeFirstPolicyError` unless the router is free-first.

        The rule MAX_SPEND=0 is enforced here *before* any execution, so a
        misconfigured budget policy cannot silently produce a paid or
        unknown-cost call.
        """
        policy = self.router.budget_guard.policy
        if policy.max_spend_usd != 0.0 or policy.allow_unknown_cost:
            raise FreeFirstPolicyError(
                "refusing to execute: the router budget policy is not free-first "
                f"(max_spend_usd={policy.max_spend_usd}, "
                f"allow_unknown_cost={policy.allow_unknown_cost})"
            )

    # --------------------------------------------------------------- routing

    def classify(self, task: str) -> RoutingDecision:
        """Return the capability profile that should own ``task``."""
        return self.registry.resolve(task_text=task)

    def recommend(self, capability: str, *, min_attempts: int = 1) -> ProviderScore | None:
        """Return the scorecard's best provider for ``capability``, if any data."""
        if self.scorecard is None:
            return None
        return self.scorecard.recommend(capability, min_attempts=min_attempts)

    def _build_request(
        self,
        task: str,
        *,
        temperature: float,
        max_tokens: int,
    ) -> PromptRequest:
        """Build a :class:`PromptRequest` gated by the privacy policy.

        Only provider kinds the privacy policy permits are listed as preferred,
        and ``allow_remote`` is False unless remote sending is explicitly
        enabled and at least one permitted kind exists.
        """
        default_kinds = self.registry.default_profile.preferred_providers
        permitted = [kind for kind in default_kinds if self.privacy.permits(ProviderKind(kind))]
        allow_remote = self.privacy.allow_remote and bool(permitted)
        return PromptRequest(
            prompt=task,
            temperature=temperature,
            max_tokens=max_tokens,
            allow_remote=allow_remote,
            preferred_providers=tuple(ProviderKind(kind) for kind in permitted) or None,
        )

    # -------------------------------------------------------------- execution

    async def execute(
        self,
        task: str,
        *,
        task_type: str | None = None,
        summary: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ExperienceRecord:
        """Execute ``task`` free-first, verify the result, and return an episode.

        The returned record always carries the *real* outcome: provider, model,
        latency, attempt count, the verification verdict, and the output
        fingerprint. If the router fails every provider, a record with
        ``verified=False`` and ``test_result="unverified"`` is returned so the
        failure is captured as data rather than swallowed.
        """
        self.assert_free_first()
        if task_type is None:
            decision = self.classify(task)
        else:
            decision = self.registry.select(task_type)
        profile = decision.profile
        profile_temperature = temperature if temperature is not None else profile.temperature
        profile_max_tokens = max_tokens if max_tokens is not None else profile.max_tokens

        request = self._build_request(
            task, temperature=profile_temperature, max_tokens=profile_max_tokens
        )
        episode_id = f"ep_{uuid.uuid4().hex[:12]}"
        episode_summary = summary if summary is not None else task[:200]

        try:
            response: CompletionResponse = await self.router.route(request)
        except Exception:
            # A total routing failure is data, not a crash: record it as an
            # unverified attempt so the learning memory sees the failure.
            return ExperienceRecord(
                episode_id=episode_id,
                task_type=decision.task_type,
                task_summary=episode_summary,
                provider="none",
                model="router-error",
                verified=False,
                test_result="unverified",
            )

        verification = self.verifier(response.text)
        return ExperienceRecord(
            episode_id=episode_id,
            task_type=decision.task_type,
            task_summary=episode_summary,
            provider=response.provider.value,
            model=response.model,
            latency_ms=response.latency_ms,
            attempts=len(response.attempts),
            verified=verification.passed,
            test_result="passed" if verification.passed else "failed",
            quality_score=0.0,
            accepted=None,
            output_fingerprint=fingerprint_text(response.text) if verification.passed else "",
            cost_usd=response.cost_usd or 0.0,
            billing_tier=response.billing_tier.value,
            cost_status=response.cost_status.value,
        )


__all__ = [
    "FreeFirstPolicyError",
    "MetaRouter",
    "PrivacyPolicy",
    "VerificationResult",
    "default_verifier",
]
