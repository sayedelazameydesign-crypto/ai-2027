"""CELIA persona: one identity, audience-scoped rights, honest capabilities."""

from __future__ import annotations

import pytest

from ai2027_protocol.celia_persona import (
    CELIA_CUSTOMER_PROMPT,
    CELIA_OWNER_FALLBACK,
    CELIA_OWNER_PROMPT,
    Audience,
    celia_system_prompt,
)

_OWNER_ONLY = "مالك المنظومة"


@pytest.mark.parametrize("prompt", [CELIA_OWNER_PROMPT, CELIA_CUSTOMER_PROMPT])
def test_both_audiences_share_one_identity_and_the_honesty_rules(prompt: str) -> None:
    assert "سيليا" in prompt
    assert "CELIA" in prompt
    # Never claims an action this channel cannot perform, never invents memory.
    assert "لا تدّعي أبداً أنك أرسلتِ" in prompt
    assert "لا تتذكرينه بدل اختلاقه" in prompt
    assert "كلمات مرور" in prompt  # no sensitive data requests


def test_owner_scope_is_personal_assistant_with_approval_gate() -> None:
    assert _OWNER_ONLY in CELIA_OWNER_PROMPT
    assert "بوابة الموافقة" in CELIA_OWNER_PROMPT


def test_customer_scope_never_carries_owner_context_or_invented_facts() -> None:
    assert _OWNER_ONLY not in CELIA_CUSTOMER_PROMPT
    assert "Celia Fashion" in CELIA_CUSTOMER_PROMPT
    assert "لا تخترعي" in CELIA_CUSTOMER_PROMPT
    assert "أكواد خصم" in CELIA_CUSTOMER_PROMPT


def test_unknown_audience_fails_closed_to_the_narrower_customer_scope() -> None:
    assert celia_system_prompt(Audience.OWNER) == CELIA_OWNER_PROMPT
    assert celia_system_prompt("owner") == CELIA_OWNER_PROMPT
    assert celia_system_prompt("customer") == CELIA_CUSTOMER_PROMPT
    assert celia_system_prompt("admin") == CELIA_CUSTOMER_PROMPT
    assert celia_system_prompt("") == CELIA_CUSTOMER_PROMPT


def test_prompts_stay_small_enough_for_the_local_model_context() -> None:
    # qwen3.5:0.8b runs with num_ctx=2048 on the 4GB profile; the persona must
    # leave room for the transcript and the reply.
    for prompt in (CELIA_OWNER_PROMPT, CELIA_CUSTOMER_PROMPT):
        assert len(prompt) < 1600


def test_fallback_is_in_celia_voice_and_honest() -> None:
    assert "سيليا" in CELIA_OWNER_FALLBACK
    assert "لم أصغ رداً" in CELIA_OWNER_FALLBACK
    assert "simulated" not in CELIA_OWNER_FALLBACK
