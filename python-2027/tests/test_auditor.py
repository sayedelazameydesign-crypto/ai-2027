from __future__ import annotations

from pathlib import Path

import pytest

from ai2027_protocol.auditor import ProtocolAuditor, format_audit_markdown
from ai2027_protocol.contracts import RuleStatus

_PKG_ROOT = Path(__file__).resolve().parents[1]


async def test_auditor_scores_python_2027_at_full_compliance() -> None:
    workspace_root = _PKG_ROOT.parent
    auditor = ProtocolAuditor(workspace_root=workspace_root)
    report = await auditor.audit_projects(["python-2027"])
    assert report.scanned_projects == 1
    summary = report.projects[0]
    assert summary.project_name == "python-2027"
    assert summary.score_pct == 100.0
    assert all(rule.status == RuleStatus.PASS for rule in summary.rules)
    md = format_audit_markdown(report)
    assert "python-2027" in md
    assert "100.0%" in md


async def test_auditor_detects_non_compliant_and_adr_exempt_projects(tmp_path: Path) -> None:
    bad = tmp_path / "bad_service"
    bad.mkdir()
    (bad / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (bad / "app.py").write_text(
        "import time\nasync def run(x):\n    time.sleep(1)\n    return x\n",
        encoding="utf-8",
    )

    exempt = tmp_path / "agi-system"
    exempt.mkdir()
    (exempt / "core.py").write_text(
        '"""Stdlib module."""\ndef ok(x: int) -> int:\n    """Add one."""\n    return x + 1\n',
        encoding="utf-8",
    )

    auditor = ProtocolAuditor(workspace_root=tmp_path)
    report = await auditor.audit_projects(["bad_service", "agi-system"])
    assert report.scanned_projects == 2
    bad_summary, exempt_summary = report.projects

    rules_by_name = {r.name: r for r in bad_summary.rules}
    assert rules_by_name["async_taskgroup"].status == RuleStatus.FAIL
    assert rules_by_name["security_hygiene"].status == RuleStatus.FAIL
    assert rules_by_name["wow_dx_docstrings"].status == RuleStatus.FAIL
    assert exempt_summary.adr_exempt is True
    assert exempt_summary.rules[0].status == RuleStatus.EXEMPT

    with pytest.raises(FileNotFoundError):
        await auditor.audit_projects(["does-not-exist"])
