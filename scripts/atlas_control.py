#!/usr/bin/env python3
"""AI-2027 Atlas Control Center — additive, read-only engineering oversight.

Seven modules, all offline and standard-library only:

  scan        Live Repository Scanner  (git blob-level delta vs a saved baseline)
  validate    Evidence Validator       (status claims must have matching evidence)
  architecture Architecture Map        (engine <-> project relations from docs)
  security    Security Monitor         (recorded findings + local security surface)
  ci          CI Intelligence          (workflows, pins, historical test matrix)
  progress    Progress Intelligence    (coverage + ranked bottlenecks)
  regression  Regression Detector      (file-level regression proxy vs baseline)

It never imports or modifies the kernel, and never rewrites an existing module.
Outputs: docs/atlas-baseline.json (created once), docs/atlas-control-report.json,
docs/atlas-control-center.html (self-contained, no network assets).

Usage:
    python3 scripts/atlas_control.py
    python3 scripts/atlas_control.py --json
    python3 scripts/atlas_control.py --refresh-baseline   # explicit opt-in
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ATLAS_HTML = ROOT / "docs" / "achievements-atlas.html"
CATALOG = ROOT / "catalog.json"
INVENTORY = ROOT / "docs" / "inventory-2027.json"
BASELINE = ROOT / "docs" / "atlas-baseline.json"
REPORT_JSON = ROOT / "docs" / "atlas-control-report.json"
OUT = ROOT / "docs" / "atlas-control-center.html"
AR_FONT = ROOT / "website" / "productivity-kit" / "fonts" / "readex-pro-arabic.woff2"
LAT_FONT = ROOT / "website" / "productivity-kit" / "fonts" / "readex-pro-latin.woff2"
REPO_URL = "https://github.com/sayedelazameydesign-crypto/ai-2027"

STRONG_STATUSES = {"PROVEN", "MEASURED", "LIVE"}
WEAK_EVIDENCE = {"FILE_EXISTS", "DOCUMENTED_ONLY"}
CRITICAL_PREFIXES = ("scripts/", ".github/workflows/", "python-2027/src/", "kernel-2027/", "memory/")


# --------------------------------------------------------------------------- #
# shared readers
# --------------------------------------------------------------------------- #
def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def git_blobs() -> dict[str, str]:
    result = subprocess.run(
        ["git", "ls-files", "-s", "-z"], cwd=ROOT, capture_output=True, check=True, timeout=60
    )
    blobs: dict[str, str] = {}
    for entry in result.stdout.split(b"\0"):
        if not entry:
            continue
        head, _, path = entry.partition(b"\t")
        parts = head.split()
        if len(parts) >= 2 and path:
            blobs[path.decode("utf-8", "replace")] = parts[1].decode("ascii", "replace")
    return blobs


def git_files() -> list[str]:
    return list(git_blobs())


def git_meta(key: str) -> str:
    try:
        return subprocess.run(
            ["git", key] if key == "branch" else ["git", "rev-parse", "HEAD"],
            cwd=ROOT, capture_output=True, text=True, check=True, timeout=15,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def atlas_payload() -> dict[str, Any]:
    line = next((l for l in ATLAS_HTML.read_text(encoding="utf-8").splitlines() if l.startswith("const ATLAS = ")), None)
    if not line:
        raise RuntimeError("atlas payload missing — run achievements_atlas.py first")
    return json.loads(line[len("const ATLAS = ") :].rstrip().rstrip(";"))


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def counts_of(files: list[str]) -> dict[str, int]:
    test_ext = (".py", ".js", ".mjs", ".ts", ".tsx", ".sh")
    tests = []
    for name in files:
        p = Path(name)
        if p.suffix in test_ext and (p.name.startswith("test_") or p.name.endswith(("_test" + p.suffix, ".test" + p.suffix))):
            tests.append(name)
    return {
        "files": len(files),
        "python": len([f for f in files if f.endswith(".py")]),
        "tests": len(tests),
        "docs": len([f for f in files if f.startswith("docs/") and f.endswith(".md")]),
        "workflows": len([f for f in files if f.startswith(".github/workflows/") and f.endswith((".yml", ".yaml"))]),
        "scripts": len([f for f in files if f.startswith("scripts/") and f.endswith(".py")]),
        "projects": len([f for f in files if f.startswith("projects/") and f.count("/") == 1]),
    }


def summary(label: str, value: Any, note: str = "") -> dict[str, Any]:
    return {"label": label, "value": value, "note": note}


# --------------------------------------------------------------------------- #
# 1 — Live Repository Scanner
# --------------------------------------------------------------------------- #
def build_snapshot() -> dict[str, Any]:
    files = git_files()
    return {
        "created_at": utcnow(),
        "commit": git_meta("rev-parse"),
        "branch": git_meta("branch"),
        "blobs": git_blobs(),
        "counts": counts_of(files),
    }


def module_scan(baseline: dict[str, Any], current: dict[str, Any], created: bool) -> dict[str, Any]:
    base_blobs, cur_blobs = baseline["blobs"], current["blobs"]
    added = sorted(set(cur_blobs) - set(base_blobs))
    removed = sorted(set(base_blobs) - set(cur_blobs))
    modified = sorted(p for p in set(base_blobs) & set(cur_blobs) if base_blobs[p] != cur_blobs[p])
    deltas = {k: current["counts"][k] - baseline["counts"].get(k, 0) for k in current["counts"]}
    return {
        "status": "BASELINE" if created else ("CHANGED" if added or removed or modified else "CLEAN"),
        "summary": [
            summary("ملفات متتبّعة", current["counts"]["files"], "git ls-files -s · مقاس الآن"),
            summary("ملفات جديدة", len(added), "مقارنة بالخط الأساس"),
            summary("ملفات محذوفة", len(removed), "مقارنة بالخط الأساس"),
            summary("ملفات معدّلة", len(modified), "تغيّر blob SHA-1"),
        ],
        "rows": (
            [{"type": "added", "path": p} for p in added[:200]]
            + [{"type": "removed", "path": p} for p in removed[:200]]
            + [{"type": "modified", "path": p} for p in modified[:200]]
        ),
        "deltas": deltas,
        "notes": [
            "الخط الأساس هو docs/atlas-baseline.json؛ أُنشئ من حالة المستودع المُدقَّقة.",
            "هذا فحص بنيوي على مستوى الملفات، ولا يثبت أن الوحدات تعمل.",
        ],
    }


# --------------------------------------------------------------------------- #
# 2 — Evidence Validator
# --------------------------------------------------------------------------- #
def module_validate(atlas: dict[str, Any]) -> dict[str, Any]:
    allowed = set(atlas["evidenceLabels"])
    issues: list[dict[str, str]] = []
    strong_count = 0
    for card in atlas["cards"]:
        evidence = card.get("evidence", {})
        level = evidence.get("level", "")
        basis = evidence.get("basis", "")
        source = card["source"]
        if level not in allowed:
            issues.append({"id": card["id"], "problem": "unknown evidence level", "detail": level})
        if not basis:
            issues.append({"id": card["id"], "problem": "missing evidence basis", "detail": ""})
        if not (ROOT / source).exists() and not source.startswith("http"):
            issues.append({"id": card["id"], "problem": "missing source file", "detail": source})
        if card["status"] in STRONG_STATUSES:
            strong_count += 1
            if level in WEAK_EVIDENCE:
                issues.append({
                    "id": card["id"],
                    "problem": "strong status on file-only evidence",
                    "detail": f"{card['status']} + {level}",
                })
    return {
        "status": "PASS" if not issues else "WARN",
        "summary": [
            summary("بطاقات مُحَقَّقة", len(atlas["cards"]), "كل بطاقة في الأطلس"),
            summary("ادعاءات قوية", strong_count, "PROVEN / MEASURED / LIVE"),
            summary("مشاكل الأدلة", len(issues), "صفر يعني لا مبالغة في الحالة"),
        ],
        "rows": issues[:200],
        "notes": [
            "القاعدة: لا يُقبل PROVEN أو MEASURED أو LIVE مع دليل «الملف موجود» فقط.",
            "الحالات التاريخية موافَق عليها بشرط ذكر تاريخ المصدر صراحةً في السند.",
            "هذا التحقق يفحص الاتساق والتسمية؛ لا يُعيد تشغيل الاختبارات التاريخية.",
        ],
    }


# --------------------------------------------------------------------------- #
# 3 — Architecture Map
# --------------------------------------------------------------------------- #
def module_architecture(cat: dict[str, Any], inv: dict[str, Any]) -> dict[str, Any]:
    engines = inv.get("engines", [])
    projects = cat.get("projects", [])
    docs = sorted((ROOT / "docs").glob("*.md")) + [ROOT / "README.md"]
    texts: dict[str, str] = {}
    for path in docs:
        try:
            texts[path.name] = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

    relations: list[dict[str, Any]] = []
    for engine in engines:
        eid = engine["id"]
        for project in projects:
            name = project["name"]
            hits: dict[str, int] = {}
            for doc_name, text in texts.items():
                # Co-occurrence in the same document is a documented relation, not a runtime dependency.
                if eid in text and name in text:
                    hits[doc_name] = min(text.count(eid), text.count(name))
            if hits:
                relations.append({
                    "engine": eid,
                    "project": name,
                    "category": project.get("category_ar", project.get("category", "")),
                    "engine_status": engine.get("status", ""),
                    "project_status": project.get("operational_status", ""),
                    "weight": sum(hits.values()),
                    "sources": ", ".join(sorted(hits)[:4]),
                })
    relations.sort(key=lambda r: (-r["weight"], r["engine"], r["project"]))
    nodes = [{"id": f"engine:{e['id']}", "kind": "engine", "label": e["id"], "status": e.get("status", "")} for e in engines]
    nodes += [{"id": f"project:{p['name']}", "kind": "project", "label": p["name"], "status": p.get("operational_status", "")} for p in projects]
    return {
        "status": "MAP",
        "summary": [
            summary("محركات", len(engines), "من inventory-2027.json"),
            summary("مشاريع", len(projects), "من catalog.json"),
            summary("علاقات موثّقة", len(relations), "تشارك في مستند واحد على الأقل"),
            summary("محركات بلا علاقة", len([e for e in engines if not any(r["engine"] == e["id"] for r in relations)]), "فجوة توثيق محتملة"),
        ],
        "rows": relations[:300],
        "nodes": nodes,
        "notes": [
            "العلاقة مبنية على ورود اسم المحرك واسم المشروع في نفس الوثيقة — علاقة توثيقية، لا تبعية تشغيلية.",
            "الوزن = عدد مرات التشارك في الوثائق (سقف لكل وثيقة لتجنّب تضخيم الملفات الطويلة).",
        ],
    }


# --------------------------------------------------------------------------- #
# 4 — Security Monitor
# --------------------------------------------------------------------------- #
def module_security(inv: dict[str, Any], files: list[str]) -> dict[str, Any]:
    rows: list[dict[str, str]] = []
    for item in inv.get("high_findings", []):
        rows.append({
            "severity": "HIGH",
            "signal": item.get("project", ""),
            "detail": item.get("issue", ""),
            "source": "docs/inventory-2027.json",
        })
    for path in sorted(f for f in files if f.startswith("scripts/") and f.endswith(".py")):
        lowered = path.lower()
        if any(k in lowered for k in ("secret_scan", "live_egress", "network_preflight", "workflow_secret", "policy_hardening")):
            rows.append({
                "severity": "CONTROL",
                "signal": Path(path).stem,
                "detail": "حاجز أمان محلي موجود في المستودع (وجود الملف لا يعني أنه نُفّذ الآن).",
                "source": path,
            })
    for path in sorted(f for f in files if f.startswith(".github/workflows/") and f.endswith(".yml")):
        if any(k in path for k in ("codeql", "live-egress-audit", "pr-lens")):
            rows.append({
                "severity": "CONTROL",
                "signal": Path(path).stem,
                "detail": "سير عمل مرتبط بالأمان مُعرَّف محلياً.",
                "source": path,
            })
    for path in ("docs/SECURITY.md", "docs/DEFECT-LEDGER.md"):
        if (ROOT / path).exists():
            rows.append({
                "severity": "DOC",
                "signal": Path(path).name,
                "detail": "سجل السياسات/العيوب متاح للمراجعة اليدوية.",
                "source": path,
            })
    return {
        "status": "MONITOR",
        "summary": [
            summary("ملاحظات عالية", len([r for r in rows if r["severity"] == "HIGH"]), "من الجرد التاريخي"),
            summary("حواجز محلية", len([r for r in rows if r["severity"] == "CONTROL"]), "ملفات/سير عمل موجودة"),
            summary("سجلات", len([r for r in rows if r["severity"] == "DOC"]), "وثائق"),
        ],
        "rows": rows,
        "notes": [
            "لا يوجد فحص CVE أو اتصال شبكي هنا: المراقبة محلية بالكامل وتعتمد على الملفات المُسجَّلة.",
            "CONTROL يعني أن الحاجز موجود كملف؛ تشغيله ونتائجه يحتاجان إلى فحص مستقل.",
        ],
    }


# --------------------------------------------------------------------------- #
# 5 — CI Intelligence
# --------------------------------------------------------------------------- #
def module_ci(files: list[str], inv: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    workflows = sorted(f for f in files if f.startswith(".github/workflows/") and f.endswith((".yml", ".yaml")))
    for path in workflows:
        text = (ROOT / path).read_text(encoding="utf-8", errors="ignore")
        name_match = re.search(r"(?m)^name:\s*(.+)$", text)
        jobs = len(re.findall(r"(?m)^  [A-Za-z0-9_-]+:\s*$", text.split("jobs:", 1)[1])) if "jobs:" in text else 0
        triggers = [t for t in ("push", "pull_request", "schedule", "workflow_dispatch") if re.search(rf"(?m)^\s{{2}}{t}:", text)]
        uses = re.findall(r"(?m)^\s*uses:\s*([^\s#]+)", text)
        # Local reusable workflows (./.github/...) are not external actions; no SHA pin applies.
        external = [u for u in uses if not u.startswith("./")]
        pinned = [u for u in external if "@" in u and re.fullmatch(r"[0-9a-f]{40}", u.rsplit("@", 1)[1])]
        unpinned = [u for u in external if u not in pinned]
        rows.append({
            "workflow": Path(path).stem,
            "name": name_match.group(1).strip() if name_match else "",
            "jobs": jobs,
            "triggers": ", ".join(triggers) or "-",
            "pinned": len(pinned),
            "unpinned": len(unpinned),
            "status": "OK" if not unpinned else "REVIEW",
            "source": path,
        })
    matrix = inv.get("test_matrix", [])
    rows.append({
        "workflow": "historical test matrix",
        "name": "docs/inventory-2027.json (2026-11-22)",
        "jobs": len(matrix),
        "triggers": "recorded",
        "pinned": 0,
        "unpinned": 0,
        "status": "HISTORICAL",
        "source": "docs/inventory-2027.json",
    })
    return {
        "status": "PASS" if all(r["status"] != "REVIEW" for r in rows) else "REVIEW",
        "summary": [
            summary("سير عمل", len(workflows), "مقاس الآن"),
            summary("إجراءات مثبّتة SHA", sum(r["pinned"] for r in rows), "full-length SHA"),
            summary("إجراءات بلا تثبيت", sum(r["unpinned"] for r in rows), "مراجعة مطلوبة إن وُجدت"),
            summary("اختبارات مسجّلة", sum(r.get("passed", 0) for r in matrix), "تاريخية · لم تُعَد هنا"),
        ],
        "rows": rows,
        "notes": [
            "مجموع الاختبارات من الجرد التاريخي؛ لا يُحتسب كتشغيل حالي.",
            "فحص التثبيت يستحقّ النص فقط ولا يضمن أن الإجراء مُحدَّث أو آمن.",
            "المسارات المحلية ./.github/workflows/ هي reusable workflows وليست إجراءات خارجية، فلا تُحتسب ضمن التثبيت.",
        ],
    }


# --------------------------------------------------------------------------- #
# 6 — Progress Intelligence
# --------------------------------------------------------------------------- #
def module_progress(cat: dict[str, Any], inv: dict[str, Any], atlas: dict[str, Any]) -> dict[str, Any]:
    m = inv["metrics"]
    counts = cat.get("verified_counts", {})
    engines = inv.get("engines", [])
    present = len([e for e in engines if e.get("status") == "present"])
    partial = len([e for e in engines if e.get("status") == "partial"])
    total_projects = max(1, sum(counts.values()) or len(cat.get("projects", [])))
    proven = counts.get("PROVEN", 0)
    bottlenecks = [
        {"area": "مشاريع DECLARED", "count": counts.get("DECLARED", 0), "action": "تشغيل اختباراتها محلياً قبل ترقيتها", "source": "catalog.json"},
        {"area": "محركات PARTIAL", "count": partial, "action": "إغلاق الفجوات المعلنة واحدة واحدة", "source": "docs/inventory-2027.json"},
        {"area": "مشاريع SIMULATED", "count": counts.get("SIMULATED", 0), "action": "بناء قدرة حقيقية أو إزالة الادعاء", "source": "catalog.json"},
        {"area": "تصحيحات مسجّلة", "count": len(inv.get("corrections", [])), "action": "منع عودة الادعاءات المصحَّحة", "source": "docs/inventory-2027.json"},
        {"area": "ملاحظات أمان عالية", "count": len(inv.get("high_findings", [])), "action": "إعادة فحصها قبل الاعتماد", "source": "docs/inventory-2027.json"},
        {"area": "مشاريع بلا رخصة", "count": m.get("projects_without_license", 0), "action": "تحديد حالة الترخيص قبل إعادة الاستخدام", "source": "docs/inventory-2027.json"},
        {"area": "واجهات متوقفة", "count": len(cat.get("live_surfaces_verified_2026_11_17", {}).get("dead", [])), "action": "تحديث الحالة أو إصلاح النشر", "source": "catalog.json"},
    ]
    bottlenecks.sort(key=lambda b: -b["count"])
    return {
        "status": "MEASURED",
        "summary": [
            summary("نسبة PROVEN في الأرشيف", f"{(proven / total_projects * 100):.1f}%", f"{proven} من {total_projects} · من catalog.json"),
            summary("محركات حاضرة", f"{(present / max(1, len(engines)) * 100):.1f}%", f"{present} من {len(engines)}"),
            summary("اختبارات ناجحة (تاريخي)", m.get("tests_passed", 0), "2026-11-22 · لم تُعَد هنا"),
            summary("بطاقات الأطلس", len(atlas["cards"]), "كل بطاقة مرتبطة بمصدر"),
        ],
        "rows": bottlenecks,
        "notes": [
            "النسب محسوبة من ملفات المصدر ولا تُساوي «نسبة اكتمال المنتج».",
            "الاختناقات مرتّبة بعدد العناصر، لا بخطورتها؛ الترتيب للعمل لا للتقييم.",
        ],
    }


# --------------------------------------------------------------------------- #
# 7 — Regression Detector
# --------------------------------------------------------------------------- #
def module_regression(scan: dict[str, Any], atlas: dict[str, Any], created: bool) -> dict[str, Any]:
    if created:
        return {
            "status": "BASELINE",
            "summary": [summary("الحالة", "BASELINE CREATED", "لا مقارنة في أول تشغيل")],
            "rows": [],
            "notes": ["أُنشئ الخط الأساس من الحالة المُدقَّقة. التشغيل التالي سيقارن blob SHA-1 لكل ملف."],
        }
    removed = [r["path"] for r in scan["rows"] if r["type"] == "removed"]
    critical_removed = [p for p in removed if p.startswith(CRITICAL_PREFIXES) or p in {"README.md", "catalog.json"}]
    broken = []
    for card in atlas["cards"]:
        src = card["source"]
        if not src.startswith("http") and not (ROOT / src).exists():
            broken.append(src)
    fatal = bool(critical_removed or broken)
    rows = (
        [{"type": "critical removed", "path": p} for p in critical_removed[:100]]
        + [{"type": "broken atlas source", "path": p} for p in sorted(set(broken))[:100]]
    )
    return {
        "status": "FAIL" if fatal else ("WARN" if scan["rows"] else "CLEAN"),
        "summary": [
            summary("ملفات حرجة محذوفة", len(critical_removed), "scripts/workflows/kernel/memory"),
            summary("مصادر أطلس مكسورة", len(set(broken)), "من بطاقات الأطلس"),
            summary("تغيّرات غير حرجة", max(0, len(scan["rows"]) - len(rows)), "إضافة/تعديل اعتيادي"),
        ],
        "rows": rows,
        "notes": [
            "هذا وكيل regression على مستوى الملفات: يكتشف اختفاء الملفات أو كسر المصادر.",
            "لا يستطيع اكتشاف تراجع سلوكي إلا إذا سُجِّلت نتائج تشغيل سابقة وقورنت.",
        ],
    }


# --------------------------------------------------------------------------- #
# HTML control center (offline, self-contained)
# --------------------------------------------------------------------------- #
HTML = r"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>مركز رقابة AI-2027</title>
<style>
@font-face{font-family:Readex;src:url(data:font/woff2;base64,__AR_FONT__) format('woff2');font-display:swap}
@font-face{font-family:ReadexLatin;src:url(data:font/woff2;base64,__LAT_FONT__) format('woff2');font-display:swap}
:root{--bg:#060917;--panel:rgba(17,23,46,.78);--line:rgba(148,163,184,.18);--ink:#eef2ff;--muted:#a5b0cd;--gold:#f6cb75;--mint:#54e6c1;--violet:#a78bfa;--rose:#fb7185}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 15% 0%,rgba(124,58,237,.18),transparent 35%),radial-gradient(circle at 85% 100%,rgba(6,182,212,.12),transparent 35%),var(--bg);color:var(--ink);font-family:Readex,"Segoe UI",Tahoma,sans-serif;line-height:1.75;min-height:100vh}
.wrap{max-width:1200px;margin:auto;padding:44px 20px 80px}
h1{font-size:clamp(30px,5vw,52px);margin:0;background:linear-gradient(100deg,#fff,var(--gold),var(--mint));-webkit-background-clip:text;background-clip:text;color:transparent}
.sub{color:var(--muted);max-width:760px;margin:8px 0 20px}
.top{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:26px}
.btn{cursor:pointer;text-decoration:none;color:var(--ink);border:1px solid var(--line);background:rgba(255,255,255,.05);padding:9px 15px;border-radius:12px;font:inherit}
.btn:hover{border-color:var(--gold)}
.verdict{display:inline-flex;gap:8px;align-items:center;border:1px solid rgba(84,230,193,.35);background:rgba(84,230,193,.08);color:var(--mint);border-radius:999px;padding:6px 14px;font-size:13px}
.tabs{display:flex;gap:8px;overflow-x:auto;padding-bottom:12px;margin-bottom:18px}
.tab{white-space:nowrap;cursor:pointer;border:1px solid var(--line);background:rgba(255,255,255,.04);color:var(--muted);border-radius:12px;padding:9px 14px}
.tab.active{color:#fff;border-color:rgba(246,203,117,.6);background:linear-gradient(120deg,rgba(246,203,117,.18),rgba(167,139,250,.18))}
.panel{display:none;border:1px solid var(--line);background:var(--panel);backdrop-filter:blur(14px);border-radius:20px;padding:22px}
.panel.active{display:block}
h2{margin:0 0 4px;font-size:23px}
.note{color:var(--muted);font-size:13px;margin:4px 0 18px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:20px}
.metric{border:1px solid var(--line);border-radius:14px;padding:14px;background:rgba(255,255,255,.03)}
.metric strong{display:block;font-size:24px;color:var(--gold);font-family:ReadexLatin,sans-serif;word-break:break-word}
.metric span{font-size:12px}.metric small{display:block;color:var(--muted);font-size:11px;margin-top:4px}
table{width:100%;border-collapse:collapse;font-size:12px;direction:rtl}
th,td{text-align:right;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--gold);position:sticky;top:0;background:#111730}
td{direction:auto;text-align:right;word-break:break-word}
td[dir="rtl"]{direction:rtl}
code{direction:ltr;color:var(--mint);font-size:11px}
.notes{margin-top:18px;border-top:1px dashed var(--line);padding-top:12px;color:var(--muted);font-size:12px}
.notes li{margin-bottom:5px}
.pill{padding:3px 8px;border-radius:8px;border:1px solid var(--line);font-size:11px}
.ok{color:var(--mint)}.warn{color:var(--gold)}.bad{color:var(--rose)}
.map{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:10px;margin-top:10px}
.engine{border:1px solid var(--line);border-radius:14px;padding:12px;cursor:pointer;background:rgba(255,255,255,.03)}
.engine:hover{border-color:var(--gold)}
.engine b{display:block;color:var(--mint);direction:ltr;text-align:left;font-size:13px}
.engine span{font-size:11px;color:var(--muted)}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0}
.chip{border:1px solid var(--line);border-radius:999px;padding:3px 10px;font-size:11px;background:rgba(167,139,250,.1)}
footer{margin-top:40px;color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding-top:16px}
</style></head><body><main class="wrap">
<h1>🛡️ مركز رقابة AI-2027</h1>
<p class="sub">سبع وحدات رقابة تعمل محلياً وبمكتبة Python القياسية فقط. لا تلمس النواة، لا تتصل بالإنترنت، ولا تعتبر محاكاةً دليلاً على تشغيل حقيقي.</p>
<div class="top"><span class="verdict" id="verdict"></span><a class="btn" href="achievements-atlas.html">← العودة إلى أطلس الإنجازات</a><a class="btn" href="ATLAS-AUDIT.md">📋 تقرير التدقيق المستقل</a><a class="btn" href="OPERATIONAL-EVIDENCE.md">🧪 الإثبات التشغيلي 2.1</a></div>
<div class="tabs" id="tabs"></div>
<div id="panels"></div>
<footer id="foot"></footer>
</main>
<script>
const REPORT = __REPORT_DATA__;
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const nf = new Intl.NumberFormat('ar-EG');
const keyLabel = (k) => ({status:'الحالة',type:'النوع',path:'المسار',id:'المعرّف',problem:'المشكلة',detail:'التفصيل',severity:'الخطورة',signal:'الإشارة',source:'المصدر',workflow:'سير العمل',name:'الاسم',jobs:'المهام',triggers:'المشغّلات',pinned:'مثبّت',unpinned:'غير مثبّت',area:'المجال',count:'العدد',action:'الإجراء',engine:'المحرك',project:'المشروع',category:'التصنيف',engine_status:'حالة المحرك',project_status:'حالة المشروع',weight:'الوزن',sources:'المصادر'}[k] || k);
const renderValue = (v) => typeof v === 'number' ? nf.format(v) : esc(v);
function table(rows) {
  if (!rows || !rows.length) return '<p class="note">لا توجد صفوف في هذا القسم.</p>';
  const keys = Object.keys(rows[0]);
  return '<table><thead><tr>' + keys.map(k => `<th>${esc(keyLabel(k))}</th>`).join('') + '</tr></thead><tbody>' +
    rows.map(r => '<tr>' + keys.map(k => `<td>${k === 'status' || k === 'severity' ? `<span class="pill ${String(r[k]).toLowerCase() === 'ok' || String(r[k]).toLowerCase() === 'pass' ? 'ok' : String(r[k]).toLowerCase() === 'fail' || String(r[k]).toLowerCase() === 'high' ? 'bad' : 'warn'}">${renderValue(r[k])}</span>` : renderValue(r[k])}</td>`).join('') + '</tr>').join('') + '</tbody></table>';
}
function panelHtml(name, mod) {
  const cards = (mod.summary || []).map(s => `<div class="metric"><strong>${renderValue(s.value)}</strong><span>${esc(s.label)}</span><small>${esc(s.note || '')}</small></div>`).join('');
  const notes = (mod.notes || []).map(n => `<li>${esc(n)}</li>`).join('');
  let extra = '';
  if (mod.rows && mod.nodes) {
    const engines = [...new Set(mod.rows.map(r => r.engine))];
    extra = `<div class="chips">${engines.map(e => `<button class="tab arch" data-engine="${esc(e)}">${esc(e)}</button>`).join('')}</div><div class="map" id="arch-map">${mod.rows.map(r => `<div class="engine" data-engine="${esc(r.engine)}"><b>${esc(r.engine)}</b><span>${esc(r.project)} · ${esc(r.project_status)} · وزن ${r.weight}</span></div>`).join('')}</div>`;
  }
  return `<section class="panel" id="p-${name}"><h2>${esc(mod.title)}</h2><p class="note">${esc(mod.subtitle || '')}</p><div class="cards">${cards}</div>${extra}${table(mod.rows)}<div class="notes"><ul>${notes}</ul></div></section>`;
}
const titles = {
  scan:['🔍Live Repository Scanner','فحص بنيوي للملفات مقابل خط أساس محفوظ'],
  validator:['🧪Evidence Validator','الحالة القوية تحتاج دليلاً قوياً'],
  architecture:['📊Architecture Map','علاقات توثيقية بين المحركات والمشاريع'],
  security:['🛡️Security Monitor','إشارات أمان محلية وملاحظات مُسجَّلة'],
  ci:['⚙️CI Intelligence','سير العمل والتثبيت ونتائج الاختبارات المسجّلة'],
  progress:['🧠Progress Intelligence','مؤشرات تغطية واختناقات مرتّبة'],
  regression:['🔄Regression Detector','كشف فقدان الملفات أو كسر مصادر الأطلس']
};
const names = Object.keys(titles);
document.getElementById('tabs').innerHTML = names.map((n,i) => `<button class="tab${i === 0 ? ' active' : ''}" data-tab="${n}">${titles[n][0]}</button>`).join('');
document.getElementById('panels').innerHTML = names.map((n,i) => `<div id="wrap-${n}" style="display:${i === 0 ? 'block' : 'none'}">${panelHtml(n, Object.assign({title:titles[n][0],subtitle:titles[n][1]}, REPORT.modules[n]))}</div>`).join('');
document.getElementById('verdict').textContent = 'حكم التدقيق: ' + REPORT.verdict + ' · ' + REPORT.generated_at;
document.getElementById('foot').textContent = REPORT.method + ' · ' + REPORT.report_path;
document.getElementById('tabs').addEventListener('click', e => {
  const tab = e.target.closest('[data-tab]'); if (!tab) return;
  document.querySelectorAll('#tabs .tab').forEach(t => t.classList.toggle('active', t === tab));
  names.forEach(n => document.getElementById('wrap-' + n).style.display = (n === tab.dataset.tab ? 'block' : 'none'));
});
document.addEventListener('click', e => {
  const btn = e.target.closest('.arch'); if (!btn) return;
  const engine = btn.dataset.engine;
  document.querySelectorAll('#arch-map .engine').forEach(el => el.style.display = (!engine || el.dataset.engine === engine) ? 'block' : 'none');
});
</script></body></html>
"""


def render_html(report: dict[str, Any]) -> str:
    ar = base64.b64encode(AR_FONT.read_bytes()).decode("ascii") if AR_FONT.exists() else ""
    lat = base64.b64encode(LAT_FONT.read_bytes()).decode("ascii") if LAT_FONT.exists() else ""
    payload = json.dumps(report, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = HTML.replace("__AR_FONT__", ar).replace("__LAT_FONT__", lat).replace("__REPORT_DATA__", payload)
    return html


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print report JSON")
    parser.add_argument("--refresh-baseline", action="store_true", help="explicitly overwrite the baseline")
    args = parser.parse_args(argv)

    current = build_snapshot()
    created = args.refresh_baseline or not BASELINE.exists()
    baseline = current if created else read_json(BASELINE)
    if created:
        BASELINE.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    atlas = atlas_payload()
    cat = read_json(CATALOG)
    inv = read_json(INVENTORY)
    files = git_files()

    scan_result = module_scan(baseline, current, created)
    modules = {
        "scan": scan_result,
        "validator": module_validate(atlas),
        "architecture": module_architecture(cat, inv),
        "security": module_security(inv, files),
        "ci": module_ci(files, inv),
        "progress": module_progress(cat, inv, atlas),
        "regression": module_regression(scan_result, atlas, created),
    }
    bad = [name for name, mod in modules.items() if mod["status"] in {"FAIL", "WARN", "REVIEW"}]
    report = {
        "generated_at": utcnow(),
        "method": "read-only Python stdlib; no kernel import, no network, no simulated test treated as proof",
        "baseline_created": created,
        "baseline_path": str(BASELINE.relative_to(ROOT)),
        "report_path": str(REPORT_JSON.relative_to(ROOT)),
        "modules": modules,
        "verdict": "WATCH" if bad else "PASS",
        "watch_modules": bad,
    }
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OUT.write_text(render_html(report), encoding="utf-8")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"🛡️ مركز الرقابة — {report['verdict']} · خط أساس {'جديد' if created else 'موجود'}")
        for name, mod in modules.items():
            print(f"  {name:<12} {mod['status']:<10} " + " · ".join(f"{s['label']}={s['value']}" for s in mod["summary"][:2]))
        print(f"  reports: {REPORT_JSON.relative_to(ROOT)}, {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
