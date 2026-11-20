#!/usr/bin/env python3
"""ask.py — say what you want, in your own words. That is the whole interface.

Usage:
    python3 kernel-2027/ask.py "عايز أبدأ مشروع ومش عارف أبدأ منين"
    python3 kernel-2027/ask.py "build me a website for my project" --json

This exists to make the product's claim testable by hand, not just in tests: a
person with no technical vocabulary types one sentence and immediately sees
what the system understood, what it assumed, and what it would do — before any
work begins and before anything is spent.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kernel2027.intent import IntentError, resolve


def main(argv: list[str] | None = None) -> int:
    """Read one sentence and print the plan, or explain why it could not."""
    parser = argparse.ArgumentParser(
        description="Say what you want in plain language; see what the system understood."
    )
    parser.add_argument("request", nargs="+", help="what you want, in your own words")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--steps", action="store_true", help="show the plan the system would run"
    )
    args = parser.parse_args(argv)

    utterance = " ".join(args.request)
    try:
        result = resolve(utterance)
    except IntentError as exc:
        # Fail closed and helpfully: no invented plan, and a sentence that shows
        # what a usable request looks like.
        print(f"لم أفهم الطلب: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0

    print(result.explain())
    status = "سيبدأ العمل الآن" if result.can_start else "لن يبدأ العمل قبل موافقتك"
    print(f"\n{status} · الناتج: {result.artifact_label}")

    if args.steps:
        print("\nالخطة:")
        for step in result.steps:
            waits = f" (بعد {', '.join(step.depends_on)})" if step.depends_on else ""
            print(f"  {step.step_id:<22} {step.action}{waits}")
    else:
        print("(أضف --steps لرؤية الخطة كاملة)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
