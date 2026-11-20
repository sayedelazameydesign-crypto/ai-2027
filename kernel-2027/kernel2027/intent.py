"""Intent resolution — turn "I want X" into an executable plan, without prompts.

The problem this solves
-----------------------
Every other layer in this repository assumes the user already knows what they
want, in the shape the system accepts: a goal string, a step plan, a tool call.
That is a reasonable assumption for an operator and an unreasonable one for the
person the product is actually for — someone who says "عايز أبدأ مشروع ومش عارف
أبدأ منين" and has never heard of context engineering.

So the rule this module enforces is the product's central promise:

    **The user states what they want. The system decides how.**

Concretely, that means three things, and each is a design decision here:

1. **Questions are the exception, not the interface.** A question is only asked
   when its answer *changes the plan*. Everything else becomes an assumption.
2. **Every question has a default.** "I don't know" is always a valid answer and
   must never block work. A system that stops until the user learns its
   vocabulary has failed the only test that matters.
3. **Assumptions are visible.** The user is told what was assumed and can correct
   any of it afterwards. Silent assumption is how a helpful system becomes an
   unpredictable one.

Why this is deterministic
-------------------------
This is a rule engine, not a model call. Two reasons, both load-bearing:

* **It must work with no provider, no key and no network** — the zero-spend
  guard in this repository is not negotiable, and understanding the user is not
  a good place to start spending.
* **It must be testable.** "Did the system understand?" is only answerable if
  the same sentence always produces the same reading. An LLM may *refine* this
  result later; it may never be required for it.

It is deliberately bilingual (Arabic + English) because the product is.

What this module does not do
----------------------------
It does not execute anything, choose models, or touch the network. It produces a
:class:`Resolution` — a structured, auditable reading of one utterance — which
the caller turns into a ``TaskSpec``. Keeping the decision separate from the
action is what makes the decision reviewable when it is wrong.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from .contracts import KernelError

# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #

#: Domain signals. Latin matches on lower-cased text, Arabic on normalised text.
#: Weights matter: a word like "موقع" is unambiguous, "عمل" is not.
_DOMAIN_SIGNALS: dict[str, tuple[tuple[str, float], ...]] = {
    "website": (
        ("موقع", 3.0), ("صفحة", 2.0), ("متجر", 2.5), ("واجهة", 1.5),
        ("website", 3.0), ("web site", 3.0), ("landing", 2.5), ("homepage", 2.5),
        ("portfolio", 2.0), ("store", 1.5), ("app", 1.5), ("تطبيق", 1.5),
    ),
    "document": (
        ("مستند", 2.5), ("وثيقة", 2.5), ("تقرير", 2.5), ("عرض", 2.0),
        ("سيرة", 2.5), ("cv", 2.5), ("document", 2.5), ("report", 2.5),
        ("presentation", 2.5), ("slides", 2.5), ("resume", 2.5), ("pdf", 2.0),
        ("اكتب لي", 1.5), ("write me", 1.5),
    ),
    "research": (
        ("ابحث", 2.5), ("بحث", 2.0), ("قارن", 2.5), ("مقارنة", 2.5),
        ("حلل", 2.0), ("تحليل", 2.0), ("ادرس", 1.5),
        ("research", 2.5), ("compare", 2.5), ("comparison", 2.5),
        ("analyze", 2.0), ("analyse", 2.0), ("find out", 2.0), ("investigate", 2.0),
    ),
    "organize": (
        ("نظم", 2.5), ("تنظيم", 2.5), ("خطة", 2.0), ("جدول", 2.0),
        ("تذكير", 2.5), ("مواعيد", 2.0), ("مهامي", 2.0),
        ("organize", 2.5), ("organise", 2.5), ("plan my", 2.0), ("schedule", 2.5),
        ("remind", 2.5), ("reminder", 2.5), ("to-do", 2.0), ("todo", 2.0),
    ),
    "learn": (
        ("اشرح", 2.5), ("علمني", 2.5), ("فهمني", 2.5), ("تعلم", 2.0), ("درس", 1.5),
        ("explain", 2.5), ("teach me", 2.5), ("learn", 2.0), ("tutorial", 2.0),
        ("help me understand", 2.5),
    ),
    "travel": (
        ("سفر", 2.5), ("رحلة", 2.5), ("فندق", 2.0), ("طيران", 2.0), ("مصيف", 1.5),
        ("travel", 2.5), ("trip", 2.5), ("flight", 2.0), ("hotel", 2.0),
        ("itinerary", 2.5), ("vacation", 2.0),
    ),
    "business": (
        ("مشروع", 2.5), ("شركة", 2.0), ("تسويق", 2.5), ("مبيعات", 2.0),
        ("عمل حر", 2.0), ("فكرة", 1.0),
        ("business", 2.5), ("startup", 2.5), ("marketing", 2.5),
        ("sales", 2.0), ("launch", 1.5), ("my project", 2.0),
    ),
    "code": (
        ("كود", 2.5), ("برمجة", 2.5), ("خطأ", 1.5), ("عطل", 2.0), ("اصلح", 2.0),
        ("code", 2.5), ("programming", 2.5), ("bug", 2.0), ("debug", 2.5),
        ("fix", 1.5), ("script", 1.5), ("function", 1.0),
    ),
    "email": (
        ("بريد", 2.5), ("ايميل", 2.5), ("رسالة", 1.5), ("وارد", 2.0),
        ("email", 2.5), ("e-mail", 2.5), ("inbox", 2.5), ("reply", 1.5),
    ),
}

#: Words that signal the user does NOT want to be asked technical questions.
#: Detecting these is the difference between "helpful" and "interrogation".
_NOVICE_SIGNALS = (
    "مش عارف", "لا أعرف", "لا اعرف", "مبتدئ", "مبتدئة", "مش فاهم", "ابتدائي",
    "don't know", "dont know", "no idea", "beginner", "not technical",
    "i'm not sure", "im not sure", "never done", "first time",
)

#: Words that signal time pressure — a deadline changes what the plan optimises.
_URGENT_SIGNALS = (
    "بسرعة", "سريع", "ضروري", "النهاردة", "بكرة", "اسرع", "عاجل",
    "urgent", "asap", "quickly", "today", "tomorrow", "deadline", "fast",
)

#: Domains that produce a *file* the user keeps, and what to call it.
_ARTIFACT_DOMAINS = {
    "website": ("static site", ("index.html",)),
    "document": ("document", ("document.md",)),
    "research": ("report", ("report.md",)),
    "organize": ("plan", ("plan.md",)),
    "learn": ("explanation", ("explanation.md",)),
    "travel": ("itinerary", ("itinerary.md",)),
    "business": ("business plan", ("plan.md",)),
    "code": ("code change", ("change.diff",)),
    "email": ("draft", ("draft.txt",)),
}

#: Per-domain plan templates: (step suffix, agent, action, depends_on_suffixes).
#: These are *shapes*, not fixed work: the runner fills in the specifics.
_DOMAIN_PLANS: dict[str, tuple[tuple[str, str, str, tuple[str, ...]], ...]] = {
    "website": (
        ("brief", "planner", "clarify_goal", ()),
        ("build", "builder", "write_artifact", ("brief",)),
        ("verify", "verifier", "verify_artifact", ("build",)),
    ),
    "document": (
        ("brief", "planner", "clarify_goal", ()),
        ("build", "builder", "write_artifact", ("brief",)),
        ("verify", "verifier", "verify_artifact", ("build",)),
    ),
    "research": (
        ("brief", "planner", "clarify_goal", ()),
        ("gather", "researcher", "collect_sources", ("brief",)),
        ("build", "builder", "write_artifact", ("gather",)),
        ("verify", "verifier", "verify_artifact", ("build",)),
    ),
    "organize": (
        ("brief", "planner", "clarify_goal", ()),
        ("build", "builder", "write_artifact", ("brief",)),
        ("verify", "verifier", "verify_artifact", ("build",)),
    ),
    "learn": (
        ("brief", "planner", "clarify_goal", ()),
        ("build", "teacher", "write_artifact", ("brief",)),
        ("verify", "verifier", "verify_artifact", ("build",)),
    ),
    "travel": (
        ("brief", "planner", "clarify_goal", ()),
        ("build", "researcher", "write_artifact", ("brief",)),
        ("verify", "verifier", "verify_artifact", ("build",)),
    ),
    "business": (
        ("brief", "planner", "clarify_goal", ()),
        ("build", "builder", "write_artifact", ("brief",)),
        ("verify", "verifier", "verify_artifact", ("build",)),
    ),
    "code": (
        ("brief", "planner", "clarify_goal", ()),
        ("build", "builder", "write_artifact", ("brief",)),
        ("verify", "verifier", "verify_artifact", ("build",)),
    ),
    "email": (
        ("brief", "planner", "clarify_goal", ()),
        ("build", "writer", "write_artifact", ("brief",)),
        ("approve", "verifier", "verify_artifact", ("build",)),
    ),
}

#: Domains whose last step leaves the machine under the user's identity, or
#: costs money. Only these stop for consent. Producing a draft — a plan, a site,
#: a report — never does: asking permission to think is how a helper turns into
#: an obstacle.
_NEEDS_CONSENT = {"email"}

#: Human-facing Arabic labels so `explain()` reads like a person, not a schema.
_SETTING_LABELS = {
    "language": "اللغة",
    "audience": "الجمهور",
    "quality": "مستوى الإتقان",
    "pages": "عدد الصفحات",
    "hosting": "الاستضافة",
    "length": "الطول",
    "travellers": "عدد المسافرين",
    "scope": "نطاق التعديل",
    "explain_level": "أسلوب الشرح",
    "deadline": "الموعد النهائي",
    "budget_usd": "الميزانية",
    "reference_url": "الرابط المرجعي",
}

#: Arabic names for the deliverable, so the one line the user actually reads
#: ("your output will be …") is not half-English.
_ARTIFACT_LABELS = {
    "static site": "موقع جاهز للنشر",
    "document": "مستند",
    "report": "تقرير",
    "plan": "خطة",
    "explanation": "شرح",
    "itinerary": "برنامج رحلة",
    "business plan": "خطة عمل",
    "code change": "تعديل برمجي",
    "draft": "مسودة رسالة",
}

_VALUE_LABELS = {
    "ar": "العربية",
    "general": "قارئ عام",
    "good enough to use today": "جاهز للاستخدام اليوم",
    "static, no account needed": "استضافة ثابتة بلا حساب",
    "no-jargon, step-by-step": "بلا مصطلحات، خطوة بخطوة",
    "as soon as possible": "في أسرع وقت",
    "about 2 pages": "صفحتان تقريبًا",
    "smallest change that fixes it": "أصغر تعديل يحل المشكلة",
}


class IntentError(KernelError):
    """Raised when an utterance cannot be read at all (empty, unusable)."""


# --------------------------------------------------------------------------- #
# Value objects
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Question:
    """A question that is worth the user's time, with a usable default.

    ``default`` is what the system will do if the user says "I don't know" or
    nothing at all. A question without a default is a blocker, and blockers only
    exist for consent — never for configuration.
    """

    key: str
    ask: str
    why: str
    default: str
    options: tuple[str, ...] = ()

    def answered(self, value: str | None) -> str:
        """Return ``value`` if given, else the default. Silence is never an error."""
        cleaned = (value or "").strip()
        return cleaned or self.default

    def to_dict(self) -> dict[str, Any]:
        """Serialise for logs and for the UI that renders it."""
        return {
            "key": self.key,
            "ask": self.ask,
            "why": self.why,
            "default": self.default,
            "options": list(self.options),
        }


@dataclass(frozen=True)
class Setting:
    """A value the plan depends on, and whether the user stated it or we assumed."""

    key: str
    value: str
    source: str = "explicit"

    def to_dict(self) -> dict[str, Any]:
        """Serialise for logs and for the 'here is what I assumed' reply."""
        return {"key": self.key, "value": self.value, "source": self.source}


@dataclass(frozen=True)
class Step:
    """One planned unit, in the shape ``fleet2027.PlanStep`` accepts."""

    step_id: str
    agent: str
    action: str
    depends_on: tuple[str, ...] = ()
    critical: bool = True
    verification: tuple[str, ...] = ()


@dataclass(frozen=True)
class Resolution:
    """The system's reading of one utterance — structured, auditable, correctable."""

    utterance: str
    domain: str
    confidence: float
    goal: str
    artifact: str
    steps: tuple[Step, ...]
    settings: tuple[Setting, ...] = field(default_factory=tuple)
    questions: tuple[Question, ...] = field(default_factory=tuple)
    alternatives: tuple[tuple[str, float], ...] = field(default_factory=tuple)
    needs_consent: bool = False

    @property
    def assumed(self) -> tuple[Setting, ...]:
        """Everything the system decided on the user's behalf."""
        return tuple(item for item in self.settings if item.source != "explicit")

    @property
    def understood(self) -> tuple[Setting, ...]:
        """The facts taken from the user's own words.

        Shown back to them because an unacknowledged fact is indistinguishable
        from an ignored one: someone who says "3 people, $2000" and sees only a
        list of assumptions cannot tell whether their numbers were read at all.
        """
        return tuple(item for item in self.settings if item.source == "explicit")

    @property
    def can_start(self) -> bool:
        """Whether work may begin now — true even with questions outstanding.

        This is the whole design in one property: an open question is not a
        blocker, because every question carries a default. Only consent blocks.
        """
        return bool(self.steps) and not self.needs_consent

    @property
    def is_confident(self) -> bool:
        """Whether the domain reading is safe to act on without asking which one."""
        return self.confidence >= CONFIDENCE_FLOOR

    @property
    def artifact_label(self) -> str:
        """The deliverable in the user's language, for the one-line summary."""
        return _ARTIFACT_LABELS.get(self.artifact, self.artifact)

    def settings_map(self) -> dict[str, str]:
        """All resolved settings as a plain mapping."""
        return {item.key: item.value for item in self.settings}

    def explain(self) -> str:
        """A short, jargon-free statement of what will happen and what was assumed.

        Written for the person who knows nothing about the system: no model
        names, no step ids, no tool names. If this text needs a manual, the
        layer has failed.
        """
        lines = [f"فهمت أنك تريد: {self.goal}"]
        if self.understood:
            shown = []
            for item in self.understood[:6]:
                label = _SETTING_LABELS.get(item.key, item.key)
                value = _VALUE_LABELS.get(item.value, item.value)
                shown.append(f"{label}: {value}")
            lines.append("أخذت من كلامك: " + " · ".join(shown))
        if self.assumed:
            lines.append("سأفترض ما يلي (يمكنك تصحيحه في أي وقت):")
            for item in self.assumed[:6]:
                label = _SETTING_LABELS.get(item.key, item.key)
                value = _VALUE_LABELS.get(item.value, item.value)
                lines.append(f"  • {label}: {value}")
        if self.questions:
            lines.append("سؤال واحد فقط قد يغيّر النتيجة (وإن لم تجب، سأكمل بالافتراض):")
            for question in self.questions[:2]:
                lines.append(f"  • {question.ask} → الافتراضي: {question.default}")
        if not self.is_confident and self.alternatives:
            others = ", ".join(name for name, _ in self.alternatives[:3])
            lines.append(f"لست متأكدًا من النوع؛ الاحتمالات: {others}.")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the whole reading, for the audit log or the UI."""
        return {
            "utterance": self.utterance,
            "domain": self.domain,
            "confidence": round(self.confidence, 3),
            "goal": self.goal,
            "artifact": self.artifact,
            "can_start": self.can_start,
            "needs_consent": self.needs_consent,
            "settings": [item.to_dict() for item in self.settings],
            "assumed": [item.to_dict() for item in self.assumed],
            "questions": [item.to_dict() for item in self.questions],
            "steps": [
                {
                    "step_id": step.step_id,
                    "agent": step.agent,
                    "action": step.action,
                    "depends_on": list(step.depends_on),
                }
                for step in self.steps
            ],
            "alternatives": [{"domain": name, "score": round(score, 3)}
                             for name, score in self.alternatives],
        }


#: Below this, the system says which domains it is torn between instead of
#: guessing silently. Tuned so an unambiguous sentence never triggers it.
CONFIDENCE_FLOOR = 0.45

#: How much the top domain must beat the runner-up by, or the reading is
#: reported as ambiguous. A near-tie is a coin flip and must be surfaced.
_MARGIN = 1.35

#: A score at or above this means the sentence named the outcome outright
#: ("موقع", "report", "ابحث"). Such a word is not made uncertain by an
#: incidental mention nearby: in "اعمل لي موقع لمشروعي" the *website* is the
#: object and "مشروعي" merely says whose it is. Treating the two as a tie would
#: make the system ask a question the user already answered.
_STRONG_SIGNAL = 3.0


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #

_ARABIC_DIACRITICS = re.compile(r"[\u064B-\u0652\u0670\u0640]")
_WHITESPACE = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Fold case, strip Arabic diacritics and tatweel, collapse whitespace.

    Applied to both the signals and the utterance so that "مَوْقِع" and "موقع"
    are the same word. Without this the Arabic half of the vocabulary would only
    match text that was typed without diacritics — which is most text, but not
    all of it, and the failure would be silent.
    """
    folded = unicodedata.normalize("NFKC", text or "")
    folded = _ARABIC_DIACRITICS.sub("", folded)
    folded = folded.replace("\u0623", "\u0627").replace("\u0625", "\u0627")
    folded = folded.replace("\u0649", "\u064a").replace("\u0629", "\u0647")
    return _WHITESPACE.sub(" ", folded.strip().lower())


def _score_domains(text: str) -> dict[str, float]:
    """Sum weighted signal hits per domain over normalised text."""
    scores: dict[str, float] = {}
    for domain, signals in _DOMAIN_SIGNALS.items():
        total = 0.0
        for phrase, weight in signals:
            needle = normalise(phrase)
            if not needle:
                continue
            # Word-boundary match for latin to avoid "app" inside "happy";
            # plain substring for Arabic, where word splitting is unreliable.
            if needle.isascii():
                if re.search(rf"(?<![a-z]){re.escape(needle)}(?![a-z])", text):
                    total += weight
            elif needle in text:
                total += weight
        if total:
            scores[domain] = round(total, 3)
    return scores


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #

_URL = re.compile(r"https?://[^\s<>\"')]+")
#: NB: these run against *normalised* text, so every Arabic alternative must be
#: spelled post-normalisation (ايام not أيام, ساعه not ساعة). Writing them in
#: display form silently never matches, and the failure is invisible: the plan
#: would quietly assume a deadline instead of reading the one that was given.
_DEADLINE = re.compile(
    r"\b(?:in|within|by)\s+(\d{1,3})\s*(day|days|week|weeks|hour|hours|month|months)\b"
    r"|(?:خلال|في غضون)\s*(\d{1,3})\s*(يوم|ايام|اسبوع|اسابيع|ساعه|ساعات|شهر|شهور)"
)
_BUDGET = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)|(\d[\d,]*)\s*(?:دولار|جنيه|ريال|درهم)")
#: Party size, in either order — "٢ أشخاص" and "عدد المسافرين 2" are both
#: normal in Arabic, and English puts the number first. Missing one form would
#: mean silently assuming a party size the user had already given.
_PEOPLE = re.compile(
    r"(\d{1,2})\s*(?:اشخاص|شخص|مسافر|مسافرين|افراد)"
    r"|(?:اشخاص|شخص|مسافر|مسافرين|افراد)\s*(\d{1,2})"
    r"|\b(\d{1,2})\s*(?:people|persons?|persons|travell?ers?|guests?|adults?)\b"
    r"|\b(?:people|persons?|travell?ers?|guests?|adults?)\s*[:=]?\s*(\d{1,2})\b"
)


def _extract_settings(text: str, *, novice: bool, urgent: bool, domain: str) -> list[Setting]:
    """Pull the concrete facts a plan actually needs. Never guesses a value."""
    found: list[Setting] = []
    urls = _URL.findall(text)
    if urls:
        found.append(Setting("reference_url", urls[0]))
    deadline = _DEADLINE.search(text)
    if deadline:
        number = deadline.group(1) or deadline.group(3) or ""
        unit = deadline.group(2) or deadline.group(4) or ""
        if number and unit:
            found.append(Setting("deadline", f"{number} {unit}"))
    elif urgent:
        # Inferred from "quickly/asap", not stated as a date — so it is an
        # assumption the user is told about and may correct, never a fact.
        found.append(Setting("deadline", "as soon as possible", source="assumed"))
    people = _PEOPLE.search(text)
    if people:
        count = next((group for group in people.groups() if group), None)
        if count:
            found.append(Setting("travellers", count))
    budget = _BUDGET.search(text)
    if budget:
        amount = (budget.group(1) or budget.group(2) or "").replace(",", "")
        if amount:
            found.append(Setting("budget_usd", amount))
    if novice:
        # Recorded as a setting, not just a flag: the plan must read it, or the
        # "user knows nothing" promise is only a comment in the source. Marked
        # "assumed" because the user never said it — they said they were stuck.
        found.append(
            Setting("explain_level", "no-jargon, step-by-step", source="assumed")
        )
    return found


def _default_settings(domain: str) -> list[Setting]:
    """The assumptions that let work begin with no questions asked."""
    base = [
        Setting("language", "ar", source="assumed"),
        Setting("audience", "general", source="assumed"),
        Setting("quality", "good enough to use today", source="assumed"),
    ]
    if domain == "website":
        base.append(Setting("pages", "1", source="assumed"))
        base.append(Setting("hosting", "static, no account needed", source="assumed"))
    elif domain in {"document", "research", "business"}:
        base.append(Setting("length", "about 2 pages", source="assumed"))
    elif domain == "travel":
        base.append(Setting("travellers", "1", source="assumed"))  # ask about it
    elif domain == "code":
        base.append(Setting("scope", "smallest change that fixes it", source="assumed"))
    return base


def _questions_for(domain: str, settings: list[Setting]) -> list[Question]:
    """Questions that materially change the plan — at most two, ever.

    The cap is deliberate. A novice answering five questions is doing the
    system's job for it; the goal is zero questions in the common case, and one
    or two only when the answer genuinely reshapes the output.
    """
    catalogue: dict[str, tuple[Question, ...]] = {
        "website": (
            Question(
                key="purpose",
                ask="ما الهدف من الموقع: عرض عمل، متجر، أم مجرد صفحة تعريفية؟",
                why="يحدد هذا شكل الصفحات وما نحتاجه فعلًا.",
                default="صفحة تعريفية واحدة",
                options=("عرض عمل", "متجر", "صفحة تعريفية"),
            ),
            Question(
                key="brand",
                ask="هل لديك اسم أو شعار تريد استخدامه؟",
                why="يوفّر وقتًا ويمنع اختراع اسم لا يناسبك.",
                default="سأقترح اسمًا مؤقتًا ويمكنك تغييره",
            ),
        ),
        "document": (
            Question(
                key="reader",
                ask="لمن هذا المستند؟",
                why="المستوى واللغة يتغيران تمامًا حسب القارئ.",
                default="قارئ عام",
            ),
        ),
        "research": (
            Question(
                key="depth",
                ask="هل تريد مقارنة سريعة أم تحليلًا مفصلًا بمصادر؟",
                why="يحدد عدد المصادر وطول التقرير.",
                default="مقارنة سريعة بمصادر أساسية",
                options=("سريعة", "مفصلة"),
            ),
        ),
        "travel": (
            Question(
                key="travellers",
                ask="كم شخصًا في الرحلة؟",
                why="الأعداد تغيّر التكلفة وأماكن الإقامة.",
                default="شخص واحد",
            ),
        ),
        "code": (
            Question(
                key="symptom",
                ask="ما الذي يحدث بالضبط، وما الذي تتوقع أن يحدث؟",
                why="بدون الفرق بين الاثنين يصبح الإصلاح تخمينًا.",
                default="سأكتشف الفرق بنفسي من الأدلة",
            ),
        ),
    }
    chosen = catalogue.get(domain, ())
    # Only an EXPLICIT answer removes a question. A default is not an answer:
    # silencing the question because we guessed a value would leave the user
    # unaware that a guess was made at all.
    explicit = {item.key for item in settings if item.source == "explicit"}
    return [question for question in chosen if question.key not in explicit][:2]


#: What each action must prove. Empty means "nothing to check", and the fleet's
#: independent verifier treats that as a failure — correctly. A step that
#: declares no evidence is a step nobody can trust, so every action that
#: produces something must name the check that proves it. This mapping existed
#: only for ``write_artifact`` at first, which made every verification step
#: dead-letter with "no check could be computed".
_VERIFICATION: dict[str, tuple[str, ...]] = {
    "write_artifact": ("artifact_sha256",),
    "run_tests": ("exit_code_zero", "test_passed"),
    "verify_artifact": ("output_non_empty",),
    "collect_sources": ("output_non_empty",),
}


def _build_steps(domain: str, task_key: str) -> tuple[Step, ...]:
    """Materialise the domain's plan template into concrete, ordered steps."""
    template = _DOMAIN_PLANS.get(domain)
    if template is None:
        raise IntentError(f"no plan template for domain {domain!r}")
    steps: list[Step] = []
    for suffix, agent, action, deps in template:
        steps.append(
            Step(
                step_id=f"{task_key}_{suffix}",
                agent=agent,
                action=action,
                depends_on=tuple(f"{task_key}_{dep}" for dep in deps),
                critical=suffix != "approve",
                verification=_VERIFICATION.get(action, ()),
            )
        )
    return tuple(steps)


def _make_goal(utterance: str, domain: str) -> str:
    """Restate the request as a goal, keeping the user's own words.

    Deliberately not paraphrased: the user's sentence is the most reliable
    evidence of what they meant, and rewriting it invites confident error.
    """
    stripped = _WHITESPACE.sub(" ", utterance or "").strip()
    if not stripped:
        raise IntentError("utterance is empty")
    verb = {
        "website": "بناء موقع",
        "document": "إعداد مستند",
        "research": "إجراء بحث",
        "organize": "تنظيم خطة",
        "learn": "شرح موضوع",
        "travel": "تخطيط رحلة",
        "business": "إعداد خطة عمل",
        "code": "إصلاح/بناء برمجي",
        "email": "صياغة رسالة",
    }[domain]
    return f"{verb} — «{stripped}»"


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def resolve(utterance: str, *, task_key: str = "intent") -> Resolution:
    """Read one utterance and return a plan the fleet can execute.

    The contract, stated plainly:

    * empty or meaningless input raises :class:`IntentError` — never a silent
      "here is a generic plan";
    * the returned steps always exist for a recognised domain, so ``can_start``
      is true even when questions are outstanding;
    * anything the system decided is listed in ``assumed`` and is correctable.

    ``task_key`` prefixes generated step ids so several utterances can live in
    one task without colliding.
    """
    cleaned = _WHITESPACE.sub(" ", utterance or "").strip()
    if not cleaned:
        raise IntentError("utterance is empty — nothing to understand")
    if len(cleaned) < 2:
        raise IntentError(f"utterance {cleaned!r} is too short to act on")

    text = normalise(cleaned)
    scores = _score_domains(text)
    if not scores:
        raise IntentError(
            "could not recognise what you want to do — try naming the outcome, "
            "e.g. 'أريد موقعًا' or 'organize my week'"
        )

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    domain, top = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    confidence = top / (top + runner_up) if top + runner_up else 0.0
    decisively_named = top >= _STRONG_SIGNAL
    if runner_up and top / runner_up < _MARGIN and not decisively_named:
        # A near-tie is reported, not hidden. Acting on a coin flip is how a
        # system earns a reputation for doing the wrong thing confidently.
        # But a named outcome is not a coin flip — see _STRONG_SIGNAL.
        confidence = min(confidence, CONFIDENCE_FLOOR - 0.01)

    novice = any(normalise(signal) in text for signal in _NOVICE_SIGNALS)
    urgent = any(normalise(signal) in text for signal in _URGENT_SIGNALS)

    settings = _extract_settings(text, novice=novice, urgent=urgent, domain=domain)
    for item in _default_settings(domain):
        if not any(existing.key == item.key for existing in settings):
            settings.append(item)

    artifact, _files = _ARTIFACT_DOMAINS[domain]

    return Resolution(
        utterance=cleaned,
        domain=domain,
        confidence=confidence,
        goal=_make_goal(cleaned, domain),
        artifact=artifact,
        steps=_build_steps(domain, task_key),
        settings=tuple(settings),
        questions=tuple(_questions_for(domain, settings)),
        alternatives=tuple(ranked[:3]),
        needs_consent=domain in _NEEDS_CONSENT,
    )


def check_feasible(resolution: Resolution, known: Any) -> tuple[str, ...]:
    """Return the agents this plan needs that the registry cannot provide.

    Why this exists, in one sentence: **a plan that cannot run must be rejected
    when it is made, not when it fails.** The first end-to-end run of this
    feature ended in ``dead_letter`` — the plan named ``planner``/``builder``/
    ``verifier``, the registry had ``analyst``/``backend-engineer``/``qa-engineer``,
    and nothing noticed until a durable task had already been written to the
    ledger and marked failed. That is the worst possible time to find out: the
    user has a broken task in their history, and the error surfaces as a fleet
    failure rather than as "this system cannot do that yet".

    ``known`` is any iterable of **agent names** (``registry.names`` in the
    fleet, or a plain set in a test), so the kernel still does not know what an
    engine's registry is. Capability tags are deliberately not accepted: a step
    names an agent, and ``handler_for`` may resolve a tag to a *different* agent
    than the one the plan asked for — silently substituting an agent is how a
    plan stops meaning what it says.
    """
    available = {str(item) for item in known}
    return tuple(
        sorted({step.agent for step in resolution.steps if step.agent not in available})
    )


def to_task_spec(
    resolution: Resolution,
    *,
    task_id: str,
    build_step: Any,
    build_spec: Any,
    created_by: str = "intent",
) -> Any:
    """Hand the reading to an engine, without the kernel knowing which engine.

    This is the boundary rule of the whole package expressed as a signature.
    A previous version imported ``fleet2027`` here to construct the ``TaskSpec``
    itself — convenient, and wrong: it would have made the shared kernel depend
    on one engine, so that engine's contract changes could break every other
    consumer, and the kernel could no longer be tested or reasoned about alone.
    Its own ``test_kernel_imports_nothing_from_an_engine`` caught it.

    So the caller supplies the two constructors (``PlanStep`` and ``TaskSpec``
    for the fleet, or anything else with the same shape). The kernel decides
    *what* to build — ids, ordering, settings, assumptions — and the engine
    decides *how* it is represented.
    """
    # `domain` rides along with the settings because every downstream decision
    # (which file to write, which template to use, what to call the artifact)
    # depends on it, and a step that cannot see the domain silently produces the
    # fallback. It did exactly that on the first end-to-end run: a website
    # request produced `document.md`.
    settings = {**resolution.settings_map(), "domain": resolution.domain}
    steps = [
        build_step(
            step_id=step.step_id,
            agent=step.agent,
            action=step.action,
            depends_on=step.depends_on,
            inputs={"settings": settings},
            critical=step.critical,
            verification=step.verification,
        )
        for step in resolution.steps
    ]
    return build_spec(
        task_id=task_id,
        goal=resolution.goal,
        steps=tuple(steps),
        created_by=created_by,
        metadata={
            "domain": resolution.domain,
            "confidence": round(resolution.confidence, 3),
            "artifact": resolution.artifact,
            "assumed": [item.key for item in resolution.assumed],
        },
    )
