"""Async AST-based auditor measuring compliance with the 10 Python 2027 rules."""

from __future__ import annotations

import asyncio
import time
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .auditor_ast import is_test_file, iter_python_files, scan_project_ast
from .contracts import (
    ProjectAuditSummary,
    ProtocolAuditReport,
    RuleResult,
    RuleStatus,
    TelemetrySink,
)
from .security import resolve_safe_path
from .telemetry import ObservabilityRuntime

_ADR_ZERO_DEP_TARGETS = frozenset(
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


def _read_pyproject(project_dir: Path) -> dict[str, Any]:
    path = project_dir / "pyproject.toml"
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _status_from_score(score: float) -> RuleStatus:
    if score >= 0.99:
        return RuleStatus.PASS
    if score >= 0.5:
        return RuleStatus.WARN
    return RuleStatus.FAIL


def _evaluate_project_sync(project_dir: Path, repo_root: Path | None) -> ProjectAuditSummary:
    if not project_dir.is_dir():
        raise FileNotFoundError(f"Audit target directory does not exist: {project_dir.name}")
    metrics = scan_project_ast(project_dir)
    pyproject = _read_pyproject(project_dir)
    tool_cfg = pyproject.get("tool", {}) if isinstance(pyproject.get("tool"), dict) else {}
    deps_raw = " ".join(str(x) for x in pyproject.get("project", {}).get("dependencies", []))
    dev_raw = str(pyproject.get("dependency-groups", {})) + str(
        pyproject.get("project", {}).get("optional-dependencies", {})
    )
    all_deps_str = (deps_raw + " " + dev_raw).lower()
    adr_exempt = project_dir.name in _ADR_ZERO_DEP_TARGETS

    # Rule 1: Python 3.13+, uv, ruff, strict typing + Pydantic V2
    has_pyproject = bool(pyproject)
    has_ruff = "ruff" in tool_cfg or "ruff" in all_deps_str
    has_mypy = "mypy" in tool_cfg or "mypy" in all_deps_str or "pyright" in all_deps_str
    has_pydantic = metrics.uses_pydantic_v2 or "pydantic" in all_deps_str
    type_ratio = metrics.typed_public_funcs / metrics.public_funcs if metrics.public_funcs else 1.0
    r1_parts = [has_pyproject, has_ruff, has_mypy, has_pydantic, type_ratio >= 0.85]
    r1_score = round(sum(r1_parts) / len(r1_parts), 2)
    r1_status = _status_from_score(r1_score)

    # Rule 2: Small modules, clear contracts, replaceable components
    small_modules = metrics.max_file_loc <= 650 if metrics.python_files else False
    has_contracts = metrics.protocol_classes >= 1
    r2_score = (0.5 if small_modules else 0.2) + (0.5 if has_contracts else 0.0)
    r2_status = _status_from_score(r2_score)

    # Rule 3: asyncio + TaskGroup, non-blocking
    if metrics.blocking_in_async > 0:
        r3_score, r3_status = 0.0, RuleStatus.FAIL
    elif metrics.async_defs > 0 and metrics.uses_taskgroup:
        r3_score, r3_status = 1.0, RuleStatus.PASS
    elif metrics.async_defs > 0:
        r3_score, r3_status = 0.6, RuleStatus.WARN
    else:
        r3_score, r3_status = 0.3, RuleStatus.WARN

    # Rule 4: Measure before optimizing (benchmarks with real numbers)
    has_bench_file = any(
        list(project_dir.rglob("bench*.py")) + list(project_dir.rglob("benchmark*.py"))
    )
    if has_bench_file and metrics.uses_perf_counter:
        r4_score, r4_status = 1.0, RuleStatus.PASS
    elif has_bench_file or metrics.uses_perf_counter:
        r4_score, r4_status = 0.5, RuleStatus.WARN
    else:
        r4_score, r4_status = 0.0, RuleStatus.FAIL

    # Rule 5: pytest + hypothesis, coverage > 90%
    py_files = iter_python_files(project_dir)
    has_tests = any(is_test_file(p, project_dir) for p in py_files)
    cov_cfg = tool_cfg.get("coverage", {}).get("report", {}) if isinstance(tool_cfg, dict) else {}
    cov_target = float(cov_cfg.get("fail_under", 0)) >= 90.0 if isinstance(cov_cfg, dict) else False
    has_hyp = metrics.uses_hypothesis or "hypothesis" in all_deps_str
    has_pytest = metrics.uses_pytest or "pytest" in all_deps_str
    r5_parts = [has_tests, has_pytest, has_hyp, cov_target]
    r5_score = round(sum(r5_parts) / len(r5_parts), 2)
    r5_status = _status_from_score(r5_score)

    # Rule 6: No secrets in code, validate all inputs
    forbidden_env = any(
        (project_dir / name).is_file() for name in (".env", ".env.local", "env.local")
    )
    has_env_example = (project_dir / ".env.example").is_file()
    has_security_mod = "security" in " ".join(p.name for p in py_files)
    if metrics.secret_hits > 0 or forbidden_env:
        r6_score, r6_status = 0.0, RuleStatus.FAIL
    elif has_env_example and (has_pydantic or has_security_mod):
        r6_score, r6_status = 1.0, RuleStatus.PASS
    else:
        r6_score, r6_status = 0.6, RuleStatus.WARN

    # Rule 7: structlog + OpenTelemetry
    r7_parts = [metrics.uses_structlog, metrics.uses_otel]
    r7_score = round(sum(r7_parts) / 2.0, 2)
    r7_status = (
        RuleStatus.PASS if r7_score == 1.0 else RuleStatus.WARN if r7_score > 0 else RuleStatus.FAIL
    )

    # Rule 8: Free-First AI, fallback, timeout & retry
    r8_parts = [metrics.uses_free_providers, metrics.uses_timeout_retry]
    r8_score = round(sum(r8_parts) / 2.0, 2)
    r8_status = (
        RuleStatus.PASS if r8_score == 1.0 else RuleStatus.WARN if r8_score > 0 else RuleStatus.FAIL
    )

    # Rule 9: Automated CI + $0 deployment targets (Workers / Colab / Pages)
    has_workers = any(project_dir.rglob("wrangler.*"))
    has_colab = any(project_dir.rglob("*.ipynb"))
    has_local_ci = (project_dir / ".github" / "workflows").is_dir()
    workflows_root = repo_root / ".github" / "workflows" if repo_root is not None else None
    has_repo_ci = bool(
        workflows_root is not None
        and workflows_root.is_dir()
        and any(
            project_dir.name in wf.read_text(encoding="utf-8", errors="ignore")
            for wf in workflows_root.glob("*.yml")
        )
    )
    has_ci = has_local_ci or has_repo_ci
    r9_parts = [has_ci, has_workers or has_colab]
    r9_score = round(sum(r9_parts) / 2.0, 2)
    r9_status = (
        RuleStatus.PASS if r9_score == 1.0 else RuleStatus.WARN if r9_score > 0 else RuleStatus.FAIL
    )

    # Rule 10: Works first run, 1-command demo, 1-line docstrings on public API
    doc_ratio = metrics.documented_public_defs / metrics.public_defs if metrics.public_defs else 1.0
    has_demo = any(
        list(project_dir.rglob("demo.py"))
        + list(project_dir.rglob("Makefile"))
        + list(project_dir.rglob("one_command_demo.sh"))
    )
    has_readme = (project_dir / "README.md").is_file()
    r10_parts = [doc_ratio >= 0.9, has_demo, has_readme]
    r10_score = round(sum(r10_parts) / len(r10_parts), 2)
    r10_status = _status_from_score(r10_score)

    rules = (
        RuleResult(
            rule_id=1,
            name="foundation_typing",
            status=RuleStatus.EXEMPT if adr_exempt and r1_status != RuleStatus.PASS else r1_status,
            score=r1_score,
            evidence=(
                f"pyproject={has_pyproject}, ruff={has_ruff}, mypy={has_mypy}, "
                f"pydantic={has_pydantic}, "
                f"typed_public={metrics.typed_public_funcs}/{metrics.public_funcs}"
            ),
            remediation=(
                None
                if r1_status == RuleStatus.PASS
                else "Add uv/ruff/mypy strict typing and Pydantic V2 models."
            ),
        ),
        RuleResult(
            rule_id=2,
            name="modular_contracts",
            status=r2_status,
            score=r2_score,
            evidence=(
                f"max_file_loc={metrics.max_file_loc}, protocol_classes={metrics.protocol_classes}"
            ),
            remediation=(
                None
                if r2_status == RuleStatus.PASS
                else "Split files <= 650 LOC and define typing.Protocol contracts."
            ),
        ),
        RuleResult(
            rule_id=3,
            name="async_taskgroup",
            status=r3_status,
            score=r3_score,
            evidence=(
                f"async_defs={metrics.async_defs}, taskgroup={metrics.uses_taskgroup}, "
                f"blocking_in_async={metrics.blocking_in_async}"
            ),
            remediation=(
                None
                if r3_status == RuleStatus.PASS
                else "Use async/await with asyncio.TaskGroup and zero blocking calls."
            ),
        ),
        RuleResult(
            rule_id=4,
            name="measured_perf",
            status=r4_status,
            score=r4_score,
            evidence=f"bench_file={has_bench_file}, perf_counter={metrics.uses_perf_counter}",
            remediation=(
                None
                if r4_status == RuleStatus.PASS
                else "Add reproducible benchmark script measuring latency before tuning."
            ),
        ),
        RuleResult(
            rule_id=5,
            name="testing_hypothesis",
            status=r5_status,
            score=r5_score,
            evidence=(
                f"tests={has_tests}, pytest={has_pytest}, "
                f"hypothesis={has_hyp}, cov>=90={cov_target}"
            ),
            remediation=(
                None
                if r5_status == RuleStatus.PASS
                else "Add pytest + hypothesis property tests with --cov-fail-under=90."
            ),
        ),
        RuleResult(
            rule_id=6,
            name="security_hygiene",
            status=r6_status,
            score=r6_score,
            evidence=(
                f"secret_hits={metrics.secret_hits}, "
                f"forbidden_env={forbidden_env}, env_example={has_env_example}"
            ),
            remediation=(
                None
                if r6_status == RuleStatus.PASS
                else "Remove plaintext secrets/env files and validate every input."
            ),
        ),
        RuleResult(
            rule_id=7,
            name="observability",
            status=RuleStatus.EXEMPT if adr_exempt and r7_status != RuleStatus.PASS else r7_status,
            score=r7_score,
            evidence=f"structlog={metrics.uses_structlog}, opentelemetry={metrics.uses_otel}",
            remediation=(
                None
                if r7_status == RuleStatus.PASS
                else "Adopt structlog structured JSON logs and OpenTelemetry spans."
            ),
        ),
        RuleResult(
            rule_id=8,
            name="free_first_ai",
            status=r8_status,
            score=r8_score,
            evidence=(
                f"free_providers={metrics.uses_free_providers}, "
                f"timeout_retry={metrics.uses_timeout_retry}"
            ),
            remediation=(
                None
                if r8_status == RuleStatus.PASS
                else "Route free providers first with fallback, timeout, and retry."
            ),
        ),
        RuleResult(
            rule_id=9,
            name="zero_cost_ci_deploy",
            status=r9_status,
            score=r9_score,
            evidence=f"ci={has_ci}, workers={has_workers}, colab={has_colab}",
            remediation=(
                None
                if r9_status == RuleStatus.PASS
                else "Wire GitHub Actions CI plus $0 Workers/Colab deployment targets."
            ),
        ),
        RuleResult(
            rule_id=10,
            name="wow_dx_docstrings",
            status=r10_status,
            score=r10_score,
            evidence=(
                f"docstrings={metrics.documented_public_defs}/{metrics.public_defs}, "
                f"demo={has_demo}, readme={has_readme}"
            ),
            remediation=(
                None
                if r10_status == RuleStatus.PASS
                else "Add 1-line docstrings to all public definitions and a 1-command demo."
            ),
        ),
    )
    score_pct = round(100.0 * sum(r.score for r in rules) / len(rules), 1)
    rel_path = (
        project_dir.relative_to(repo_root).as_posix()
        if repo_root is not None and project_dir.is_relative_to(repo_root)
        else project_dir.name
    )
    return ProjectAuditSummary(
        project_name=project_dir.name,
        project_path=rel_path or ".",
        python_files=metrics.python_files,
        total_loc=metrics.total_loc,
        score_pct=score_pct,
        adr_exempt=adr_exempt,
        rules=rules,
    )


class ProtocolAuditor:
    """Concurrent AST auditor scoring Python packages against the 10-rule protocol."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        telemetry: TelemetrySink | None = None,
    ) -> None:
        """Bind auditor to a workspace root and optional telemetry sink."""
        self._root = workspace_root.resolve()
        self._telemetry: TelemetrySink = telemetry or ObservabilityRuntime()

    async def audit_projects(self, relative_paths: Sequence[str]) -> ProtocolAuditReport:
        """Audit one or more safe workspace paths concurrently using asyncio.TaskGroup."""
        start_ns = time.perf_counter_ns()
        resolved_dirs = [resolve_safe_path(self._root, rel) for rel in relative_paths]
        summaries: list[ProjectAuditSummary | None] = [None] * len(resolved_dirs)

        async def _audit_one(idx: int, directory: Path) -> None:
            summaries[idx] = await asyncio.to_thread(_evaluate_project_sync, directory, self._root)

        missing_error: BaseException | None = None
        with self._telemetry.start_span("audit.scan", {"targets": len(resolved_dirs)}):
            trace_id = self._telemetry.current_trace_id()
            try:
                async with asyncio.TaskGroup() as group:
                    for idx, target in enumerate(resolved_dirs):
                        group.create_task(_audit_one(idx, target))
            except* FileNotFoundError as exc_group:
                missing_error = exc_group.exceptions[0]

        if missing_error is not None:
            raise missing_error

        completed = tuple(item for item in summaries if item is not None)
        avg = round(sum(p.score_pct for p in completed) / len(completed), 1) if completed else 0.0
        duration_ms = round((time.perf_counter_ns() - start_ns) / 1e6, 3)
        self._telemetry.log(
            "info",
            "protocol_audit_completed",
            scanned=len(completed),
            average_score_pct=avg,
            duration_ms=duration_ms,
        )
        return ProtocolAuditReport(
            root_path=str(self._root),
            scanned_projects=len(completed),
            average_score_pct=avg,
            projects=completed,
            duration_ms=duration_ms,
            trace_id=trace_id,
        )


def format_audit_markdown(report: ProtocolAuditReport) -> str:
    """Format a ProtocolAuditReport as a concise Markdown scorecard table."""
    lines = [
        "# Python 2027 Protocol Compliance Scorecard",
        "",
        (
            f"- Scanned projects: **{report.scanned_projects}** · "
            f"Average score: **{report.average_score_pct}%** · "
            f"Duration: `{report.duration_ms} ms`"
        ),
        f"- Trace ID: `{report.trace_id}`",
        "",
        "| Project | Python files | LOC | Score | ADR-0002 Exempt | Weakest rules |",
        "|---|---:|---:|---:|:---:|---|",
    ]
    for proj in report.projects:
        weak = (
            ", ".join(
                f"R{r.rule_id}:{r.status.value}"
                for r in proj.rules
                if r.status in {RuleStatus.FAIL, RuleStatus.WARN}
            )
            or "none (10/10 PASS)"
        )
        lines.append(
            f"| `{proj.project_path}` | {proj.python_files} | {proj.total_loc} | "
            f"**{proj.score_pct}%** | {'yes' if proj.adr_exempt else 'no'} | {weak} |"
        )
    return "\n".join(lines)
