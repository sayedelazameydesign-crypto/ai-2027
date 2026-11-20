"""Quality scoring — a ruler, built before anything is measured with it.

Why this exists
---------------
Every gate in this repository measures *reliability*: did the step finish, is
the digest right, does the chain verify. ``fleet-2027/gate.py`` says so itself,
three times, in as many words: *"proves recovery and failure isolation — not the
quality of any LLM."*

That honesty left a real hole. "All eight domains succeed" meant "eight files
with correct digests", not "eight files worth reading". Without a ruler, any
future change to a prompt or a model is judged by impression, and impression
cannot be compared across runs.

So this module builds the ruler. It scores a *file on disk*, and it does not
know or care what wrote the file — a template, a model, or a person. That
ignorance is the design: a scorer that knows the author will favour it.

What it cannot do
-----------------
It cannot tell you whether the writing is *good*. It can tell you whether the
artifact is well-formed, on-topic, complete, free of filler, honest about what
it does not know, and usable without repair. Those six things are checkable
mechanically and they correlate with usefulness. Taste is not measurable here
and is not claimed.

The one test that matters
-------------------------
The deterministic scaffold in ``fleet2027.generalist`` is deliberately thin. It
must score **low**. If this module ever rates that scaffold highly, the scorer
has stopped measuring and started flattering, and its numbers mean nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Tags whose *contents* are not prose. A style block and a script block are
#: instructions to a browser or an interpreter, never text a reader reads.
_NON_PROSE_TAGS = ("script", "style", "head")

#: Below this, an artifact is not worth showing to a user as a deliverable.
#: Set from measurement, not taste, and raised once: at 0.50 the deterministic
#: scaffold — which literally says "replace this text" — was rated usable at
#: 0.58. A floor that accepts an admitted stub is not a floor. 0.70 sits between
#: the measured scaffold (0.58) and measured hand-written content (0.91), and
#: docs/QUALITY-BASELINE.md records both numbers.
USABLE_FLOOR = 0.70

#: Per-criterion weights, each with the reason it has that weight. The sum is
#: 1.0, and the weights are a product decision, so they live in the kernel and
#: not in an engine: two engines scoring the same file must agree.
WEIGHTS: dict[str, float] = {
    # SUBSTANCE outranks every other criterion, and the reason is a measurement:
    # without it, the deterministic scaffold — which contains the sentence
    # "replace this text with your real content" — scored 0.94 and was rated
    # usable. Form without substance is exactly the failure this ruler exists to
    # catch, and weighting it below structure hid it completely.
    "substance": 0.30,
    # Structure is binary in practice: a broken page is not a page.
    "structure": 0.15,
    # On-topic is what separates "a report" from "a report about the thing you
    # asked for". High, but below substance.
    "relevance": 0.15,
    # Completeness: a 40-byte answer to a large request is a refusal in disguise.
    "completeness": 0.15,
    # Filler is the most common model failure and the cheapest to detect.
    "no_filler": 0.08,
    # Honesty outranks polish: an artifact that invents sources is worse than a
    # short honest one. Weighted deliberately above usability.
    "honesty": 0.10,
    # Usability last: it is largely implied by structure for HTML, and only
    # meaningful on its own for documents that reference external material.
    "usable": 0.07,
}

#: Words that signal the artifact addresses the domain it was asked for.
#: Deliberately short: a long list would let a template that mentions every
#: domain score as relevant to all of them.
_DOMAIN_TOKENS: dict[str, tuple[str, ...]] = {
    "website": ("<html", "<body", "href", "<h1", "<title", "<p>"),
    # A document's topic words are added, never substituted: the markdown
    # tokens stay because removing one lowers the score of any artifact that
    # scored on it, and the ratchet must never move down. `research` already
    # lists the Arabic "المصدر"; a report that cites its source is on-topic
    # for `document` too, and it scored 0/4 here before these were added.
    "document": (
        "#",
        "##",
        "-",
        "1.",
        "تقرير",
        "الملخص",
        "المصدر",
        "التوصيات",
        "مقدمة",
        "خاتمة",
        "report",
        "summary",
        "source",
        "recommendation",
        "introduction",
    ),
    "research": ("المصدر", "مصدر", "source", "found", "evidence", "بحث"),
    "organize": (
        "خطوه",
        "خطوة",
        "step",
        "اليوم",
        "day",
        "1.",
        "2.",
        "خطوات",
        "steps",
        "جدول",
        "schedule",
        "ترتيب",
        "order",
    ),
    "learn": ("مثال", "example", "لماذا", "because", "يعني", "means"),
    "travel": ("يوم", "day", "فندق", "hotel", "تكلفه", "تكلفة", "cost", "صباح"),
    "business": ("العميل", "customer", "السوق", "market", "الربح", "revenue"),
    "code": ("def ", "return", "function", "{", "}", "="),
    "email": ("تحيات", "regards", "مرحبا", "hello", "شكرا", "thanks"),
}

#: Filler patterns. These are the sentences that add length and no information,
#: and they are the most reliable signal that a model was padding rather than
#: answering.
_FILLER = (
    "بالتأكيد",
    "بالطبع",
    "سأساعدك",
    "كمساعد",
    "كما ذكرت سابقا",
    "كما ذكرت سابقًا",
    "أتمنى أن يكون هذا مفيدا",
    "أتمنى أن يكون هذا مفيدًا",
    "certainly!",
    "of course!",
    "as an ai",
    "i'd be happy to",
    "i would be happy to",
    "great question",
    "let me know if",
    "hope this helps",
    "in conclusion,",
    "it is important to note that",
    "as mentioned earlier",
)

#: Text that addresses the *reader of the template*, not the reader of the
#: product. A file containing any of these is admitting it is unfinished, and
#: no amount of correct markup compensates for that.
#: Phrases that mean "the author did not write this yet". Each must be a
#: *sentence an unfilled template would contain*, never a single vocabulary
#: word. `placeholder` and `insert ` were on this list and rejected a real
#: lesson about CSS placeholders — the artifact was penalised for using the
#: term it was explaining. A criterion that fires on correct work is worse
#: than one that misses: it teaches the writer to avoid accurate words.
_PLACEHOLDERS = (
    "استبدل هذا",
    "استبدل النص",
    "اكتب هنا",
    "ضع النص هنا",
    "محتواك الحقيقي",
    "replace this",
    "replace the text",
    "write here",
    "your content here",
    "lorem ipsum",
    "todo:",
    "tbd",
    "متجرك هنا",
    # Template instructions, phrased as instructions rather than as nouns.
    "insert your ",
    "insert the text",
    "[insert",
    "<insert",
    "your text here",
)

#: Claims that need evidence. A number with a currency or a percentage, and a
#: "source:" line with nothing after it, are the two cheapest ways to look
#: authoritative while saying nothing.
_VAGUE_AUTHORITY = (
    "دراسات تشير",
    "تشير الدراسات",
    "الخبراء يقولون",
    "وفقا للخبراء",
    "وفقًا للخبراء",
    "studies show",
    "experts say",
    "research suggests",
    "statistics show",
)

#: Hedging that is *good*: it tells the reader the artifact knows its limits.
_HEDGES = (
    "لا أعرف",
    "غير متأكد",
    "غير متأكد",
    "يجب التحقق",
    "لم أتحقق",
    "افتراض",
    "assumption",
    "not verified",
    "i don't know",
    "unverified",
    "estimate",
)


@dataclass(frozen=True)
class Criterion:
    """One dimension of quality: a score, and the reason for it."""

    name: str
    score: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the report."""
        return {"name": self.name, "score": round(self.score, 4), "reason": self.reason}


@dataclass(frozen=True)
class QualityReport:
    """The measured quality of one artifact, with every component explained."""

    domain: str
    chars: int
    criteria: tuple[Criterion, ...] = field(default_factory=tuple)

    @property
    def score(self) -> float:
        """Weighted mean of the criteria, in 0..1.

        Weighted rather than averaged because the criteria are not equally
        consequential: a broken page is a total failure, a slightly long one is
        not. The weights are declared in :data:`WEIGHTS` with their reasons.
        """
        if not self.criteria:
            return 0.0
        total = sum(WEIGHTS.get(c.name, 0.0) * c.score for c in self.criteria)
        weight = sum(WEIGHTS.get(c.name, 0.0) for c in self.criteria)
        return round(total / weight, 4) if weight else 0.0

    @property
    def usable(self) -> bool:
        """Whether this artifact is worth handing to a user at all."""
        return self.score >= USABLE_FLOOR

    def failures(self) -> tuple[str, ...]:
        """The criteria that scored below half, worst first.

        Half is not a magic number: a criterion at 0.5 means "partly there",
        and anything below it is a specific thing to fix rather than a vague
        "improve quality".
        """
        weak = [c for c in self.criteria if c.score < 0.5]
        return tuple(c.name for c in sorted(weak, key=lambda c: c.score))

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the benchmark report."""
        return {
            "domain": self.domain,
            "chars": self.chars,
            "score": self.score,
            "usable": self.usable,
            "criteria": [c.to_dict() for c in self.criteria],
            "failures": list(self.failures()),
        }


# --------------------------------------------------------------------------- #
# Criteria
# --------------------------------------------------------------------------- #


def _structure(domain: str, text: str) -> Criterion:
    """Is the artifact well-formed for its kind?

    HTML is the only domain with a checkable grammar here, so it gets the real
    check. Everything else is checked for the shape its format requires.
    """
    low = text.lower()
    if domain == "website":
        has_doctype = "<!doctype" in low
        has_html = "<html" in low and "</html>" in low
        has_body = "<body" in low and "</body>" in low
        opened = len(re.findall(r"<(div|section|article|p)\b", low))
        closed = len(re.findall(r"</(div|section|article|p)>", low))
        balanced = opened == closed or (opened and closed and abs(opened - closed) <= 1)
        parts = [has_doctype, has_html, has_body, bool(balanced)]
        score = sum(parts) / len(parts)
        missing = [
            name
            for name, ok in zip(
                ("doctype", "html", "body", "balanced tags"), parts, strict=True
            )
            if not ok
        ]
        reason = "malformed: " + ", ".join(missing) if missing else "well-formed html"
        return Criterion("structure", score, reason)

    # Markdown and plain text: at least one heading or list, and real line breaks.
    lines = [line for line in text.splitlines() if line.strip()]
    has_heading = any(line.lstrip().startswith("#") for line in lines)
    has_list = any(re.match(r"\s*(?:[-*]|\d+[.)])\s", line) for line in lines)
    multi = len(lines) >= 3
    score = sum([has_heading or has_list, multi]) / 2
    reason = (
        "no heading or list" if not (has_heading or has_list) else "structured"
    ) + ("" if multi else "; too few lines to be an artifact")
    return Criterion("structure", score, reason)


#: Arabic attaches the article and possessive endings to the same root, so a
#: literal substring test misses the word it is looking for: a report saying
#: "التوصية" (one recommendation) contains neither "التوصيات" (the list) nor
#: "توصيات". Measured: a complete 483-character Arabic report about a market
#: scored relevance 0.00 and was called unusable. Stripping these affixes is
#: the narrow fix — it does not lower any score, because a token that matched
#: before still matches.
_AR_ARTICLE = "ال"
_AR_SUFFIXES = ("يات", "ات", "ون", "ين", "ية", "ها", "ه", "ة", "ي", "ا")


def _fold_arabic(word: str) -> str:
    """Reduce an Arabic word to the stem a keyword match should use."""
    word = word.strip()
    if word.startswith(_AR_ARTICLE) and len(word) > len(_AR_ARTICLE) + 2:
        word = word[len(_AR_ARTICLE) :]
    for suffix in _AR_SUFFIXES:
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            word = word[: -len(suffix)]
            break
    return word


def _matches(haystack: str, needle: str) -> bool:
    """Whether `needle` appears in `haystack`, allowing Arabic inflection."""
    if needle.lower() in haystack:
        return True
    folded_needle = _fold_arabic(needle)
    if not folded_needle or folded_needle == needle:
        # Not an inflectable token (markdown syntax, latin word): the literal
        # test above is the whole answer.
        return False
    words = re.findall(r"[^\W\d_]+", haystack)
    return any(_fold_arabic(word) == folded_needle for word in words)


def _relevance(domain: str, text: str) -> Criterion:
    """Does the artifact actually address the domain it was asked for?"""
    tokens = _DOMAIN_TOKENS.get(domain, ())
    if not tokens:
        return Criterion("relevance", 0.5, "no token list for this domain")
    low = text.lower()
    hits = sum(1 for token in tokens if _matches(low, token))
    # Three hits is enough to establish the artifact is about the domain; more
    # than that adds no confidence and would reward keyword stuffing.
    score = min(1.0, hits / 3)
    return Criterion(
        "relevance",
        score,
        f"{hits}/{len(tokens)} domain markers present",
    )


def _completeness(domain: str, text: str) -> Criterion:
    """Is there enough here to be a deliverable rather than a stub?

    Thresholds are deliberately modest. A short, finished page beats a long,
    abandoned one, so passing this does not require length — it only requires
    enough substance that a reader is not left asking "and then?".

    The size measured is the *prose*, not the markup. Measured: the scaffold with
    one sentence removed reported "592 chars against a 800-char floor" while its
    actual text was eight words — the tags and the style block were doing the
    work, and completeness scored 0.74 for an empty page.
    """
    prose = _prose_text(text)
    size = len(text.strip()) if prose is None else len(prose.strip())
    # Raised after measuring: 400 chars let a one-sentence page pass. A page a
    # user would keep is a few paragraphs; 800 is still modest.
    floor = {"website": 800, "email": 200, "code": 80}.get(domain, 400)
    if size == 0:
        return Criterion("completeness", 0.0, "empty")
    score = min(1.0, size / floor)
    return Criterion(
        "completeness", score, f"{size} chars against a {floor}-char floor for {domain}"
    )


def _substance(text: str) -> Criterion:
    r"""Is this written for a reader, or is it a note telling someone else to write it?

    This is the criterion the first version of the scorer lacked, and its
    absence was not theoretical: the scaffold supplies a heading, a paragraph
    that names the audience, and the sentence "replace this text with your real
    content." Every mechanical check passed. It scored 0.94.

    A deliverable that instructs the user to finish it is not a deliverable.

    Two defects were measured here and both made the criterion *laxer* than the
    number it printed:

    1. **Tag names were counted as content.** `[^\W\d_]{3,}` over raw HTML found
       25 "words" in a page whose entire text was four words (موقعي, موقعي,
       الرئيسية, اتصل بنا); 21 of them were html, head, meta, charset, title,
       style, margin, body. That page scored 0.8443 overall — above the 0.70
       floor — and was called usable. Counting text nodes gives 5 words.
    2. **The criterion was all-or-nothing.** Removing one placeholder sentence
       from the scaffold moved it from 0.6506 (rejected) to 0.8927 (usable):
       the presence of one phrase decided the outcome and nothing measured how
       much of the artifact was actual content.

    So the count is over *text nodes*, and the score is graded by how much text
    there is rather than switching on at 25 words.
    """
    low = text.lower()
    found = [phrase for phrase in _PLACEHOLDERS if phrase in low]
    if found:
        # One placeholder means the artifact is a stub. Not a deduction — a
        # floor, because the reader's next action is to write it themselves.
        return Criterion(
            "substance",
            0.05,
            "placeholder text: " + ", ".join(found[:3]),
        )
    # No placeholder: count words in the *prose*, not in the markup.
    prose = _prose_text(text)
    words = re.findall(r"[^\W\d_]{3,}", prose) if prose is not None else []
    if prose is None:
        # Not markup: the raw text is the prose.
        words = re.findall(r"[^\W\d_]{3,}", text)
    count = len(words)
    # Binary at 25 prose words -- the rule that was already here, which was
    # correct; it was counting tags. Measured on prose only: chrome-only page 4,
    # scaffold-minus-a-sentence 8, a real 386-char Arabic report 59, a real HTML
    # page 180. A graded ramp was tried FIRST and removed: it dropped that
    # complete Arabic report to 0.6084, because grading punishes honest brevity
    # as if it were emptiness. Brevity is completeness's job; substance asks only
    # whether prose exists at all.
    if count < 25:
        return Criterion(
            "substance", min(1.0, count / 25), f"only {count} words of content"
        )
    return Criterion("substance", 1.0, f"{count} words of content")


def _prose_text(text: str) -> str | None:
    r"""The text a reader sees, or ``None`` when this is not markup.

    ``None`` rather than the input, because a markdown document has no tags and
    stripping them would be a no-op that silently changed nothing -- the caller
    must know which case it is in.

    Measured: a page whose entire text was four words scored 1.00 on substance
    because 21 of the 25 "words" `[^\W\d_]{3,}` found were tag names -- html,
    head, meta, charset, title, style, margin, body. This function is what
    separates the markup from the prose the reader actually reads.
    """
    lowered = text.lower()
    has_markup = any(f"<{tag}" in lowered for tag in _NON_PROSE_TAGS) or any(
        f"<{tag}" in lowered for tag in ("p", "div", "h1", "html", "body")
    )
    if "<" not in text or not has_markup:
        return None
    stripped = text
    for tag in _NON_PROSE_TAGS:
        stripped = re.sub(
            rf"<{tag}\b[^>]*>.*?</{tag}\s*>",
            " ",
            stripped,
            flags=re.DOTALL | re.IGNORECASE,
        )
    stripped = re.sub(r"<!--.*?-->", " ", stripped, flags=re.DOTALL)
    stripped = re.sub(r"<[^>]+>", " ", stripped)
    return stripped


def _no_filler(text: str) -> Criterion:
    """How much of this is padding?"""
    low = text.lower()
    found = [phrase for phrase in _FILLER if phrase in low]
    if not found:
        return Criterion("no_filler", 1.0, "no filler phrases")
    # One filler phrase is a mannerism; three is padding.
    score = max(0.0, 1.0 - 0.34 * len(found))
    return Criterion("no_filler", score, "filler: " + ", ".join(found[:4]))


def _honesty(text: str) -> Criterion:
    """Does the artifact avoid claiming authority it does not have?

    Two opposing signals, and both matter:

    * **Bad:** "studies show", "experts say" — authority with no source. A
      reader cannot check it, so it reads as knowledge and is not.
    * **Good:** a stated assumption, an admitted gap. This is what an honest
      artifact looks like, and it should *raise* the score, not lower it.

    An artifact with neither is neutral: nothing was claimed, nothing was hidden.
    """
    low = text.lower()
    vague = [phrase for phrase in _VAGUE_AUTHORITY if phrase in low]
    hedges = [phrase for phrase in _HEDGES if phrase in low]
    if vague:
        return Criterion(
            "honesty",
            max(0.0, 0.3 - 0.1 * len(vague)),
            "unsourced authority: " + ", ".join(vague[:3]),
        )
    if hedges:
        return Criterion("honesty", 1.0, "states its limits: " + ", ".join(hedges[:2]))
    return Criterion("honesty", 0.6, "no claims and no stated limits")


def _usable(domain: str, text: str) -> Criterion:
    """Can this be used without repair?

    The main failure this catches is a page or document that depends on
    something the user does not have: a remote stylesheet, a missing image, a
    link to a file nobody wrote.
    """
    if domain == "website":
        remote = re.findall(
            r"""(?:src|href)\s*=\s*["'](?:https?:)?//""", text, re.IGNORECASE
        )
        if remote:
            return Criterion(
                "usable",
                0.2,
                f"{len(remote)} remote dependency/dependencies: needs network to render",
            )
        return Criterion("usable", 1.0, "self-contained")
    # For text artifacts, unusable means pointing at something absent.
    dangling = re.findall(r"\[[^\]]+\]\((?!https?://)[^)]+\)", text)
    if dangling:
        return Criterion(
            "usable", 0.4, f"{len(dangling)} link(s) to files that are not here"
        )
    return Criterion("usable", 1.0, "no dangling references")


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def score(domain: str, text: str) -> QualityReport:
    """Score one artifact against the six criteria.

    ``domain`` is the *intent* domain (``website``, ``travel``, ...), because
    the same text is acceptable as a plan and unacceptable as a web page.

    Deterministic: the same bytes always produce the same report, so a number
    in a document can be reproduced by anyone, on any machine, with no network.
    """
    body = text or ""
    criteria = (
        _substance(body),
        _structure(domain, body),
        _relevance(domain, body),
        _completeness(domain, body),
        _no_filler(body),
        _honesty(body),
        _usable(domain, body),
    )
    return QualityReport(domain=domain, chars=len(body.strip()), criteria=criteria)


def score_file(domain: str, path: str) -> QualityReport:
    """Score a file from disk. The scorer never learns who wrote it.

    Reading from disk rather than accepting a string is deliberate: it is the
    only way to be sure the artifact being scored is the artifact that exists,
    and not a copy the caller produced for the occasion.
    """
    with open(path, encoding="utf-8", errors="replace") as handle:
        return score(domain, handle.read())
