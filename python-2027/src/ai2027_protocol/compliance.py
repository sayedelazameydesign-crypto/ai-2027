"""Three-tier compliance registry across the 48 catalog projects and core subsystems."""

from __future__ import annotations

import ast
import asyncio
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field

from .auditor import _evaluate_project_sync
from .auditor_ast import iter_python_files
from .contracts import ProjectAuditSummary, RuleStatus, StrictModel, TelemetrySink
from .telemetry import ObservabilityRuntime

_CONDITIONAL_STDLIB = frozenset({"tomllib"})
_KERNEL_EXEMPT_SET = frozenset(
    {
        "agi-system",
        "live-agent",
        "command-hud",
        "memory",
        "deployment",
        "monitoring",
        "browser_skill_adapter",
        "render_control_adapter",
    }
)


class ProjectTier(StrEnum):
    """Three-tier engineering standard respecting architectural & licensing boundaries."""

    TIER_1_PRODUCTION = "tier_1_production"
    TIER_2_EDGE_LIGHTWEIGHT = "tier_2_edge_lightweight"
    TIER_3_LEGACY_OR_KERNEL = "tier_3_legacy_or_kernel"


class ComplianceEntry(StrictModel):
    """Compliance evaluation record for one project or platform component."""

    project_name: str
    project_path: str
    tier: ProjectTier
    language_profile: str
    python_files: int = Field(ge=0)
    total_files: int = Field(ge=0)
    total_loc: int = Field(ge=0)
    score_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    status: Literal["compliant", "partial", "exempt", "non_compliant", "not_python"]
    exemptions: tuple[str, ...] = ()
    weakest_rules: tuple[str, ...] = ()
    notes: str = ""


class ComplianceRegistryReport(StrictModel):
    """Repository-wide compliance registry report preserving ADR-0002."""

    generated_at: str
    root_path: str
    catalog_total_projects: int = Field(ge=0)
    entries_count: int = Field(ge=0)
    adr_0002_preserved: bool
    tier_counts: dict[str, int]
    status_counts: dict[str, int]
    entries: tuple[ComplianceEntry, ...]
    trace_id: str


def verify_adr_0002_preserved(workspace_root: Path) -> bool:
    """Verify via AST that projects/agi-system/core still imports stdlib only."""
    core_dir = workspace_root / "projects" / "agi-system" / "core"
    if not core_dir.is_dir():
        return True
    stdlib = set(sys.stdlib_module_names) | _CONDITIONAL_STDLIB
    first_party = {"core", "server", "agi_kernel", "memory"}
    for path in sorted(core_dir.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            return False
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            for mod in names:
                if mod not in stdlib and mod not in first_party:
                    return False
    return True


def _count_files_fast(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    return sum(
        1
        for p in directory.rglob("*")
        if p.is_file() and ".git" not in p.parts and ".venv" not in p.parts
    )


def _evaluate_edge_component(rel_path: str, directory: Path) -> ComplianceEntry:
    total_files = _count_files_fast(directory)
    py_files = iter_python_files(directory) if directory.is_dir() else []
    if rel_path.endswith("python-worker"):
        wrangler = (directory / "wrangler.toml").read_text(encoding="utf-8", errors="ignore")
        pyproj = (directory / "pyproject.toml").read_text(encoding="utf-8", errors="ignore")
        entry_txt = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in py_files)
        checks = {
            "python_workers_314": "python_workers_314" in wrangler,
            "requires_py_313": ">=3.13" in pyproj,
            "fail_closed_auth": "Fail-closed" in entry_txt,
            "bounded_body": "read_bounded_request_body" in entry_txt,
            "budget_guard": "BudgetGuard" in entry_txt or "unknown_cost" in entry_txt,
        }
        score = round(100.0 * sum(checks.values()) / len(checks), 1)
        weak = tuple(f"edge:{k}" for k, ok in checks.items() if not ok)
        total_loc = sum(
            len(p.read_text(encoding="utf-8", errors="ignore").splitlines()) for p in py_files
        )
        return ComplianceEntry(
            project_name="python-worker",
            project_path=rel_path,
            tier=ProjectTier.TIER_2_EDGE_LIGHTWEIGHT,
            language_profile="python-workers-pyodide",
            python_files=len(py_files),
            total_files=total_files,
            total_loc=total_loc,
            score_pct=score,
            status="compliant" if score == 100.0 else "partial",
            exemptions=("Zero third-party pip deps for Pyodide Worker bundle",),
            weakest_rules=weak,
            notes="Cloudflare Python Worker contract-tested locally; live deploy unverified.",
        )

    if rel_path.endswith("edge-gateway"):
        src = (directory / "src" / "index.mjs").read_text(encoding="utf-8", errors="ignore")
        wrangler = (directory / "wrangler.jsonc").read_text(encoding="utf-8", errors="ignore")
        checks = {
            "rate_limit": "checkRateLimit" in src,
            "fail_closed_auth": "Fail-closed" in src or "fail-closed" in src.lower(),
            "bounded_body": "readBoundedBody" in src or "content-length" in src.lower(),
            "https_upstream": "https:" in src,
            "modern_compat_date": "2026-" in wrangler,
        }
        score = round(100.0 * sum(checks.values()) / len(checks), 1)
        weak = tuple(f"edge:{k}" for k, ok in checks.items() if not ok)
        return ComplianceEntry(
            project_name="edge-gateway",
            project_path=rel_path,
            tier=ProjectTier.TIER_2_EDGE_LIGHTWEIGHT,
            language_profile="javascript-worker",
            python_files=0,
            total_files=total_files,
            total_loc=len(src.splitlines()),
            score_pct=score,
            status="compliant" if score == 100.0 else "partial",
            exemptions=("Zero-dependency ES module Cloudflare Worker",),
            weakest_rules=weak,
            notes="Verified via node --test and wrangler dev --local; live CF deploy unverified.",
        )

    nb = directory / "ai2027_python_protocol_lab.ipynb"
    nb_txt = nb.read_text(encoding="utf-8", errors="ignore") if nb.is_file() else ""
    has_drive = "drive" in nb_txt.lower() or "persistent" in nb_txt.lower()
    return ComplianceEntry(
        project_name="colab-lab",
        project_path=rel_path,
        tier=ProjectTier.TIER_2_EDGE_LIGHTWEIGHT,
        language_profile="jupyter-colab",
        python_files=0,
        total_files=total_files,
        total_loc=len(nb_txt.splitlines()),
        score_pct=100.0 if (nb.is_file() and has_drive) else 50.0,
        status="compliant" if (nb.is_file() and has_drive) else "partial",
        exemptions=("Colab notebook validated via AST & offline cell execution in pytest",),
        weakest_rules=() if has_drive else ("edge:durable_storage_note",),
        notes="Targets Colab Python 3.13+ runtime with Drive/GitHub persistence guidance.",
    )


def _classify_python_entry(
    summary: ProjectAuditSummary,
    *,
    tier: ProjectTier,
    total_files: int,
    exemptions: tuple[str, ...],
) -> ComplianceEntry:
    weak = tuple(
        f"R{r.rule_id}:{r.status.value}"
        for r in summary.rules
        if r.status in {RuleStatus.FAIL, RuleStatus.WARN}
    )
    if exemptions and summary.adr_exempt:
        status: Literal["compliant", "partial", "exempt", "non_compliant", "not_python"] = "exempt"
    elif summary.score_pct >= 90.0:
        status = "compliant"
    elif summary.score_pct >= 50.0:
        status = "partial"
    else:
        status = "non_compliant"
    return ComplianceEntry(
        project_name=summary.project_name,
        project_path=summary.project_path,
        tier=tier,
        language_profile="python",
        python_files=summary.python_files,
        total_files=total_files,
        total_loc=summary.total_loc,
        score_pct=summary.score_pct,
        status=status,
        exemptions=exemptions,
        weakest_rules=weak,
    )


def _scan_registry_sync(workspace_root: Path, trace_id: str) -> ComplianceRegistryReport:
    root = workspace_root.resolve()
    catalog_path = root / "catalog.json"
    empty_mirrors: set[str] = set()
    catalog_entries: list[dict[str, object]] = []
    if catalog_path.is_file():
        cat = json.loads(catalog_path.read_text(encoding="utf-8"))
        empty_mirrors = {str(x) for x in cat.get("empty_source_repos", [])}
        raw_projs = cat.get("projects", [])
        if isinstance(raw_projs, list):
            catalog_entries = [p for p in raw_projs if isinstance(p, dict)]

    entries: list[ComplianceEntry] = []

    # Tier 1 core production packages
    for rel, tier, ex in (
        ("python-2027", ProjectTier.TIER_1_PRODUCTION, ()),
        (
            "connectors-hub",
            ProjectTier.TIER_1_PRODUCTION,
            ("Independent L5 FastAPI service with OS keyring/vault fail-closed design",),
        ),
    ):
        pdir = root / rel
        if pdir.is_dir():
            summary = _evaluate_project_sync(pdir, root)
            entries.append(
                _classify_python_entry(
                    summary, tier=tier, total_files=_count_files_fast(pdir), exemptions=ex
                )
            )

    # Tier 2 lightweight edge / Workers / Colab components
    for rel in (
        "python-2027/workers/python-worker",
        "python-2027/workers/edge-gateway",
        "python-2027/colab",
    ):
        pdir = root / rel
        if pdir.is_dir():
            entries.append(_evaluate_edge_component(rel, pdir))

    # Tier 3 core zero-dep L0/L1/L2 subsystems outside projects/
    for rel in (
        "memory",
        "live-agent",
        "command-hud",
        "browser_skill_adapter",
        "deployment",
        "monitoring",
        "render_control_adapter",
    ):
        pdir = root / rel
        if pdir.is_dir():
            summary = _evaluate_project_sync(pdir, root)
            entries.append(
                _classify_python_entry(
                    summary,
                    tier=ProjectTier.TIER_3_LEGACY_OR_KERNEL,
                    total_files=_count_files_fast(pdir),
                    exemptions=("ADR-0002 stdlib-only component; exempt from external deps",),
                )
            )

    # All 48 catalog projects under projects/
    seen_paths: set[str] = {e.project_path for e in entries}
    project_items: list[tuple[str, str]] = [
        (str(item.get("name", "")), str(item.get("path", "")))
        for item in catalog_entries
        if item.get("path")
    ]
    if not project_items and (root / "projects").is_dir():
        project_items = [
            (d.name, f"projects/{d.name}")
            for d in sorted((root / "projects").iterdir())
            if d.is_dir()
        ]

    for name, rel_path in project_items:
        if rel_path in seen_paths:
            continue
        seen_paths.add(rel_path)
        pdir = root / rel_path
        total_files = _count_files_fast(pdir)
        py_files = iter_python_files(pdir) if pdir.is_dir() else []

        if name in empty_mirrors or not pdir.is_dir():
            entries.append(
                ComplianceEntry(
                    project_name=name or Path(rel_path).name,
                    project_path=rel_path,
                    tier=ProjectTier.TIER_3_LEGACY_OR_KERNEL,
                    language_profile="empty-mirror",
                    python_files=0,
                    total_files=total_files,
                    total_loc=0,
                    score_pct=None,
                    status="exempt",
                    exemptions=("Empty upstream mirror listed in catalog.json",),
                    notes="Archived placeholder without upstream source code.",
                )
            )
            continue

        if not py_files:
            entries.append(
                ComplianceEntry(
                    project_name=name or Path(rel_path).name,
                    project_path=rel_path,
                    tier=ProjectTier.TIER_3_LEGACY_OR_KERNEL,
                    language_profile="non-python-web-or-js",
                    python_files=0,
                    total_files=total_files,
                    total_loc=0,
                    score_pct=None,
                    status="not_python",
                    exemptions=("Non-Python archived repo; Python 2027 AST rules not applicable",),
                )
            )
            continue

        summary = _evaluate_project_sync(pdir, root)
        exemptions: tuple[str, ...] = (
            ("ADR-0002 zero-dependency stdlib-only kernel; third-party deps forbidden",)
            if summary.project_name in _KERNEL_EXEMPT_SET
            else ("Tier 3 archived project; gradual wrapper adoption without breaking archive",)
        )
        entries.append(
            _classify_python_entry(
                summary,
                tier=ProjectTier.TIER_3_LEGACY_OR_KERNEL,
                total_files=total_files,
                exemptions=exemptions,
            )
        )

    tier_counts = {str(k): int(v) for k, v in Counter(e.tier.value for e in entries).items()}
    status_counts = {str(k): int(v) for k, v in Counter(e.status for e in entries).items()}
    return ComplianceRegistryReport(
        generated_at=datetime.now(UTC).isoformat(),
        root_path=str(root),
        catalog_total_projects=len(project_items),
        entries_count=len(entries),
        adr_0002_preserved=verify_adr_0002_preserved(root),
        tier_counts=tier_counts,
        status_counts=status_counts,
        entries=tuple(entries),
        trace_id=trace_id,
    )


async def build_compliance_registry(
    workspace_root: Path,
    *,
    telemetry: TelemetrySink | None = None,
) -> ComplianceRegistryReport:
    """Build a three-tier compliance registry report asynchronously."""
    runtime = telemetry or ObservabilityRuntime(json_logs=True)
    with runtime.start_span("compliance.registry.build"):
        trace_id = runtime.current_trace_id()
        report = await asyncio.to_thread(_scan_registry_sync, workspace_root, trace_id)
    runtime.log(
        "info",
        "compliance_registry_built",
        entries=report.entries_count,
        catalog_projects=report.catalog_total_projects,
        adr_0002_preserved=report.adr_0002_preserved,
    )
    return report


def format_compliance_markdown(report: ComplianceRegistryReport) -> str:
    """Render a concise Markdown summary of the three-tier compliance registry."""
    lines = [
        "# AI-2027 Three-Tier Compliance Registry",
        "",
        (
            f"- Entries audited: **{report.entries_count}** "
            f"(Catalog repos: **{report.catalog_total_projects}**) · "
            f"ADR-0002 zero-dep kernel preserved: **{report.adr_0002_preserved}**"
        ),
        f"- Tiers: `{report.tier_counts}`",
        f"- Statuses: `{report.status_counts}`",
        "",
        "| Component / Project | Tier | Profile | Score | Status | Exemptions / Notes |",
        "|---|---|---|---:|---|---|",
    ]
    for item in report.entries:
        score_str = f"{item.score_pct:.1f}%" if item.score_pct is not None else "n/a"
        note = "; ".join(item.exemptions) if item.exemptions else (item.notes or "-")
        lines.append(
            f"| `{item.project_path}` | `{item.tier.value}` | {item.language_profile} | "
            f"**{score_str}** | `{item.status}` | {note[:90]} |"
        )
    return "\n".join(lines)
