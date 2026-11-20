#!/usr/bin/env python3
"""Quality ratchet — the measured score may not go down.

Why a ratchet and not a threshold
---------------------------------
A fixed pass mark invites two kinds of dishonesty: lowering it when you fail,
and writing to it rather than past it. A ratchet has neither. It records what the
system scores today and refuses any future change that scores less. Improvement
is the only direction available.

The baseline is a file in the repository, so the number is reviewable in a diff.
That is deliberate: raising or lowering a quality bar should be a visible change
that someone argues for, not an environment variable that quietly moved.

What this gate refuses to do
----------------------------
It never reports quality it did not measure. When the corpus fingerprint changes,
it fails rather than comparing two different benchmarks. When a run crashes, it
fails rather than falling back to the last good number. A green gate that was not
earned is the exact thing every other guard in this repository exists to stop.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "fleet-2027"))

BASELINE = ROOT / "fleet-2027" / "bench" / "baseline.json"
LATEST = ROOT / "fleet-2027" / "bench" / "latest.json"

#: How much worse is tolerated before the gate fails. Small enough to catch a
#: real regression, large enough that float noise never fails a build.
TOLERANCE = 0.002


def _load(path: Path) -> dict:
    """Read a JSON report, or fail loudly."""
    if not path.exists():
        raise SystemExit(f"FAIL: missing {path.relative_to(ROOT)}")
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    """Run the benchmark and compare it against the recorded baseline."""
    parser = argparse.ArgumentParser(
        description="Refuse a quality regression, measured on a fixed corpus."
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="record this run as the new baseline (a deliberate, reviewable act)",
    )
    parser.add_argument("--quiet", action="store_true", help="print only the verdict")
    args = parser.parse_args(argv)

    from fleet2027.bench import run_benchmark, write_report

    report = run_benchmark()
    current = report.to_dict()
    write_report(report, LATEST)

    summary = current["summary"]
    if not args.quiet:
        print(
            f"corpus={current['corpus_sha256'][:16]} backend={current['backend']} "
            f"model_called={current['model_called']}"
        )
        print(
            f"sentences={summary['sentences']} mean={summary['mean_score']:.4f} "
            f"usable={summary['usable_rate']:.0%} complete={summary['completion_rate']:.0%}"
        )

    if args.write_baseline:
        BASELINE.write_text(
            json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"baseline written: {BASELINE.relative_to(ROOT)}")
        return 0

    baseline = _load(BASELINE)

    # A baseline from a different corpus measures a different system. Comparing
    # them would be arithmetic without meaning.
    if baseline["corpus_sha256"] != current["corpus_sha256"]:
        print(
            "FAIL: corpus changed since the baseline was recorded "
            f"({baseline['corpus_sha256'][:16]} -> {current['corpus_sha256'][:16]}); "
            "re-record with --write-baseline and say why in the commit.",
            file=sys.stderr,
        )
        return 1

    # The ruler is part of the measurement. A baseline scored by a different
    # scorer is a different number for the same system, and comparing them
    # silently is how a "regression" gets argued that never happened.
    if baseline.get("scorer_sha256") != current.get("scorer_sha256"):
        print(
            "FAIL: the scorer changed since the baseline was recorded "
            f"({baseline.get('scorer_sha256', 'unrecorded')} -> "
            f"{current.get('scorer_sha256')}); every number moved with it. "
            "Re-record with --write-baseline and say what changed and why in "
            "the commit.",
            file=sys.stderr,
        )
        return 1

    if baseline["backend"] != current["backend"]:
        print(
            f"FAIL: baseline backend {baseline['backend']!r} but this run was "
            f"{current['backend']!r}; a different brain is a different measurement.",
            file=sys.stderr,
        )
        return 1

    before = baseline["summary"]["mean_score"]
    after = summary["mean_score"]
    delta = after - before

    if delta < -TOLERANCE:
        print(
            f"FAIL: quality regressed {before:.4f} -> {after:.4f} ({delta:+.4f})",
            file=sys.stderr,
        )
        print("worst sentences:", file=sys.stderr)
        for row in sorted(current["results"], key=lambda r: r["score"])[:5]:
            print(
                f"  {row['id']:<12} {row['score']:.2f}  {row['text'][:40]}",
                file=sys.stderr,
            )
        return 1

    verdict = "unchanged" if abs(delta) <= TOLERANCE else f"improved {delta:+.4f}"
    print(f"PASS: quality {verdict} (baseline {before:.4f}, now {after:.4f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
