from __future__ import annotations

from pathlib import Path

import pytest

from ai2027_protocol.auditor import ProtocolAuditor
from ai2027_protocol.bridges import ConnectorsBridge, KernelSandboxBridge, MemoryBridge
from ai2027_protocol.telemetry import ObservabilityRuntime
from ai2027_protocol.tools import SandboxToolRuntime, ToolContext, ToolInvocation

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_ctx(tmp_path: Path, *, read_only: bool = False) -> ToolContext:
    sessions_root = tmp_path / "sessions"
    sdir = sessions_root / "sess_tools01"
    sandbox = sdir / "sandbox"
    sandbox.mkdir(parents=True, exist_ok=True)
    runtime = ObservabilityRuntime(json_logs=True)
    return ToolContext(
        session_id="sess_tools01",
        workspace_root=_REPO_ROOT,
        sandbox_dir=sandbox,
        ledger_path=sdir / "ledger.jsonl",
        sessions_root=sessions_root,
        kernel_bridge=KernelSandboxBridge(_REPO_ROOT),
        memory_bridge=MemoryBridge(
            workspace_root=_REPO_ROOT,
            db_path=tmp_path / "memory.db",
            sandbox_root=sessions_root,
        ),
        connectors_bridge=ConnectorsBridge(store_path=tmp_path / "approvals.json"),
        auditor=ProtocolAuditor(workspace_root=_REPO_ROOT, telemetry=runtime),
        read_only=read_only,
    )


async def test_workspace_read_grep_list_and_security(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    runtime = SandboxToolRuntime()
    assert len(runtime.specs()) >= 10

    batch = await runtime.execute_parallel(
        [
            ToolInvocation(
                tool="workspace.list_files", args={"path": "python-2027/src", "limit": 20}
            ),
            ToolInvocation(
                tool="workspace.read_file",
                args={"path": "python-2027/pyproject.toml", "max_lines": 15},
            ),
            ToolInvocation(
                tool="workspace.grep",
                args={"query": "FreeFirstRouter", "path": "python-2027/src", "limit": 10},
            ),
            ToolInvocation(tool="repo.audit", args={"path": "python-2027"}),
            ToolInvocation(tool="connector.inspect", args={}),
        ],
        ctx,
    )
    assert len(batch) == 5
    assert all(out.ok for out in batch)
    assert "ai2027-protocol" in batch[1].output
    assert "FreeFirstRouter" in batch[2].output
    assert "100.0%" in batch[3].output

    secret_read = await runtime.execute(
        ToolInvocation(tool="workspace.read_file", args={"path": "python-2027/.env"}), ctx
    )
    assert secret_read.ok is False

    traversal_list = await runtime.execute(
        ToolInvocation(tool="workspace.list_files", args={"path": "../../etc"}), ctx
    )
    assert traversal_list.ok is False


async def test_sandbox_write_edit_python_env_isolation_and_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_key = "nvapi-" + "isolated-test-token-12345"
    monkeypatch.setenv("NVIDIA_API_KEY", fake_key)
    ctx = _make_ctx(tmp_path)
    runtime = SandboxToolRuntime()

    write_res = await runtime.execute(
        ToolInvocation(
            tool="sandbox.write_artifact",
            args={"path": "notes/app.py", "content": "print('v1')\n"},
        ),
        ctx,
    )
    assert write_res.ok is True
    assert write_res.verified_sha256 is not None

    edit_res = await runtime.execute(
        ToolInvocation(
            tool="sandbox.edit_file",
            args={
                "path": "notes/app.py",
                "old_text": "print('v1')",
                "new_text": "import os\nprint('v2', 'NVIDIA_API_KEY' in os.environ)",
            },
        ),
        ctx,
    )
    assert edit_res.ok is True

    py_res = await runtime.execute(
        ToolInvocation(tool="sandbox.run_python", args={"path": "notes/app.py"}),
        ctx,
    )
    assert py_res.ok is True
    assert "v2 False" in py_res.output

    timeout_res = await runtime.execute(
        ToolInvocation(
            tool="sandbox.run_python",
            args={
                "path": "slow.py",
                "code": "import time\ntime.sleep(3)",
                "timeout_s": 0.25,
            },
        ),
        ctx,
    )
    assert timeout_res.ok is False
    assert "timed out" in timeout_res.summary.lower()

    mem_res = await runtime.execute(
        ToolInvocation(tool="memory.search", args={"query": "notes app", "k": 3}),
        ctx,
    )
    assert mem_res.ok is True

    prop_res = await runtime.execute(
        ToolInvocation(
            tool="connector.propose_action",
            args={
                "connector": "notion",
                "summary": "Sync report",
                "method": "POST",
                "path": "/v1/pages",
                "payload": {"title": "Report"},
            },
        ),
        ctx,
    )
    assert prop_res.ok is True
    assert prop_res.approval_id is not None

    unknown = await runtime.execute(ToolInvocation(tool="no.such.tool", args={}), ctx)
    assert unknown.ok is False

    ro_ctx = _make_ctx(tmp_path, read_only=True)
    for mut_tool, args in (
        ("sandbox.write_artifact", {"path": "x.txt", "content": "x"}),
        ("sandbox.edit_file", {"path": "x.txt", "old_text": "a", "new_text": "b"}),
        ("sandbox.run_python", {"path": "x.py", "code": "print(1)"}),
        ("connector.propose_action", {"connector": "gmail", "summary": "x"}),
    ):
        res = await runtime.execute(ToolInvocation(tool=mut_tool, args=args), ro_ctx)
        assert res.ok is False
        assert "READ_ONLY" in res.summary
