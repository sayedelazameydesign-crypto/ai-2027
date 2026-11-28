"""CELIA persona: one identity, five modes, audience-scoped rights, honest limits."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ai2027_protocol.celia_persona import (
    CELIA_CUSTOMER_PROMPT,
    CELIA_OWNER_FALLBACK,
    CELIA_OWNER_PROMPT,
    CHANNEL_AUTONOMY,
    IDENTITY,
    MEMORY_BLOCK_MAX_CHARS,
    Audience,
    AutonomyLevel,
    CeliaMode,
    celia_system_prompt,
    detect_mode,
    mode_label,
    render_system_prompt,
)

_OWNER_ONLY = "مالك المنظومة"
_ALL_PROMPTS = [render_system_prompt(Audience.OWNER, m) for m in CeliaMode] + [
    CELIA_CUSTOMER_PROMPT
]


@pytest.mark.parametrize("prompt", _ALL_PROMPTS)
def test_every_mode_and_audience_keeps_one_identity_and_the_honesty_rules(prompt: str) -> None:
    assert "سيليا" in prompt
    assert "CELIA" in prompt
    assert IDENTITY["principle"] in prompt
    assert "لا تدّعي أبداً أنك أرسلتِ" in prompt  # never claims an action
    assert "لا تتذكرينه بدل اختلاقه" in prompt  # never invents memories
    assert "كلمات مرور" in prompt  # never asks for secrets
    assert "لا تدّعي وعياً" in prompt  # a program, not a person


def test_identity_sheet_is_immutable() -> None:
    with pytest.raises(TypeError):
        IDENTITY["name"] = "someone else"  # type: ignore[index]


def test_owner_scope_is_l0_personal_assistant_with_approval_gate() -> None:
    assert CHANNEL_AUTONOMY is AutonomyLevel.L0_CONVERSATION
    assert _OWNER_ONLY in CELIA_OWNER_PROMPT
    assert "L0" in CELIA_OWNER_PROMPT
    assert "بوابة الموافقة" in CELIA_OWNER_PROMPT
    assert "«تذكّري: …»" in CELIA_OWNER_PROMPT  # tells him how memory works


def test_customer_scope_never_carries_owner_context_or_memories() -> None:
    prompt = render_system_prompt(Audience.CUSTOMER, CeliaMode.DEVELOPER, ["سر المالك"])
    assert prompt == CELIA_CUSTOMER_PROMPT  # mode and memories are ignored
    assert "سر المالك" not in prompt
    assert _OWNER_ONLY not in prompt
    assert "Celia Fashion" in prompt
    assert "لا تخترعي" in prompt


def test_unknown_audience_fails_closed_to_the_narrower_customer_scope() -> None:
    assert celia_system_prompt(Audience.OWNER) == CELIA_OWNER_PROMPT
    assert celia_system_prompt("owner") == CELIA_OWNER_PROMPT
    for value in ("customer", "admin", ""):
        assert celia_system_prompt(value) == CELIA_CUSTOMER_PROMPT
    assert "سر" not in render_system_prompt("admin", memories=["سر"])


@pytest.mark.parametrize(
    ("text", "mode"),
    [
        ("صلّحي الكود ده", CeliaMode.DEVELOPER),
        ("Fix this bug in the repo", CeliaMode.DEVELOPER),
        ("افتحي PR للتعديل", CeliaMode.DEVELOPER),
        ("رتّبي أولويات هذا الأسبوع", CeliaMode.EXECUTIVE),
        ("سيليا، كمّلنا فين؟", CeliaMode.EXECUTIVE),
        ("ابحثي عن أحدث الأبحاث في هذا المجال", CeliaMode.INTELLIGENCE),
        ("compare these two options", CeliaMode.INTELLIGENCE),
        ("فكّري معي في فكرة منتج جديد", CeliaMode.CREATIVE),
        ("ازيك يا سيليا", CeliaMode.COMPANION),
        ("latest news?", CeliaMode.COMPANION),  # "test" must not hit "latest"
        ("وضع المطوّرة: كلميني عن يومك", CeliaMode.DEVELOPER),  # explicit wins
        ("mode creative please", CeliaMode.CREATIVE),
    ],
)
def test_mode_detection_is_deterministic_and_bilingual(text: str, mode: CeliaMode) -> None:
    assert detect_mode(text) is mode


def test_each_mode_changes_how_she_works_not_who_she_is() -> None:
    prompts = {m: render_system_prompt(Audience.OWNER, m) for m in CeliaMode}
    assert len(set(prompts.values())) == len(CeliaMode)
    for mode, prompt in prompts.items():
        assert mode_label(mode) in prompt
    assert "نقاط قوتها وضعفها" in prompts[CeliaMode.CREATIVE]  # not just cheering
    assert "لا تملكين بحثاً على الإنترنت" in prompts[CeliaMode.INTELLIGENCE]


def test_memory_block_is_bounded_for_the_local_model_context() -> None:
    memories = [f"#{i} معلومة طويلة " + "س" * 200 for i in range(40)]
    prompt = render_system_prompt(Audience.OWNER, CeliaMode.EXECUTIVE, memories)
    base = render_system_prompt(Audience.OWNER, CeliaMode.EXECUTIVE)
    assert "#0 معلومة" in prompt
    assert len(prompt) - len(base) <= MEMORY_BLOCK_MAX_CHARS + 120
    for p in _ALL_PROMPTS:  # qwen3.5:0.8b runs with num_ctx=2048
        assert len(p) < 2000


@settings(max_examples=200, deadline=None)
@given(st.text(max_size=300))
def test_mode_detection_never_raises(text: str) -> None:
    assert isinstance(detect_mode(text), CeliaMode)


def test_fallback_is_in_celia_voice_and_honest() -> None:
    assert "سيليا" in CELIA_OWNER_FALLBACK
    assert "لم أصغ رداً" in CELIA_OWNER_FALLBACK
    assert "simulated" not in CELIA_OWNER_FALLBACK
