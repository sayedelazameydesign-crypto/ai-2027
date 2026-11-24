#!/usr/bin/env python3
"""Independent audit of the Achievement Atlas.

This script deliberately DOES NOT import scripts/achievements_atlas.py and does
not trust any number printed in a report. It re-reads the original source files
(catalog.json, inventory, CHANGELOG, git file list), parses the embedded JSON
straight out of the generated HTML, and compares the two independently.

It also checks that every card carries a verifiable evidence level, so a
historical claim cannot be presented as a freshly executed proof.

Usage:
    python3 scripts/audit_achievements_atlas.py --http --run-tests
    python3 scripts/audit_achievements_atlas.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "docs" / "achievements-atlas.html"
CATALOG = ROOT / "catalog.json"
INVENTORY = ROOT / "docs" / "inventory-2027.json"
CHANGELOG = ROOT / "CHANGELOG.md"
REPORT_JSON = ROOT / "docs" / "atlas-audit.json"
REPORT_MD = ROOT / "docs" / "ATLAS-AUDIT.md"

TEST_EXTENSIONS = (".py", ".js", ".mjs", ".ts", ".tsx", ".sh")


def is_test_file(path: str) -> bool:
    """Independent implementation of the shared test-file policy."""
    name = Path(path).name
    suffix = Path(path).suffix
    if suffix not in TEST_EXTENSIONS:
        return False
    return name.startswith("test_") or name.endswith(("_test" + suffix, ".test" + suffix))


ALLOWED_EVIDENCE = {
    "RECOMPUTED_NOW",
    "FILE_EXISTS",
    "DOCUMENTED_ONLY",
    "HISTORICAL_INVENTORY",
    "HISTORICAL_CATALOG",
    "HISTORICAL_SURFACE",
    "LIVE_CHECKED",
}


def ok(condition: bool, label: str, detail: str = "") -> dict[str, Any]:
    return {"label": label, "status": "PASS" if condition else "FAIL", "detail": detail}


def git_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True, timeout=30
    )
    return [p.decode("utf-8", "replace") for p in result.stdout.split(b"\0") if p]


def extract_atlas(html: str) -> dict[str, Any]:
    line = next((l for l in html.splitlines() if l.startswith("const ATLAS = ")), None)
    if not line:
        raise ValueError("embedded ATLAS payload not found")
    raw = line[len("const ATLAS = ") :].rstrip().rstrip(";")
    return json.loads(raw)  # JSON '\/' escapes are handled natively


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--http", action="store_true", help="independently HTTP-GET the atlas page")
    parser.add_argument("--run-tests", action="store_true", help="run the atlas unittest suite independently")
    parser.add_argument("--json", action="store_true", help="print the audit report as JSON")
    parser.add_argument("--no-write", action="store_true", help="reproduce the audit without touching report files")
    parser.add_argument("--url", default="http://127.0.0.1:8000/docs/achievements-atlas.html")
    args = parser.parse_args(argv)

    checks: list[dict[str, Any]] = []
    findings: list[dict[str, str]] = []

    html = HTML.read_text(encoding="utf-8")
    atlas = extract_atlas(html)
    cards = atlas["cards"]
    cat = json.loads(CATALOG.read_text(encoding="utf-8"))
    inv = json.loads(INVENTORY.read_text(encoding="utf-8"))
    changelog = CHANGELOG.read_text(encoding="utf-8")
    files = git_files()

    # 1. Recompute the card counts from the original sources.
    legend = inv.get("legend_2027", {})
    live = cat.get("live_surfaces_verified_2026_11_17", {})
    expected_kinds = {
        "project": len(cat.get("projects", [])),
        "surface": len(live.get("live", [])) + len(live.get("dead", [])),
        "test": len(inv.get("test_matrix", [])),
        "engine": len(inv.get("engines", [])),
        "correction": len(inv.get("corrections", [])),
        "finding": len(inv.get("high_findings", [])),
        "governance": 1 + len(legend.get("foundation_proven", []))
        + len(legend.get("upgrade_path", [])) + len(legend.get("excluded_until_built", [])),
        "changelog": len(re.findall(r"(?m)^### ", changelog)),
        "doc": len([f for f in files if f.startswith("docs/") and f.endswith(".md")]),
        "workflow": len([f for f in files if f.startswith(".github/workflows/") and f.endswith((".yml", ".yaml"))]),
        "script": len([f for f in files if f.startswith("scripts/") and f.endswith(".py")]),
    }
    actual_kinds = Counter(c["kind"] for c in cards)
    mismatches = {k: {"expected": v, "actual": actual_kinds.get(k, 0)} for k, v in expected_kinds.items() if actual_kinds.get(k, 0) != v}
    checks.append(ok(not mismatches, "card counts recomputed from source files", json.dumps(mismatches, ensure_ascii=False) if mismatches else f"{len(cards)} cards across {len(expected_kinds)} kinds"))
    if mismatches:
        findings.append({"severity": "HIGH", "issue": "card counts differ from source files", "detail": json.dumps(mismatches, ensure_ascii=False)})

    # 2. Uniqueness and basic shape.
    ids = [c["id"] for c in cards]
    duplicates = [i for i, n in Counter(ids).items() if n > 1]
    checks.append(ok(not duplicates, "achievement ids are unique", f"{len(duplicates)} duplicates" if duplicates else f"{len(ids)} unique ids"))
    malformed = [c["id"] for c in cards if not all(c.get(k) for k in ("title", "summary", "source", "status", "category", "kind"))]
    checks.append(ok(not malformed, "every card has title/summary/source/status/category", f"{len(malformed)} malformed" if malformed else "well formed"))

    # 3. Every source must exist and every rendered link must resolve.
    missing_sources = []
    broken_links = []
    for card in cards:
        src = card["source"]
        if not src.startswith("http") and not (ROOT / src).exists():
            missing_sources.append(f"{card['id']} -> {src}")
        url = card.get("url", "")
        if url.startswith("http"):
            continue
        target = (ROOT / "docs" / url).resolve()
        if not target.exists():
            broken_links.append(f"{card['id']} -> {url}")
    checks.append(ok(not missing_sources, "every card source file exists", f"{len(missing_sources)} missing" if missing_sources else f"{len(cards)} sources exist"))
    checks.append(ok(not broken_links, "every card link resolves from docs/", f"{len(broken_links)} broken" if broken_links else "all resolve"))
    for issue in missing_sources[:10]:
        findings.append({"severity": "HIGH", "issue": "missing source file", "detail": issue})
    for issue in broken_links[:10]:
        findings.append({"severity": "MEDIUM", "issue": "broken relative link", "detail": issue})

    # 4. Evidence discipline — the core of this independent audit.
    unlabeled = [c["id"] for c in cards if not isinstance(c.get("evidence"), dict) or not c["evidence"].get("level") or not c["evidence"].get("basis")]
    invalid_level = [c["id"] for c in cards if isinstance(c.get("evidence"), dict) and c["evidence"].get("level") not in ALLOWED_EVIDENCE]
    checks.append(ok(not unlabeled, "every card declares a verifiable evidence level", f"{len(unlabeled)} cards have no evidence level/basis" if unlabeled else "all labeled"))
    checks.append(ok(not invalid_level, "evidence levels use the controlled vocabulary", f"{len(invalid_level)} invalid" if invalid_level else "valid"))
    if unlabeled:
        findings.append({
            "severity": "HIGH",
            "issue": "status labels are not separated from evidence levels",
            "detail": f"{len(unlabeled)} cards show PROVEN/MEASURED/PRESENT without saying whether the proof was re-executed now or copied from a dated inventory. Add evidence metadata before using the atlas for oversight.",
        })

    # 5. Headline stats independently recomputed.
    m = inv["metrics"]
    stats = atlas["stats"]
    stat_checks = {
        "Python file count": (stats[1]["value"], len([f for f in files if f.endswith(".py")])),
        "test file count": (stats[2]["value"], len([f for f in files if is_test_file(f)])),
        "historical tests passed": (stats[3]["value"], m["tests_passed"]),
        "red-team attacks/breaches": (stats[4]["value"], f"{m['redteam_attacks']} / {m['redteam_breaches']}"),
        "workflow count": (stats[5]["value"], len([f for f in files if f.startswith(".github/workflows/") and f.endswith((".yml", ".yaml"))])),
    }
    stat_bad = {k: {"published": a, "recomputed": b} for k, (a, b) in stat_checks.items() if a != b}
    checks.append(ok(not stat_bad, "headline statistics match recomputation", json.dumps(stat_bad, ensure_ascii=False) if stat_bad else "all 5 stats match"))

    honesty = atlas["honesty"]
    honesty_bad = (
        honesty["archive_proven"] != m["archive_proven"]
        or honesty["archive_declared"] != m["archive_declared"]
        or honesty["archive_simulated"] != m["archive_simulated"]
        or honesty["archive_failed"] != m["archive_failed"]
        or honesty["corrections"] != len(inv.get("corrections", []))
        or honesty["findings"] != len(inv.get("high_findings", []))
    )
    checks.append(ok(not honesty_bad, "honesty panel matches source files", "mismatch" if honesty_bad else "matches catalog + inventory"))

    versions = list(dict.fromkeys(re.findall(r"(?m)^## \[(.+?)\]", changelog)))
    checks.append(ok(len(atlas["milestones"]) == len(versions), "milestones match CHANGELOG releases", f"{len(atlas['milestones'])} vs {len(versions)}"))

    # 6. Self-contained / no-network guarantee.
    external_refs = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
    external_assets = [u for u in external_refs if not u.startswith("https://github.com/")]
    checks.append(ok(not external_assets, "page has no external runtime assets", ", ".join(external_assets[:3]) if external_assets else "fonts + data embedded"))
    checks.append(ok("__ATLAS_DATA__" not in html and "data:font/woff2;base64," in html, "HTML template fully rendered", "placeholders/font check"))

    # 7. Optional independent HTTP check.
    if args.http:
        try:
            with urllib.request.urlopen(args.url, timeout=10) as response:
                body = response.read()
                good = response.status == 200 and b"const ATLAS = " in body and len(body) > 100_000
                checks.append(ok(good, "independent HTTP GET of published page", f"HTTP {response.status}, {len(body):,} bytes"))
        except Exception as exc:  # noqa: BLE001 - report, never crash the audit
            checks.append(ok(False, "independent HTTP GET of published page", str(exc)))

    # 8. Optional independent test execution.
    if args.run_tests:
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "scripts", "-p", "test_achievements_atlas.py"],
            cwd=ROOT, capture_output=True, text=True, timeout=180,
        )
        tail = (result.stderr or result.stdout).strip().splitlines()[-1] if (result.stderr or result.stdout).strip() else ""
        checks.append(ok(result.returncode == 0, "unittest suite executed independently", tail))

    failed = [c for c in checks if c["status"] == "FAIL"]
    report = {
        "audit_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "method": "independent re-derivation from source files; atlas generator NOT imported",
        "source_files": ["catalog.json", "docs/inventory-2027.json", "CHANGELOG.md", "git ls-files", "docs/achievements-atlas.html"],
        "published_card_count": len(cards),
        "recomputed_kind_counts": expected_kinds,
        "actual_kind_counts": dict(actual_kinds),
        "checks": checks,
        "findings": findings,
        "verdict": "PASS" if not failed else "FAIL",
        "passed": len(checks) - len(failed),
        "total": len(checks),
    }

    if not args.no_write:
        REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# تدقيق مستقل — أطلس إنجازات AI-2027",
        "",
        f"- **الوقت:** {report['audit_at']}",
        f"- **الطريقة:** {report['method']}",
        f"- **الحكم:** {'✅ PASS' if report['verdict'] == 'PASS' else '❌ FAIL'} ({report['passed']}/{report['total']} فحص)",
        "",
        "| الفحص | النتيجة | التفاصيل |",
        "|---|---|---|",
    ]
    for c in checks:
        safe_detail = c["detail"].replace("|", "\\|")
        lines.append(f"| {c['label']} | {c['status']} | {safe_detail} |")
    if findings:
        lines += ["", "## الملاحظات", ""]
        for f in findings:
            lines.append(f"- **[{f['severity']}]** {f['issue']}: {f['detail']}")
    lines += [
        "",
        "## الحدود",
        "",
        "هذا التدقيق يتحقق من اتساق الأطلس مع ملفات المستودع ووجود الأدلة، ولا يُعيد تشغيل الاختبارات التاريخية أو الواجهات الحية إلا عند تمرير `--http` و`--run-tests`."
        " الحالات HISTORICAL تظل وصفاً لما سُجّل في تاريخ محدد، لا إثباتاً على الحالة التشغيلية الحالية.",
        "",
    ]
    if not args.no_write:
        REPORT_MD.write_text("\n".join(lines), encoding="utf-8")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"{'✅' if report['verdict'] == 'PASS' else '❌'} {report['verdict']} — {report['passed']}/{report['total']} checks")
        for c in checks:
            print(f"  {c['status']:<4} {c['label']}" + (f" — {c['detail']}" if c["status"] == "FAIL" else ""))
        if args.no_write:
            print("  reports: not written (--no-write)")
        else:
            print(f"  reports: {REPORT_JSON.relative_to(ROOT)}, {REPORT_MD.relative_to(ROOT)}")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
