"""Hermetic unit tests for the shared channel conversation assistant.

These tests simulate the full receive -> transcript -> route -> reply path for
both WhatsApp and Telegram with **no network and no secrets**: providers and
senders are injected fakes, so every branch (free-provider selection, paid
block, no-provider fallback, idempotency, chunking, polling isolation) runs
bare in CI.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ai2027_protocol.budget import BudgetPolicy
from ai2027_protocol.channel_assistant import (
    ARABIC_NO_PROVIDER_FALLBACK,
    ARABIC_SYSTEM_PROMPT,
    Channel,
    ChannelAssistant,
    ChannelInboundMessage,
    MemoryConversationStore,
    SenderResult,
    TelegramReplySender,
    WhatsAppReplySender,
    _env_max_history_turns,
    build_assistant_router,
    chunk_text,
    parse_telegram_update,
)
from ai2027_protocol.contracts import (
    BillingTier,
    BudgetSnapshot,
    CompletionResponse,
    CostStatus,
    PromptRequest,
    ProviderKind,
)
from ai2027_protocol.router import FreeFirstRouter

#: Fake, non-secret value for the Telegram bot token in hermetic tests. Named
#: without a secret-looking identifier so bandit's hardcoded-password rules do
#: not fire on the test file.
_FAKE_BOT = "tok"


class _FakeProvider:
    """Scripted AIProvider: records every request, returns canned text."""

    def __init__(
        self,
        *,
        kind: ProviderKind = ProviderKind.GEMINI,
        model: str = "gemini-2.5-flash",
        billing_tier: BillingTier = BillingTier.FREE_TIER,
        text: str = "مرحباً، تفضل.",
        configured: bool = True,
    ) -> None:
        self.kind = kind
        self._model = model
        self.is_remote = True
        self.billing_tier = billing_tier
        self._text = text
        self._configured = configured
        self.requests: list[PromptRequest] = []
        self.calls = 0

    @property
    def model(self) -> str:
        return self._model

    def is_configured(self) -> bool:
        return self._configured

    async def complete(self, request: PromptRequest, *, timeout_s: float) -> str:
        self.calls += 1
        self.requests.append(request)
        return self._text


class _RecordingSender:
    """ReplySender that records (chat_id, text) pairs and reports ok."""

    def __init__(self, *, ok: bool = True, stage: str = "sent") -> None:
        self.ok = ok
        self.stage = stage
        self.sent: list[tuple[str, str]] = []

    async def send(self, chat_id: str, text: str) -> SenderResult:
        self.sent.append((chat_id, text))
        return SenderResult(ok=self.ok, stage=self.stage, chunks_sent=1)


def _router(providers: Sequence[_FakeProvider]) -> FreeFirstRouter:
    return FreeFirstRouter(
        providers=list(providers),
        budget_policy=BudgetPolicy(max_spend_usd=0.0),
    )


def _msg(
    *,
    channel: Channel = Channel.TELEGRAM,
    chat_id: str = "42",
    sender_id: str = "7",
    text: str = "أهلاً بك",
    message_id: str = "m-1",
) -> ChannelInboundMessage:
    return ChannelInboundMessage(
        channel=channel,
        chat_id=chat_id,
        sender_id=sender_id,
        text=text,
        message_id=message_id,
    )


# --------------------------------------------------------------------- routing
async def test_free_provider_selected_and_reply_delivered() -> None:
    provider = _FakeProvider(text="مرحباً، تفضل بطلبك.")
    router = _router([provider])
    sender = _RecordingSender()
    assistant = ChannelAssistant(router=router, sender=sender)

    reply = await assistant.handle_inbound(_msg())

    assert reply.stage == "replied"
    assert reply.provider == "gemini"
    assert reply.billing_tier == BillingTier.FREE_TIER
    assert reply.cost_usd == 0.0
    assert reply.chunks_sent == 1
    assert sender.sent == [("42", "مرحباً، تفضل بطلبك.")]
    assert provider.calls == 1
    # BudgetGuard recorded a declared free-tier call and spent nothing.
    assert reply.budget is not None
    assert reply.budget.declared_free_tier_calls == 1
    assert reply.budget.spent_usd == 0.0


async def test_arabic_system_prompt_and_user_text_are_sent() -> None:
    provider = _FakeProvider()
    assistant = ChannelAssistant(router=_router([provider]), sender=_RecordingSender())

    await assistant.handle_inbound(_msg(text="كيف أبدأ؟"))

    request = provider.requests[0]
    assert request.system_prompt == ARABIC_SYSTEM_PROMPT
    assert any(ord(c) > 127 for c in request.system_prompt)  # Arabic content
    assert "كيف أبدأ؟" in request.prompt


async def test_conversation_history_accumulates_across_turns() -> None:
    provider = _FakeProvider(text="الرد الأول")
    assistant = ChannelAssistant(router=_router([provider]), sender=_RecordingSender())

    await assistant.handle_inbound(_msg(text="السؤال الأول", message_id="m-1"))
    await assistant.handle_inbound(_msg(text="السؤال الثاني", message_id="m-2"))

    second = provider.requests[1]
    assert "السؤال الأول" in second.prompt
    assert "الرد الأول" in second.prompt
    assert "السؤال الثاني" in second.prompt


async def test_paid_provider_blocked_and_never_called() -> None:
    paid = _FakeProvider(
        kind=ProviderKind.GEMINI,
        model="paid-model",
        billing_tier=BillingTier.PAID,
        text="لا يُسمح به",
    )
    free = _FakeProvider(text="الرد المجاني")
    router = _router([paid, free])  # paid first, free second
    sender = _RecordingSender()
    assistant = ChannelAssistant(router=router, sender=sender)

    reply = await assistant.handle_inbound(_msg())

    assert reply.stage == "replied"
    assert reply.provider == "gemini"
    assert reply.cost_usd == 0.0
    assert paid.calls == 0  # never reached the paid provider
    assert free.calls == 1
    assert reply.budget is not None
    assert reply.budget.paid_blocked >= 1
    assert reply.budget.spent_usd == 0.0


async def test_no_free_provider_returns_deterministic_fallback() -> None:
    unconfigured = _FakeProvider(configured=False)
    router = _router([unconfigured])
    sender = _RecordingSender()
    assistant = ChannelAssistant(router=router, sender=sender)

    reply = await assistant.handle_inbound(_msg())

    assert reply.stage == "no_provider_available"
    assert reply.reply_text == ARABIC_NO_PROVIDER_FALLBACK
    assert reply.provider == "deterministic-fallback"
    assert reply.billing_tier == BillingTier.LOCAL
    assert reply.cost_usd == 0.0
    assert sender.sent == [("42", ARABIC_NO_PROVIDER_FALLBACK)]
    assert unconfigured.calls == 0


async def test_duplicate_message_id_is_not_reprocessed() -> None:
    provider = _FakeProvider()
    sender = _RecordingSender()
    assistant = ChannelAssistant(router=_router([provider]), sender=sender)
    message = _msg()

    first = await assistant.handle_inbound(message)
    second = await assistant.handle_inbound(message)

    assert first.stage == "replied"
    assert second.stage == "duplicate_message"
    assert provider.calls == 1
    assert len(sender.sent) == 1


async def test_empty_text_is_invalid_message() -> None:
    provider = _FakeProvider()
    sender = _RecordingSender()
    assistant = ChannelAssistant(router=_router([provider]), sender=sender)

    reply = await assistant.handle_inbound(_msg(text="   "))

    assert reply.stage == "invalid_message"
    assert provider.calls == 0
    assert sender.sent == []


async def test_send_failure_is_reported_as_stage() -> None:
    provider = _FakeProvider(text="رد")
    sender = _RecordingSender(ok=False, stage="not_ready")
    assistant = ChannelAssistant(router=_router([provider]), sender=sender)

    reply = await assistant.handle_inbound(_msg())

    assert reply.stage == "send_failed"
    assert reply.reply_text == "رد"
    assert reply.cost_usd == 0.0


# -------------------------------------------------------------------- senders
async def test_telegram_sender_chunks_long_reply() -> None:
    posted: list[dict[str, Any]] = []

    async def post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        posted.append(payload)
        return {"ok": True}

    sender = TelegramReplySender(
        bot_token=_FAKE_BOT,
        chat_id="42",
        post_fn=post,
    )
    long_text = "أ" * 10_000
    result = await sender.send("42", long_text)

    assert result.ok
    assert result.chunks_sent == 3  # ceil(10000 / 4096)
    assert len(posted) == 3
    assert all(len(p["text"]) <= 4096 for p in posted)


async def test_telegram_sender_never_calls_getupdates() -> None:
    seen_urls: list[str] = []

    async def post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        seen_urls.append(url)
        return {"ok": True}

    sender = TelegramReplySender(bot_token=_FAKE_BOT, chat_id="42", post_fn=post)
    await sender.send("42", "مرحبا")

    assert seen_urls, "expected a sendMessage call"
    assert all(url.endswith("/sendMessage") for url in seen_urls)
    assert not any("getUpdates" in url for url in seen_urls)


async def test_telegram_sender_fail_closed_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AI2027_ASSISTANT_TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("PA_APPROVAL_BOT_TOKEN", raising=False)
    sender = TelegramReplySender(bot_token="", chat_id="")

    result = await sender.send("42", "مرحبا")

    assert result.ok is False
    assert result.stage == "not_configured"


async def test_whatsapp_sender_chunks_and_wraps_send_fn() -> None:
    calls: list[tuple[str, str]] = []

    def send_fn(chat_id: str, text: str) -> dict[str, Any]:
        calls.append((chat_id, text))
        return {"ok": True, "stage": "sent"}

    sender = WhatsAppReplySender(send_fn)
    result = await sender.send("201100000000@c.us", "ب" * 9_000)

    assert result.ok
    assert result.chunks_sent == 3
    assert all(len(text) <= 4096 for _, text in calls)


async def test_whatsapp_sender_reports_stage_on_failure() -> None:
    def send_fn(chat_id: str, text: str) -> dict[str, Any]:
        return {"ok": False, "stage": "not_ready"}

    sender = WhatsAppReplySender(send_fn)
    result = await sender.send("201100000000@c.us", "مرحبا")

    assert result.ok is False
    assert result.stage == "not_ready"


async def test_assistant_uses_telegram_sender_chunks_for_long_reply() -> None:
    provider = _FakeProvider(text="أ" * 10_000)
    posted: list[dict[str, Any]] = []

    async def post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        posted.append(payload)
        return {"ok": True}

    sender = TelegramReplySender(bot_token=_FAKE_BOT, chat_id="42", post_fn=post)
    assistant = ChannelAssistant(router=_router([provider]), sender=sender)

    reply = await assistant.handle_inbound(_msg())

    assert reply.stage == "replied"
    assert reply.chunks_sent == 3
    assert len(posted) == 3


# ------------------------------------------------------------- telegram bridge
def test_parse_telegram_update_extracts_text_message() -> None:
    update = {
        "message": {
            "message_id": 123,
            "text": "مرحبا",
            "date": 1_700_000_000,
            "chat": {"id": 42},
            "from": {"id": 7},
        }
    }
    message = parse_telegram_update(update)
    assert message is not None
    assert message.channel == Channel.TELEGRAM
    assert message.chat_id == "42"
    assert message.sender_id == "7"
    assert message.text == "مرحبا"
    assert message.message_id == "123"
    assert message.timestamp_s == 1_700_000_000


def test_parse_telegram_update_ignores_approval_callback() -> None:
    # A callback_query belongs to the approval path — the assistant must ignore
    # it so it never advances the shared getUpdates offset.
    update = {"callback_query": {"id": "q1", "data": "appr:abc:grant"}}
    assert parse_telegram_update(update) is None


def test_parse_telegram_update_ignores_non_text_and_malformed() -> None:
    assert parse_telegram_update({"message": {"message_id": 1, "photo": []}}) is None
    assert parse_telegram_update({"message": {"text": "  ", "chat": {"id": 1}}}) is None
    assert parse_telegram_update({"message": {"text": "x"}}) is None  # no chat
    assert parse_telegram_update(None) is None


# -------------------------------------------------------------------- chunking
@given(st.text(min_size=1, max_size=200), st.integers(min_value=1, max_value=40))
@settings(max_examples=200)
def test_chunk_text_never_exceeds_limit(text: str, limit: int) -> None:
    chunks = chunk_text(text, limit)
    assert chunks
    assert all(len(c) <= limit for c in chunks)


def test_chunk_text_short_text_is_single_chunk() -> None:
    assert chunk_text("مرحبا", 4096) == ["مرحبا"]


# ----------------------------------------------------------- default router
def test_build_assistant_router_has_no_paid_provider() -> None:
    router = build_assistant_router()
    # AIProvider is a Protocol without billing_tier; the concrete providers all
    # carry it, so read it through a cast rather than the protocol surface.
    tiers = {cast(Any, p).billing_tier for p in router.providers}
    assert BillingTier.PAID not in tiers
    # Only local + policy-declared free-tier providers are in the chain.
    assert tiers <= {BillingTier.LOCAL, BillingTier.FREE_TIER}


def test_memory_store_is_bounded() -> None:
    store = MemoryConversationStore(max_turns=2)
    for i in range(5):
        store.append(Channel.TELEGRAM, "42", "user", f"u{i}")
    history = store.history(Channel.TELEGRAM, "42")
    assert len(history) == 2


# ------------------------------------------------- edge branches (coverage)
def test_memory_store_rejects_negative_turns() -> None:
    with pytest.raises(ValueError):
        MemoryConversationStore(max_turns=-1)


def test_chunk_text_rejects_non_positive_limit() -> None:
    with pytest.raises(ValueError):
        chunk_text("مرحبا", 0)


def test_chunk_text_hard_splits_oversized_word() -> None:
    # A single token with no whitespace must still be hard-split to the limit.
    chunks = chunk_text("أ" * 25, 10)
    assert [len(c) for c in chunks] == [10, 10, 5]


def test_whatsapp_sender_rejects_non_callable() -> None:
    with pytest.raises(TypeError):
        WhatsAppReplySender(send_fn="not-callable")  # type: ignore[arg-type]


async def test_whatsapp_sender_non_dict_result_is_send_failed() -> None:
    def send_fn(chat_id: str, text: str) -> dict[str, Any]:
        return "not-a-dict"  # type: ignore[return-value]

    sender = WhatsAppReplySender(send_fn)
    result = await sender.send("201100000000@c.us", "مرحبا")
    assert result.ok is False
    assert result.stage == "send_failed"


def test_parse_telegram_update_requires_chat_and_message_id() -> None:
    assert parse_telegram_update({"message": {"text": "x", "message_id": 1}}) is None
    assert parse_telegram_update({"message": {"text": "x", "chat": {"id": 1}}}) is None


async def test_paid_billing_tier_is_refused_defense_in_depth() -> None:
    # A router must never return a PAID result under MAX_SPEND=0, but if it did,
    # the assistant refuses to deliver rather than spending.
    paid_response = CompletionResponse(
        provider=ProviderKind.GEMINI,
        model="paid-model",
        text="should-not-be-sent",
        billing_tier=BillingTier.PAID,
        cost_status=CostStatus.PAID_BLOCKED,
        cost_known=False,
        cost_usd=None,
        trace_id="0123456789abcdef",
        attempts=(),
        latency_ms=0.0,
    )

    class _PaidRouterStub:
        def __init__(self) -> None:
            self._snap = BudgetSnapshot()

        async def route(self, request: PromptRequest) -> CompletionResponse:
            return paid_response

        def budget_snapshot(self) -> BudgetSnapshot:
            return self._snap

    sender = _RecordingSender()
    assistant = ChannelAssistant(router=cast(FreeFirstRouter, _PaidRouterStub()), sender=sender)
    reply = await assistant.handle_inbound(_msg())

    assert reply.stage == "paid_not_allowed"
    assert reply.reply_text == ""
    assert reply.billing_tier == BillingTier.PAID
    assert sender.sent == []  # nothing was delivered


async def test_telegram_default_httpx_transport(monkeypatch: pytest.MonkeyPatch) -> None:

    posted: list[dict[str, Any]] = []

    class _FakeResponse:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"ok": True}

    class _FakeClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        async def post(self, url: str, json: dict[str, Any]) -> _FakeResponse:
            posted.append(json)
            return _FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", _FakeClient)
    sender = TelegramReplySender(bot_token=_FAKE_BOT, chat_id="42")
    result = await sender.send("42", "مرحبا")

    assert result.ok
    assert result.chunks_sent == 1
    assert posted[0]["text"] == "مرحبا"


async def test_telegram_default_httpx_maps_error_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    class _FakeResponse:
        status_code = 500

        def json(self) -> dict[str, Any]:
            return {"ok": False}

    class _FakeClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        async def post(self, url: str, json: dict[str, Any]) -> _FakeResponse:
            return _FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", _FakeClient)
    sender = TelegramReplySender(bot_token=_FAKE_BOT, chat_id="42")
    result = await sender.send("42", "مرحبا")

    assert result.ok is False
    assert result.stage == "send_failed"


def test_env_max_history_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    from ai2027_protocol.channel_assistant import DEFAULT_MAX_HISTORY_TURNS

    monkeypatch.delenv("AI2027_ASSISTANT_MAX_HISTORY_TURNS", raising=False)
    assert _env_max_history_turns() == DEFAULT_MAX_HISTORY_TURNS

    monkeypatch.setenv("AI2027_ASSISTANT_MAX_HISTORY_TURNS", "12")
    assert _env_max_history_turns() == 12

    monkeypatch.setenv("AI2027_ASSISTANT_MAX_HISTORY_TURNS", "not-a-number")
    assert _env_max_history_turns() == DEFAULT_MAX_HISTORY_TURNS

    monkeypatch.setenv("AI2027_ASSISTANT_MAX_HISTORY_TURNS", "-3")
    assert _env_max_history_turns() == DEFAULT_MAX_HISTORY_TURNS


# ---------------------------------------------------------------------------
# Production-wiring guarantees (deterministic echo, history state, drafts)
# ---------------------------------------------------------------------------


async def test_default_chain_deterministic_echo_is_never_delivered() -> None:
    """With no free model answering, the default chain ends in the simulated
    DeterministicFallbackProvider; its debug echo must never reach a person."""
    from ai2027_protocol.ai_providers import DeterministicFallbackProvider

    sender = _RecordingSender()
    router = build_assistant_router(
        providers=(_FakeProvider(configured=False), DeterministicFallbackProvider())
    )
    assistant = ChannelAssistant(router=router, sender=sender)

    reply = await assistant.handle_inbound(_msg(text="ما هي عاصمة مصر؟"))

    assert reply.stage == "no_provider_available"
    assert sender.sent == [("42", ARABIC_NO_PROVIDER_FALLBACK)]
    assert "deterministic-offline-fallback" not in sender.sent[0][1]
    assert "simulated" not in sender.sent[0][1]


async def test_blank_model_text_falls_back_instead_of_sending_empty() -> None:
    sender = _RecordingSender()
    assistant = ChannelAssistant(router=_router([_FakeProvider(text="   ")]), sender=sender)

    reply = await assistant.handle_inbound(_msg())

    assert reply.stage == "no_provider_available"
    assert sender.sent == [("42", ARABIC_NO_PROVIDER_FALLBACK)]


async def test_fallback_notice_is_not_kept_in_history() -> None:
    store = MemoryConversationStore(max_turns=8)
    assistant = ChannelAssistant(
        router=_router([_FakeProvider(configured=False)]),
        sender=_RecordingSender(),
        store=store,
    )

    await assistant.handle_inbound(_msg(text="سؤال"))

    assert store.history(Channel.TELEGRAM, "42") == [("user", "سؤال")]
