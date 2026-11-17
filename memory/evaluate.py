#!/usr/bin/env python3
"""Honest retrieval evaluation for the evidence-gated memory store.

Builds a corpus from REAL kernel runs (nothing hand-written), then reports:

  * precision@k and recall@k on labelled positive queries
  * FALSE-POSITIVE RATE on labelled negative queries (unrelated)
  * the measured noise floor (p95 cosine between unrelated memories)
  * whether Arabic normalisation actually retrieves Arabic content

The point is not to look good. A lexical embedding WILL miss synonyms, and this
report says so with numbers instead of adjectives.

    python3 memory/evaluate.py
    python3 memory/evaluate.py --json

Stdlib only. Python 3.10+.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "command-hud"))

import kernel_bridge          # noqa: E402
from store import EvidenceMemoryStore  # noqa: E402

# (path, content, label) — every row becomes a real kernel run.
CORPUS = [
    ("print/halftone.txt", "halftone 55 lpi cmyk separation for screen printing", "print"),
    ("print/angles.txt", "screen angles 45 15 75 degrees for cmyk moire avoidance", "print"),
    ("print/spot.txt", "spot colour pantone 186c discharge base for dark shirts", "print"),
    ("print/fabric.txt", "cotton fabric 180 gsm requires 43 tpi mesh count", "print"),
    ("kids/sizes.txt", "kids t-shirt sizes 2 4 6 8 years chart measurements", "kids"),
    ("kids/fabric.txt", "kids pyjama cotton jersey soft breathable fabric choice", "kids"),
    ("kids/colors.txt", "kids clothing pastel colour palette safe non toxic inks", "kids"),
    ("pricing/quote.txt", "quotation for 120 pieces screen print three colours total cost", "pricing"),
    ("pricing/ink.txt", "ink consumption estimate per 100 prints plastisol grams", "pricing"),
    ("pricing/margin.txt", "wholesale margin calculation for streetwear brand order", "pricing"),
    ("design/mockup.txt", "mockup generation flat lay photography for lookbook", "design"),
    ("design/logo.txt", "logo vector cleanup and raster to vector conversion notes", "design"),
    ("design/font.txt", "arabic font pairing for clothing brand identity", "design"),
    ("ops/schedule.txt", "production schedule press queue lead time four days", "ops"),
    ("ops/quality.txt", "quality check list before packing finished garments", "ops"),
    ("ops/supplier.txt", "supplier contact for blank shirts and delivery window", "ops"),
    ("ar/طباعة.txt", "فصل الألوان للطباعة بالشبكة الحريرية خمسة وخمسون خط في البوصة", "arabic"),
    ("ar/أسعار.txt", "عرض سعر لمئة وعشرين قطعة طباعة ثلاثة ألوان والتكلفة الإجمالية", "arabic"),
    ("ar/أقمشة.txt", "اختيار القماش القطني للأطفال مريح وخفيف ولا يسبب حساسية", "arabic"),
    ("ar/جودة.txt", "فحص الجودة قبل التغليف والتأكد من عدم وجود عيوب في الطباعة", "arabic"),
]

POSITIVE_QUERIES = [
    ("halftone lpi separation", "print"),
    ("screen angles moire", "print"),
    ("pantone spot colour discharge", "print"),
    ("mesh count cotton gsm", "print"),
    ("kids sizes chart years", "kids"),
    ("kids jersey fabric soft", "kids"),
    ("quotation three colours cost", "pricing"),
    ("ink consumption plastisol grams", "pricing"),
    ("mockup lookbook flat lay", "design"),
    ("arabic font brand identity", "design"),
    ("press queue lead time", "ops"),
    ("supplier blank shirts delivery", "ops"),
    ("فصل الألوان خط في البوصة", "arabic"),
    ("عرض سعر قطعة طباعة ألوان", "arabic"),
    ("القماش القطني للأطفال", "arabic"),
    ("فحص الجودة التغليف", "arabic"),
]

# These MUST NOT return confident hits: nothing in the corpus is about them.
NEGATIVE_QUERIES = [
    "kubernetes cluster autoscaling",
    "quantum error correction surface code",
    "javascript bundler tree shaking",
    "رواتب المحاسبين في البنوك",
    "نظرية الأوتار في الفيزياء",
]

# PARAPHRASE SET — deliberately avoids the exact tokens used in CORPUS.
# This is the set that actually tests generalisation instead of string overlap.
HARD_POSITIVE_QUERIES = [
    ("dot pattern resolution for silk screens", "print"),
    ("rotating mesh to prevent interference patterns", "print"),
    ("special mix base on black garments", "print"),
    ("threads per inch needed for jersey blanks", "print"),
    ("age based garment chart for children", "kids"),
    ("soft breathable sleepwear material", "kids"),
    ("price for a small run of three plates", "pricing"),
    ("how much paste is used per hundred garments", "pricing"),
    ("overhead photography of finished pieces", "design"),
    ("typeface pairing for a clothing label", "design"),
    ("how long before an order reaches the press", "ops"),
    ("who supplies blank tees and when", "ops"),
    ("دقة النقاط على الشاشة الحريرية", "arabic"),
    ("تكلفة تشغيل صغير بثلاثة ألوان", "arabic"),
    ("ملابس نوم قطنية لطيفة على الجلد", "arabic"),
    ("مراجعة القطع قبل وضعها في الكراتين", "arabic"),
]

# NEAR-MISS NEGATIVES — they share vocabulary with the corpus but ask something
# different. A lexical model is EXPECTED to be tempted here; that is the test.
NEAR_MISS_NEGATIVE_QUERIES = [
    "inkjet printer driver installation for office laser",
    "separation anxiety in toddlers bedtime routine",
    "screen time limits for children tablets",
    "quotation letter template for job application",
    "sorting algorithms complexity comparison",
    "حبر الطابعة المنزلية سعر الخرطوشة",
    "فصل الشاشة عن الكمبيوتر المحمول",
]


def build() -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="ai2027-mem-eval-"))
    srv = kernel_bridge.load(runtime=tmp, fresh=True)
    store = EvidenceMemoryStore(tmp / "mem.sqlite", sandbox_root=tmp / "sandboxes")

    admitted = refused = 0
    label_of: dict[str, str] = {}
    for path, content, label in CORPUS:
        rec = srv.run_goal("create_file", {"path": path, "content": content})
        v = store.ingest(rec)
        if v.admitted and not v.duplicate:
            admitted += 1
            label_of[v.memory_id] = label
        elif not v.admitted:
            refused += 1

    floor = store.noise_floor()

    def run_set(queries):
        hits = total = 0
        rows = []
        for query, want in queries:
            res = store.search(query, k=3)
            got = [label_of.get(h["memory_id"], "?") for h in res["results"]
                   if h["above_noise"]]
            hit = want in got
            total += 1
            hits += int(hit)
            rows.append({"query": query, "want": want, "hit": hit,
                         "above_noise": got, "threshold": res["threshold"],
                         "top_score": res["results"][0]["score"] if res["results"] else None})
        return hits, total, rows

    pos_hits, pos_total, details = run_set(POSITIVE_QUERIES)
    hard_hits, hard_total, hard_details = run_set(HARD_POSITIVE_QUERIES)

    def run_neg(queries):
        confident = 0
        rows = []
        for query in queries:
            res = store.search(query, k=3)
            n = res["confident"]
            confident += int(n > 0)
            rows.append({"query": query, "confident_hits": n,
                         "top_score": res["results"][0]["score"] if res["results"] else None,
                         "top_text": res["results"][0]["text"][:70] if res["results"] else None})
        return confident, rows

    neg_confident, neg_details = run_neg(NEGATIVE_QUERIES)
    near_confident, near_details = run_neg(NEAR_MISS_NEGATIVE_QUERIES)

    return {
        "corpus_size": len(CORPUS),
        "admitted": admitted,
        "refused": refused,
        "noise_floor": floor,
        "positive_queries": len(POSITIVE_QUERIES),
        "positive_hits_at_3": pos_hits,
        "recall_at_3": round(pos_hits / pos_total, 3) if pos_total else None,
        "hard_queries": len(HARD_POSITIVE_QUERIES),
        "hard_hits_at_3": hard_hits,
        "hard_recall_at_3": round(hard_hits / hard_total, 3) if hard_total else None,
        "negative_queries": len(NEGATIVE_QUERIES),
        "negative_with_confident_hit": neg_confident,
        "false_positive_rate": round(neg_confident / len(NEGATIVE_QUERIES), 3),
        "near_miss_queries": len(NEAR_MISS_NEGATIVE_QUERIES),
        "near_miss_confident_hits": near_confident,
        "near_miss_false_positive_rate": round(near_confident / len(NEAR_MISS_NEGATIVE_QUERIES), 3),
        "positive_details": details,
        "hard_details": hard_details,
        "negative_details": neg_details,
        "near_miss_details": near_details,
        "backend": "sqlite3 + hashed-lexical embedding (dim 512), no sqlite-vec, no model",
        "honest_limits": [
            "Lexical, not neural: true synonyms are missed. The HARD set quantifies this.",
            "The EASY set reuses corpus vocabulary — it is a wiring check, NOT evidence of quality.",
            "Corpus is 20 memories; recall@3 on 20 items is a smoke test, not a benchmark.",
            "Near-miss negatives share vocabulary with the corpus on purpose; a confident hit there is a real false positive.",
            "Detection thresholds use the measured p95 noise floor on THIS corpus.",
        ],
    }


def markdown(r: dict) -> str:
    L = ["# 🧠 Memory retrieval — measured, not asserted", "",
         f"**Backend:** {r['backend']}", "",
         "| Metric | Value |", "|---|---:|",
         f"| Corpus (real kernel runs) | {r['admitted']} admitted / {r['refused']} refused |",
         f"| Noise floor p95 | {r['noise_floor'].get('p95')} |",
         f"| Recall@3 — EASY set (corpus vocabulary) | {r['positive_hits_at_3']}/{r['positive_queries']} = {r['recall_at_3']} |",
         f"| **Recall@3 — HARD paraphrase set** | **{r['hard_hits_at_3']}/{r['hard_queries']} = {r['hard_recall_at_3']}** |",
         f"| False-positive rate — unrelated | {r['negative_with_confident_hit']}/{r['negative_queries']} = {r['false_positive_rate']} |",
         f"| **False-positive rate — near-miss** | **{r['near_miss_confident_hits']}/{r['near_miss_queries']} = {r['near_miss_false_positive_rate']}** |",
         "", "> The EASY set proves the plumbing works. The HARD set is the honest number,",
         "> because it deliberately avoids the tokens stored in the corpus.",
         "", "## HARD-set misses (published deliberately)", ""]
    misses = [d for d in r["hard_details"] if not d["hit"]]
    if misses:
        L += ["| Query | Wanted | Retrieved |", "|---|---|---|"]
        for m in misses:
            L.append(f"| {m['query']} | {m['want']} | {', '.join(m['above_noise']) or '—'} |")
    else:
        L.append("_None._")
    L += ["", "## Honest limits", ""]
    L += [f"- {x}" for x in r["honest_limits"]]
    return "\n".join(L) + "\n"


def main() -> int:
    r = build()
    if "--markdown" in sys.argv:
        print(markdown(r))
    else:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
