#!/usr/bin/env python3
"""PAP-2027 gap audit: every claimed engine must still have code and tests.

    python3 scripts/pap_gap_audit.py            # human table
    python3 scripts/pap_gap_audit.py --json     # machine-readable
    python3 scripts/pap_gap_audit.py --assert   # CI gate: non-zero on drift

The catalogue lives in ``docs/pap-2027-engines.json``. This script does not
judge quality — it refuses to let the audit silently rot: if an evidence path is
moved, deleted, or a smoke test disappears, the gate fails instead of leaving a
document that claims a capability the repository no longer has.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "docs" / "pap-2027-engines.json"
VALID_STATUSES = ("present", "partial", "absent")


@dataclass
class EngineResult:
    """Resolved state of one engine entry."""

    engine_id: str
    title: str
    status: str
    evidence_files: int = 0
    test_cases: int = 0
    gap: str = ""
    problems: list[str] = field(default_factory=list)


@dataclass
class AuditReport:
    """Whole-catalogue result."""

    engines: list[EngineResult] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Return True when no drift was found."""
        return not self.problems


def load_catalog(path: Path) -> dict:
    """Load and shape-check the engine catalogue."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("engines"), list):
        raise ValueError(f"{path} must contain an object with an 'engines' list")
    seen: set[str] = set()
    for entry in data["engines"]:
        engine_id = entry.get("id")
        if not isinstance(engine_id, str) or not engine_id.strip():
            raise ValueError("every engine needs a non-empty string id")
        if engine_id in seen:
            raise ValueError(f"duplicate engine id: {engine_id}")
        seen.add(engine_id)
        if entry.get("status") not in VALID_STATUSES:
            raise ValueError(f"{engine_id}: status must be one of {VALID_STATUSES}")
        for key in ("evidence", "tests"):
            if not isinstance(entry.get(key, []), list):
                raise ValueError(f"{engine_id}: '{key}' must be a list")
    return data


def audit(catalog: dict, root: Path = ROOT) -> AuditReport:
    """Check every evidence path and test marker against the working tree."""
    report = AuditReport()
    for entry in catalog["engines"]:
        engine_id = str(entry["id"])
        status = str(entry["status"])
        result = EngineResult(
            engine_id=engine_id,
            title=str(entry.get("title_ar", engine_id)),
            status=status,
            gap=str(entry.get("gap_ar", "")),
        )

        for rel in entry.get("evidence", []):
            target = root / str(rel)
            if target.is_file():
                result.evidence_files += 1
            else:
                result.problems.append(f"{engine_id}: missing evidence file {rel}")

        for spec in entry.get("tests", []):
            rel = str(spec.get("file", ""))
            marker = str(spec.get("marker", ""))
            target = root / rel
            if not rel or not marker:
                result.problems.append(f"{engine_id}: test entry needs 'file' and 'marker'")
                continue
            if not target.is_file():
                result.problems.append(f"{engine_id}: missing test file {rel}")
                continue
            body = target.read_text(encoding="utf-8", errors="replace")
            found = body.count(marker)
            if not found:
                result.problems.append(f"{engine_id}: {rel} has no '{marker}' test")
                continue
            result.test_cases += found

        # A claim must be consistent with what is on disk.
        if status == "present" and (result.evidence_files == 0 or result.test_cases == 0):
            result.problems.append(
                f"{engine_id}: status 'present' needs evidence files and tests"
            )
        if status == "partial" and result.evidence_files == 0:
            result.problems.append(f"{engine_id}: status 'partial' needs at least one evidence file")
        if status == "absent" and entry.get("evidence"):
            result.problems.append(
                f"{engine_id}: status 'absent' must not list evidence — mark it partial"
            )

        report.engines.append(result)
        report.problems.extend(result.problems)
    return report


def render_table(report: AuditReport) -> str:
    """Return a fixed-width table of the audit result."""
    lines = [
        f"{'Engine':<20} | {'Status':<8} | {'Code':>4} | {'Tests':>5} | Gap",
        "-" * 108,
    ]
    for item in report.engines:
        lines.append(
            f"{item.engine_id:<20} | {item.status:<8} | {item.evidence_files:>4} | "
            f"{item.test_cases:>5} | {item.gap or '—'}"
        )
    present = sum(1 for i in report.engines if i.status == "present")
    partial = sum(1 for i in report.engines if i.status == "partial")
    absent = sum(1 for i in report.engines if i.status == "absent")
    lines.append("-" * 108)
    lines.append(
        f"{len(report.engines)} engines · present={present} · partial={partial} · "
        f"absent={absent} · drift problems={len(report.problems)}"
    )
    for problem in report.problems:
        lines.append(f"  ✗ {problem}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run the audit and return a process exit code."""
    parser = argparse.ArgumentParser(description="PAP-2027 engine/gap audit (stdlib only).")
    parser.add_argument("--catalog", type=Path, default=CATALOG, help="Engine catalogue JSON.")
    parser.add_argument("--root", type=Path, default=ROOT, help="Repository root to audit.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")
    parser.add_argument(
        "--assert",
        dest="assert_ok",
        action="store_true",
        help="Exit non-zero when a referenced file, test, or status is inconsistent.",
    )
    args = parser.parse_args(argv)

    try:
        catalog = load_catalog(args.catalog)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL: cannot read catalogue {args.catalog}: {exc}", file=sys.stderr)
        return 2

    report = audit(catalog, args.root)

    if args.json:
        payload = {
            "protocol": catalog.get("protocol", "PAP-2027"),
            "ok": report.ok,
            "engines": [
                {
                    "id": item.engine_id,
                    "title_ar": item.title,
                    "status": item.status,
                    "evidence_files": item.evidence_files,
                    "test_cases": item.test_cases,
                    "gap_ar": item.gap,
                }
                for item in report.engines
            ],
            "problems": report.problems,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(render_table(report))

    if args.assert_ok and not report.ok:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
