#!/usr/bin/env python3
"""Retrieval scoring for the evidence-gated memory store — v2.

Why this module exists
  v1 scored purely on hashed uni/bi-gram cosine. Measured result on the honest
  evaluation set (`memory/evaluate.py`): 16/16 on queries that reuse corpus
  vocabulary, but only **2/16** on paraphrases. That is not a bug, it is the
  definition of a lexical model — and it was published, not hidden.

  v2 keeps the zero-dependency rule and adds three *declared* devices:

  1. **A hand-written thesaurus** (`SYNONYM_GROUPS`): N concept groups over the
     domain vocabulary (screen printing / kids clothing / pricing / Arabic).
     It is a resource like a stop-word list — inspectable, countable, and it is
     measured, never trusted. Nothing here is learned or downloaded.
  2. **Light stemming** (English suffixes, Arabic clitics) so "قطنية" meets
     "قطني" and "plates" meets "plate".
  3. **IDF-weighted coverage with a concept-count guard**: a hit must cover a
     large share of the *informative* query concepts (not just one common word)
     and must match at least `MIN_MATCHED_CONCEPTS` distinct concepts inside the
     same document. This is what keeps near-miss negatives quiet.

Honest position: this is still lexical + hand-written resources. It will never
match a neural encoder on truly novel phrasing, and `memory/evaluate.py` reports
that number every time it runs. The declared boundary test
(`test_11_uncovered_paraphrase_is_still_missed`) fails loudly if we ever start
pretending otherwise.
"""
from __future__ import annotations

import math
import re
import unicodedata

# Reuse the store's normalisation contract (single source of truth).
ARABIC_DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")
PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
TOKEN_RE = re.compile(r"[a-z0-9\u0600-\u06FF]+")

# Guard rails (tuned against the labelled sets — see docs/ROADMAP-2027.md T3).
MIN_MATCHED_CONCEPTS = 2      # one shared word is never a memory match
MIN_COVERAGE = 0.45           # share of informative query concepts that must match
MIN_SCORE = 0.15              # absolute floor, independent of corpus noise
THESAURUS_VERSION = "2027.1"

# --------------------------------------------------------------------------- #
# Declared thesaurus — hand-written, grouped by ONE concept each.
# Both English and Arabic spellings live in the same group on purpose:
# the corpus and the queries are bilingual.
# --------------------------------------------------------------------------- #
SYNONYM_GROUPS: tuple[frozenset[str], ...] = (
    # print process
    frozenset({"halftone", "halftoning", "dot", "dots", "نقاط", "نقطه", "نقط"}),
    frozenset({"resolution", "dpi", "lpi", "inch", "inches", "بوصه", "البوصه", "دقه"}),
    # NOTE: Arabic "شاشة" (a display) is NOT grouped with "شبكة/حريرية" (mesh /
    # silk screen). Grouping them produced a measured false positive:
    # "فصل الشاشة عن الكمبيوتر" (unplug the monitor) matched the silk-print note.
    frozenset({"silk", "silkscreen", "screen", "screens", "mesh", "شبكه",
               "حريري", "حرير", "حريريه"}),
    frozenset({"شاشه", "شاشات"}),
    frozenset({"separation", "separations", "separate", "فصل", "فصلا"}),
    frozenset({"spot", "special", "pantone", "pms", "سبوت"}),
    frozenset({"discharge", "base", "تفريغ", "قاعده"}),
    frozenset({"interference", "moire", "moiré", "تداخل"}),
    frozenset({"angle", "angles", "rotation", "rotating", "زاويه", "زوايا", "تدوير"}),
    frozenset({"thread", "threads", "tpi", "خيط", "خيوط"}),
    frozenset({"ink", "inks", "paste", "plastisol", "حبر", "معجون"}),
    frozenset({"consumption", "usage", "used", "استهلاك", "استخدام"}),
    frozenset({"colour", "colours", "color", "colors", "لون", "الوان", "اللوان"}),
    frozenset({"plate", "plates", "شبلونه", "الشبلونه"}),
    # product / garment
    frozenset({"garment", "garments", "shirt", "shirts", "tee", "tees", "blank",
               "قميص", "تيشيرت", "ملابس", "قطعه", "قطع"}),
    frozenset({"kid", "kids", "child", "children", "اطفال", "طفل", "الاطفال"}),
    frozenset({"pyjama", "pajama", "sleepwear", "نوم", "بيجامه"}),
    frozenset({"soft", "breathable", "comfortable", "gentle", "مريح", "لطيف", "ناعم"}),
    frozenset({"cotton", "قطني", "قطن", "قطنيه"}),
    frozenset({"jersey", "fabric", "cloth", "قماش", "خامه"}),
    # money / operations
    frozenset({"price", "pricing", "quotation", "quote", "cost", "سعر", "عرض",
               "تكلفه", "تسعير"}),
    frozenset({"small", "run", "order", "orders", "طلب", "طلبيات", "تشغيل"}),
    frozenset({"supplier", "vendor", "supplies", "مورد", "مورده", "موردين"}),
    frozenset({"delivery", "window", "lead", "time", "deadline", "تسليم", "مده",
               "موعد"}),
    frozenset({"schedule", "queue", "press", "production", "جدول", "طابور",
               "انتاج", "مكبس"}),
    frozenset({"packing", "packaging", "carton", "cartons", "box", "boxes",
               "تغليف", "كراتين", "كرتون", "تعبئه"}),
    frozenset({"quality", "check", "checklist", "inspection", "review", "جوده",
               "فحص", "مراجعه", "تدقيق"}),
    frozenset({"mockup", "mock", "موكاب"}),
    frozenset({"photography", "photo", "overhead", "flatlay", "lookbook", "shot",
               "تصوير", "فوتوغرافي"}),
    frozenset({"font", "typeface", "typography", "lettering", "خط", "خطوط"}),
    frozenset({"brand", "identity", "label", "هويه", "علامه", "براند"}),
    frozenset({"logo", "vector", "vectorize", "شعار", "فيكتور"}),
    frozenset({"pastel", "palette", "باستيل", "لوحه"}),
    frozenset({"safe", "toxic", "non toxic", "امن", "سام"}),
)

def _is_arabic(text: str) -> bool:
    return any("\u0600" <= ch <= "\u06FF" for ch in text)


# Concept ids are LANGUAGE-SCOPED: "حبر" and "ink" are the same idea, but a
# bilingual group let Arabic queries hit English memories on shared vocabulary
# alone (measured: 2/7 near-miss false positives). Cross-lingual grouping was
# removed, not hidden: the evaluation reports the result.
CONCEPT_OF: dict[str, str] = {}
for _gid, _group in enumerate(SYNONYM_GROUPS):
    for _word in _group:
        _cid = ("ar:" if _is_arabic(_word) else "en:") + f"g{_gid}"
        CONCEPT_OF.setdefault(_word, _cid)

# Declared, inspectable stop-list: function words and generic verbs carry no
# topical weight, and counting them would punish every natural paraphrase.
STOPWORDS = frozenset(
    "for the and with of on in to a an is are was be been by from at as or per "
    "how when who what which where why before after during need needs needed "
    "require requires required using use uses used make makes made get gets got "
    "total number estimate estimates estimated prevent prevents avoiding avoid "
    "based kind type".split()
) | frozenset(
    "في من علي الي مع عن هذا هذه ذلك التي الذي الذين قبل بعد عند حتى بين لكل كل "
    "هو هي هم كان كانت يتم تم وضع وضعها دون بدون عبر لدى وقد لم لن لا ما أن او أو".split()
)

EN_SUFFIXES = ("ations", "ation", "ings", "ing", "ies", "es", "ed", "s", "ly")
AR_PREFIXES = ("وال", "بال", "كال", "فال", "ال")
AR_SUFFIXES = ("يات", "ات", "ون", "ين", "ها", "هم", "ية", "يه")


def normalise(text: str) -> str:
    """Same contract as store.normalise (kept local to avoid an import cycle)."""
    t = unicodedata.normalize("NFKC", str(text)).translate(PERSIAN_DIGITS).lower()
    t = ARABIC_DIACRITICS.sub("", t)
    t = (t.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
          .replace("ى", "ي").replace("ة", "ه"))
    return re.sub(r"\s+", " ", t).strip()


def stem(token: str) -> str:
    """Light, deterministic, reversible-by-inspection stemming."""
    if not token:
        return token
    is_arabic = any("\u0600" <= ch <= "\u06FF" for ch in token)
    if is_arabic:
        for pre in AR_PREFIXES:
            if token.startswith(pre) and len(token) - len(pre) >= 3:
                token = token[len(pre):]
                break
        for suf in AR_SUFFIXES:
            if token.endswith(suf) and len(token) - len(suf) >= 3:
                token = token[: -len(suf)]
                break
        return token
    for suf in EN_SUFFIXES:
        if token.endswith(suf) and len(token) - len(suf) >= 3:
            return token[: -len(suf)]
    return token


def raw_tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(normalise(text))


def _concept_of_token(tok: str) -> str:
    stemmed = stem(tok)
    direct = CONCEPT_OF.get(tok) or CONCEPT_OF.get(stemmed)
    return direct if direct else stemmed


def concepts(text: str) -> set[str]:
    """Concept ids for a text: stemmed token → thesaurus group when declared."""
    return {_concept_of_token(t) for t in raw_tokens(text)}


def concept_tokens(text: str, drop_stopwords: bool = True) -> list[str]:
    """One concept id per *token* (multiplicity kept), stopwords/numbers dropped.

    Matching is counted per token, not per group: "overhead photography" is two
    pieces of evidence even though both words sit in one concept group.
    """
    out: list[str] = []
    for tok in raw_tokens(text):
        if tok.isdigit():
            continue
        if drop_stopwords and (tok in STOPWORDS or stem(tok) in STOPWORDS):
            continue
        out.append(_concept_of_token(tok))
    return out


def idf_map(doc_concepts: list[set[str]]) -> dict[str, float]:
    n = len(doc_concepts)
    df: dict[str, int] = {}
    for cs in doc_concepts:
        for c in cs:
            df[c] = df.get(c, 0) + 1
    return {c: math.log(1.0 + (n - d + 0.5) / (d + 0.5)) for c, d in df.items()}


# A query word we have never stored still counts — but at a fraction of a known
# term, otherwise one unknown synonym erases an obvious match. Measured on the
# labelled sets; see docs/ROADMAP-2027.md T3.
UNSEEN_WEIGHT_FACTOR = 0.4


def _weight(c: str, idf: dict[str, float]) -> float:
    if c in idf:
        return idf[c]
    return UNSEEN_WEIGHT_FACTOR * (max(idf.values()) if idf else 1.0)


def coverage(query_tokens: list[str], doc_concepts: set[str],
             idf: dict[str, float]) -> tuple[float, int]:
    """IDF-weighted share of the query's tokens covered by one document.

    Unseen query tokens are down-weighted, not ignored: a query about something
    we never stored must not look like a perfect match, but one unknown synonym
    must not erase a real hit either.
    """
    uniq = list(dict.fromkeys(query_tokens))
    if not uniq:
        return 0.0, 0
    total = sum(_weight(c, idf) for c in uniq)
    matched = [c for c in uniq if c in doc_concepts]
    if not matched:
        return 0.0, 0
    return sum(_weight(c, idf) for c in matched) / total, len(matched)


def score(query_text: str, doc_text: str, idf: dict[str, float]) -> dict:
    return score_with(concept_tokens(query_text), concepts(doc_text), idf)


def score_with(query_tokens: list[str], doc_concepts: set[str],
               idf: dict[str, float]) -> dict:
    cov, matched = coverage(query_tokens, doc_concepts, idf)
    return {"coverage": round(cov, 4), "matched": matched,
            "score": round(cov, 4)}


def confident(hit: dict, threshold: float) -> bool:
    """The full gate a hit must pass to be called a memory."""
    return (hit["score"] > max(threshold, MIN_SCORE)
            and hit["coverage"] >= MIN_COVERAGE
            and hit["matched"] >= MIN_MATCHED_CONCEPTS)


def pair_floor(doc_texts: list[str], idf: dict[str, float]) -> dict:
    """Score distribution between UNRELATED stored memories, same statistic as
    search uses. p95 of this is the threshold a real hit must beat."""
    sims: list[float] = []
    n = len(doc_texts)
    tokens = [concept_tokens(t) for t in doc_texts]
    sets = [concepts(t) for t in doc_texts]
    for i in range(n):
        for j in range(i + 1, n):
            a = coverage(tokens[i], sets[j], idf)[0]
            b = coverage(tokens[j], sets[i], idf)[0]
            sims.append((a + b) / 2.0)
    if not sims:
        return {"pairs": 0, "median": None, "p95": None, "max": None}
    sims.sort()
    return {
        "pairs": len(sims),
        "median": round(sims[len(sims) // 2], 4),
        "p95": round(sims[min(len(sims) - 1, int(0.95 * len(sims)))], 4),
        "max": round(sims[-1], 4),
    }


def describe() -> dict:
    """Publishable description of the retrieval stack (no hidden parts)."""
    return {
        "version": THESAURUS_VERSION,
        "thesaurus_groups": len(SYNONYM_GROUPS),
        "thesaurus_terms": len(CONCEPT_OF),
        "min_matched_concepts": MIN_MATCHED_CONCEPTS,
        "min_coverage": MIN_COVERAGE,
        "min_score": MIN_SCORE,
        "stemming": {"en_suffixes": list(EN_SUFFIXES),
                     "ar_prefixes": list(AR_PREFIXES),
                     "ar_suffixes": list(AR_SUFFIXES)},
        "note": "hand-written, inspectable resources; no model, no network",
    }
