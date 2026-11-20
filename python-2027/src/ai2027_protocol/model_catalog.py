"""Single source of truth for default, free-tier, and retired remote model IDs.

A provider-side retirement used to surface only as a raw ``HTTP 404/410`` inside a
live check, with the old ID still advertised in docs and defaults. Every remote
model ID that we ship as a default therefore lives here, and every ID a provider
has stopped serving is recorded with its retirement date and replacement so the
guard blocks it *before* a request is spent and tells the operator what to use.
"""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import ProviderKind

#: Default model per remote provider, used when the matching ``*_MODEL`` env var
#: is empty. None of these is assumed free by itself: `BudgetGuard` still decides.
DEFAULT_MODELS: dict[ProviderKind, str] = {
    ProviderKind.GEMINI: "gemini-2.5-flash",
    # The previous default was retired by NVIDIA on 2026-08-26 (HTTP 410).
    # This id is on NVIDIA's published free-endpoint list.
    ProviderKind.NVIDIA: "z-ai/glm-5.3-flash",
    ProviderKind.GROQ: "openai/gpt-oss-20b",
    ProviderKind.OPENROUTER: "openrouter/free",
}

#: OpenRouter's dynamic free router alias. It is accepted next to the historical
#: ``:free`` suffix because a pinned free model can be withdrawn at any time.
OPENROUTER_FREE_ROUTER = "openrouter/free"

#: Environment variable that carries each remote provider's model ID.
MODEL_ENV_VARS: dict[ProviderKind, str] = {
    ProviderKind.GEMINI: "GEMINI_MODEL",
    ProviderKind.NVIDIA: "NVIDIA_MODEL",
    ProviderKind.GROQ: "GROQ_MODEL",
    ProviderKind.OPENROUTER: "OPENROUTER_MODEL",
}


@dataclass(frozen=True, slots=True)
class RetiredModel:
    """A model ID a provider no longer serves, with its dated replacement."""

    kind: ProviderKind
    model: str
    retired_on: str
    replacement: str
    evidence: str


#: Retirements we have evidence for. Ordering is provider, newest retirement first.
RETIRED_MODELS: tuple[RetiredModel, ...] = (
    RetiredModel(
        # Observed directly by this repo's smoke step (run 44728029339):
        #   [nvidia] http 410 - The model 'meta/llama-3.3-70b-instruct' has
        #   reached its end of life on 2026-08-26T09:00:00Z
        # Note the trap this created: 3.1's recorded replacement was itself
        # already retired, so the registry pointed at a dead model and three
        # files kept defaulting to it.
        kind=ProviderKind.NVIDIA,
        model="meta/llama-3.3-70b-instruct",
        retired_on="2026-08-26",
        replacement="z-ai/glm-5.3-flash",
        evidence="NVIDIA NIM answers HTTP 410 Gone: 'reached its end of life on "
                 "2026-08-26T09:00:00Z and is no longer available'",
    ),
    RetiredModel(
        kind=ProviderKind.NVIDIA,
        model="meta/llama-3.1-8b-instruct",
        retired_on="2026-08-26",
        replacement="z-ai/glm-5.3-flash",
        evidence="NVIDIA NIM answers HTTP 410 Gone for this endpoint",
    ),
    RetiredModel(
        kind=ProviderKind.GROQ,
        model="llama-3.1-8b-instant",
        retired_on="2026-08-16",
        replacement="openai/gpt-oss-20b",
        evidence="Groq retired it for free and developer tiers; free keys answer HTTP 404",
    ),
    RetiredModel(
        kind=ProviderKind.GROQ,
        model="llama-3.3-70b-versatile",
        retired_on="2026-08-16",
        replacement="openai/gpt-oss-120b",
        evidence="Groq retired it for free and developer tiers; free keys answer HTTP 404",
    ),
    RetiredModel(
        kind=ProviderKind.GROQ,
        model="meta-llama/llama-4-scout-17b-16e-instruct",
        retired_on="2026-07-17",
        replacement="openai/gpt-oss-120b",
        evidence="Groq retired this preview model",
    ),
    RetiredModel(
        kind=ProviderKind.GROQ,
        model="qwen/qwen3-32b",
        retired_on="2026-07-17",
        replacement="openai/gpt-oss-120b",
        evidence="Groq retired this preview model",
    ),
)


def normalize_model_id(raw_model: str) -> str:
    """Return a comparison form of a model ID (trimmed, lower-case, no parameters)."""
    return raw_model.strip().lower().split(" ", 1)[0]


def find_retired_model(kind: ProviderKind, model: str) -> RetiredModel | None:
    """Return the retirement record for ``(kind, model)`` when the ID is dead."""
    norm = normalize_model_id(model)
    for entry in RETIRED_MODELS:
        if entry.kind == kind and entry.model == norm:
            return entry
    return None


def retirement_reason(entry: RetiredModel) -> str:
    """Return an operator-facing explanation naming the dated replacement."""
    return (
        f"{entry.kind.value} model {entry.model!r} was retired on {entry.retired_on} "
        f"({entry.evidence}); use {entry.replacement!r} instead."
    )


def is_openrouter_free_model(model: str) -> bool:
    """Return True for OpenRouter ``:free`` IDs and the dynamic free router alias."""
    norm = normalize_model_id(model)
    return norm.endswith(":free") or norm == OPENROUTER_FREE_ROUTER
