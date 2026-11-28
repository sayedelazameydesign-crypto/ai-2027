"""CeliaBrain — one turn of CELIA: command, or mode + memory + free model.

The smallest real slice of the CELIA.SH "Intelligence Engine"
(``docs/CELIA.md``), built only from existing parts:

=================  ==========================================================
Engine (spec)      Implementation here
=================  ==========================================================
Reasoning/intent   :func:`celia_persona.detect_mode` + :func:`parse_memory_command`
Memory             :class:`celia_personal_memory.PersonalMemory` (consented)
Model router       :class:`router.FreeFirstRouter` + ``BudgetGuard`` ($0)
Policy             L0: memory writes only on explicit owner command
Execution          :class:`channel_assistant.ChannelAssistant` (send, history)
Verification       memory confirmations only after a durable write
=================  ==========================================================

Memory commands never reach a model: they run in code and CELIA's
confirmation is a fact. Everything else is answered by a free model under a
prompt rendered for the detected mode with the relevant memories.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .celia_persona import (
    CELIA_OWNER_FALLBACK,
    Audience,
    CeliaMode,
    detect_mode,
    render_system_prompt,
)
from .celia_personal_memory import PersonalMemory, parse_memory_command
from .channel_assistant import (
    ChannelAssistant,
    ChannelInboundMessage,
    ConversationStore,
    ReplySender,
)
from .contracts import BillingTier
from .router import FreeFirstRouter

#: Reply when a memory command arrives but no durable store is connected.
MEMORY_UNAVAILABLE_TEXT = (
    "ذاكرتي الدائمة غير متصلة في هذا التشغيل، لذلك لم أحفظ ولم أحذف شيئاً. "
    "ستعمل الأوامر في دورة البوت المعتادة."
)


@dataclass(frozen=True, slots=True)
class CeliaTurn:
    """Outcome of one turn. ``reply_text`` is never logged by the runners."""

    stage: str
    mode: CeliaMode | None
    reply_text: str = ""
    provider: str | None = None
    billing_tier: BillingTier | None = None
    cost_usd: float = 0.0
    chunks_sent: int = 0
    memories_used: int = 0


class CeliaBrain:
    """Owner-scope CELIA over a Free-First router and consented memory."""

    def __init__(
        self,
        *,
        router: FreeFirstRouter,
        sender_factory: Callable[[str], ReplySender],
        store: ConversationStore,
        memory: PersonalMemory | None = None,
        fallback_text: str = CELIA_OWNER_FALLBACK,
        recall_k: int = 6,
    ) -> None:
        """Wire the collaborators; ``memory=None`` disables memory commands honestly."""
        self._router = router
        self._sender_factory = sender_factory
        self._store = store
        self._memory = memory
        self._fallback = fallback_text
        self._recall_k = recall_k

    @property
    def memory(self) -> PersonalMemory | None:
        """The consented memory, if one is connected."""
        return self._memory

    async def handle(self, message: ChannelInboundMessage) -> CeliaTurn:
        """Answer one inbound owner message and send the reply."""
        sender = self._sender_factory(message.chat_id)
        command = parse_memory_command(message.text)
        if command is not None:
            if self._memory is None:
                stage, text = "memory_unavailable", MEMORY_UNAVAILABLE_TEXT
            else:
                outcome = self._memory.execute_durably(
                    command, source=f"{message.channel.value}:{message.message_id}"
                )
                stage, text = outcome.stage, outcome.reply
            sent = await sender.send(message.chat_id, text)
            return CeliaTurn(
                stage=stage if sent.ok else "send_failed",
                mode=None,
                reply_text=text,
                billing_tier=BillingTier.LOCAL,
                chunks_sent=sent.chunks_sent,
            )

        mode = detect_mode(message.text)
        memories = self._memory.recall(message.text, k=self._recall_k) if self._memory else []
        assistant = ChannelAssistant(
            router=self._router,
            sender=sender,
            store=self._store,
            system_prompt=render_system_prompt(Audience.OWNER, mode, memories),
            fallback_text=self._fallback,
        )
        reply = await assistant.handle_inbound(message)
        return CeliaTurn(
            stage=reply.stage,
            mode=mode,
            reply_text=reply.reply_text,
            provider=reply.provider,
            billing_tier=reply.billing_tier,
            cost_usd=reply.cost_usd,
            chunks_sent=reply.chunks_sent,
            memories_used=len(memories),
        )


__all__ = ["MEMORY_UNAVAILABLE_TEXT", "CeliaBrain", "CeliaTurn"]
