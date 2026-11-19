from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from ai2027_protocol.auditor import ProtocolAuditor
from ai2027_protocol.bridges import ConnectorsBridge, KernelSandboxBridge, MemoryBridge
from ai2027_protocol.connector_hub_bridge import LiveConnectorClient
from ai2027_protocol.security import SecurityValidationError
from ai2027_protocol.telemetry import ObservabilityRuntime
from ai2027_protocol.tools import SandboxToolRuntime, ToolContext, ToolInvocation

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_ctx(tmp_path: Path, client: httpx.AsyncClient | None = None) -> ToolContext:
    sessions_root = tmp_path / "studio" / "sessions"
    sdir = sessions_root / "sess_adv0001"
    sandbox_dir = sdir / "sandbox"
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    return ToolContext(
        session_id="sess_adv0001",
        workspace_root=_REPO_ROOT,
        sandbox_dir=sandbox_dir,
        ledger_path=sdir / "ledger.jsonl",
        sessions_root=sessions_root,
        kernel_bridge=KernelSandboxBridge(_REPO_ROOT),
        memory_bridge=MemoryBridge(
            workspace_root=_REPO_ROOT,
            db_path=tmp_path / "studio" / "memory.db",
            sandbox_root=sessions_root,
        ),
        connectors_bridge=ConnectorsBridge(store_path=tmp_path / "studio" / "approvals.json"),
        auditor=ProtocolAuditor(
            workspace_root=_REPO_ROOT, telemetry=ObservabilityRuntime(json_logs=True)
        ),
        connector_client=LiveConnectorClient(client=client),
    )


async def test_sandbox_run_tests_and_verify_artifact_detects_tamper(tmp_path: Path) -> None:
    rt = SandboxToolRuntime()
    ctx = _make_ctx(tmp_path)

    # 0 tests must fail closed cleanly rather than reporting false success.
    empty = await rt.execute(ToolInvocation(tool="sandbox.run_tests", args={}), ctx)
    assert empty.ok is False

    w1 = await rt.execute(
        ToolInvocation(
            tool="sandbox.write_artifact",
            args={
                "path": "test_math.py",
                "content": (
                    "import unittest\n"
                    "class T(unittest.TestCase):\n"
                    "    def test_ok(self):\n"
                    "        self.assertEqual(2 + 2, 4)\n"
                ),
            },
        ),
        ctx,
    )
    assert w1.ok is True

    test_res = await rt.execute(
        ToolInvocation(tool="sandbox.run_tests", args={"path": "test_math.py"}), ctx
    )
    assert test_res.ok is True
    assert test_res.policy_checks["ran_tests"] is True

    verify_ok = await rt.execute(
        ToolInvocation(tool="sandbox.verify_artifact", args={"path": "test_math.py"}), ctx
    )
    assert verify_ok.ok is True
    assert verify_ok.policy_checks["hash_matches"] is True
    assert verify_ok.policy_checks["not_tampered"] is True

    # Silently tamper the artifact on disk and assert independent verifier catches it.
    (ctx.sandbox_dir / "test_math.py").write_text("# tampered\n", encoding="utf-8")
    verify_bad = await rt.execute(
        ToolInvocation(tool="sandbox.verify_artifact", args={"path": "test_math.py"}), ctx
    )
    assert verify_bad.ok is False
    assert verify_bad.policy_checks["not_tampered"] is False


async def test_workspace_git_diff_and_connector_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "api.github.com" and request.url.path == "/search/repositories":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "full_name": "sayedelazameydesign-crypto/ai-2027",
                            "html_url": "https://github.com/sayedelazameydesign-crypto/ai-2027",
                            "description": "Token gsk_FAKE1234567890SECRET000000 scrubbed",
                        }
                    ]
                },
            )
        if host == "api.github.com" and request.url.path == "/repos/owner/404":
            return httpx.Response(404, json={"message": "Not Found"})
        if host == "api.github.com" and request.url.path.startswith("/repos/"):
            return httpx.Response(200, json={"full_name": "owner/repo", "stargazers_count": 42})
        if host == "api.notion.com" and request.url.path.startswith("/v1/pages/"):
            return httpx.Response(200, json={"id": "pg_1", "object": "page"})
        if host == "api.notion.com":
            return httpx.Response(
                200,
                json={"results": [{"id": "pg_1", "object": "page", "url": "https://notion.so/1"}]},
            )
        if host == "api.linear.app":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "issueSearch": {
                            "nodes": [{"id": "1", "identifier": "AI-1", "title": "Ship Studio"}]
                        }
                    }
                },
            )
        if host == "www.googleapis.com":
            return httpx.Response(
                200,
                json={"files": [{"id": "drv_1", "name": "Spec.md", "webViewLink": "https://x"}]},
            )
        if host == "api.vercel.com" and request.url.path.startswith("/v9/projects/"):
            return httpx.Response(200, json={"id": "prj_1", "name": "ai2027-web"})
        if host == "api.vercel.com":
            return httpx.Response(200, json={"projects": [{"id": "prj_1", "name": "ai2027-web"}]})
        return httpx.Response(404, json={"error": "not found"})

    mock_http = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    ctx = _make_ctx(tmp_path, client=mock_http)
    rt = SandboxToolRuntime()

    diff_res = await rt.execute(ToolInvocation(tool="workspace.git_diff", args={"path": "."}), ctx)
    assert diff_res.ok is True

    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    absent = await ctx.connector_client.search("notion", "roadmap")
    assert absent.ok is False and absent.configured is False

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_mocktoken1234567890")
    monkeypatch.setenv("NOTION_TOKEN", "secret_notion123")
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_123")
    monkeypatch.setenv("GOOGLE_ACCESS_TOKEN", "ya29.mock")
    monkeypatch.setenv("VERCEL_TOKEN", "ver_123")

    gh_tool = await rt.execute(
        ToolInvocation(
            tool="connector.search", args={"connector": "github", "query": "ai-2027", "limit": 2}
        ),
        ctx,
    )
    assert gh_tool.ok is True and "ai-2027" in gh_tool.output

    gh_raw = await ctx.connector_client.search("github", "ai-2027")
    assert gh_raw.ok is True
    assert "gsk_FAKE" not in json.dumps(gh_raw.model_dump())

    gh_read = await rt.execute(
        ToolInvocation(
            tool="connector.read", args={"connector": "github", "resource_id": "owner/repo"}
        ),
        ctx,
    )
    assert gh_read.ok is True and "stargazers_count" in gh_read.output

    notion_read = await ctx.connector_client.read("notion", "pg_1")
    assert notion_read.ok is True and "pg_1" in notion_read.content

    vercel_read = await ctx.connector_client.read("vercel", "prj_1")
    assert vercel_read.ok is True

    not_found_res = await rt.execute(
        ToolInvocation(
            tool="connector.read", args={"connector": "github", "resource_id": "owner/404"}
        ),
        ctx,
    )
    assert not_found_res.ok is False and not_found_res.error is not None

    fail_http = await ctx.connector_client.read("github", "owner/repo/README.md")
    assert fail_http.ok is True

    unsupported = await ctx.connector_client.search("dropbox", "spec")
    assert unsupported.ok is False

    for cid in ("notion", "linear", "google_drive", "vercel"):
        r = await ctx.connector_client.search(cid, "studio")
        assert r.ok is True and len(r.items) == 1

    with pytest.raises(SecurityValidationError):
        await ctx.connector_client.read("github", "../escape")

    await mock_http.aclose()
