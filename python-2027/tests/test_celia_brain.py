"""CeliaBrain: memory commands run in code; chat gets mode + memories + free model."""

from __future__ import annotations

from collections.abc import Sequence

from ai2027_protocol.budget import BudgetPolicy
from ai2027_protocol.celia_brain import MEMORY_UNAVAILABLE_TEXT, CeliaBrain
from ai2027_protocol.celia_persona import CELIA_OWNER_FALLBACK, CeliaMode
from ai2027_protocol.celia_personal_memory import ListMemoryBackend, PersonalMemory
from ai2027_protocol.channel_assistant import (
    Channel,
    ChannelInboundMessage,
    MemoryConversationStore,
    SenderResult,
)
from ai2027_protocol.contracts import BillingTier, PromptRequest, ProviderKind
from ai2027_protocol.router import FreeFirstRouter


class _Free:
    def __init__(self, text: str = "تمام.", *, configured: bool = True) -> None:
        self.kind = ProviderKind.GROQ
        self.model = "groq-free"
        self.is_remote = True
        self.billing_tier = BillingTier.FREE_TIER
        self._text = text
        self._configured = configured
        self.requests: list[PromptRequest] = []

    def is_configured(self) -> bool:
        return self._configured

    async def complete(self, request: PromptRequest, *, timeout_s: float) -> str:
        self.requests.append(request)
        return self._text


class _Sender:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.sent: list[tuple[str, str]] = []

    async def send(self, chat_id: str, text: str) -> SenderResult:
        self.sent.append((chat_id, text))
        return SenderResult(ok=self.ok, stage="sent" if self.ok else "http_500", chunks_sent=1)


def _brain(
    providers: Sequence[_Free], sender: _Sender, memory: PersonalMemory | None
) -> CeliaBrain:
    return CeliaBrain(
        router=FreeFirstRouter(
            providers=list(providers), budget_policy=BudgetPolicy(max_spend_usd=0.0)
        ),
        sender_factory=lambda chat: sender,
        store=MemoryConversationStore(),
        memory=memory,
    )


def _msg(text: str, message_id: str = "1") -> ChannelInboundMessage:
    return ChannelInboundMessage(
        channel=Channel.TELEGRAM, chat_id="42", sender_id="7", text=text, message_id=message_id
    )


async def test_memory_command_runs_in_code_without_any_model_call() -> None:
    model, sender = _Free(), _Sender()
    memory = PersonalMemory(ListMemoryBackend())
    turn = await _brain([model], sender, memory).handle(_msg("تذكّري: أحب الردود القصيرة", "55"))
    assert turn.stage == "memory_saved"
    assert turn.mode is None and turn.cost_usd == 0.0
    assert model.requests == []  # $0, deterministic, no hallucinated "saved"
    assert sender.sent[0][1].startswith("حفظتُ ✅ رقم 1")
    assert memory.records[0].source == "telegram:55"


async def test_without_a_memory_store_she_says_so_instead_of_pretending() -> None:
    sender = _Sender()
    turn = await _brain([_Free()], sender, None).handle(_msg("تذكّري: شيء"))
    assert turn.stage == "memory_unavailable"
    assert sender.sent == [("42", MEMORY_UNAVAILABLE_TEXT)]


async def test_chat_uses_detected_mode_and_relevant_memories() -> None:
    model, sender = _Free("خطة اليوم: …"), _Sender()
    memory = PersonalMemory(ListMemoryBackend())
    brain = _brain([model], sender, memory)
    await brain.handle(_msg("تذكّري: المرحلة التالية في مشروع AI-2027 هي الذاكرة", "1"))
    turn = await brain.handle(_msg("رتّبي لي خطة مشروع AI-2027 لليوم", "2"))
    assert turn.stage == "replied"
    assert turn.mode is CeliaMode.EXECUTIVE
    assert turn.memories_used == 1
    prompt = model.requests[0].system_prompt or ""
    assert "مديرة المهام" in prompt
    assert "المرحلة التالية في مشروع AI-2027 هي الذاكرة" in prompt
    assert sender.sent[-1] == ("42", "خطة اليوم: …")


async def test_no_free_model_gives_celias_honest_fallback() -> None:
    sender = _Sender()
    turn = await _brain([_Free(configured=False)], sender, None).handle(_msg("ازيك"))
    assert turn.stage == "no_provider_available"
    assert sender.sent == [("42", CELIA_OWNER_FALLBACK)]


async def test_a_failed_confirmation_send_is_reported() -> None:
    memory = PersonalMemory(ListMemoryBackend())
    turn = await _brain([_Free()], _Sender(ok=False), memory).handle(_msg("ماذا تتذكرين؟"))
    assert turn.stage == "send_failed"
