#!/usr/bin/env python3
"""Compare two benchmark reports, per domain, and say whether one is better.

Why a separate tool
-------------------
``bench_gate.py`` answers one question: did *this* configuration regress against
its own recorded baseline? That is a ratchet, and a ratchet cannot answer the
question a person actually asks when a new model arrives: **is this brain better
than the one we had?**

That comparison needs three things the gate deliberately does not do:

1. **A per-domain table.** A model can win overall while being worse at
   websites, and that is the kind of thing an aggregate hides.
2. **A refusal to compare unlike runs.** Different corpora measure different
   systems. Different sentence counts hide dropped failures.
3. **A stated verdict.** Numbers without a decision leave the reader to do the
   arithmetic, which is how a 0.02 improvement gets called "much better".

What this tool will not do
--------------------------
It will not call a difference real when it is within noise, and it will not
pretend a comparison between a scaffolding and a model is a comparison between
two models. Both cases are reported as what they are.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: Differences smaller than this are reported as "indistinguishable". With 24
#: sentences, a smaller gap is not separable from which sentences happened to
#: land well, and calling it an improvement would be reading tea leaves.
NOISE = 0.02


def _load(path: Path) -> dict:
    """Read a report, or explain which file is missing."""
    if not path.exists():
        raise SystemExit(f"missing report: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def compare(before: dict, after: dict) -> tuple[int, list[str]]:
    """Return an exit code and the lines describing the comparison."""
    lines: list[str] = []

    # A different corpus is a different measurement. Saying so is more useful
    # than printing a difference that means nothing.
    if before.get("corpus_sha256") != after.get("corpus_sha256"):
        return 1, [
            (
                "FAIL: the two reports used different corpora "
                f"({str(before.get('corpus_sha256'))[:16]} vs "
                f"{str(after.get('corpus_sha256'))[:16]}); "
                "the comparison would be meaningless."
            )
        ]
    if before["summary"]["sentences"] != after["summary"]["sentences"]:
        return 1, [
            (
                "FAIL: different sentence counts "
                f"({before['summary']['sentences']} vs {after['summary']['sentences']}); "
                "a dropped failure looks like an improvement."
            )
        ]

    lines.append(
        f"corpus {before['corpus_sha256'][:16]} · "
        f"{before['summary']['sentences']} sentences"
    )
    lines.append(f"  before: backend={before['backend']} model_called={before['model_called']}")
    lines.append(f"  after : backend={after['backend']} model_called={after['model_called']}")
    lines.append("")

    before_mean = before["summary"]["mean_score"]
    after_mean = after["summary"]["mean_score"]
    delta = round(after_mean - before_mean, 4)
    lines.append(f"mean score   {before_mean:.4f} -> {after_mean:.4f}  ({delta:+.4f})")
    lines.append(
        f"usable rate  {before['summary']['usable_rate']:.0%} -> "
        f"{after['summary']['usable_rate']:.0%}"
    )
    lines.append(
        f"completion   {before['summary']['completion_rate']:.0%} -> "
        f"{after['summary']['completion_rate']:.0%}"
    )
    lines.append("")

    domains = sorted(
        set(before["summary"]["by_domain"]) | set(after["summary"]["by_domain"])
    )
    lines.append(f"{'domain':<12}{'before':>8}{'after':>8}{'delta':>9}   verdict")
    worst_regression = 0.0
    for domain in domains:
        old = before["summary"]["by_domain"].get(domain, 0.0)
        new = after["summary"]["by_domain"].get(domain, 0.0)
        change = round(new - old, 4)
        if change < -NOISE:
            verdict = "WORSE"
            worst_regression = min(worst_regression, change)
        elif change > NOISE:
            verdict = "better"
        else:
            verdict = "same"
        lines.append(f"{domain:<12}{old:>8.3f}{new:>8.3f}{change:>+9.3f}   {verdict}")

    lines.append("")
    if abs(delta) <= NOISE and worst_regression == 0.0:
        lines.append(
            f"VERDICT: indistinguishable (|{delta:+.4f}| <= {NOISE}); "
            "with this many sentences the difference is noise."
        )
        return 0, lines
    if delta < -NOISE or worst_regression < -NOISE:
        lines.append(
            f"VERDICT: WORSE overall by {delta:+.4f}"
            + (f", worst domain {worst_regression:+.3f}" if worst_regression else "")
        )
        return 1, lines
    lines.append(f"VERDICT: BETTER overall by {delta:+.4f}")
    return 0, lines


def main(argv: list[str] | None = None) -> int:
    """Compare two report files and exit non-zero on a regression."""
    parser = argparse.ArgumentParser(
        description="Compare two quality benchmark reports, per domain."
    )
    parser.add_argument("before", help="the reference report (e.g. baseline.json)")
    parser.add_argument("after", help="the report to judge (e.g. model.json)")
    args = parser.parse_args(argv)

    before = _load(Path(args.before))
    after = _load(Path(args.after))
    code, lines = compare(before, after)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
