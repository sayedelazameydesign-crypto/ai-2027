#!/usr/bin/env python3
"""Achievement Atlas — extract every documented achievement from the repository.

Pure standard-library Python. The generator reads the repository's own data files
(catalog.json, docs/inventory-2027.json, CHANGELOG.md, docs/, scripts/,
.github/workflows/) plus a live filesystem/git measurement, then renders a single
self-contained Arabic RTL HTML page: docs/achievements-atlas.html.

Usage:
    python3 scripts/achievements_atlas.py                # write the HTML
    python3 scripts/achievements_atlas.py --json          # emit the data model
    python3 scripts/achievements_atlas.py --print         # terminal summary
    python3 scripts/achievements_atlas.py --check         # verify output exists
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "achievements-atlas.html"
CATALOG = ROOT / "catalog.json"
INVENTORY = ROOT / "docs" / "inventory-2027.json"
CHANGELOG = ROOT / "CHANGELOG.md"
AR_FONT = ROOT / "website" / "productivity-kit" / "fonts" / "readex-pro-arabic.woff2"
LAT_FONT = ROOT / "website" / "productivity-kit" / "fonts" / "readex-pro-latin.woff2"

REPO_URL = "https://github.com/sayedelazameydesign-crypto/ai-2027"

CATEGORIES: dict[str, str] = {
    "evidence": "الأدلة والتحقق",
    "tests": "الاختبارات",
    "engines": "المحركات",
    "kernel": "النواة والبروتوكول",
    "security": "الأمان والحواجز",
    "automation": "الأتمتة و CI",
    "product": "المنتجات والواجهات",
    "archive": "المشاريع المؤرشفة",
    "honesty": "الصدق والتصحيحات",
    "governance": "الحوكمة والقواعد",
    "knowledge": "الوثائق والمعرفة",
    "publishing": "النشر والتوعية",
}

CATEGORY_ICONS: dict[str, str] = {
    "evidence": "🔍",
    "tests": "🧪",
    "engines": "⚙️",
    "kernel": "🧠",
    "security": "🛡️",
    "automation": "🤖",
    "product": "✨",
    "archive": "📦",
    "honesty": "🧭",
    "governance": "📜",
    "knowledge": "📚",
    "publishing": "📣",
}

ENGINE_LABELS: dict[str, str] = {
    "intelligence-core": "نواة الذكاء",
    "python-alchemy": "كيمياء Python",
    "model-router": "موجّه النماذج",
    "agent-fleet": "أسطول الوكلاء",
    "browser-engine": "محرك المتصفح",
    "computer-engine": "محرك الحاسوب",
    "skill-engine": "محرك المهارات",
    "memory-engine": "محرك الذاكرة",
    "automation-engine": "محرك الأتمتة",
    "cloud-runtime": "زمن التشغيل السحابي",
    "security-engine": "محرك الأمان",
    "evidence-engine": "محرك الأدلة",
    "shared-kernel": "النواة المشتركة",
}

EVIDENCE_LABELS: dict[str, str] = {
    "RECOMPUTED_NOW": "مُعاد حسابها الآن",
    "FILE_EXISTS": "الملف موجود",
    "DOCUMENTED_ONLY": "موثّق نصياً",
    "HISTORICAL_INVENTORY": "جرد تاريخي",
    "HISTORICAL_CATALOG": "كتالوج تاريخي",
    "HISTORICAL_SURFACE": "فحص واجهة تاريخي",
    "LIVE_CHECKED": "فُحص حياً",
}

# Explicit, shared policy: fixtures like test_input.txt are NOT test files.
TEST_EXTENSIONS = (".py", ".js", ".mjs", ".ts", ".tsx", ".sh")


def is_test_file(path: str) -> bool:
    name = Path(path).name
    if Path(path).suffix not in TEST_EXTENSIONS:
        return False
    return name.startswith("test_") or name.endswith(("_test" + Path(path).suffix, ".test" + Path(path).suffix))


TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "security": ("security", "secret", "token", "auth", "attack", "red-team", "fail-closed", "credential", "guard", "rate limit", "hardening", "أمان", "سر"),
    "automation": ("ci", "workflow", "gate", "deploy", "release", "heartbeat", "actions", "schedule", "pipeline", "gate", "runtime", "automation"),
    "evidence": ("evidence", "verification", "audit", "truth", "proven", "claim", "identity", "sbom", "inventory", "دليل", "تحقق"),
    "kernel": ("kernel", "planner", "memory", "lease", "session", "protocol", "ast", "checkpoint", "fencing", "نواة"),
    "product": ("studio", "ui", "workspace", "live agent", "browser", "demo", "cli", "connector", "hud", "product", " experience"),
    "governance": ("governance", "policy", "license", "prompt", "community", "roadmap", "zero-cost", "zero cost", "rule", "قاعدة"),
    "publishing": ("episode", "blog", "promotion", "visual tour", "publish", "kit", "نشر", "حلقة"),
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def clean_md(text: str) -> str:
    """Strip markdown noise while keeping the human-readable claim."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = text.replace("`", "").replace("**", "").replace("__", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def shorten(text: str, width: int = 260) -> str:
    text = clean_md(text)
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9\u0600-\u06ff]+", "-", text.lower()).strip("-")
    return slug or "item"


def repo_path(path: str) -> str:
    """Strip only a literal leading './' — never eat a leading dot in '.github'."""
    return path[2:] if path.startswith("./") else path


def source_url(path: str) -> str:
    """A repo-root-relative link that works in the local preview server."""
    from urllib.parse import quote

    if path.startswith("http"):
        return path
    return "../" + quote(repo_path(path))


def github_url(path: str) -> str:
    from urllib.parse import quote

    return f"{REPO_URL}/blob/HEAD/{quote(repo_path(path))}"


def classify(text: str) -> str:
    lowered = text.lower()
    for category, words in TOPIC_KEYWORDS.items():
        if any(word in lowered for word in words):
            return category
    return "evidence"


def make_card(
    card_id: str,
    title: str,
    summary: str,
    category: str,
    status: str,
    source: str,
    *,
    metric: str = "",
    bullets: list[str] | None = None,
    tags: list[str] | None = None,
    date: str = "",
    kind: str = "",
    evidence_level: str = "DOCUMENTED_ONLY",
    evidence_basis: str = "",
) -> dict[str, Any]:
    category = category if category in CATEGORIES else "evidence"
    return {
        "id": slugify(card_id),
        "title": shorten(title, 180),
        "summary": shorten(summary, 320),
        "category": category,
        "category_label": CATEGORIES[category],
        "icon": CATEGORY_ICONS[category],
        "status": status,
        "status_key": slugify(status),
        "source": source,
        "url": source_url(source),
        "github": github_url(source) if not source.startswith("http") else source,
        "metric": metric,
        "bullets": bullets or [],
        "tags": tags or [],
        "date": date,
        "kind": kind or category,
        "evidence": {
            "level": evidence_level,
            "basis": evidence_basis or f"Source file {source} is present in this checkout; no runtime proof asserted.",
        },
    }


# --------------------------------------------------------------------------- #
# live measurements (the "Python magic")
# --------------------------------------------------------------------------- #
def git_tracked_files() -> list[str]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            capture_output=True,
            check=True,
            timeout=30,
        )
        files = [part.decode("utf-8", "replace") for part in result.stdout.split(b"\0") if part]
        if files:
            return files
    except (OSError, subprocess.SubprocessError):
        pass
    skip = {".git", "node_modules", ".venv", "__pycache__", "dist", "build", ".next"}
    return [
        str(path.relative_to(ROOT))
        for path in ROOT.rglob("*")
        if path.is_file() and not (set(path.relative_to(ROOT).parts) & skip)
    ]


def measure(files: list[str]) -> dict[str, Any]:
    python_files = [name for name in files if name.endswith(".py")]
    test_files = [name for name in files if is_test_file(name)]
    total_lines = 0
    for name in python_files:
        try:
            with (ROOT / name).open(encoding="utf-8", errors="ignore") as handle:
                total_lines += sum(1 for _ in handle)
        except OSError:
            continue
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = "unknown"
    try:
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        branch = "unknown"
    return {
        "tracked_files": len(files),
        "python_files": len(python_files),
        "python_lines": total_lines,
        "test_files": len(test_files),
        "docs_files": len([n for n in files if n.startswith("docs/") and n.endswith(".md")]),
        "workflows": len([n for n in files if n.startswith(".github/workflows/") and n.endswith((".yml", ".yaml"))]),
        "scripts": len([n for n in files if n.startswith("scripts/") and n.endswith(".py")]),
        "commit": commit,
        "branch": branch,
    }


# --------------------------------------------------------------------------- #
# card builders
# --------------------------------------------------------------------------- #
def changelog_cards() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    lines = CHANGELOG.read_text(encoding="utf-8").splitlines()
    version = "Unreleased"
    current: dict[str, Any] | None = None
    sections: list[dict[str, Any]] = []

    def close() -> None:
        nonlocal current
        if current:
            sections.append(current)
            current = None

    for number, line in enumerate(lines, 1):
        if line.startswith("## "):
            close()
            version = clean_md(line[3:])
        elif line.startswith("### "):
            close()
            raw = clean_md(line[4:])
            found = re.search(r"\d{4}-\d{2}-\d{2}", raw)
            current = {
                "title": raw,
                "version": version,
                "date": found.group(0) if found else "",
                "line": number,
                "bullets": [],
            }
        elif current is not None and line.startswith("- "):
            current["bullets"].append(line[2:].strip())

    close()
    cards: list[dict[str, Any]] = []
    for index, section in enumerate(sections, 1):
        title = section["title"]
        body = " ".join(section["bullets"][:3])
        lowered = title.lower()
        if "planned" in lowered:
            status, category = "PLANNED", "governance"
        elif "known limit" in lowered:
            status, category = "KNOWN LIMIT", "honesty"
        elif "security" in lowered or "secret" in lowered:
            status, category = "DOCUMENTED", "security"
        elif "documentation" in lowered:
            status, category = "DOCUMENTED", "knowledge"
        else:
            status = "DOCUMENTED"
            category = classify(title + " " + body)
        summary = section["bullets"][0] if section["bullets"] else f"سجل تغيير موثّق في CHANGELOG.md — {section['version']}"
        cards.append(
            make_card(
                f"changelog-{index}-{title}",
                title,
                summary,
                category,
                status,
                "CHANGELOG.md",
                metric=section["version"],
                bullets=[clean_md(b) for b in section["bullets"][:8]],
                tags=["CHANGELOG", section["version"]],
                date=section["date"],
                kind="changelog",
                evidence_level="DOCUMENTED_ONLY",
                evidence_basis=f"CHANGELOG.md line {section['line']} — a human-written record in version {section['version']}; not a runtime proof.",
            )
        )

    milestones: list[dict[str, Any]] = []
    for version_name in dict.fromkeys(s["version"] for s in sections):
        group = [s for s in sections if s["version"] == version_name]
        dates = [s["date"] for s in group if s["date"]]
        milestones.append(
            {
                "version": version_name,
                "date": dates[0] if dates else "",
                "count": len(group),
                "highlights": [s["title"] for s in group[:4]],
            }
        )
    return cards, milestones


def inventory_cards(inv: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    metrics = inv["metrics"]
    cards: list[dict[str, Any]] = []

    for index, row in enumerate(inv.get("test_matrix", []), 1):
        module = row["module"]
        cards.append(
            make_card(
                f"tests-{index}-{module}",
                f"{module} — {row['passed']} اختبار ناجح",
                f"مجموعة اختبارات مُنفَّذة ومسجّلة في جرد 2026-11-22: {row['passed']} نجاح، "
                f"{row['skipped']} تخطٍ، {row['failed']} فشل. الحالة المعلنة: {row['status']}.",
                "tests",
                "MEASURED" if row["status"] == "PROVEN" else "PRESENT",
                "docs/inventory-2027.json",
                metric=f"{row['passed']} PASS · {row['skipped']} SKIP · {row['failed']} FAIL",
                tags=[row["status"], "test matrix"],
                date="2026-11-22",
                kind="test",
                evidence_level="HISTORICAL_INVENTORY",
                evidence_basis="docs/inventory-2027.json test_matrix row. Executed and recorded on 2026-11-22; NOT re-executed by this atlas.",
            )
        )

    for index, engine in enumerate(inv.get("engines", []), 1):
        state = str(engine.get("status", "")).upper()
        label = ENGINE_LABELS.get(engine["id"], engine["id"])
        cards.append(
            make_card(
                f"engine-{index}-{engine['id']}",
                f"{label} · {engine['id']}",
                f"محرك ضمن معمارية 2027، حالته في الجرد: {state}. "
                + ("موجود ومُثبت جزئياً." if state == "PARTIAL" else "حاضر ومسجّل." if state == "PRESENT" else "حالته موثّقة في الجرد."),
                "engines",
                state or "DECLARED",
                "docs/inventory-2027.json",
                metric=f"{state} · 1/13 محرك",
                tags=["engine", engine["id"], state],
                date="2026-11-22",
                kind="engine",
                evidence_level="HISTORICAL_INVENTORY",
                evidence_basis=f"docs/inventory-2027.json engines list; state {state} recorded 2026-11-22, not probed at generation time.",
            )
        )

    correction_cards = []
    for index, item in enumerate(inv.get("corrections", []), 1):
        verdict = clean_md(str(item.get("verdict", "")))
        correction_cards.append(
            make_card(
                f"correction-{index}-{item.get('claim', '')}",
                f"تصحيح: {item.get('claim', 'ادعاء غير محدد')}",
                item.get("fact", ""),
                "honesty",
                "KNOWN LIMIT" if verdict.lower() in {"not found", "incorrect", "false"} else "MEASURED",
                "docs/inventory-2027.json",
                metric=f"الحكم: {verdict}",
                bullets=[f"الادعاء: {item.get('claim', '')}", f"الحكم: {verdict}", f"الحقيقة: {item.get('fact', '')}"],
                tags=["correction", verdict],
                date="2026-11-22",
                kind="correction",
                evidence_level="HISTORICAL_INVENTORY",
                evidence_basis="docs/inventory-2027.json corrections ledger — a recorded self-correction, not a new verification run.",
            )
        )

    for index, item in enumerate(inv.get("high_findings", []), 1):
        correction_cards.append(
            make_card(
                f"finding-{index}-{item.get('project', '')}",
                f"ملاحظة عالية الأولوية: {item.get('project', '')}",
                item.get("issue", ""),
                "honesty",
                "KNOWN LIMIT",
                "docs/inventory-2027.json",
                metric="HIGH FINDING",
                tags=["finding", "audit"],
                date="2026-11-22",
                kind="finding",
                evidence_level="HISTORICAL_INVENTORY",
                evidence_basis="docs/inventory-2027.json high_findings — source inspection from 2026-11-22; re-check with the Security Monitor before acting.",
            )
        )

    legend = inv.get("legend_2027", {})
    correction_cards.append(
        make_card(
            "legend-rule",
            "القاعدة الحاكمة: Zero-Cost First",
            legend.get("rule", ""),
            "governance",
            "DOCUMENTED",
            "docs/inventory-2027.json",
            metric=f"Master prompt v{legend.get('master_prompt_version', '?')}",
            bullets=[clean_md(b) for b in legend.get("rule_bullets", [])],
            tags=["legend", "zero-cost"],
            kind="governance",
            evidence_level="DOCUMENTED_ONLY",
            evidence_basis="Governing rule text in docs/inventory-2027.json; a policy, not an executed capability.",
        )
    )
    for index, item in enumerate(legend.get("foundation_proven", []), 1):
        correction_cards.append(
            make_card(
                f"foundation-{index}-{item.get('module', '')}",
                f"أساس مُثبت: {item.get('module', '')}",
                item.get("evidence", ""),
                "evidence",
                "PROVEN",
                "docs/inventory-2027.json",
                metric=f"التكلفة: {item.get('cost', '?')}",
                tags=["foundation", "zero-cost"],
                kind="governance",
                evidence_level="HISTORICAL_INVENTORY",
                evidence_basis=f"legend_2027.foundation_proven — recorded evidence \"{item.get('evidence', '')}\" on 2026-11-22; not re-executed here.",
            )
        )
    for index, item in enumerate(legend.get("upgrade_path", []), 1):
        correction_cards.append(
            make_card(
                f"upgrade-{index}-{item.get('category', '')}",
                f"مسار الترقية: {item.get('category', '')}",
                item.get("rule", ""),
                "governance",
                "DECLARED",
                "docs/inventory-2027.json",
                metric=f"{item.get('count', '?')} عنصر",
                tags=["promotion path"],
                kind="governance",
                evidence_level="DOCUMENTED_ONLY",
                evidence_basis="legend_2027.upgrade_path — a promotion rule, not evidence that promotion already happened.",
            )
        )
    for index, item in enumerate(legend.get("excluded_until_built", []), 1):
        correction_cards.append(
            make_card(
                f"excluded-{index}-{item.get('item', '')}",
                f"مستبعد حتى يُبنى: {item.get('item', '')}",
                item.get("reason", ""),
                "honesty",
                "BLOCKED",
                "docs/inventory-2027.json",
                tags=["excluded", "honesty"],
                kind="governance",
                evidence_level="DOCUMENTED_ONLY",
                evidence_basis="legend_2027.excluded_until_built — an explicit absence/limit recorded in the inventory.",
            )
        )
    return cards, correction_cards


def catalog_cards(cat: dict[str, Any]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for project in cat.get("projects", []):
        status = str(project.get("operational_status", "DECLARED")).upper()
        cards.append(
            make_card(
                f"project-{project.get('name', 'unknown')}",
                project.get("name", "مشروع غير مسمى"),
                project.get("description_ar") or project.get("description") or "مشروع مؤرشف في الكتالوج.",
                "archive",
                status,
                project.get("path", "catalog.json"),
                metric=f"{project.get('files', '?')} ملف · {project.get('category_ar', project.get('category', ''))}",
                bullets=[
                    f"الحالة التشغيلية: {status}",
                    f"الدليل: {project.get('operational_evidence', 'غير محدد')}",
                    f"تاريخ التحقق: {project.get('verified_on', 'غير محدد')}",
                    f"المصدر الأصلي: {project.get('source', 'غير محدد')}",
                ],
                tags=[project.get("category", ""), status, "archive"],
                date=str(project.get("verified_on", "")),
                kind="project",
                evidence_level="HISTORICAL_CATALOG",
                evidence_basis=f"catalog.json operational_status {status}, verified {project.get('verified_on', 'unknown')}. The folder exists in this checkout; running capability was NOT re-tested.",
            )
        )

    surfaces = cat.get("live_surfaces_verified_2026_11_17", {})
    for index, item in enumerate(surfaces.get("live", []), 1):
        cards.append(
            make_card(
                f"surface-live-{index}",
                f"واجهة حية: {item.get('what', item.get('url', ''))}",
                f"فُتحت فعلياً في 2026-11-17: {item.get('url', '')}. الصفحة تُخدَم؛ لا يعني ذلك أن كل وظائفها الداخلية مُختبرة.",
                "product",
                "LIVE",
                "catalog.json",
                metric=item.get("url", ""),
                tags=["live surface", "2026-11-17"],
                date="2026-11-17",
                kind="surface",
                evidence_level="HISTORICAL_SURFACE",
                evidence_basis=f"catalog.json records this URL as served on 2026-11-17: {item.get('url', '')}. NOT re-fetched when this atlas was generated — use a live check before relying on it.",
            )
        )
    for index, item in enumerate(surfaces.get("dead", []), 1):
        cards.append(
            make_card(
                f"surface-dead-{index}",
                f"واجهة متوقفة: {item.get('url', '')}",
                f"حالة مُعلنة بصدق في الكتالوج: {item.get('status', '')}. لا تُعدّ إنجازاً تشغيلياً.",
                "honesty",
                "DEAD",
                "catalog.json",
                metric=item.get("status", ""),
                tags=["dead surface", "honesty"],
                date="2026-11-17",
                kind="surface",
                evidence_level="HISTORICAL_SURFACE",
                evidence_basis=f"catalog.json recorded {item.get('status', '')} on 2026-11-17; not re-fetched here. Treated as a known limit, not an achievement.",
            )
        )
    return cards


def filesystem_cards(files: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    doc_cards: list[dict[str, Any]] = []
    docs = [name for name in files if name.startswith("docs/") and name.endswith(".md")]
    for index, path in enumerate(sorted(docs), 1):
        try:
            text = (ROOT / path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        lines = text.splitlines()
        title = next((clean_md(line[2:]) for line in lines[:25] if line.startswith("# ")), Path(path).stem)
        body = ""
        for line in lines[:40]:
            stripped = clean_md(line)
            if stripped and not stripped.startswith("#") and len(stripped) > 35:
                body = stripped
                break
        category = "governance" if "/adr/" in path else "knowledge"
        doc_cards.append(
            make_card(
                f"doc-{index}-{path}",
                title,
                body or f"وثيقة محفوظة في المستودع: {path}",
                category,
                "DOCUMENTED",
                path,
                metric=f"{len(lines)} سطر",
                tags=["doc", Path(path).stem],
                kind="doc",
                evidence_level="FILE_EXISTS",
                evidence_basis=f"git ls-files lists {path} and the file was read at generation time; content is documentation, not runtime proof.",
            )
        )

    workflow_cards: list[dict[str, Any]] = []
    workflows = [n for n in files if n.startswith(".github/workflows/") and n.endswith((".yml", ".yaml"))]
    for index, path in enumerate(sorted(workflows), 1):
        try:
            text = (ROOT / path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        name_match = re.search(r"(?m)^name:\s*(.+)$", text)
        name = clean_md(name_match.group(1)) if name_match else Path(path).stem
        jobs = len(re.findall(r"(?m)^  [A-Za-z0-9_-]+:\s*$", text.split("jobs:", 1)[1])) if "jobs:" in text else 0
        triggers = [t for t in ("push", "pull_request", "schedule", "workflow_dispatch") if t in text]
        workflow_cards.append(
            make_card(
                f"workflow-{index}-{path}",
                f"سير عمل: {name}",
                f"أتمتة مُعرَّفة في {path} بعدد {jobs} مهمة تقريباً، ومشغّلات: {', '.join(triggers) or 'غير محددة'}.",
                "automation",
                "PRESENT",
                path,
                metric=f"~{jobs} job · {len(triggers)} trigger",
                tags=["workflow", "CI", *triggers],
                kind="workflow",
                evidence_level="FILE_EXISTS",
                evidence_basis=f"git ls-files lists {path}; workflow YAML was parsed locally. A defined workflow is not proof that its last run passed.",
            )
        )

    script_cards: list[dict[str, Any]] = []
    scripts = [n for n in files if n.startswith("scripts/") and n.endswith(".py")]
    for index, path in enumerate(sorted(scripts), 1):
        try:
            tree = ast.parse((ROOT / path).read_text(encoding="utf-8", errors="ignore"))
            doc = (ast.get_docstring(tree) or "").strip().splitlines()
            summary = doc[0] if doc else f"أداة Python قابلة للتشغيل: python3 {path}"
        except (OSError, SyntaxError, ValueError):
            summary = f"أداة Python قابلة للتشغيل: python3 {path}"
        lowered = path.lower()
        if any(word in lowered for word in ("secret", "egress", "preflight", "workflow_secret", "policy_hardening")):
            category = "security"
        elif any(word in lowered for word in ("verify", "audit", "sbom", "published", "identity")):
            category = "evidence"
        elif "test_" in lowered:
            category = "tests"
        else:
            category = "automation"
        script_cards.append(
            make_card(
                f"script-{index}-{path}",
                Path(path).stem.replace("_", " "),
                summary,
                category,
                "PRESENT",
                path,
                metric=f"python3 {path}",
                tags=["script", "Python"],
                kind="script",
                evidence_level="FILE_EXISTS",
                evidence_basis=f"git ls-files lists {path} and ast.parse succeeded at generation time; presence is not a passing test result.",
            )
        )
    return doc_cards, workflow_cards, script_cards


def build_data() -> dict[str, Any]:
    files = git_tracked_files()
    measured = measure(files)
    inv = read_json(INVENTORY)
    cat = read_json(CATALOG)

    changes, milestones = changelog_cards()
    inv_tests, inv_honesty = inventory_cards(inv)
    projects = catalog_cards(cat)
    docs, workflows, scripts = filesystem_cards(files)
    metrics = inv["metrics"]

    cards = changes + inv_tests + inv_honesty + projects + docs + workflows + scripts
    # Stable, deterministic ordering: source-driven groups first, then alphabetical.
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for card in cards:
        if card["id"] not in seen:
            seen.add(card["id"])
            unique.append(card)

    status_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    evidence_counts: dict[str, int] = {}
    for card in unique:
        status_counts[card["status"]] = status_counts.get(card["status"], 0) + 1
        category_counts[card["category"]] = category_counts.get(card["category"], 0) + 1
        level = card["evidence"]["level"]
        evidence_counts[level] = evidence_counts.get(level, 0) + 1

    stats = [
        {"label": "إنجاز موثّق", "value": len(unique), "note": "بطاقة مستخرَجة من المستودع", "icon": "✦"},
        {"label": "ملفات Python", "value": measured["python_files"], "note": "مقاس الآن · git ls-files", "icon": "🐍"},
        {"label": "ملفات اختبار", "value": measured["test_files"], "note": "مقاس الآن · test_*/ *_test.* بامتدادات معروفة", "icon": "🧪"},
        {"label": "اختبارات ناجحة", "value": metrics["tests_passed"], "note": "جرد 2026-11-22 · 0 فشل", "icon": "✅"},
        {"label": "هجوم / اختراق", "value": f"{metrics['redteam_attacks']} / {metrics['redteam_breaches']}", "note": "red-team · جرد 2026-11-22", "icon": "🛡️"},
        {"label": "سير عمل CI", "value": measured["workflows"], "note": "مقاس الآن · .github/workflows", "icon": "🤖"},
    ]

    sources = [
        {"path": "catalog.json", "what": "48 مشروعاً + واجهات حية/متوقفة"},
        {"path": "docs/inventory-2027.json", "what": "اختبارات · محركات · تصحيحات · قاعدة Zero-Cost"},
        {"path": "CHANGELOG.md", "what": "سجل الإنجازات والتغييرات"},
        {"path": "README.md", "what": "الواجهة العامة والبداية السريعة"},
        {"path": "docs/", "what": f"{measured['docs_files']} وثيقة Markdown"},
        {"path": "scripts/", "what": f"{measured['scripts']} أداة Python"},
        {"path": ".github/workflows/", "what": f"{measured['workflows']} سير عمل آلي"},
    ]

    return {
        "meta": {
            "title": "أطلس إنجازات AI-2027",
            "subtitle": "كل إنجاز داخل المستودع، مُستخرَج بسحر Python من الملفات نفسها — لا من الذاكرة.",
            "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "commit": measured["commit"],
            "branch": measured["branch"],
            "method": "read-only Python stdlib extraction + git ls-files measurement",
            "inventory_date": inv.get("generated_at", ""),
            "inventory_version": inv.get("version", ""),
            "repo_url": REPO_URL,
        },
        "measured": measured,
        "stats": stats,
        "cards": unique,
        "milestones": milestones,
        "statusCounts": status_counts,
        "categoryCounts": category_counts,
        "evidenceCounts": evidence_counts,
        "evidenceLabels": EVIDENCE_LABELS,
        "categories": CATEGORIES,
        "categoryIcons": CATEGORY_ICONS,
        "sources": sources,
        "honesty": {
            "archive_proven": metrics["archive_proven"],
            "archive_declared": metrics["archive_declared"],
            "archive_simulated": metrics["archive_simulated"],
            "archive_failed": metrics["archive_failed"],
            "corrections": len(inv.get("corrections", [])),
            "findings": len(inv.get("high_findings", [])),
        },
    }


# --------------------------------------------------------------------------- #
# HTML rendering — a single self-contained, offline-capable page
# --------------------------------------------------------------------------- #
HTML_TEMPLATE = r"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>__TITLE__ — AI 2027</title>
<style>
@font-face{font-family:Readex;src:url(data:font/woff2;base64,__AR_FONT__) format('woff2');font-display:swap}
@font-face{font-family:ReadexLatin;src:url(data:font/woff2;base64,__LAT_FONT__) format('woff2');font-display:swap}
:root{--bg:#070a16;--panel:rgba(18,24,48,.72);--line:rgba(148,163,184,.18);--ink:#eef2ff;--muted:#a5b0cd;--gold:#f6cb75;--mint:#54e6c1;--violet:#a78bfa;--rose:#fb7185}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font-family:Readex,"Segoe UI",Tahoma,sans-serif;line-height:1.75;min-height:100vh;overflow-x:hidden}
body::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;background:radial-gradient(circle at 12% 8%,rgba(167,139,250,.15),transparent 32%),radial-gradient(circle at 88% 14%,rgba(84,230,193,.12),transparent 30%),radial-gradient(circle at 50% 90%,rgba(246,203,117,.08),transparent 40%),linear-gradient(180deg,#080b18,#050712)}
.orb{position:fixed;border-radius:50%;filter:blur(80px);opacity:.28;pointer-events:none;z-index:0;animation:float 16s ease-in-out infinite}
.orb.a{width:380px;height:380px;background:#7c3aed;top:-120px;right:-80px}
.orb.b{width:320px;height:320px;background:#06b6d4;bottom:8%;left:-90px;animation-delay:-6s}
.orb.c{width:260px;height:260px;background:#f59e0b;top:42%;right:8%;opacity:.13;animation-delay:-11s}
@keyframes float{0%,100%{transform:translate(0,0)}50%{transform:translate(30px,-40px)}}
.stars{position:fixed;inset:0;z-index:0;pointer-events:none;opacity:.35;background-image:radial-gradient(1px 1px at 20% 30%,#fff,transparent),radial-gradient(1px 1px at 70% 60%,#fff,transparent),radial-gradient(1.5px 1.5px at 40% 80%,#f6cb75,transparent),radial-gradient(1px 1px at 85% 25%,#54e6c1,transparent);background-size:280px 240px,340px 300px,420px 360px,260px 320px;animation:twinkle 7s ease-in-out infinite}
@keyframes twinkle{0%,100%{opacity:.2}50%{opacity:.5}}
.wrap{position:relative;z-index:1;max-width:1280px;margin:0 auto;padding:0 22px 90px}
header{padding:64px 0 30px;text-align:center}
.eyebrow{display:inline-flex;gap:9px;align-items:center;border:1px solid var(--line);background:rgba(255,255,255,.04);padding:7px 16px;border-radius:999px;color:var(--gold);font-size:13px;letter-spacing:.3px}
h1{margin:20px 0 8px;font-size:clamp(34px,6vw,64px);line-height:1.18;letter-spacing:-1px;background:linear-gradient(100deg,#fff 8%,var(--gold) 46%,var(--mint) 88%);-webkit-background-clip:text;background-clip:text;color:transparent}
.subtitle{max-width:800px;margin:0 auto;color:var(--muted);font-size:clamp(14px,2vw,18px)}
.hero-actions{display:flex;gap:12px;justify-content:center;flex-wrap:wrap;margin-top:26px}
button,.btn,input,select{font:inherit}
.btn{cursor:pointer;border:1px solid var(--line);background:rgba(255,255,255,.05);color:var(--ink);padding:10px 18px;border-radius:14px;transition:.2s ease;text-decoration:none;display:inline-flex;align-items:center;gap:8px}
.btn:hover{transform:translateY(-2px);border-color:var(--gold);box-shadow:0 10px 28px rgba(246,203,117,.12)}
.btn.primary{background:linear-gradient(120deg,rgba(246,203,117,.22),rgba(167,139,250,.22));border-color:rgba(246,203,117,.5)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:12px;margin:26px 0 34px}
.stat{border:1px solid var(--line);background:var(--panel);backdrop-filter:blur(14px);border-radius:18px;padding:18px;text-align:center}
.stat .icon{font-size:24px}
.stat .value{display:block;font-family:ReadexLatin,sans-serif;font-size:30px;font-weight:800;color:var(--gold);line-height:1.3}
.stat .label{display:block;font-size:14px;font-weight:700}
.stat .note{display:block;font-size:11px;color:var(--muted);line-height:1.5}
.banner{border:1px solid rgba(246,203,117,.28);background:linear-gradient(100deg,rgba(246,203,117,.09),rgba(84,230,193,.05));border-radius:18px;padding:16px 20px;margin-bottom:26px;display:flex;gap:14px;align-items:flex-start}
.banner b{color:var(--gold)}
.banner .legend{color:var(--muted);font-size:13px;margin-top:5px}
.controls{position:sticky;top:0;z-index:20;background:rgba(7,10,22,.86);backdrop-filter:blur(16px);border:1px solid var(--line);border-radius:20px;padding:14px;margin-bottom:24px}
.search-row{display:flex;gap:10px;flex-wrap:wrap}
.search{flex:1 1 260px;position:relative}
.search input{width:100%;background:rgba(255,255,255,.05);border:1px solid var(--line);color:var(--ink);border-radius:13px;padding:12px 42px 12px 16px;outline:none}
.search input:focus{border-color:var(--mint);box-shadow:0 0 0 3px rgba(84,230,193,.1)}
.search span{position:absolute;right:15px;top:11px;color:var(--muted)}
select{background:#141a34;color:var(--ink);border:1px solid var(--line);border-radius:13px;padding:12px;outline:none}
.chips{display:flex;gap:8px;overflow-x:auto;padding:14px 2px 2px;scrollbar-width:thin}
.chip{white-space:nowrap;cursor:pointer;border:1px solid var(--line);background:rgba(255,255,255,.035);color:var(--muted);border-radius:999px;padding:7px 13px;font-size:13px;transition:.18s}
.chip:hover,.chip.active{background:linear-gradient(120deg,rgba(246,203,117,.2),rgba(167,139,250,.2));border-color:rgba(246,203,117,.55);color:#fff}
.result-line{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:0 4px 14px;color:var(--muted);font-size:13px}
.result-line strong{color:var(--mint);font-family:ReadexLatin,sans-serif}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(295px,1fr));gap:14px}
.card{position:relative;border:1px solid var(--line);background:var(--panel);backdrop-filter:blur(12px);border-radius:20px;padding:18px;transition:.22s ease;overflow:hidden}
.card::after{content:"";position:absolute;inset:0;border-radius:20px;pointer-events:none;background:linear-gradient(135deg,rgba(255,255,255,.05),transparent 40%);opacity:0;transition:.22s}
.card:hover{transform:translateY(-4px);border-color:rgba(246,203,117,.42);box-shadow:0 18px 40px rgba(0,0,0,.28)}
.card:hover::after{opacity:1}
.card-top{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:10px}
.card-icon{font-size:22px}
.badge{font-size:11px;padding:4px 9px;border-radius:999px;border:1px solid var(--line);font-family:ReadexLatin,sans-serif;font-weight:700;letter-spacing:.3px;white-space:nowrap}
.badge.proven,.badge.measured,.badge.live,.badge.present{color:#6ef0c8;border-color:rgba(84,230,193,.4);background:rgba(84,230,193,.1)}
.badge.partial,.badge.declared{color:#ffd48a;border-color:rgba(246,203,117,.4);background:rgba(246,203,117,.1)}
.badge.simulated,.badge.documented{color:#c4b5fd;border-color:rgba(167,139,250,.4);background:rgba(167,139,250,.1)}
.badge.failed,.badge.dead,.badge.known-limit,.badge.blocked{color:#fda4af;border-color:rgba(251,113,133,.4);background:rgba(251,113,133,.1)}
.badge.planned{color:#a5b0cd}
.badge.evidence{font-family:Readex,sans-serif;font-weight:600;letter-spacing:0}
.badge.evidence-historical-inventory,.badge.evidence-historical-catalog,.badge.evidence-historical-surface{color:#ffd48a;border-color:rgba(246,203,117,.32);background:rgba(246,203,117,.07)}
.badge.evidence-file-exists,.badge.evidence-recomputed-now,.badge.evidence-live-checked{color:#6ef0c8;border-color:rgba(84,230,193,.32);background:rgba(84,230,193,.07)}
.badge.evidence-documented-only{color:#c4b5fd;border-color:rgba(167,139,250,.32);background:rgba(167,139,250,.07)}
.card h3{margin:0 0 8px;font-size:17px;line-height:1.5}
.card p{margin:0;color:var(--muted);font-size:13px;line-height:1.8;display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}
.card.open p{display:block}
.meta{display:flex;gap:8px;flex-wrap:wrap;margin-top:13px}
.pill{font-size:11px;padding:3px 9px;border-radius:8px;background:rgba(255,255,255,.05);color:#cad3ea;border:1px solid var(--line)}
.pill.metric{color:var(--gold);font-family:ReadexLatin,sans-serif}
.card-foot{display:flex;justify-content:space-between;align-items:center;gap:8px;border-top:1px solid var(--line);margin-top:14px;padding-top:11px}
.card-foot a{color:#8fd9ff;font-size:11px;text-decoration:none;max-width:65%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;direction:ltr}
.card-foot a:hover{text-decoration:underline}
.expand{cursor:pointer;background:none;border:0;color:var(--gold);font-size:12px;padding:4px;white-space:nowrap}
.details{display:none;margin-top:12px;border-top:1px dashed var(--line);padding-top:10px}
.card.open .details{display:block}
.details ul{margin:0;padding-right:18px;color:#c8d1e8;font-size:12px;line-height:1.9}
.details li{margin-bottom:6px}
.details a{color:var(--mint)}
.load-more{display:block;margin:28px auto 0;padding:12px 26px;border-radius:14px;border:1px solid rgba(84,230,193,.35);background:rgba(84,230,193,.08);color:var(--mint);cursor:pointer}
.load-more:hover{background:rgba(84,230,193,.16)}
section.block{margin-top:52px}
section.block h2{font-size:25px;margin:0 0 6px;color:#fff}
section.block h2 span{color:var(--gold)}
.section-note{color:var(--muted);font-size:13px;margin:0 0 18px}
.timeline{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px}
.milestone{border:1px solid var(--line);background:var(--panel);border-radius:18px;padding:18px;position:relative}
.milestone::before{content:"✦";position:absolute;top:-13px;right:18px;color:var(--gold);background:#0b1022;padding:0 6px}
.milestone .version{font-family:ReadexLatin,sans-serif;color:var(--mint);font-weight:800;font-size:18px}
.milestone .date{color:var(--muted);font-size:12px}
.milestone ul{margin:10px 0 0;padding-right:18px;color:#c8d1e8;font-size:12px;line-height:1.8}
.honesty-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.honesty-cell{border:1px solid var(--line);border-radius:16px;padding:16px;background:rgba(255,255,255,.03);text-align:center}
.honesty-cell strong{display:block;font-family:ReadexLatin,sans-serif;font-size:26px;color:var(--gold)}
.honesty-cell span{font-size:12px;color:var(--muted)}
.source-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px}
.source-item{border:1px solid var(--line);border-radius:14px;padding:12px 15px;background:rgba(255,255,255,.03)}
.source-item code{direction:ltr;display:block;color:var(--mint);font-size:12px}
.source-item span{color:var(--muted);font-size:12px}
footer{margin-top:56px;border-top:1px solid var(--line);padding-top:22px;color:var(--muted);font-size:12px;display:flex;flex-wrap:wrap;gap:10px;justify-content:space-between}
footer code{color:var(--gold);direction:ltr}
.sparkle{position:fixed;z-index:9999;pointer-events:none;color:var(--gold);font-size:18px;animation:spark 700ms ease-out forwards}
@keyframes spark{0%{opacity:1;transform:translate(0,0) scale(.6) rotate(0)}100%{opacity:0;transform:translate(var(--dx),var(--dy)) scale(1.4) rotate(180deg)}}
.empty{text-align:center;padding:60px 20px;color:var(--muted)}
@media (max-width:640px){header{padding-top:40px}.stats{grid-template-columns:repeat(2,1fr)}.wrap{padding-inline:14px}}
@media print{.orb,.stars,.controls,.hero-actions{display:none}.card{break-inside:avoid;background:#fff;color:#000}.card p,.card-foot a{color:#333}}
</style>
</head>
<body>
<div class="orb a"></div><div class="orb b"></div><div class="orb c"></div><div class="stars"></div>
<main class="wrap">
<header>
  <div class="eyebrow">✦ Python stdlib · read-only extraction · no claims without files</div>
  <h1>__TITLE__</h1>
  <p class="subtitle">__SUBTITLE__</p>
  <div class="hero-actions">
    <button class="btn primary" id="spell">🪄 انسخ تعويذة التوليد</button>
    <a class="btn" href="#atlas">ابدأ الجولة ↓</a>
    <a class="btn" href="atlas-control-center.html">🛡️ مركز الرقابة</a>
    <a class="btn" href="ATLAS-AUDIT.md">📋 التدقيق المستقل</a>
    <a class="btn" href="__GITHUB__" target="_blank" rel="noopener">المستودع على GitHub ↗</a>
  </div>
</header>

<div class="stats" id="stats"></div>

<div class="banner">
  <div>
    <b>🧭 قاعدة الصدق:</b> الحالة (PROVEN / DECLARED / SIMULATED / FAILED) هي ما سجّله المصدر، أما <b>مستوى الدليل</b> فيقول إن كان مُعاد فحصه الآن أم مأخوذاً من جرد تاريخي. لا يُعدّ أي سجل تاريخي إثباتاً لتشغيل حالي.
    <div class="legend">بعد التدقيق المستقل: كل بطاقة تحمل مستوى دليل وسنداً قابلاً للتحقق. الأرقام «مقاس الآن» من git ls-files عند التوليد؛ و«جرد 2026-11-22» تاريخية.</div>
  </div>
</div>

<div class="controls" id="atlas">
  <div class="search-row">
    <label class="search"><span>⌕</span><input id="q" type="search" placeholder="ابحث في كل الإنجازات: نواة، أمان، اختبار، مشروع…" autocomplete="off" aria-label="البحث"></label>
    <select id="status" aria-label="تصفية الحالة"><option value="all">كل الحالات</option></select>
    <select id="evidence" aria-label="تصفية مستوى الدليل"><option value="all">كل مستويات الدليل</option></select>
    <select id="sort" aria-label="الترتيب"><option value="default">الترتيب الافتراضي</option><option value="title">حسب العنوان</option><option value="status">حسب الحالة</option><option value="category">حسب التصنيف</option></select>
  </div>
  <div class="chips" id="chips"></div>
</div>

<div class="result-line"><span>عرض <strong id="shown">0</strong> من <strong id="total">0</strong> إنجاز</span><span>اضغط <code>/</code> للبحث · انقر البطاقة لتفاصيلها</span></div>
<div class="grid" id="grid"></div>
<button class="load-more" id="more" hidden>✦ اعرض المزيد</button>

<section class="block">
  <h2><span>🕰️</span> محطات الإصدار</h2>
  <p class="section-note">محطات مُقتطعة من CHANGELOG.md — كل محطة تجمع الإنجازات الموثّقة في نسخة واحدة.</p>
  <div class="timeline" id="timeline"></div>
</section>

<section class="block">
  <h2><span>🧭</span> ميزان الصدق</h2>
  <p class="section-note">الإنجاز هنا يشمل أيضاً إعلان ما لم يكتمل؛ الأرقام من catalog.json و inventory-2027.json.</p>
  <div class="honesty-grid" id="honesty"></div>
</section>

<section class="block">
  <h2><span>🗂️</span> مصادر السحر</h2>
  <p class="section-note">كل بطاقة في هذا الأطلس تشير إلى ملف حقيقي داخل المستودع. هذه أهم المصادر:</p>
  <div class="source-list" id="sources"></div>
</section>

<footer>
  <span>وُلد هذا الأطلس في __GENERATED__ من الفرع <code>__BRANCH__</code> عند <code>__COMMIT__</code>.</span>
  <span>أعد التوليد بـ <code>python3 scripts/achievements_atlas.py</code></span>
</footer>
</main>

<script>
const ATLAS = __ATLAS_DATA__;
const byId = (id) => document.getElementById(id);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const norm = (value) => String(value ?? '').normalize('NFKD').replace(/[\u064B-\u065F]/g, '').toLowerCase();
const nf = new Intl.NumberFormat('ar-EG');
const state = {q: '', cat: 'all', status: 'all', evidence: 'all', sort: 'default', shown: 60};

function buildControls() {
  const chips = [{key:'all', label:'كل الإنجازات'}, ...Object.entries(ATLAS.categories).map(([key,label]) => ({key,label}))];
  byId('chips').innerHTML = chips.map((c) =>
    `<button class="chip${c.key === 'all' ? ' active' : ''}" data-cat="${esc(c.key)}">${esc(ATLAS.categoryIcons[c.key] || '✦')} ${esc(c.label)} <small>(${nf.format(ATLAS.categoryCounts[c.key] || 0)})</small></button>`
  ).join('');
  const statuses = [...new Set(ATLAS.cards.map((c) => c.status))].sort();
  byId('status').innerHTML += statuses.map((s) => `<option value="${esc(s)}">${esc(s)} (${nf.format(ATLAS.statusCounts[s] || 0)})</option>`).join('');
  byId('evidence').innerHTML += Object.entries(ATLAS.evidenceCounts)
    .sort((a,b) => b[1] - a[1])
    .map(([level,count]) => `<option value="${esc(level)}">${esc(ATLAS.evidenceLabels[level] || level)} (${nf.format(count)})</option>`).join('');
}

function buildStatic() {
  byId('stats').innerHTML = ATLAS.stats.map((s) =>
    `<div class="stat"><span class="icon">${esc(s.icon)}</span><strong class="value">${esc(typeof s.value === 'number' ? nf.format(s.value) : s.value)}</strong><span class="label">${esc(s.label)}</span><span class="note">${esc(s.note)}</span></div>`
  ).join('');
  byId('timeline').innerHTML = ATLAS.milestones.map((m) =>
    `<article class="milestone"><div class="version">${esc(m.version)}</div><div class="date">${esc(m.date || 'بدون تاريخ')} · ${nf.format(m.count)} إنجاز</div><ul>${m.highlights.map((h) => `<li>${esc(h)}</li>`).join('')}</ul></article>`
  ).join('');
  const h = ATLAS.honesty;
  const cells = [
    [h.archive_proven, 'PROVEN — نُفِّذ هنا'], [h.archive_declared, 'DECLARED — موثّق'],
    [h.archive_simulated, 'SIMULATED — محاكاة'], [h.archive_failed, 'FAILED — فشل معلن'],
    [h.corrections, 'تصحيح ادعاءات'], [h.findings, 'ملاحظات عالية الأولوية'],
    [ATLAS.evidenceCounts['FILE_EXISTS'] || 0, 'دليل: الملف موجود'],
    [ATLAS.evidenceCounts['HISTORICAL_INVENTORY'] || 0, 'دليل: جرد تاريخي']
  ];
  byId('honesty').innerHTML = cells.map(([v,l]) => `<div class="honesty-cell"><strong>${nf.format(v)}</strong><span>${esc(l)}</span></div>`).join('');
  byId('sources').innerHTML = ATLAS.sources.map((s) =>
    `<div class="source-item"><code>${esc(s.path)}</code><span>${esc(s.what)}</span></div>`
  ).join('');
}

function filtered() {
  const q = norm(state.q.trim());
  let list = ATLAS.cards.filter((c) => {
    if (state.cat !== 'all' && c.category !== state.cat) return false;
    if (state.status !== 'all' && c.status !== state.status) return false;
    if (state.evidence !== 'all' && c.evidence.level !== state.evidence) return false;
    if (!q) return true;
    return norm([c.title, c.summary, c.source, c.metric, c.category_label, c.status, c.evidence.level, c.evidence.basis, c.tags.join(' '), c.bullets.join(' ')].join(' ')).includes(q);
  });
  if (state.sort === 'title') list = list.sort((a,b) => a.title.localeCompare(b.title, 'ar'));
  if (state.sort === 'status') list = list.sort((a,b) => a.status.localeCompare(b.status) || a.title.localeCompare(b.title,'ar'));
  if (state.sort === 'category') list = list.sort((a,b) => a.category.localeCompare(b.category) || a.title.localeCompare(b.title,'ar'));
  return list;
}

function cardHtml(c) {
  const bullets = `<div class="details"><ul>${c.bullets.map((b) => `<li>${esc(b)}</li>`).join('')}<li><b>الدليل:</b> ${esc(c.evidence.basis)}</li></ul><a href="${esc(c.github)}" target="_blank" rel="noopener">افتح المصدر على GitHub ↗</a></div>`;
  const evidenceLabel = ATLAS.evidenceLabels[c.evidence.level] || c.evidence.level;
  return `<article class="card" data-id="${esc(c.id)}">
    <div class="card-top"><span class="card-icon">${esc(c.icon)}</span><span><span class="badge ${esc(c.status_key)}">${esc(c.status)}</span> <span class="badge evidence evidence-${esc(c.evidence.level.toLowerCase().replace(/_/g,'-'))}" title="${esc(c.evidence.basis)}">${esc(evidenceLabel)}</span></span></div>
    <h3>${esc(c.title)}</h3><p>${esc(c.summary)}</p>
    <div class="meta"><span class="pill">${esc(c.category_label)}</span>${c.metric ? `<span class="pill metric">${esc(c.metric)}</span>` : ''}${c.date ? `<span class="pill">${esc(c.date)}</span>` : ''}</div>
    <div class="card-foot"><a href="${esc(c.url)}" target="_blank" rel="noopener" title="${esc(c.source)}">${esc(c.source)}</a><button class="expand" aria-expanded="false">✦ تفاصيل</button></div>
    ${bullets}
  </article>`;
}

function render() {
  const list = filtered();
  byId('total').textContent = nf.format(list.length);
  byId('shown').textContent = nf.format(Math.min(state.shown, list.length));
  byId('grid').innerHTML = list.length
    ? list.slice(0, state.shown).map(cardHtml).join('')
    : '<div class="empty">✧ لا توجد بطاقات مطابقة — جرّب كلمة أخرى أو أزل التصفية.</div>';
  byId('more').hidden = list.length <= state.shown;
}

byId('q').addEventListener('input', (e) => { state.q = e.target.value; state.shown = 60; render(); });
byId('status').addEventListener('change', (e) => { state.status = e.target.value; state.shown = 60; render(); });
byId('evidence').addEventListener('change', (e) => { state.evidence = e.target.value; state.shown = 60; render(); });
byId('sort').addEventListener('change', (e) => { state.sort = e.target.value; render(); });
byId('chips').addEventListener('click', (e) => {
  const chip = e.target.closest('[data-cat]'); if (!chip) return;
  state.cat = chip.dataset.cat; state.shown = 60;
  document.querySelectorAll('.chip').forEach((el) => el.classList.toggle('active', el === chip));
  render();
});
byId('more').addEventListener('click', () => { state.shown += 60; render(); });
byId('grid').addEventListener('click', (e) => {
  const button = e.target.closest('.expand'); if (!button) return;
  const card = button.closest('.card'); card.classList.toggle('open');
  button.setAttribute('aria-expanded', card.classList.contains('open') ? 'true' : 'false');
  button.textContent = card.classList.contains('open') ? '✧ إخفاء' : '✦ تفاصيل';
});
byId('spell').addEventListener('click', async (e) => {
  const command = 'python3 scripts/achievements_atlas.py';
  try { await navigator.clipboard.writeText(command); e.currentTarget.textContent = '✅ نُسخت التعويذة'; }
  catch (_) { e.currentTarget.textContent = command; }
  setTimeout(() => { e.currentTarget.textContent = '🪄 انسخ تعويذة التوليد'; }, 2200);
});
document.addEventListener('keydown', (e) => {
  if (e.key === '/' && document.activeElement.tagName !== 'INPUT') { e.preventDefault(); byId('q').focus(); }
  if (e.key === 'Escape' && document.activeElement === byId('q')) { byId('q').value = ''; state.q = ''; render(); byId('q').blur(); }
});
document.addEventListener('pointerdown', (e) => {
  if (e.target.closest('button, a, input, select')) return;
  for (let i = 0; i < 3; i++) {
    const s = document.createElement('span'); s.className = 'sparkle'; s.textContent = '✦';
    s.style.left = (e.clientX + (Math.random() - .5) * 24) + 'px';
    s.style.top = (e.clientY + (Math.random() - .5) * 24) + 'px';
    s.style.setProperty('--dx', ((Math.random() - .5) * 70) + 'px');
    s.style.setProperty('--dy', (-30 - Math.random() * 50) + 'px');
    document.body.appendChild(s); setTimeout(() => s.remove(), 750);
  }
});

buildControls(); buildStatic(); render();
</script>
</body>
</html>
"""


def render_html(data: dict[str, Any]) -> str:
    import base64

    ar = base64.b64encode(AR_FONT.read_bytes()).decode("ascii") if AR_FONT.exists() else ""
    lat = base64.b64encode(LAT_FONT.read_bytes()).decode("ascii") if LAT_FONT.exists() else ""
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    replacements = {
        "__TITLE__": data["meta"]["title"],
        "__SUBTITLE__": data["meta"]["subtitle"],
        "__ATLAS_DATA__": payload,
        "__AR_FONT__": ar,
        "__LAT_FONT__": lat,
        "__GENERATED__": data["meta"]["generated_at"],
        "__BRANCH__": data["meta"]["branch"],
        "__COMMIT__": data["meta"]["commit"],
        "__GITHUB__": data["meta"]["repo_url"],
    }
    # Replace the long payload last so none of its contents can be substituted.
    ordered = sorted(replacements.items(), key=lambda kv: -len(kv[1]))
    html = HTML_TEMPLATE
    for token, value in ordered:
        if token == "__ATLAS_DATA__":
            continue
        html = html.replace(token, value)
    return html.replace("__ATLAS_DATA__", payload)


def print_summary(data: dict[str, Any]) -> None:
    print(f"✨ {data['meta']['title']}")
    print(f"   {len(data['cards'])} إنجازاً · {data['meta']['generated_at']} · {data['meta']['branch']}@{data['meta']['commit']}")
    print("   التصنيفات:")
    for key, count in sorted(data["categoryCounts"].items(), key=lambda kv: -kv[1]):
        print(f"     {data['categories'][key]:<24} {count:>4}")
    print("   الحالات:")
    for key, count in sorted(data["statusCounts"].items(), key=lambda kv: -kv[1]):
        print(f"     {key:<20} {count:>4}")
    print(f"   الصفحة: {OUT.relative_to(ROOT)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print the extracted data model as JSON")
    parser.add_argument("--print", dest="do_print", action="store_true", help="print a terminal summary")
    parser.add_argument("--check", action="store_true", help="verify the generated page is present and non-empty")
    parser.add_argument("--output", type=Path, default=OUT, help="custom output path")
    args = parser.parse_args(argv)

    if args.check:
        if not args.output.exists() or args.output.stat().st_size < 5000:
            print(f"FAIL: {args.output} missing or too small", file=sys.stderr)
            return 1
        print(f"OK: {args.output} ({args.output.stat().st_size:,} bytes)")
        return 0

    data = build_data()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    page = render_html(data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(page, encoding="utf-8")
    if args.do_print:
        print_summary(data)
    print(f"✨ wrote {args.output.relative_to(ROOT)} ({len(page):,} chars, {len(data['cards'])} achievements)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
