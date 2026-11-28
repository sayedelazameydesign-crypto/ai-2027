"""Runtime adapters that put :mod:`channel_assistant` online.

Kept apart from the core module so the assistant contract stays small
(protocol rule R2). Two production runners use it, both behind
``AI2027_ASSISTANT_ENABLED`` (``docs/CHANNEL-ASSISTANT.md`` §11):

- ``scripts/approval_bot_ci.py`` — the stateless GitHub Actions approval-bot
  cycle answers the owner's Telegram texts. :class:`SnapshotConversationStore`
  carries the bounded transcript between cycles in the bot's state file.
- ``scripts/free_stack_runner.py`` — the WhatsApp webhook drafts replies with
  :func:`make_whatsapp_draft_generator`. The sender is a
  :class:`DraftCaptureSender`, so nothing is sent: the text is staged
  ``pending_approval`` and leaves only through ``GovernedReplyBridge`` after a
  single-use approval.

The names the runners need from the core module and CELIA's persona
(:mod:`celia_persona`) are re-exported, so each runner imports exactly one
module.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from .celia_brain import CeliaBrain, CeliaTurn
from .celia_persona import (
    CELIA_CUSTOMER_PROMPT,
    CELIA_OWNER_FALLBACK,
    CELIA_OWNER_PROMPT,
    CeliaMode,
)
from .celia_personal_memory import SHEET_HEADERS, ListMemoryBackend, PersonalMemory
from .channel_assistant import (
    ARABIC_NO_PROVIDER_FALLBACK,
    AssistantReply,
    Channel,
    ChannelAssistant,
    ChannelInboundMessage,
    MemoryConversationStore,
    SenderResult,
    TelegramReplySender,
    _env_max_history_turns,
    build_assistant_router,
    parse_telegram_update,
)


def env_max_history_turns() -> int:
    """Return ``AI2027_ASSISTANT_MAX_HISTORY_TURNS`` (fail-closed default 8)."""
    return _env_max_history_turns()


class SnapshotConversationStore(MemoryConversationStore):
    """:class:`MemoryConversationStore` that can be saved and reloaded as JSON."""

    def snapshot(self, *, max_chats: int = 50, max_chars: int = 4000) -> list[dict[str, Any]]:
        """Return a JSON-safe, bounded copy of every transcript.

        Used by stateless runners (the GitHub Actions approval-bot cycle) to
        carry the conversation into the next process. At most ``max_chats``
        conversations are kept (most recently inserted last) and each turn is
        truncated to ``max_chars`` so the state file stays small.
        """
        items = list(self._turns.items())[-max_chats:] if max_chats > 0 else []
        return [
            {
                "channel": channel.value,
                "chat_id": chat_id,
                "turns": [[role, text[:max_chars]] for role, text in turns],
            }
            for (channel, chat_id), turns in items
        ]

    def restore(self, data: object) -> int:
        """Load a :meth:`snapshot`; malformed entries are skipped. Returns chats loaded."""
        if not isinstance(data, list):
            return 0
        loaded = 0
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                channel = Channel(str(item.get("channel", "")))
            except ValueError:
                continue
            chat_id = str(item.get("chat_id", "")).strip()
            turns = item.get("turns")
            if not chat_id or not isinstance(turns, list):
                continue
            clean = [
                (str(t[0]), str(t[1]))
                for t in turns
                if isinstance(t, (list, tuple))
                and len(t) == 2
                and str(t[0]) in ("user", "assistant")
            ]
            if self._max_turns:
                clean = clean[-self._max_turns :]
            else:
                clean = []
            self._turns[(channel, chat_id)] = clean
            loaded += 1
        return loaded


class DraftCaptureSender:
    """ReplySender that *records* the reply instead of delivering it.

    Used by approval-gated channels (WhatsApp through ``GovernedReplyBridge``):
    the assistant writes the draft, and the governed executor sends it only
    after a single-use approval. Nothing leaves the process here.
    """

    def __init__(self) -> None:
        """Start with no captured drafts."""
        self.drafts: dict[str, str] = {}

    async def send(self, chat_id: str, text: str) -> SenderResult:
        """Capture ``text`` for ``chat_id`` and report success without I/O."""
        self.drafts[chat_id] = text
        return SenderResult(ok=True, stage="drafted", chunks_sent=0)


def make_whatsapp_draft_generator(
    assistant: ChannelAssistant,
    capture: DraftCaptureSender,
    *,
    timeout_s: float = 20.0,
    on_error: Callable[[object], str] | None = None,
) -> Callable[[object, object], str]:
    """Adapt the assistant to ``ZeroCostOrchestrator(reply_generator=...)``.

    The returned sync callable takes a verified ``InboundWhatsAppEvent``
    (duck-typed: ``wamid``, ``sender_wa_id``, ``text_body``, ``timestamp_s``)
    and returns the reply *draft* text. ``assistant`` must have been built
    with ``capture`` as its sender, so no message is sent — the orchestrator
    stages the text as ``pending_approval`` and the governed bridge delivers
    it only after an approval.

    It never raises: any failure or a run longer than ``timeout_s`` returns
    ``on_error(event)`` (the orchestrator's deterministic default reply) or
    the Arabic $0 notice, so a slow free provider cannot stall the webhook.
    """

    def _fallback(event: object) -> str:
        if on_error is not None:
            return on_error(event)
        return ARABIC_NO_PROVIDER_FALLBACK

    def generate(event: object, _orchestrator: object = None) -> str:
        try:
            sender = str(getattr(event, "sender_wa_id", "") or "").strip()
            message = ChannelInboundMessage(
                channel=Channel.WHATSAPP,
                chat_id=sender,
                sender_id=sender,
                text=str(getattr(event, "text_body", "") or "")[:16_000],
                message_id=str(getattr(event, "wamid", "") or ""),
                timestamp_s=max(0, int(getattr(event, "timestamp_s", 0) or 0)),
            )
        except (ValueError, TypeError):
            return _fallback(event)

        async def _run() -> AssistantReply:
            return await asyncio.wait_for(assistant.handle_inbound(message), timeout=timeout_s)

        try:
            reply = asyncio.run(_run())
        except Exception:  # noqa: BLE001 - webhook must never crash on a draft
            return _fallback(event)
        if reply.stage != "replied":
            # No free model (or a duplicate/invalid message): the customer
            # gets the orchestrator's normal reply, not the assistant notice.
            return _fallback(event)
        # ``reply.reply_text`` is exactly what the capture sender recorded;
        # reading it from the reply (not the shared dict) is race-free when
        # the threaded webhook drafts two messages from one sender at once.
        capture.drafts.pop(message.chat_id, None)
        return reply.reply_text.strip() or _fallback(event)

    return generate


__all__ = [
    "SHEET_HEADERS",
    "CeliaBrain",
    "CeliaMode",
    "CeliaTurn",
    "ListMemoryBackend",
    "PersonalMemory",
    "CELIA_CUSTOMER_PROMPT",
    "CELIA_OWNER_FALLBACK",
    "CELIA_OWNER_PROMPT",
    "Channel",
    "ChannelAssistant",
    "ChannelInboundMessage",
    "DraftCaptureSender",
    "SnapshotConversationStore",
    "TelegramReplySender",
    "build_assistant_router",
    "env_max_history_turns",
    "make_whatsapp_draft_generator",
    "parse_telegram_update",
]
