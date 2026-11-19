from __future__ import annotations

import os
from pathlib import Path

import pytest

from ai2027_protocol.bridges import (
    ConnectorsBridge,
    KernelSandboxBridge,
    MemoryBridge,
)
from ai2027_protocol.security import SecurityValidationError

_REPO_ROOT = Path(__file__).resolve().parents[2]


async def test_kernel_sandbox_and_memory_bridge_end_to_end(tmp_path: Path) -> None:
    sessions_root = tmp_path / "sessions"
    sdir = sessions_root / "sess_test0001"
    sandbox = sdir / "sandbox"
    ledger = sdir / "ledger.jsonl"

    kbridge = KernelSandboxBridge(_REPO_ROOT)
    assert kbridge.is_available is True

    res, raw = await kbridge.write_verified_file(
        sandbox_dir=sandbox,
        ledger_path=ledger,
        rel_path="reports/hello.html",
        content="<h1>Verified</h1>",
        session_id="sess_test0001",
        sessions_root=sessions_root,
    )
    assert res.status == "VERIFIED"
    assert res.sha256 is not None and len(res.sha256) == 64
    assert res.checks.get("hash_matches") is True

    denied_res, denied_raw = await kbridge.write_verified_file(
        sandbox_dir=sandbox,
        ledger_path=ledger,
        rel_path="../../outside.txt",
        content="escape",
        session_id="sess_test0001",
        sessions_root=sessions_root,
    )
    assert denied_res.status == "DENIED"
    assert denied_res.denial_reasons

    mbridge = MemoryBridge(
        workspace_root=_REPO_ROOT,
        db_path=tmp_path / "mem.db",
        sandbox_root=sessions_root,
    )
    ok_admit, reason = await mbridge.admit_record(raw)
    assert ok_admit is True
    assert "admitted" in reason

    refused, ref_reason = await mbridge.admit_record(denied_raw)
    assert refused is False
    assert "DENIED" in ref_reason

    hits = await mbridge.search("Verified reports hello", k=3)
    assert len(hits) >= 1
    assert hits[0].trust == 1.0

    stats = await mbridge.stats()
    assert stats.available is True
    assert stats.total_memories == 1
    assert stats.refusals == 1


async def test_bridges_degrade_cleanly_when_repo_absent(tmp_path: Path) -> None:
    kbridge = KernelSandboxBridge(tmp_path)
    assert kbridge.is_available is False
    res, _ = await kbridge.write_verified_file(
        sandbox_dir=tmp_path / "sb",
        ledger_path=tmp_path / "ledger.jsonl",
        rel_path="a.txt",
        content="abc",
    )
    assert res.status == "FAILED"

    mbridge = MemoryBridge(
        workspace_root=tmp_path,
        db_path=tmp_path / "m.db",
        sandbox_root=tmp_path,
    )
    admitted, msg = await mbridge.admit_record({})
    assert admitted is False
    assert "unavailable" in msg
    assert await mbridge.search("anything") == []
    assert (await mbridge.stats()).available is False


async def test_connectors_bridge_readiness_and_approval_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_tok = "tok-" + "approval-secret-01"
    monkeypatch.setenv("GITHUB_TOKEN", fake_tok)
    bridge = ConnectorsBridge(store_path=tmp_path / "approvals.json")

    connectors = await bridge.list_connectors()
    gh = next(c for c in connectors if c.connector_id == "github")
    assert gh.configured is True
    assert fake_tok not in gh.credential_state

    proposal = await bridge.propose_action(
        session_id="sess_test0002",
        connector="gmail",
        summary=f"Send digest with {fake_tok}",
        method="POST",
        path="/users/me/drafts",
        payload={"subject": "Daily brief", "api_key": fake_tok},
    )
    assert proposal.status == "pending"
    assert proposal.payload_preview["api_key"] == "[REDACTED]"
    assert len(proposal.request_sha256) == 64

    listed = await bridge.list_approvals(session_id="sess_test0002")
    assert len(listed) == 1

    approved = await bridge.decide_approval(
        action_id=proposal.action_id, approve=True, note="Looks safe"
    )
    assert approved.status == "approved"
    assert approved.decision_note == "Looks safe"

    rejected = await bridge.decide_approval(action_id=proposal.action_id, approve=False)
    assert rejected.status == "rejected"

    with pytest.raises(SecurityValidationError):
        await bridge.propose_action(
            session_id="sess_test0002",
            connector="unknown_app",
            summary="bad",
            method="POST",
            path="/x",
            payload={},
        )
    with pytest.raises(SecurityValidationError):
        await bridge.propose_action(
            session_id="sess_test0002",
            connector="gmail",
            summary="bad path",
            method="POST",
            path="../escape",
            payload={},
        )
    with pytest.raises(SecurityValidationError):
        await bridge.decide_approval(action_id="invalid-id!", approve=True)
    with pytest.raises(FileNotFoundError):
        await bridge.decide_approval(action_id="0123456789abcdef01234567", approve=True)
    os.environ.pop("GITHUB_TOKEN", None)
