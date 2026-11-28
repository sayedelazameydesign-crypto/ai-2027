"""CELIA's consented memory: explicit commands, no secrets, durable, reversible."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ai2027_protocol.celia_personal_memory import (
    CommandKind,
    ListMemoryBackend,
    MemoryCommand,
    MemoryKind,
    PersonalMemory,
    classify_kind,
    looks_sensitive,
    parse_memory_command,
)

_NOW = "2026-11-28T10:00:00Z"


def _memory(rows: object = None, **kw: int) -> PersonalMemory:
    return PersonalMemory(ListMemoryBackend(rows), clock=lambda: _NOW, **kw)


def _say(memory: PersonalMemory, text: str) -> str:
    command = parse_memory_command(text)
    assert command is not None, text
    return memory.execute_durably(command, source="telegram:1").stage


@pytest.mark.parametrize(
    ("text", "kind", "argument"),
    [
        ("تذكّري: أفضّل الردود القصيرة صباحاً", CommandKind.REMEMBER, "أفضّل الردود القصيرة صباحاً"),
        (
            "سيليا، تذكري أن اجتماع المستثمرين الخميس",
            CommandKind.REMEMBER,
            "اجتماع المستثمرين الخميس",
        ),
        ("إحفظي: الإصدار القادم 2.0", CommandKind.REMEMBER, "الإصدار القادم 2.0"),
        ("remember: I prefer English for code", CommandKind.REMEMBER, "I prefer English for code"),
        ("انسي رقم 3", CommandKind.FORGET, "3"),
        ("انسي ٢", CommandKind.FORGET, "2"),
        ("forget #7", CommandKind.FORGET, "7"),
        ("انسي: الخميس", CommandKind.FORGET, "الخميس"),
        ("ماذا تتذكرين؟", CommandKind.LIST, ""),
        ("سيليا ماذا تتذكرين", CommandKind.LIST, ""),
        ("What do you remember?", CommandKind.LIST, ""),
    ],
)
def test_explicit_commands_are_recognised_and_keep_the_owner_spelling(
    text: str, kind: CommandKind, argument: str
) -> None:
    assert parse_memory_command(text) == MemoryCommand(kind, argument)


@pytest.mark.parametrize(
    "text",
    [
        "تذكري لما كنا بنشتغل على المشروع؟",  # a question, not a save
        "تذكريني بكرة",  # "remind me"
        "انسي الموضوع ده",  # chat, not a delete
        "انسيت أقول لك",  # "I forgot"
        "ازيك يا سيليا",
        "",
    ],
)
def test_ordinary_chat_never_writes_or_deletes_memory(text: str) -> None:
    assert parse_memory_command(text) is None


@pytest.mark.parametrize(
    ("text", "sensitive"),
    [
        ("كلمة المرور hunter2", True),
        ("رمز التحقق 482913", True),
        ("4111 1111 1111 1111", True),
        ("ghp_abcdefghijklmnop1234", True),
        ("AIzaSyA_abcdefghijklmnop", True),
        ("EG380019000500000000263180002", True),
        ("أفضّل القهوة بدون سكر", False),
        ("spin the wheel", False),
        ("رقم تليفوني 01012345678", False),
    ],
)
def test_sensitive_detection(text: str, sensitive: bool) -> None:
    assert looks_sensitive(text) is sensitive


def test_secrets_are_refused_never_stored() -> None:
    memory = _memory()
    assert _say(memory, "احفظي: كلمة السر hunter2") == "memory_refused_sensitive"
    assert len(memory) == 0


def test_save_list_forget_cycle_with_source_date_and_layer() -> None:
    memory = _memory()
    assert _say(memory, "تذكّري: أفضّل الردود القصيرة صباحاً") == "memory_saved"
    assert _say(memory, "تذكّري: مشروع AI-2027 هدفه منظومة وكلاء مجانية") == "memory_saved"
    assert _say(memory, "تذكّري: أفضل الردود القصيرة صباحا") == "memory_duplicate"
    first, second = memory.records
    assert (first.id, first.kind, first.source, first.created_at) == (
        1,
        MemoryKind.PERSONAL,
        "telegram:1",
        _NOW,
    )
    assert second.kind is MemoryKind.PROJECT
    listing = memory.describe().reply
    assert "1. [شخصي · 2026-11-28]" in listing
    assert listing.index("2.") < listing.index("1.")  # newest first
    assert _say(memory, "انسي رقم 1") == "memory_forgotten"
    assert [r.id for r in memory.records] == [2]
    assert _say(memory, "تذكّري: معلومة جديدة") == "memory_saved"
    assert memory.records[-1].id == 3  # ids are never reused


def test_forget_needs_one_match_and_reports_honestly() -> None:
    memory = _memory()
    _say(memory, "تذكّري: اجتماع الخميس مع الفريق")
    _say(memory, "تذكّري: مراجعة الخميس للتصميم")
    assert _say(memory, "انسي: الخميس") == "memory_ambiguous"
    assert _say(memory, "انسي رقم 99") == "memory_not_found"
    assert _say(memory, "انسي: التصميم") == "memory_forgotten"
    assert len(memory) == 1


def test_limits_are_explained_not_silently_applied() -> None:
    memory = _memory(max_records=1)
    assert _say(memory, "تذكّري: أولى") == "memory_saved"
    assert _say(memory, "تذكّري: ثانية") == "memory_full"
    assert _say(memory, "تذكّري: " + "ط" * 600) == "memory_too_long"
    assert memory.remember("   ", source="x").stage == "memory_empty"
    assert "فارغة" in _memory().describe().reply


def test_memory_survives_a_restart() -> None:
    """Acceptance: a saved fact is recalled after the process restarts."""
    backend = ListMemoryBackend()
    first = PersonalMemory(backend, clock=lambda: _NOW)
    first.execute_durably(
        MemoryCommand(CommandKind.REMEMBER, "قررنا عدم تعديل Kernel في مشروع CELIA"),
        source="telegram:9",
    )
    restarted = PersonalMemory(ListMemoryBackend(backend.load()))
    assert restarted.records[0].text == "قررنا عدم تعديل Kernel في مشروع CELIA"
    assert restarted.recall("هل نعدل الـ Kernel؟", k=1)[0].startswith("#1 (مشروع")


def test_a_failed_write_is_rolled_back_and_admitted() -> None:
    class _Broken(ListMemoryBackend):
        def save(self, rows: list[dict[str, object]]) -> None:
            raise OSError("sheet unavailable")

    memory = PersonalMemory(_Broken(), clock=lambda: _NOW)
    outcome = memory.execute_durably(
        MemoryCommand(CommandKind.REMEMBER, "معلومة"), source="telegram:1"
    )
    assert outcome.stage == "memory_store_failed"
    assert "لم يتغير شيء" in outcome.reply
    assert len(memory) == 0


def test_recall_ranks_relevance_and_falls_back_to_recent_context() -> None:
    memory = _memory()
    for fact in (
        "أفضّل القهوة صباحاً",
        "مشروع AI-2027: المرحلة التالية هي الذاكرة الدائمة",
        "طريقة النشر: ادمج ثم شغّل الفحص",
    ):
        _say(memory, f"تذكّري: {fact}")
    assert "المرحلة التالية" in memory.recall("ما المرحلة التالية في مشروع AI-2027؟", k=1)[0]
    recent = memory.recall("كمّلنا فين؟", k=2)  # no word overlap
    assert recent[0].startswith("#3") and recent[1].startswith("#2")
    assert memory.recall("أي شيء", k=0) == []
    assert _memory().recall("أي شيء") == []


def test_malformed_stored_rows_are_skipped_not_fatal() -> None:
    rows = [
        {"id": "1", "kind": "personal", "text": "سليم", "source": "s", "created_at": _NOW},
        {"id": "x", "kind": "personal", "text": "تالف", "source": "s", "created_at": _NOW},
        {"id": "2", "kind": "alien", "text": "تالف", "source": "s", "created_at": _NOW},
        "junk",
    ]
    memory = _memory(rows)
    assert [r.text for r in memory.records] == ["سليم"]
    assert memory.skipped_rows == 2
    assert memory.flush() is False  # loading alone never writes


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("طريقة النشر: ادمج ثم شغل الفحص", MemoryKind.PROCEDURAL),
        ("قرار المشروع: لا مزود مدفوع", MemoryKind.PROJECT),
        ("أحب الردود المختصرة", MemoryKind.PERSONAL),
        ("عاصمة اليابان طوكيو", MemoryKind.SEMANTIC),
    ],
)
def test_layer_classification(text: str, kind: MemoryKind) -> None:
    assert classify_kind(text) is kind


@settings(max_examples=200, deadline=None)
@given(st.text(max_size=400))
def test_parser_never_raises_and_saved_facts_are_never_sensitive(text: str) -> None:
    memory = _memory()
    command = parse_memory_command(text)
    if command is not None:
        memory.execute_durably(command, source="fuzz")
    for record in memory.records:
        assert not looks_sensitive(record.text)
