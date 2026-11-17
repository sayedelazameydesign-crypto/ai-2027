#!/usr/bin/env python3
"""Measurement harness for a FUTURE real-world case study. NOT a story generator.

It contains ZERO business assumptions: no ink prices, no standard times, no
"we saved X%". It only validates measurements you supply and computes plain
arithmetic from them. It REFUSES to emit anything if a measurement lacks a
method and a source, or if a before/after pair is missing.

    python3 launch/production_evidence.py --template > jobs.json   # all nulls
    python3 launch/production_evidence.py --input jobs.json --out launch/evidence/production.json
    python3 launch/production_evidence.py --verify --input jobs.json --out launch/evidence/production.json

`NO EVIDENCE = NO VERIFIED SUCCESS` — the kernel's rule, applied to marketing.
Stdlib only. Python 3.10+.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

REQUIRED = ("job_id", "stage", "measured_at", "measurement_method", "source",
            "separation_minutes", "screens", "ink_ml", "reprint_count")
NUMERIC = ("separation_minutes", "screens", "ink_ml", "reprint_count")
STAGES = {"before", "after"}

TEMPLATE = {
    "_instructions": [
        "FILL WITH REAL MEASUREMENTS ONLY. Every field is required.",
        "measurement_method: HOW you measured it. source: WHERE the record lives.",
        "Provide exactly one 'before' and one 'after' row per job_id.",
        "The tool refuses null/negative/non-numeric values and refuses to invent defaults.",
        "This template is NOT publishable. See launch/REAL-WORLD-CASE-STUDY.md",
    ],
    "jobs": [
        {k: None for k in REQUIRED} | {"stage": "before", "notes": None},
        {k: None for k in REQUIRED} | {"stage": "after", "notes": None},
    ],
}


class ValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors[:3]))


def validate(doc: object) -> list[dict]:
    errors: list[str] = []
    if not isinstance(doc, dict) or not isinstance(doc.get("jobs"), list):
        raise ValidationError(["top-level object must contain a 'jobs' array"])
    jobs = doc["jobs"]
    if not jobs:
        raise ValidationError(["'jobs' is empty — no measurements supplied"])

    clean = []
    for i, row in enumerate(jobs):
        tag = f"jobs[{i}]"
        if not isinstance(row, dict):
            errors.append(f"{tag}: must be an object"); continue
        for k in REQUIRED:
            if k not in row:
                errors.append(f"{tag}: missing '{k}'")
            elif row[k] is None or (isinstance(row[k], str) and not row[k].strip()):
                errors.append(f"{tag}: '{k}' is empty — no invented defaults allowed")
        for k in NUMERIC:
            v = row.get(k)
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0):
                errors.append(f"{tag}: '{k}' must be a non-negative number, got {v!r}")
        if row.get("stage") not in STAGES:
            errors.append(f"{tag}: 'stage' must be 'before' or 'after'")
        if isinstance(row.get("measured_at"), str):
            try:
                time.strptime(row["measured_at"], "%Y-%m-%d")
            except ValueError:
                errors.append(f"{tag}: 'measured_at' must be YYYY-MM-DD")
        clean.append(row)

    pairs: dict[str, dict[str, dict]] = {}
    for row in clean:
        if isinstance(row.get("job_id"), str) and row.get("stage") in STAGES:
            pairs.setdefault(row["job_id"], {})[row["stage"]] = row
    for jid, pair in pairs.items():
        for s in STAGES:
            if s not in pair:
                errors.append(f"job '{jid}': missing '{s}' measurement")
        if len(pair.get("before", {})) and len(pair.get("after", {})) and pair.get("before") is pair.get("after"):
            errors.append(f"job '{jid}': before and after are the same object")
    if errors:
        raise ValidationError(errors)
    return clean


def pct(before: float, after: float) -> float | None:
    return round((after - before) / before * 100, 2) if before else None


def build(doc: dict, rows: list[dict], canonical: bytes) -> dict:
    pairs: dict[str, dict[str, dict]] = {}
    for row in rows:
        pairs.setdefault(row["job_id"], {})[row["stage"]] = row
    derived = []
    for jid in sorted(pairs):
        b, a = pairs[jid]["before"], pairs[jid]["after"]
        derived.append({
            "job_id": jid,
            "before": {k: b[k] for k in NUMERIC},
            "after": {k: a[k] for k in NUMERIC},
            "delta": {k: round(a[k] - b[k], 3) for k in NUMERIC},
            "pct_change": {k: pct(b[k], a[k]) for k in NUMERIC},
            "provenance": {
                "before": {"measured_at": b["measured_at"], "method": b["measurement_method"],
                           "source": b["source"]},
                "after": {"measured_at": a["measured_at"], "method": a["measurement_method"],
                          "source": a["source"]},
            },
        })
    mins_b = [d["before"]["separation_minutes"] for d in derived]
    mins_a = [d["after"]["separation_minutes"] for d in derived]
    n = len(derived)
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "VERIFIED" if derived else "FAILED",
        "rule": "no claims without evidence; only arithmetic over supplied measurements",
        "input_sha256": hashlib.sha256(canonical).hexdigest(),
        "n_jobs": n,
        "sample_warning": ("n < 20 — do NOT publish percentiles or SLA-style claims"
                           if n < 20 else "n >= 20; still publish spread with any timing"),
        "aggregate": {
            "separation_minutes_before_total": round(sum(mins_b), 3),
            "separation_minutes_after_total": round(sum(mins_a), 3),
            "separation_minutes_pct_change": pct(sum(mins_b), sum(mins_a)),
            "mean_before": round(statistics.mean(mins_b), 3),
            "mean_after": round(statistics.mean(mins_a), 3),
            "ink_ml_total_delta": round(sum(d["delta"]["ink_ml"] for d in derived), 3),
            "reprint_total_delta": round(sum(d["delta"]["reprint_count"] for d in derived), 3),
        },
        "jobs": derived,
        "honest_limits": [
            "No customer, revenue, price or profit is asserted here.",
            "No coefficient or baseline was invented by this tool.",
            "Timings come from a human-supplied log; their measurement error is unknown.",
            "A before/after comparison is not a controlled experiment.",
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--template", action="store_true")
    ap.add_argument("--input", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()
    if args.template:
        print(json.dumps(TEMPLATE, indent=2, ensure_ascii=False))
        return 0
    if not args.input:
        ap.error("--input is required (or use --template)")
    raw = args.input.read_bytes()
    try:
        doc = json.loads(raw)
        rows = validate(doc)
    except (ValueError, ValidationError) as exc:
        print("REFUSED — NO EVIDENCE = NO VERIFIED SUCCESS", file=sys.stderr)
        if isinstance(exc, ValidationError):
            for e in exc.errors:
                print("  - " + e, file=sys.stderr)
        else:
            print("  - invalid JSON: " + str(exc), file=sys.stderr)
        return 1
    canonical = json.dumps(doc, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":")).encode()
    result = build(doc, rows, canonical)
    if args.verify:
        if not args.out or not args.out.is_file():
            print("REFUSED — evidence file not found", file=sys.stderr); return 1
        prev = json.loads(args.out.read_text(encoding="utf-8"))
        ok = prev.get("input_sha256") == result["input_sha256"]
        print("VERIFIED: input unchanged" if ok else "FAILED: input or evidence was modified")
        return 0 if ok else 1
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out} · {result['n_jobs']} jobs · sha256 {result['input_sha256'][:16]}…")
        print(result["sample_warning"])
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
