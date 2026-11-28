"""Hermetic tests for the runtime adapters that put the assistant online.

Covers what ``scripts/approval_bot_ci.py`` (Telegram) and
``scripts/free_stack_runner.py`` (WhatsApp drafts) rely on: the bounded
transcript snapshot carried between stateless cycles, and the draft-only
WhatsApp generator that never sends, never raises and never stalls the
webhook. No network, no secrets.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from ai2027_protocol.budget import BudgetPolicy
from ai2027_protocol.channel_assistant import (
    ARABIC_NO_PROVIDER_FALLBACK,
    ARABIC_SYSTEM_PROMPT,
    Channel,
    ChannelAssistant,
)
from ai2027_protocol.channel_assistant_runtime import (
    DraftCaptureSender,
    SnapshotConversationStore,
    make_whatsapp_draft_generator,
)
from ai2027_protocol.contracts import BillingTier, PromptRequest, ProviderKind
from ai2027_protocol.router import FreeFirstRouter


class _FakeProvider:
    """Scripted free-tier provider: records requests, returns canned text."""

    def __init__(self, *, text: str = "مرحباً، تفضل.") -> None:
        self.kind = ProviderKind.GEMINI
        self.model = "gemini-2.5-flash"
        self.is_remote = True
        self.billing_tier = BillingTier.FREE_TIER
        self._text = text
        self.requests: list[PromptRequest] = []
        self.calls = 0

    def is_configured(self) -> bool:
        return True

    async def complete(self, request: PromptRequest, *, timeout_s: float) -> str:
        self.calls += 1
        self.requests.append(request)
        return self._text


def _router(providers: Sequence[_FakeProvider]) -> FreeFirstRouter:
    return FreeFirstRouter(
        providers=list(providers),
        budget_policy=BudgetPolicy(max_spend_usd=0.0),
    )


def test_store_snapshot_restore_roundtrip_and_bounds() -> None:
    store = SnapshotConversationStore(max_turns=4)
    store.append(Channel.TELEGRAM, "1", "user", "a" * 10)
    store.append(Channel.TELEGRAM, "1", "assistant", "b")
    store.append(Channel.WHATSAPP, "2", "user", "c")

    snap = store.snapshot(max_chars=5)
    restored = SnapshotConversationStore(max_turns=4)
    assert restored.restore(snap) == 2
    assert restored.history(Channel.TELEGRAM, "1") == [("user", "aaaaa"), ("assistant", "b")]
    assert restored.history(Channel.WHATSAPP, "2") == [("user", "c")]
    assert len(store.snapshot(max_chats=1)) == 1


def test_store_restore_skips_malformed_entries() -> None:
    store = SnapshotConversationStore(max_turns=2)
    loaded = store.restore(
        [
            "junk",
            {"channel": "sms", "chat_id": "1", "turns": []},
            {"channel": "telegram", "chat_id": "", "turns": []},
            {"channel": "telegram", "chat_id": "9", "turns": "x"},
            {
                "channel": "telegram",
                "chat_id": "5",
                "turns": [["user", "1"], ["system", "evil"], ["assistant", "2"], ["user", "3"]],
            },
        ]
    )
    assert loaded == 1
    # "system" turns are dropped; the turn budget keeps the newest two.
    assert store.history(Channel.TELEGRAM, "5") == [("assistant", "2"), ("user", "3")]
    assert store.restore({"not": "a list"}) == 0


class _Event:
    def __init__(self, text: str = "عندي سؤال عن المقاسات", wamid: str = "wamid.ABCD1234") -> None:
        self.wamid = wamid
        self.sender_wa_id = "201012345678"
        self.text_body = text
        self.timestamp_s = 1_795_878_000


def test_whatsapp_draft_generator_drafts_without_sending() -> None:
    provider = _FakeProvider(text="المقاسات من S إلى XL.")
    capture = DraftCaptureSender()
    assistant = ChannelAssistant(router=_router([provider]), sender=capture)
    generate = make_whatsapp_draft_generator(assistant, capture)

    draft = generate(_Event(), None)

    assert draft == "المقاسات من S إلى XL."
    assert provider.calls == 1
    assert capture.drafts == {}  # consumed; nothing was delivered anywhere
    assert ARABIC_SYSTEM_PROMPT == provider.requests[0].system_prompt


def test_whatsapp_draft_generator_never_raises_and_uses_default() -> None:
    class _Boom(_FakeProvider):
        async def complete(self, request: PromptRequest, *, timeout_s: float) -> str:
            raise RuntimeError("provider exploded")

    capture = DraftCaptureSender()
    assistant = ChannelAssistant(router=_router([_Boom()]), sender=capture)
    generate = make_whatsapp_draft_generator(assistant, capture, on_error=lambda ev: "DEFAULT")
    # Provider failure -> no free model -> the orchestrator's normal reply,
    # never the "assistant unavailable" notice in a customer draft.
    assert generate(_Event(), None) == "DEFAULT"
    # Without on_error the Arabic $0 notice is the last resort.
    bare = make_whatsapp_draft_generator(
        ChannelAssistant(router=_router([_Boom()]), sender=capture), capture
    )
    assert bare(_Event(wamid="wamid.BARE0001"), None) == ARABIC_NO_PROVIDER_FALLBACK
    # A malformed event (empty wamid fails validation) -> orchestrator default.
    assert generate(_Event(wamid=""), None) == "DEFAULT"
    # A replayed wamid is a duplicate -> orchestrator default, never a resend.
    first = make_whatsapp_draft_generator(
        ChannelAssistant(router=_router([_FakeProvider()]), sender=capture),
        capture,
        on_error=lambda ev: "DEFAULT",
    )
    assert first(_Event(wamid="wamid.SAME0001"), None) == "مرحباً، تفضل."
    assert first(_Event(wamid="wamid.SAME0001"), None) == "DEFAULT"


def test_whatsapp_draft_generator_times_out_to_default() -> None:
    class _Slow(_FakeProvider):
        async def complete(self, request: PromptRequest, *, timeout_s: float) -> str:
            await asyncio.sleep(5)
            return "late"

    capture = DraftCaptureSender()
    assistant = ChannelAssistant(router=_router([_Slow()]), sender=capture)
    generate = make_whatsapp_draft_generator(
        assistant, capture, timeout_s=0.05, on_error=lambda ev: "DEFAULT"
    )
    assert generate(_Event(), None) == "DEFAULT"
