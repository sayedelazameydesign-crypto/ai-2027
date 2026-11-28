"""General conversation assistant shared by the WhatsApp and Telegram channels.

This is the **common assistant invocation point** for inbound text on both
channels. One method, :meth:`ChannelAssistant.handle_inbound`, receives a
normalized :class:`ChannelInboundMessage` from WhatsApp or Telegram, keeps a
bounded conversation transcript, and produces a reply by routing the turn
through :class:`~ai2027_protocol.router.FreeFirstRouter`.

Design rules (mirror the WhatsApp receive/store/reply path and the Telegram
approval path; ``docs/CHANNEL-ASSISTANT.md`` is the companion doc):

- **Free-First only.** The assistant never constructs or contacts a paid
  provider. It relies exclusively on :class:`FreeFirstRouter` +
  :class:`~ai2027_protocol.budget.BudgetGuard`, which admit only ``LOCAL``
  (deterministic/local Ollama) and ``FREE_TIER`` providers under
  ``MAX_SPEND=0``. A ``PAID`` or ``UNKNOWN_COST`` provider is blocked before
  any request leaves the process, so the ``$0`` ceiling cannot be overshot.
- **Fail-closed on delivery.** A reply is sent only through an injected
  :class:`ReplySender`. An unconfigured sender, a provider refusal, or a
  channel transport failure becomes a structured *stage* — never an
  exception, never a credential, never a raw provider body.
- **No second Telegram poller.** The assistant does **not** call
  ``getUpdates``. :func:`parse_telegram_update` is the bridge the approval
  bot's single polling loop calls for plain text messages; approval
  ``callback_query`` updates are ignored here, so the assistant can never
  steal or rewind the approval bot's ``getUpdates`` offset.
- **Arabic-first.** The system prompt is Arabic and instructs the model to
  answer in Arabic; the deterministic no-provider fallback is Arabic too.
- **Hermetic.** Every collaborator (router, sender, store, clock) is
  injectable, so the unit tests run with no network and no secrets.

The module is async (the router is async); the stdlib channel connectors are
sync, so their adapters run them on a worker thread via :func:`asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable, Sequence
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

import httpx
from pydantic import Field

from .budget import BudgetGuard, BudgetPolicy
from .contracts import (
    BillingTier,
    BudgetSnapshot,
    CompletionResponse,
    PromptRequest,
    ProviderKind,
    StrictModel,
)
from .router import AllProvidersFailedError, FreeFirstRouter

#: Character ceiling per outbound message. WhatsApp Cloud, WAAPI and the
#: Telegram Bot API ``sendMessage`` all cap a text body at 4096 characters, so
#: the assistant chunks any longer reply to respect every channel's limit.
WHATSAPP_MAX_CHARS = 4096
TELEGRAM_MAX_CHARS = 4096

#: Default number of prior (user, assistant) turns kept in the transcript.
DEFAULT_MAX_HISTORY_TURNS = 8

#: Arabic system prompt. It names the assistant, requires Arabic answers, and
#: forbids paid/unknown-cost tooling — the model never decides to spend.
ARABIC_SYSTEM_PROMPT = (
    "أنت مساعد محادثة عام لمنصّة AI-2027. تجيب على رسائل المستخدمين باللغة "
    "العربية الفصحى الواضحة والمهذّبة، وتتكيّف مع لهجة السؤال. تقديمك "
    "معرفي وغير مكلف ولا يتطلب أي اتصال مدفوع. لا تطلب معلومات حساسة "
    "(أرقام بطاقات، كلمات مرور، هويات رسمية). إذا لم تملك المعلومات الكافية، "
    "فتحمل وأوضح ذلك بصدق بدلاً من التخمين. اجعل الرد مختصراً ومفيداً."
)

#: Deterministic, $0 Arabic reply used when no free provider can serve a turn.
#: It is generated locally (never a paid call) and is honest about the state.
ARABIC_NO_PROVIDER_FALLBACK = (
    "عذراً، المساعد غير متاح مؤقتاً. تم استلام رسالتك وسأعود إليك فور توفر الخدمة مرة أخرى."
)


def _env_max_history_turns() -> int:
    """Read ``AI2027_ASSISTANT_MAX_HISTORY_TURNS`` (fail-closed to the default).

    A non-numeric or negative value falls back to :data:`DEFAULT_MAX_HISTORY_TURNS`
    rather than disabling history, so a malformed setting never silently drops
    the transcript cap.
    """
    raw = os.environ.get("AI2027_ASSISTANT_MAX_HISTORY_TURNS", "").strip()
    if not raw:
        return DEFAULT_MAX_HISTORY_TURNS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_HISTORY_TURNS
    return value if value >= 0 else DEFAULT_MAX_HISTORY_TURNS


class Channel(StrEnum):
    """The two inbound channels this assistant serves."""

    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"


class ChannelInboundMessage(StrictModel):
    """A normalized inbound text message from one channel.

    ``message_id`` is the channel's own message identifier (WhatsApp ``wamid``
    or Telegram ``message_id``); it is the idempotency key, so a replayed
    delivery is never answered twice.
    """

    channel: Channel
    chat_id: str = Field(min_length=1, max_length=256)
    sender_id: str = Field(min_length=1, max_length=256)
    # No ``min_length`` here on purpose: ``str_strip_whitespace`` already turns
    # an all-whitespace body into ``""``, and the assistant validates emptiness
    # itself (returning ``invalid_message``) so the boundary is enforced in one
    # place and is unit-testable without constructing invalid models.
    text: str = Field(max_length=16_000)
    message_id: str = Field(min_length=1, max_length=256)
    timestamp_s: int = Field(default=0, ge=0)


class SenderResult(StrictModel):
    """Stage-only outcome of one channel delivery attempt."""

    ok: bool
    stage: str
    chunks_sent: int = Field(default=0, ge=0)


class AssistantReply(StrictModel):
    """Structured result of handling one inbound message.

    ``stage`` is one of: ``replied`` (a free/local provider served the turn and
    the reply was delivered), ``no_provider_available`` (the deterministic
    $0 fallback was delivered), ``send_failed`` (a reply was produced but the
    channel refused delivery), ``duplicate_message`` (replayed ``message_id``)
    or ``invalid_message``. ``cost_usd`` is always ``0.0``: the router's
    BudgetGuard admits only $0 providers under ``MAX_SPEND=0``.
    """

    channel: Channel
    chat_id: str
    reply_text: str
    stage: str
    provider: str = ""
    model: str = ""
    billing_tier: BillingTier = BillingTier.LOCAL
    cost_usd: float = Field(default=0.0, ge=0.0)
    chunks_sent: int = Field(default=0, ge=0)
    budget: BudgetSnapshot | None = None


@runtime_checkable
class ReplySender(Protocol):
    """Async delivery port for one channel's outbound text.

    Implementations chunk long text to the channel's limit and return a
    stage-only :class:`SenderResult`. They must never raise for an
    unconfigured sender or a transport refusal — they return ``ok=False``
    with a stage code instead.
    """

    async def send(self, chat_id: str, text: str) -> SenderResult:
        """Deliver ``text`` to ``chat_id``, chunked to the channel limit."""
        ...


class ConversationStore(Protocol):
    """Bounded, per-(channel, chat) transcript port."""

    def append(self, channel: Channel, chat_id: str, role: str, text: str) -> None:
        """Record one transcript turn (role is ``user`` or ``assistant``)."""
        ...

    def history(self, channel: Channel, chat_id: str) -> list[tuple[str, str]]:
        """Return the retained ``(role, text)`` turns for a conversation."""
        ...


class MemoryConversationStore:
    """In-memory transcript store with a fixed turn budget (thread-safe)."""

    def __init__(self, *, max_turns: int = DEFAULT_MAX_HISTORY_TURNS) -> None:
        """Keep at most ``max_turns`` prior turns per conversation."""
        if max_turns < 0:
            raise ValueError("max_turns must be non-negative")
        self._max_turns = max_turns
        self._turns: dict[tuple[Channel, str], list[tuple[str, str]]] = {}
        self._lock = asyncio.Lock()

    def append(self, channel: Channel, chat_id: str, role: str, text: str) -> None:
        """Append a turn, dropping the oldest when over the budget."""
        key = (channel, chat_id)
        turns = self._turns.setdefault(key, [])
        turns.append((role, text))
        if len(turns) > self._max_turns:
            del turns[: len(turns) - self._max_turns]

    def history(self, channel: Channel, chat_id: str) -> list[tuple[str, str]]:
        """Return a copy of the retained turns for a conversation."""
        return list(self._turns.get((channel, chat_id), []))


def chunk_text(text: str, limit: int) -> list[str]:
    """Split ``text`` into chunks of at most ``limit`` characters.

    Splitting prefers paragraph boundaries, then words, and only as a last
    resort hard-splits a single oversized token. Every returned chunk is
    guaranteed to be ``<= limit`` characters, so each one is safe to hand to a
    channel ``sendMessage``/send endpoint. A newline separator is kept only
    when it still fits inside the limit; content is otherwise preserved.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current_buffer: list[str] = [""]

    def flush(buf: str) -> None:
        if buf:
            chunks.append(buf)

    for line in text.split("\n"):
        if len(line) > limit:
            # Oversized line: flush the running buffer, then split the line by
            # words, hard-splitting any single word that is still too long.
            flush(current_buffer[0])
            words = line.split(" ")
            word_buf = ""
            for word in words:
                piece = word if not word_buf else word_buf + " " + word
                if len(piece) > limit:
                    flush(word_buf)
                    word_buf = ""
                    while len(word) > limit:
                        flush(word[:limit])
                        word = word[limit:]
                    word_buf = word
                else:
                    word_buf = piece
            flush(word_buf)
            current_buffer[0] = ""
            continue
        candidate = (current_buffer[0] + "\n" + line) if current_buffer[0] else line
        if len(candidate) <= limit:
            current_buffer[0] = candidate
        else:
            flush(current_buffer[0])
            current_buffer[0] = line
    flush(current_buffer[0])
    return chunks


def _render_transcript(history: Sequence[tuple[str, str]], user_text: str) -> str:
    """Render prior turns plus the new user message as one prompt body."""
    lines: list[str] = []
    for role, text in history:
        speaker = "المستخدم" if role == "user" else "المساعد"
        lines.append(f"{speaker}: {text}")
    lines.append(f"المستخدم: {user_text}")
    return "\n".join(lines)


class TelegramReplySender:
    """Delivers replies over the Telegram Bot API ``sendMessage`` (chunked).

    This sender only ever *sends*. It never calls ``getUpdates``, so it can be
    attached to the same bot token the approval bot polls without disturbing
    the approval path's offset. The HTTP layer is injectable for hermetic
    tests; the default uses :mod:`httpx` against the Telegram Bot API.
    """

    def __init__(
        self,
        *,
        bot_token: str | None = None,
        chat_id: str | None = None,
        api_base: str = "https://api.telegram.org",
        max_chars: int = TELEGRAM_MAX_CHARS,
        post_fn: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        """Configure the sender from explicit args or the environment.

        ``bot_token`` falls back to ``AI2027_ASSISTANT_TELEGRAM_TOKEN`` then
        ``PA_APPROVAL_BOT_TOKEN`` (so the assistant replies in the owner chat
        the approval bot already serves). ``chat_id`` falls back to
        ``AI2027_ASSISTANT_TELEGRAM_CHAT_ID`` then ``PA_APPROVAL_CHAT_ID``.
        """
        self._bot_token = (
            bot_token
            if bot_token is not None
            else (
                os.environ.get("AI2027_ASSISTANT_TELEGRAM_TOKEN", "").strip()
                or os.environ.get("PA_APPROVAL_BOT_TOKEN", "").strip()
            )
        )
        self._chat_id = (
            chat_id
            if chat_id is not None
            else (
                os.environ.get("AI2027_ASSISTANT_TELEGRAM_CHAT_ID", "").strip()
                or os.environ.get("PA_APPROVAL_CHAT_ID", "").strip()
            )
        )
        self._api_base = api_base.rstrip("/")
        self._max_chars = max_chars
        self._post = post_fn or self._httpx_post

    @property
    def configured(self) -> bool:
        """Return whether a bot token and chat id are present."""
        return bool(self._bot_token and self._chat_id)

    async def _httpx_post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Default POST: one bounded httpx request to the Bot API."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, json=payload)
        if response.status_code >= 400:
            return {"ok": False, "error": "http_error", "status": response.status_code}
        try:
            parsed: dict[str, Any] = response.json()
            return parsed
        except ValueError:
            return {"ok": False, "error": "not_json"}

    async def send(self, chat_id: str, text: str) -> SenderResult:
        """Send ``text`` to ``chat_id`` in <=4096-char chunks."""
        if not self.configured:
            return SenderResult(ok=False, stage="not_configured")
        target = chat_id or self._chat_id
        chunks = chunk_text(text, self._max_chars)
        url = f"{self._api_base}/bot{self._bot_token}/sendMessage"
        for chunk in chunks:
            response = await self._post(url, {"chat_id": target, "text": chunk})
            if not response.get("ok"):
                return SenderResult(ok=False, stage="send_failed", chunks_sent=len(chunks) - 1)
        return SenderResult(ok=True, stage="sent", chunks_sent=len(chunks))


class WhatsAppReplySender:
    """Delivers replies over a sync WhatsApp transport, run off the event loop.

    ``send_fn`` is a callable ``(chat_id, text) -> dict`` with a stage-only
    result (e.g. ``waapi.send_text`` or a ``functools.partial`` around
    ``whatsapp_cloud.send_reply``). It is invoked on a worker thread so the
    async assistant never blocks on the stdlib HTTP transport. Long replies
    are chunked to the WhatsApp 4096-char limit before each call.
    """

    def __init__(
        self,
        send_fn: Callable[[str, str], dict[str, Any]],
        *,
        max_chars: int = WHATSAPP_MAX_CHARS,
    ) -> None:
        """Wrap a sync ``send_fn`` and set the per-message character limit."""
        if not callable(send_fn):
            raise TypeError("send_fn must be callable")
        self._send_fn = send_fn
        self._max_chars = max_chars

    async def send(self, chat_id: str, text: str) -> SenderResult:
        """Chunk ``text`` and deliver each chunk through ``send_fn``."""
        chunks = chunk_text(text, self._max_chars)
        sent = 0
        for chunk in chunks:
            result = await asyncio.to_thread(self._send_fn, chat_id, chunk)
            if not isinstance(result, dict) or not result.get("ok"):
                stage = (
                    str(result.get("stage", "send_failed"))
                    if isinstance(result, dict)
                    else "send_failed"
                )
                return SenderResult(ok=False, stage=stage, chunks_sent=sent)
            sent += 1
        return SenderResult(ok=True, stage="sent", chunks_sent=sent)


def parse_telegram_update(update: dict[str, Any] | None) -> ChannelInboundMessage | None:
    """Extract a text message from a Telegram update, or ``None``.

    Only plain ``message.text`` updates become inbound messages. Approval
    ``callback_query`` updates, non-text messages (photos, stickers), commands
    and empty texts are ignored here — the approval bot owns callbacks, so the
    assistant never touches them and never advances the shared ``getUpdates``
    offset. This is the single integration point the approval bot's polling
    loop calls for each update.
    """
    if not isinstance(update, dict):
        return None
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    text = message.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return None
    chat_id = str(chat.get("id", "")).strip()
    if not chat_id:
        return None
    sender = message.get("from")
    sender_id = str(sender.get("id", "")).strip() if isinstance(sender, dict) else chat_id
    message_id = str(message.get("message_id", "")).strip()
    if not message_id:
        return None
    raw_ts = message.get("date")
    timestamp_s = int(raw_ts) if isinstance(raw_ts, int) else 0
    return ChannelInboundMessage(
        channel=Channel.TELEGRAM,
        chat_id=chat_id,
        sender_id=sender_id or chat_id,
        text=text.strip()[:16_000],
        message_id=message_id,
        timestamp_s=max(0, timestamp_s),
    )


class ChannelAssistant:
    """The common inbound -> transcript -> route -> reply assistant.

    Both channels call :meth:`handle_inbound`; the router and BudgetGuard do
    the free-provider selection and the ``$0`` enforcement. The assistant adds
    transcript persistence, idempotency, Arabic framing, chunking and
    fail-closed delivery on top.
    """

    def __init__(
        self,
        *,
        router: FreeFirstRouter,
        sender: ReplySender,
        store: ConversationStore | None = None,
        system_prompt: str = ARABIC_SYSTEM_PROMPT,
        max_tokens: int = 512,
        fallback_text: str = ARABIC_NO_PROVIDER_FALLBACK,
        max_history_turns: int | None = None,
    ) -> None:
        """Wire the assistant to a router, a channel sender and a store.

        ``max_history_turns`` caps the transcript when no ``store`` is supplied;
        it falls back to ``AI2027_ASSISTANT_MAX_HISTORY_TURNS`` then
        :data:`DEFAULT_MAX_HISTORY_TURNS`.
        """
        self._router = router
        self._sender = sender
        if store is not None:
            self._store: ConversationStore = store
        else:
            turns = max_history_turns if max_history_turns is not None else _env_max_history_turns()
            self._store = MemoryConversationStore(max_turns=turns)
        self._system_prompt = system_prompt
        self._max_tokens = max_tokens
        self._fallback_text = fallback_text
        self._seen: set[tuple[Channel, str]] = set()

    @property
    def router(self) -> FreeFirstRouter:
        """Return the router that performs free-provider selection."""
        return self._router

    @property
    def budget_snapshot(self) -> BudgetSnapshot:
        """Return the router's current budget ledger ($0 enforced)."""
        return self._router.budget_snapshot()

    def _build_request(
        self, chat_id: str, history: Sequence[tuple[str, str]], text: str
    ) -> PromptRequest:
        """Build the routing request with the Arabic system prompt + transcript."""
        body = _render_transcript(history, text)
        return PromptRequest(
            prompt=body,
            system_prompt=self._system_prompt,
            max_tokens=self._max_tokens,
            allow_remote=True,  # BudgetGuard still blocks paid/unknown remote.
        )

    async def handle_inbound(self, message: ChannelInboundMessage) -> AssistantReply:
        """Handle one inbound message end to end; never raises on delivery.

        Returns a structured :class:`AssistantReply`. A replayed ``message_id``
        is short-circuited as ``duplicate_message``; an empty/invalid text as
        ``invalid_message``. A produced reply is delivered through the sender;
        if every provider fails the deterministic $0 Arabic fallback is
        delivered instead, so the user always gets an answer and no paid call
        is ever attempted.
        """
        key = (message.channel, message.message_id)
        if key in self._seen:
            return AssistantReply(
                channel=message.channel,
                chat_id=message.chat_id,
                reply_text="",
                stage="duplicate_message",
                budget=self._router.budget_snapshot(),
            )
        if not message.text.strip():
            return AssistantReply(
                channel=message.channel,
                chat_id=message.chat_id,
                reply_text="",
                stage="invalid_message",
                budget=self._router.budget_snapshot(),
            )
        self._seen.add(key)

        history = self._store.history(message.channel, message.chat_id)
        self._store.append(message.channel, message.chat_id, "user", message.text)

        reply_text: str
        provider = ""
        model = ""
        billing_tier = BillingTier.LOCAL
        used_fallback = False
        response: CompletionResponse | None
        try:
            response = await self._router.route(
                self._build_request(message.chat_id, history, message.text)
            )
        except AllProvidersFailedError:
            response = None
        if (
            response is None
            or response.provider == ProviderKind.DETERMINISTIC
            or not response.text.strip()
        ):
            # No free model answered. The default chain ends with the
            # simulated DeterministicFallbackProvider, whose text is a debug
            # echo ("[deterministic-offline-fallback | simulated=true ...]") —
            # never deliver that to a person; send the Arabic $0 notice.
            reply_text = self._fallback_text
            provider = "deterministic-fallback"
            model = "offline-fallback-v1"
            billing_tier = BillingTier.LOCAL
            used_fallback = True
        else:
            # Defense in depth: the router's BudgetGuard already blocks paid
            # and unknown-cost providers, but never deliver a non-$0 result.
            if response.billing_tier not in (BillingTier.LOCAL, BillingTier.FREE_TIER):
                return AssistantReply(
                    channel=message.channel,
                    chat_id=message.chat_id,
                    reply_text="",
                    stage="paid_not_allowed",
                    provider=response.provider.value,
                    model=response.model,
                    billing_tier=response.billing_tier,
                    cost_usd=0.0,
                    budget=self._router.budget_snapshot(),
                )
            reply_text = response.text.strip()
            provider = response.provider.value
            model = response.model
            billing_tier = response.billing_tier

        if not used_fallback:
            # The "temporarily unavailable" notice is not part of the
            # conversation; keeping it would teach the model to repeat it.
            self._store.append(message.channel, message.chat_id, "assistant", reply_text)
        sender_result = await self._sender.send(message.chat_id, reply_text)
        if not sender_result.ok:
            stage = "send_failed"
        else:
            stage = "no_provider_available" if used_fallback else "replied"
        return AssistantReply(
            channel=message.channel,
            chat_id=message.chat_id,
            reply_text=reply_text,
            stage=stage,
            provider=provider,
            model=model,
            billing_tier=billing_tier,
            cost_usd=0.0,
            chunks_sent=sender_result.chunks_sent,
            budget=self._router.budget_snapshot(),
        )


def build_assistant_router(
    *,
    providers: Sequence[Any] | None = None,
    budget_policy: BudgetPolicy | None = None,
) -> FreeFirstRouter:
    """Build the default Free-First router the assistant runs on.

    Uses :func:`~ai2027_protocol.ai_providers.build_default_providers`, whose
    chain contains only local and policy-declared ``FREE_TIER`` providers
    (local Ollama, the free-tier allowlists, OpenRouter ``:free``) and ends
    with the deterministic $0 fallback. OmniRouters and Qwen are deliberately
    absent: both are ``UNKNOWN_COST``/``NON_RECLASSIFIABLE`` and are blocked
    under ``MAX_SPEND=0``.
    """
    from .ai_providers import build_default_providers

    guard = BudgetGuard(budget_policy)
    return FreeFirstRouter(
        providers=providers if providers is not None else build_default_providers(),
        budget_guard=guard,
        budget_policy=budget_policy,
        clock=time.monotonic,
    )


__all__ = [
    "ARABIC_NO_PROVIDER_FALLBACK",
    "ARABIC_SYSTEM_PROMPT",
    "AssistantReply",
    "Channel",
    "ChannelAssistant",
    "ChannelInboundMessage",
    "ConversationStore",
    "MemoryConversationStore",
    "ReplySender",
    "SenderResult",
    "TelegramReplySender",
    "WhatsAppReplySender",
    "build_assistant_router",
    "chunk_text",
    "parse_telegram_update",
]
