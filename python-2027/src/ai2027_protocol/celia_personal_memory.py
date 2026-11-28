"""CELIA's durable personal memory — written only on the owner's explicit word.

The CELIA.SH spec's rule: *every saved fact has a source, a date and a
confidence, can be corrected or deleted, and nothing sensitive is saved
automatically.* This module is that rule in code:

- **Consent only.** A record is created solely by an explicit owner command
  («تذكّري: …», «احفظي: …», ``remember: …``). CELIA never saves on her own.
- **Visible and reversible.** «ماذا تتذكرين؟» lists every record with its
  number; «انسي رقم 3» / «انسي: نص» deletes. Commands run in code, so the
  confirmation she sends is a fact, not a model's claim.
- **Sensitive data is refused**, never stored: passwords, OTP codes, card or
  IBAN numbers, API keys/tokens.
- **Durable.** Records live behind :class:`MemoryBackend` — the runner stores
  them in the ``celia_memory`` tab of the system memory sheet
  (``PA_MEMORY_SHEET_ID``), with the bot state file as fallback.
- **Recall reuses** :mod:`celia_memory`'s bilingual normalization and lexical
  vectors (no new index, $0, deterministic).

Owner scope only: the WhatsApp customer path never reads this memory.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import Field, ValidationError

from .celia_memory import cosine_similarity, lexical_embed_text, normalize_bilingual_text
from .contracts import StrictModel

#: Hard caps: a record must fit a chat line, and the whole memory a prompt.
MAX_RECORD_CHARS = 500
DEFAULT_MAX_RECORDS = 300

SHEET_HEADERS = ("id", "kind", "text", "source", "created_at", "confidence")


class MemoryKind(StrEnum):
    """Durable memory layers from the CELIA.SH spec (working/episodic = chat)."""

    PERSONAL = "personal"
    PROJECT = "project"
    PROCEDURAL = "procedural"
    SEMANTIC = "semantic"


_KIND_LABELS = {
    MemoryKind.PERSONAL: "شخصي",
    MemoryKind.PROJECT: "مشروع",
    MemoryKind.PROCEDURAL: "إجراء",
    MemoryKind.SEMANTIC: "معلومة",
}


class MemoryRecord(StrictModel):
    """One consented fact: what, which layer, where it came from, when."""

    id: int = Field(ge=1)
    kind: MemoryKind
    text: str = Field(min_length=1, max_length=MAX_RECORD_CHARS)
    source: str = Field(min_length=1, max_length=120)
    created_at: str = Field(min_length=10, max_length=40)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


@runtime_checkable
class MemoryBackend(Protocol):
    """Durable storage: rows are plain dicts keyed by :data:`SHEET_HEADERS`."""

    def load(self) -> list[dict[str, object]]:
        """Return every stored row."""
        ...

    def save(self, rows: list[dict[str, object]]) -> None:
        """Replace the stored rows with ``rows``."""
        ...


class ListMemoryBackend:
    """In-process backend over a JSON-safe list (the bot state-file fallback)."""

    def __init__(self, rows: object = None) -> None:
        """Start from ``rows`` (non-list input is treated as empty)."""
        self.rows: list[dict[str, object]] = (
            [dict(r) for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
        )

    def load(self) -> list[dict[str, object]]:
        """Return a copy of the rows."""
        return [dict(r) for r in self.rows]

    def save(self, rows: list[dict[str, object]]) -> None:
        """Replace the rows."""
        self.rows = [dict(r) for r in rows]


class CommandKind(StrEnum):
    """The three owner commands."""

    REMEMBER = "remember"
    FORGET = "forget"
    LIST = "list"


@dataclass(frozen=True, slots=True)
class MemoryCommand:
    """A parsed owner command and its argument (fact, number or phrase)."""

    kind: CommandKind
    argument: str = ""


@dataclass(frozen=True, slots=True)
class MemoryOutcome:
    """What a command did: an allowlisted ``stage`` and CELIA's reply text."""

    stage: str
    reply: str


# Commands are matched on normalize_bilingual_text() output (alef/ya/ta folded,
# diacritics stripped), so «تذكّري» «تذكري» «إحفظي» all hit.
_ADDRESS = r"^(?:يا\s+)?(?:سيليا|celia)?[\s،,:]*"
# Explicit forms only, so ordinary chat never writes or deletes memory:
#   «تذكّري: X» / «تذكّري أن X» / «احفظي: X»  — not «تذكري لما كنا…» (a question)
#   «انسي رقم 3» / «انسي 3» / «انسي: X»       — not «انسي الموضوع» (chat)
_REMEMBER_RE = re.compile(
    _ADDRESS + r"(?:تذكري|احفظي|سجلي|remember|save)\b\s*(?:[:：\-–]|\s(?:ان|that)\b)\s*(.+)$",
    re.DOTALL,
)
_FORGET_RE = re.compile(
    _ADDRESS + r"(?:انسي|امسحي|forget)\b\s*(?:(?:رقم|number|#)\s*(\d+)|(\d+)|[:：\-–]\s*(.+))\s*$",
    re.DOTALL,
)
_LIST_RE = re.compile(
    _ADDRESS
    + r"(?:ماذا تتذكرين|ماذا تتذكري|ايش تتذكري|بتفتكري ايه|اعرضي الذاكره|ذاكرتك|"
    + r"what do you remember|show memory)\s*[؟?!.]*$"
)

_SENSITIVE_PATTERNS = (
    re.compile(r"(?:كلمه\s*(?:ال)?(?:سر|مرور)|باسورد|password|passwd|\bpin\b)", re.IGNORECASE),
    re.compile(r"(?:رمز\s*(?:ال)?تحقق|كود\s*(?:ال)?تحقق|\botp\b|verification code)", re.IGNORECASE),
    re.compile(r"\b(?:\d[ -]?){13,19}\b"),  # card-like digit runs
    re.compile(r"\b[a-z]{2}\d{2}[a-z0-9]{11,30}\b", re.IGNORECASE),  # IBAN
    re.compile(
        r"(?:sk-|ghp_|gho_|github_pat_|xox[bap]-|aiza|hf_|xai-|glpat-|eyj)[a-z0-9_\-]{8,}",
        re.IGNORECASE,
    ),
    re.compile(r"\b[a-z0-9_\-]{32,}\b", re.IGNORECASE),  # long opaque secrets
)

_KIND_HINTS: tuple[tuple[MemoryKind, tuple[str, ...]], ...] = (
    (
        MemoryKind.PROCEDURAL,
        ("طريقه", "خطوات", "اجراء", "عندما", "لما ", "دايما ", "how to", "steps"),
    ),
    (
        MemoryKind.PROJECT,
        (
            "مشروع",
            "ai-2027",
            "ai 2027",
            "celia",
            "سيليا",
            "مستودع",
            "pr ",
            "قرار",
            "مرحله",
            "project",
        ),
    ),
    (
        MemoryKind.PERSONAL,
        ("احب", "افضل", "لا احب", "اسلوب", "اسمي", "عيد ميلاد", "i like", "prefer"),
    ),
)


def parse_memory_command(text: str) -> MemoryCommand | None:
    """Return the owner command in ``text``, or ``None`` for ordinary chat."""
    norm = normalize_bilingual_text(text or "")
    if not norm:
        return None
    if _LIST_RE.match(norm):
        return MemoryCommand(CommandKind.LIST)
    match = _REMEMBER_RE.match(norm)
    if match:
        return MemoryCommand(CommandKind.REMEMBER, _original_tail(text, match.group(1).strip()))
    match = _FORGET_RE.match(norm)
    if match:
        number = match.group(1) or match.group(2)
        if number:
            return MemoryCommand(CommandKind.FORGET, number)
        return MemoryCommand(CommandKind.FORGET, _original_tail(text, match.group(3).strip()))
    return None


def _original_tail(text: str, normalized_tail: str) -> str:
    """The owner's own spelling of ``normalized_tail`` (diacritics, case kept).

    Normalization changes lengths, so find the shortest original suffix that
    normalizes to the same text; the command prefix is short, so only the
    first 120 offsets are tried.
    """
    original = text.strip()
    for i in range(min(len(original), 120)):
        candidate = original[i:]
        if normalize_bilingual_text(candidate) == normalized_tail:
            return candidate.strip(" :：-–")
    return normalized_tail


def looks_sensitive(text: str) -> bool:
    """``True`` if ``text`` resembles a secret CELIA must never store."""
    norm = normalize_bilingual_text(text)
    return any(p.search(norm) or p.search(text) for p in _SENSITIVE_PATTERNS)


def classify_kind(text: str) -> MemoryKind:
    """Deterministic memory layer for a fact (procedural > project > personal)."""
    norm = f" {normalize_bilingual_text(text)} "
    for kind, hints in _KIND_HINTS:
        if any(h in norm for h in hints):
            return kind
    return MemoryKind.SEMANTIC


def _phrase_stems(phrase: str) -> list[str]:
    """Words of ``phrase`` without the Arabic article, for forgiving matching.

    «التصميم» must find «للتصميم» (لـ + التصميم drops the alef) and «بالتصميم».
    """
    stems = []
    for word in normalize_bilingual_text(phrase).split():
        stem = word[2:] if word.startswith("ال") and len(word) > 3 else word
        if stem:
            stems.append(stem)
    return stems


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class PersonalMemory:
    """Consent-only durable memory over a :class:`MemoryBackend`."""

    def __init__(
        self,
        backend: MemoryBackend,
        *,
        max_records: int = DEFAULT_MAX_RECORDS,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        """Load and validate stored rows; malformed rows are skipped, not fatal."""
        self._backend = backend
        self._max = max(1, max_records)
        self._clock = clock
        self._records: list[MemoryRecord] = []
        self.skipped_rows = 0
        for row in backend.load():
            try:
                self._records.append(MemoryRecord.model_validate(row))
            except ValidationError:
                self.skipped_rows += 1
        self._dirty = False

    @property
    def records(self) -> tuple[MemoryRecord, ...]:
        """Every record, oldest first."""
        return tuple(self._records)

    def __len__(self) -> int:
        """Number of records."""
        return len(self._records)

    def execute(self, command: MemoryCommand, *, source: str) -> MemoryOutcome:
        """Run an owner command and return CELIA's factual confirmation."""
        if command.kind is CommandKind.REMEMBER:
            return self.remember(command.argument, source=source)
        if command.kind is CommandKind.FORGET:
            return self.forget(command.argument)
        return self.describe()

    def execute_durably(self, command: MemoryCommand, *, source: str) -> MemoryOutcome:
        """:meth:`execute` + :meth:`flush` as one unit: a failed write is rolled back.

        CELIA confirms «حفظتُ» / «نسيتُ» only after the backend accepted the
        change; otherwise the in-memory state is restored and she says so.
        """
        before, dirty = list(self._records), self._dirty
        outcome = self.execute(command, source=source)
        try:
            self.flush()
        except Exception:  # noqa: BLE001 - any storage failure => honest rollback
            self._records, self._dirty = before, dirty
            return MemoryOutcome(
                "memory_store_failed",
                "لم أتمكن من الكتابة في ذاكرتي الدائمة الآن، فلم يتغير شيء. أعد المحاولة بعد قليل.",
            )
        return outcome

    def remember(self, text: str, *, source: str) -> MemoryOutcome:
        """Save one fact (refusing secrets, duplicates and overflow)."""
        fact = " ".join((text or "").split())
        if not fact:
            return MemoryOutcome(
                "memory_empty", "ماذا تريدني أن أتذكر؟ اكتب: «تذكّري: …» ثم المعلومة."
            )
        if looks_sensitive(fact):
            return MemoryOutcome(
                "memory_refused_sensitive",
                "لن أحفظ هذا 🔒 يبدو أنه بيانات حساسة (كلمة مرور، رمز تحقق، رقم بطاقة أو مفتاح). "
                "احتفظ به في مدير كلمات المرور، لا في ذاكرتي.",
            )
        if len(fact) > MAX_RECORD_CHARS:
            return MemoryOutcome(
                "memory_too_long",
                f"المعلومة أطول من {MAX_RECORD_CHARS} حرف؛ لخّصها في سطر أو سطرين وأحفظها.",
            )
        key = normalize_bilingual_text(fact)
        for rec in self._records:
            if normalize_bilingual_text(rec.text) == key:
                return MemoryOutcome("memory_duplicate", f"هذه محفوظة عندي بالفعل برقم {rec.id}.")
        if len(self._records) >= self._max:
            return MemoryOutcome(
                "memory_full",
                f"ذاكرتي ممتلئة ({self._max} معلومة). احذف شيئاً بـ«انسي رقم …» ثم أعد المحاولة.",
            )
        record = MemoryRecord(
            id=max((r.id for r in self._records), default=0) + 1,
            kind=classify_kind(fact),
            text=fact,
            source=source[:120] or "owner",
            created_at=self._clock(),
        )
        self._records.append(record)
        self._dirty = True
        return MemoryOutcome(
            "memory_saved",
            f"حفظتُ ✅ رقم {record.id} ({_KIND_LABELS[record.kind]}): {record.text}\n"
            "لحذفها لاحقاً: «انسي رقم %d»." % record.id,
        )

    def forget(self, argument: str) -> MemoryOutcome:
        """Delete by number, or by a phrase that matches exactly one record."""
        arg = (argument or "").strip().lstrip("#").strip()
        digits = normalize_bilingual_text(arg)
        if digits.isdigit():
            target = [r for r in self._records if r.id == int(digits)]
        else:
            stems = _phrase_stems(arg)
            target = [
                r
                for r in self._records
                if stems and all(stem in normalize_bilingual_text(r.text) for stem in stems)
            ]
        if not target:
            return MemoryOutcome(
                "memory_not_found", "لم أجد هذا في ذاكرتي. اكتب «ماذا تتذكرين؟» لترى الأرقام."
            )
        if len(target) > 1:
            ids = "، ".join(str(r.id) for r in target[:10])
            return MemoryOutcome(
                "memory_ambiguous", f"أكثر من معلومة تطابق ({ids}). حدّد الرقم: «انسي رقم …»."
            )
        record = target[0]
        self._records = [r for r in self._records if r.id != record.id]
        self._dirty = True
        return MemoryOutcome("memory_forgotten", f"نسيتُ رقم {record.id} 🗑️: {record.text}")

    def describe(self, *, limit: int = 30) -> MemoryOutcome:
        """List records, newest first, with number, layer and date."""
        if not self._records:
            return MemoryOutcome(
                "memory_listed",
                "ذاكرتي الدائمة فارغة حتى الآن. لتحفظ شيئاً اكتب: «تذكّري: …».",
            )
        newest = list(reversed(self._records))[:limit]
        lines = [f"{r.id}. [{_KIND_LABELS[r.kind]} · {r.created_at[:10]}] {r.text}" for r in newest]
        more = len(self._records) - len(newest)
        tail = f"\n… و{more} أخرى." if more > 0 else ""
        return MemoryOutcome(
            "memory_listed",
            f"في ذاكرتي الدائمة 🧠 (العدد: {len(self._records)})\n"
            + "\n".join(lines)
            + tail
            + "\nللحذف: «انسي رقم …».",
        )

    def recall(self, query: str, *, k: int = 6) -> list[str]:
        """Return up to ``k`` records relevant to ``query`` as prompt lines.

        Lexical cosine over :func:`celia_memory.lexical_embed_text`; when
        nothing overlaps (e.g. «كمّلنا فين؟»), the most recent records are
        used, so CELIA still knows the latest context.
        """
        if not self._records or k <= 0:
            return []
        qvec = lexical_embed_text(query or "")
        scored = sorted(
            ((cosine_similarity(qvec, lexical_embed_text(r.text)), r.id, r) for r in self._records),
            key=lambda item: (item[0], item[1]),
            reverse=True,
        )
        chosen = [r for score, _, r in scored if score > 0.05][:k]
        if not chosen:
            chosen = list(reversed(self._records))[:k]
        return [f"#{r.id} ({_KIND_LABELS[r.kind]}، {r.created_at[:10]}): {r.text}" for r in chosen]

    def flush(self) -> bool:
        """Persist pending changes; returns ``True`` if anything was written."""
        if not self._dirty:
            return False
        self._backend.save(
            [r.model_dump(mode="json") for r in sorted(self._records, key=lambda r: r.id)]
        )
        self._dirty = False
        return True


__all__ = [
    "DEFAULT_MAX_RECORDS",
    "MAX_RECORD_CHARS",
    "SHEET_HEADERS",
    "CommandKind",
    "ListMemoryBackend",
    "MemoryBackend",
    "MemoryCommand",
    "MemoryKind",
    "MemoryOutcome",
    "MemoryRecord",
    "PersonalMemory",
    "classify_kind",
    "looks_sensitive",
    "parse_memory_command",
]
