"""Executable MAX_SPEND=0 BudgetGuard distinguishing LOCAL, FREE_TIER, and UNKNOWN_COST."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import Field

from .contracts import (
    AIProvider,
    BillingTier,
    BudgetSnapshot,
    CostStatus,
    ProviderKind,
    StrictModel,
)
from .model_catalog import (
    find_retired_model,
    is_openrouter_free_model,
    normalize_model_id,
    retirement_reason,
)

#: Providers whose cost classification must never be asserted by a caller.
#:
#: ``classify_billing_tier`` honours an ``explicit_tier`` for every kind except
#: OpenRouter's ``:free`` check, and ``AI2027_FREE_MODELS`` can allowlist any
#: model id. For a provider that is not free-guaranteed, either route would let
#: a caller label a paid endpoint ``FREE_TIER`` — or worse, ``LOCAL``, which
#: reports a remote call as proven-zero-cost. OmniRouters accounts receive no
#: free credit by default and its catalogue is per-account, so its cost is
#: genuinely unknown until an operator verifies it against a price list.
#:
#: These kinds are therefore classified only by policy: an explicit tier and the
#: free-model allowlist are both refused, and the result is always
#: ``UNKNOWN_COST``. A provider in this set is admitted only under an explicit
#: non-zero spend policy with ``allow_unknown_cost=True``.
NON_RECLASSIFIABLE_PROVIDERS: frozenset[ProviderKind] = frozenset(
    {ProviderKind.OMNIROUTERS, ProviderKind.QWEN}
)

#: Policy-declared free-tier IDs. Only IDs the provider still serves belong here:
#: a retired ID that lingers in this map is how a "free" check starts failing with
#: HTTP 404/410. Groq has no free Llama model left after the 2026-08-16 retirement,
#: so its set is empty on purpose — a paid-per-token model is never allowlisted by
#: default. Declare a specific model with ``GROQ_MODEL`` + ``AI2027_FREE_MODELS``
#: when you are relying on Groq's rate-limited free plan.
FREE_TIER_MODELS: dict[ProviderKind, frozenset[str]] = {
    ProviderKind.GEMINI: frozenset(
        {
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
            "gemini-1.5-flash",
            "gemini-1.5-flash-8b",
        }
    ),
    # NVIDIA retired meta/llama-3.3-70b-instruct on 2026-08-26 (HTTP 410 Gone),
    # so it can no longer be free. Only ids on NVIDIA's live free-endpoint list
    # belong here; an allowlist naming a withdrawn model fails the free check
    # with 410 instead of passing it.
    ProviderKind.NVIDIA: frozenset(
        {
            "z-ai/glm-5.3-flash",
        }
    ),
    # Groq's free plan is published per model: 30 requests/min, 1,000/day and
    # 200,000 tokens/day on gpt-oss-120b and gpt-oss-20b (checked 2026-11-19).
    # Llama 3.3 70B left the free plan, which is why the earlier set was empty.
    # 1,000 requests a day is 20x OpenRouter's free 50, and it is the tap that
    # makes a 24-sentence measurement possible on a day the others are spent.
    ProviderKind.GROQ: frozenset(
        {
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
        }
    ),
}

_normalize_model_id = normalize_model_id


def classify_billing_tier(
    *,
    kind: ProviderKind,
    model: str,
    is_remote: bool,
    explicit_tier: BillingTier | None = None,
) -> tuple[BillingTier, str]:
    """Classify provider billing tier; an API key alone NEVER proves free usage."""
    retired = find_retired_model(kind, model)
    if retired is not None:
        return BillingTier.UNKNOWN_COST, retirement_reason(retired)

    if kind in NON_RECLASSIFIABLE_PROVIDERS:
        # The cost of these providers is unverified by construction. Refuse
        # both ways a caller could assert otherwise — an explicit tier and the
        # free-model allowlist — so the only route to an allowed call is an
        # explicit non-zero spend policy.
        if explicit_tier is not None:
            return (
                BillingTier.UNKNOWN_COST,
                f"{kind.value} cost cannot be asserted by the caller: an explicit "
                f"billing tier is refused (got {explicit_tier.value}). Declare the "
                "cost against a verified price list instead.",
            )
        return (
            BillingTier.UNKNOWN_COST,
            f"{kind.value} is not free-guaranteed: its cost is unverified, and "
            "AI2027_FREE_MODELS cannot reclassify it. Only an explicit non-zero "
            "spend policy admits it.",
        )

    if explicit_tier is not None:
        if explicit_tier == BillingTier.FREE_TIER and kind == ProviderKind.OPENROUTER:
            if not is_openrouter_free_model(model):
                return (
                    BillingTier.UNKNOWN_COST,
                    "OpenRouter models without ':free' (or the 'openrouter/free' router) "
                    "cannot be claimed as FREE_TIER.",
                )
        return explicit_tier, f"Explicitly classified as {explicit_tier.value}."

    if kind == ProviderKind.DETERMINISTIC:
        return BillingTier.LOCAL, "In-process deterministic simulation ($0.00 proven locally)."

    if kind == ProviderKind.OLLAMA:
        if not is_remote:
            return BillingTier.LOCAL, "Local loopback Ollama endpoint ($0.00 local inference)."
        return BillingTier.UNKNOWN_COST, "Non-local Ollama endpoint cost is unverified."

    norm_model = _normalize_model_id(model)
    if kind == ProviderKind.OPENROUTER:
        if is_openrouter_free_model(model):
            return (
                BillingTier.FREE_TIER,
                "OpenRouter free routing ('openrouter/free' or an explicit ':free' model) "
                "is declared free-tier (no live invoice).",
            )
        return (
            BillingTier.UNKNOWN_COST,
            f"OpenRouter model {model!r} is neither ':free' nor 'openrouter/free'; "
            "key presence does not prove $0.",
        )

    allowed_models = FREE_TIER_MODELS.get(kind, frozenset())
    extra_raw = os.environ.get("AI2027_FREE_MODELS", "").strip()
    extra_models = {m.strip().lower() for m in extra_raw.split(",") if m.strip()}
    if norm_model in allowed_models or norm_model in extra_models:
        return (
            BillingTier.FREE_TIER,
            f"Allowlisted free-tier {kind.value} model {model!r} (policy-declared, no invoice).",
        )
    return (
        BillingTier.UNKNOWN_COST,
        f"Unclassified remote model {kind.value}:{model!r}; API key presence does not prove "
        "free. Declare it in AI2027_FREE_MODELS only if the free plan still serves it.",
    )


class BudgetPolicy(StrictModel):
    """Execution policy controlling zero-spend and remote free-tier admission."""

    max_spend_usd: float = Field(default=0.0, ge=0.0, le=1000.0)
    #: Permit calls whose cost cannot be estimated. NOTE: this is an
    #: authorisation, NOT a financial ceiling. An UNKNOWN_COST decision carries
    #: no ``estimated_cost_usd``, so ``record_completed`` never advances
    #: ``spent_usd`` and the ``max_spend_usd`` budget is never consumed by
    #: these calls; the dollar limit only binds providers that report a cost
    #: estimate. Use it as an explicit operator authorisation for a bounded
    #: experiment, never as a spend cap.
    #:
    #: Because the dollar ceiling cannot measure these calls, admission is
    #: additionally bounded by ``unknown_cost_daily_call_limit`` — a count the
    #: system CAN enforce. The dollar override alone never admits an
    #: unknown-cost call past that daily limit (0 = refuse them entirely).
    allow_unknown_cost: bool = False
    allow_declared_free_tier: bool = True
    #: Daily cap on COMPLETED unknown-cost calls while ``allow_unknown_cost``
    #: is active. Measurable, so it binds where ``max_spend_usd`` cannot.
    unknown_cost_daily_call_limit: int = Field(default=5, ge=0, le=100_000)

    @classmethod
    def from_env(cls) -> BudgetPolicy:
        """Build a fail-closed BudgetPolicy reading AI2027_MAX_SPEND_USD (default 0.0)."""
        raw_max = os.environ.get("AI2027_MAX_SPEND_USD", "0").strip()
        try:
            max_spend = max(0.0, float(raw_max)) if raw_max else 0.0
        except ValueError:
            max_spend = 0.0
        allow_unknown = os.environ.get("AI2027_ALLOW_UNKNOWN_COST", "0").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        if max_spend == 0.0:
            allow_unknown = False
        raw_limit = os.environ.get("AI2027_UNKNOWN_COST_DAILY_LIMIT", "").strip()
        if raw_limit == "":
            daily_limit = 5  # bounded by default; unset is not unbounded
        else:
            try:
                daily_limit = max(0, int(raw_limit))
            except ValueError:
                daily_limit = 0  # garbage fails closed: refuse unknown-cost calls
        return cls(
            max_spend_usd=max_spend,
            allow_unknown_cost=allow_unknown,
            unknown_cost_daily_call_limit=daily_limit,
        )


class BudgetDecision(StrictModel):
    """Pre-flight budget verdict emitted before any outbound provider request."""

    allowed: bool
    billing_tier: BillingTier
    cost_status: CostStatus
    cost_known: bool
    estimated_cost_usd: float | None = Field(default=None, ge=0.0)
    reason: str


class BudgetGuard:
    """Thread-safe pre-flight guard enforcing MAX_SPEND=0 and recording cost provenance."""

    def __init__(
        self,
        policy: BudgetPolicy | None = None,
        *,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        """Initialize the BudgetGuard with an explicit or env-derived fail-closed policy.

        ``now_fn`` exists so tests can prove the daily unknown-cost counter
        rolls over at UTC midnight without sleeping.
        """
        self.policy = policy or BudgetPolicy.from_env()
        self._lock = threading.Lock()
        self._now = now_fn or (lambda: datetime.now(UTC))
        self._spent_usd = 0.0
        self._known_zero_local_calls = 0
        self._declared_free_tier_calls = 0
        self._unknown_cost_blocked = 0
        self._paid_blocked = 0
        self._unknown_admitted_today = 0
        self._unknown_day = ""

    def _roll_unknown_day_locked(self) -> None:
        """Reset the completed-unknown-cost counter at each UTC day boundary."""
        today = self._now().date().isoformat()
        if today != self._unknown_day:
            self._unknown_day = today
            self._unknown_admitted_today = 0

    def evaluate_provider(self, provider: AIProvider) -> BudgetDecision:
        """Evaluate whether a provider may be called under current BudgetPolicy."""
        explicit = getattr(provider, "billing_tier", None)
        tier_val: BillingTier | None = explicit if isinstance(explicit, BillingTier) else None
        tier, tier_reason = classify_billing_tier(
            kind=provider.kind,
            model=provider.model,
            is_remote=provider.is_remote,
            explicit_tier=tier_val,
        )
        provider_reason = getattr(provider, "billing_reason", "")
        if isinstance(provider_reason, str) and provider_reason.strip():
            # A provider classified itself at construction (e.g. "not in the free
            # allowlist; declare AI2027_FREE_MODELS"); keep that explanation
            # instead of collapsing it into "explicitly classified".
            tier_reason = provider_reason

        if tier == BillingTier.LOCAL:
            return BudgetDecision(
                allowed=True,
                billing_tier=BillingTier.LOCAL,
                cost_status=CostStatus.PROVEN_ZERO_LOCAL,
                cost_known=True,
                estimated_cost_usd=0.0,
                reason=tier_reason,
            )

        if tier == BillingTier.FREE_TIER:
            if not self.policy.allow_declared_free_tier:
                return BudgetDecision(
                    allowed=False,
                    billing_tier=BillingTier.FREE_TIER,
                    cost_status=CostStatus.UNKNOWN_COST_BLOCKED,
                    cost_known=False,
                    estimated_cost_usd=None,
                    reason="Remote FREE_TIER disabled by strict local-only budget policy.",
                )
            return BudgetDecision(
                allowed=True,
                billing_tier=BillingTier.FREE_TIER,
                cost_status=CostStatus.DECLARED_FREE_TIER_UNBILLED,
                cost_known=False,
                estimated_cost_usd=0.0,
                reason=tier_reason,
            )

        if tier == BillingTier.PAID:
            raw_est = getattr(provider, "estimated_cost_usd", None)
            est = float(raw_est) if isinstance(raw_est, int | float) and raw_est >= 0 else None
            if (
                est is not None
                and self.policy.max_spend_usd > 0.0
                and (self._spent_usd + est) <= self.policy.max_spend_usd
            ):
                return BudgetDecision(
                    allowed=True,
                    billing_tier=BillingTier.PAID,
                    cost_status=CostStatus.ALLOWED_WITHIN_BUDGET,
                    cost_known=True,
                    estimated_cost_usd=est,
                    reason=(
                        f"Paid call (${est:.4f}) within MAX_SPEND=${self.policy.max_spend_usd:.4f}."
                    ),
                )
            return BudgetDecision(
                allowed=False,
                billing_tier=BillingTier.PAID,
                cost_status=CostStatus.PAID_BLOCKED,
                cost_known=est is not None,
                estimated_cost_usd=est,
                reason=(
                    f"Blocked by MAX_SPEND=${self.policy.max_spend_usd:.2f}: "
                    f"provider {provider.kind.value}:{provider.model} is PAID."
                ),
            )

        if self.policy.allow_unknown_cost and self.policy.max_spend_usd > 0.0:
            # The dollar ceiling cannot consume these calls (no estimate), so
            # admission is bounded by a MEASURABLE daily count instead. The
            # count advances in record_completed; readiness/status evaluations
            # that never complete a call therefore never burn the limit.
            limit = self.policy.unknown_cost_daily_call_limit
            with self._lock:
                self._roll_unknown_day_locked()
                admitted_today = self._unknown_admitted_today
            if limit <= 0 or admitted_today >= limit:
                return BudgetDecision(
                    allowed=False,
                    billing_tier=BillingTier.UNKNOWN_COST,
                    cost_status=CostStatus.UNKNOWN_COST_BLOCKED,
                    cost_known=False,
                    estimated_cost_usd=None,
                    reason=(
                        f"Unknown-cost daily call limit reached ({max(limit, 0)}/day): "
                        "MAX_SPEND cannot measure these calls, so admission stops at a "
                        "count this system can actually enforce."
                    ),
                )
            return BudgetDecision(
                allowed=True,
                billing_tier=BillingTier.UNKNOWN_COST,
                cost_status=CostStatus.ALLOWED_WITHIN_BUDGET,
                cost_known=False,
                estimated_cost_usd=None,
                reason=(
                    "Unknown-cost call permitted under non-zero budget override "
                    f"(daily unknown-cost call limit {limit}/day; MAX_SPEND is not a "
                    "cap for these calls)."
                ),
            )

        return BudgetDecision(
            allowed=False,
            billing_tier=BillingTier.UNKNOWN_COST,
            cost_status=CostStatus.UNKNOWN_COST_BLOCKED,
            cost_known=False,
            estimated_cost_usd=None,
            reason=f"Blocked by MAX_SPEND=${self.policy.max_spend_usd:.2f}: {tier_reason}",
        )

    def record_blocked(self, decision: BudgetDecision) -> None:
        """Increment blocked-call counters when a provider is rejected pre-flight."""
        with self._lock:
            if decision.billing_tier == BillingTier.PAID:
                self._paid_blocked += 1
            else:
                self._unknown_cost_blocked += 1

    def record_completed(self, decision: BudgetDecision) -> None:
        """Record known-local vs policy-declared free-tier vs metered spend.

        Completed UNKNOWN_COST calls advance the measurable daily counter
        (never ``spent_usd`` — they carry no estimate). Counting on completion
        keeps readiness/status evaluations from consuming the limit; a call
        admitted but never completed does not count, so a crash-loop cannot
        silently refill the budget either way.
        """
        with self._lock:
            if decision.billing_tier == BillingTier.LOCAL:
                self._known_zero_local_calls += 1
            elif decision.billing_tier == BillingTier.FREE_TIER:
                self._declared_free_tier_calls += 1
            elif decision.billing_tier == BillingTier.UNKNOWN_COST and decision.allowed:
                self._roll_unknown_day_locked()
                self._unknown_admitted_today += 1
            if decision.estimated_cost_usd is not None and decision.estimated_cost_usd > 0:
                self._spent_usd = round(self._spent_usd + decision.estimated_cost_usd, 6)

    def snapshot(self) -> BudgetSnapshot:
        """Return an immutable snapshot of budget enforcement metrics."""
        with self._lock:
            self._roll_unknown_day_locked()
            return BudgetSnapshot(
                max_spend_usd=self.policy.max_spend_usd,
                spent_usd=self._spent_usd,
                allow_unknown_cost=self.policy.allow_unknown_cost,
                known_zero_local_calls=self._known_zero_local_calls,
                declared_free_tier_calls=self._declared_free_tier_calls,
                unknown_cost_blocked=self._unknown_cost_blocked,
                paid_blocked=self._paid_blocked,
                api_key_never_implies_free=True,
                unknown_cost_daily_call_limit=self.policy.unknown_cost_daily_call_limit,
                unknown_cost_admitted_today=self._unknown_admitted_today,
            )
