from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ai2027_protocol.ai_providers import (
    DeterministicFallbackProvider,
    OllamaLocalProvider,
    ProviderError,
)
from ai2027_protocol.auditor import ProtocolAuditor
from ai2027_protocol.bridges import ConnectorsBridge, KernelSandboxBridge, MemoryBridge
from ai2027_protocol.budget import BudgetGuard, BudgetPolicy
from ai2027_protocol.celia_memory import (
    CeliaMemoryStore,
    OllamaEmbeddingBackend,
    chunk_document,
)
from ai2027_protocol.cli import main
from ai2027_protocol.contracts import (
    BillingTier,
    CostStatus,
    PromptRequest,
    ProviderKind,
    StrictModel,
)
from ai2027_protocol.ollama_layer import (
    AsyncOllamaTransport,
    HttpxOllamaTransport,
    OllamaHttpResult,
    OllamaRuntimeClient,
    create_ollama_cloud_provider,
    resolve_hardware_profile,
)
from ai2027_protocol.ollama_readiness import inspect_host_readiness, verify_ollama_capabilities
from ai2027_protocol.planner_engine import FreeFirstAgentPlanner
from ai2027_protocol.router import FreeFirstRouter
from ai2027_protocol.security import SecurityValidationError
from ai2027_protocol.server import create_app
from ai2027_protocol.tools import SandboxToolRuntime, ToolContext, ToolInvocation

_REPO_ROOT = Path(__file__).resolve().parents[2]


class _StubPlanOutput(StrictModel):
    status: str
    steps: list[str]


class _FakeOllamaTransport(AsyncOllamaTransport):
    def __init__(
        self,
        *,
        tags: list[dict[str, Any]] | None = None,
        ps: list[dict[str, Any]] | None = None,
        chat_reply: dict[str, Any] | None = None,
        embed_vectors: list[list[float]] | None = None,
        fail_tags: bool = False,
        fail_chat: bool = False,
    ) -> None:
        self.tags_payload = (
            tags
            if tags is not None
            else [
                {
                    "name": "qwen3.5:0.8b",
                    "size": 1_250_000_000,
                    "details": {
                        "parameter_size": "0.8B",
                        "quantization_level": "Q4_K_M",
                        "family": "qwen35",
                    },
                }
            ]
        )
        self.ps_payload = ps if ps is not None else []
        self.chat_reply = (
            chat_reply
            if chat_reply is not None
            else {
                "message": {
                    "content": json.dumps(
                        {
                            "status": "ok",
                            "language": "ar-en",
                            "summary": "Local Ollama structured reply verified",
                        }
                    ),
                    "tool_calls": [
                        {
                            "function": {
                                "name": "workspace.list_files",
                                "arguments": '{"path": "."}',
                            }
                        },
                        {
                            "function": {
                                "name": "powershell.exec",
                                "arguments": {"cmd": "Remove-Item -Recurse"},
                            }
                        },
                        {
                            "function": {
                                "name": "workspace.read_file",
                                "arguments": "{broken-json",
                            }
                        },
                    ],
                }
            }
        )
        self.embed_vectors = embed_vectors
        self.fail_tags = fail_tags
        self.fail_chat = fail_chat
        self.posts: list[tuple[str, dict[str, Any]]] = []

    async def get_json(
        self, url: str, *, headers: Mapping[str, str], timeout_s: float
    ) -> OllamaHttpResult:
        if self.fail_tags:
            raise ProviderError("network_error", "Ollama unreachable", retryable=True)
        if url.endswith("/api/tags"):
            return OllamaHttpResult(status_code=200, payload={"models": self.tags_payload})
        if url.endswith("/api/ps"):
            return OllamaHttpResult(status_code=200, payload={"models": self.ps_payload})
        return OllamaHttpResult(status_code=200, payload={})

    async def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any],
        timeout_s: float,
    ) -> OllamaHttpResult:
        self.posts.append((url, dict(json_body)))
        if self.fail_chat and url.endswith("/api/chat"):
            raise ProviderError("http_500", "Simulated chat error", retryable=True)
        if url.endswith("/api/embed"):
            inputs = json_body.get("input") or []
            if self.embed_vectors is not None and self.embed_vectors:
                vecs = [self.embed_vectors[i % len(self.embed_vectors)] for i in range(len(inputs))]
            else:
                vecs = [[float(i + 1), 1.0, 0.5, 0.25] for i, _ in enumerate(inputs)]
            return OllamaHttpResult(status_code=200, payload={"embeddings": vecs})
        if url.endswith("/v1/chat/completions"):
            return OllamaHttpResult(
                status_code=200,
                payload={"choices": [{"message": {"content": "ollama cloud reply"}}]},
            )
        return OllamaHttpResult(status_code=200, payload=self.chat_reply)


async def test_ollama_4gb_defaults_and_cloud_budget_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_NUM_CTX", raising=False)
    local = OllamaLocalProvider(base_url="http://127.0.0.1:11434")
    assert local.model == "qwen3.5:0.8b"
    assert local.num_ctx == 2048
    assert local.billing_tier == BillingTier.LOCAL

    transport = _FakeOllamaTransport()
    cloud = create_ollama_cloud_provider(
        api_key="fixture-key",
        model="qwen3.5:0.8b",
        transport=transport,
    )
    assert cloud.is_remote is True
    assert cloud.is_configured() is True
    assert cloud.billing_tier == BillingTier.UNKNOWN_COST

    zero_router = FreeFirstRouter(
        providers=[cloud, DeterministicFallbackProvider()],
        budget_guard=BudgetGuard(BudgetPolicy(max_spend_usd=0.0)),
    )
    resp = await zero_router.route(PromptRequest(prompt="Check Ollama Cloud guard"))
    assert resp.provider == ProviderKind.DETERMINISTIC
    assert resp.attempts[0].error_code == "budget_blocked"
    assert resp.attempts[0].cost_status == CostStatus.UNKNOWN_COST_BLOCKED
    assert transport.posts == []

    paid_router = FreeFirstRouter(
        providers=[cloud],
        budget_guard=BudgetGuard(BudgetPolicy(max_spend_usd=1.0, allow_unknown_cost=True)),
    )
    cloud_resp = await paid_router.route(PromptRequest(prompt="Allow override"))
    assert cloud_resp.text == "ollama cloud reply"

    unconfigured_cloud = create_ollama_cloud_provider(api_key="", transport=transport)
    with pytest.raises(ProviderError) as err:
        await unconfigured_cloud.complete(PromptRequest(prompt="hi"), timeout_s=2.0)
    assert err.value.code == "not_configured"


async def test_ollama_runtime_tags_ps_structured_tools_and_embeddings() -> None:
    profile = resolve_hardware_profile("4gb-laptop")
    assert profile.chat_model == "qwen3.5:0.8b"
    assert profile.coder_model == "qwen2.5-coder:0.5b"
    assert profile.embed_model == "embeddinggemma"
    assert profile.num_ctx == 2048

    transport = _FakeOllamaTransport(
        ps=[
            {
                "name": "qwen3.5:0.8b",
                "size": 1_200_000_000,
                "size_vram": 0,
                "context_length": 2048,
            }
        ]
    )
    client = OllamaRuntimeClient(
        base_url="http://127.0.0.1:11434",
        profile=profile,
        transport=transport,
    )
    tags = await client.list_tags()
    assert len(tags) == 1 and tags[0].quantization_level == "Q4_K_M"

    mem_status = await client.check_memory_guard()
    assert mem_status.running_count == 1
    assert mem_status.safe_to_load_another is False

    structured_transport = _FakeOllamaTransport(
        chat_reply={"message": {"content": '{"status": "ok", "steps": ["step1", "step2"]}'}}
    )
    s_client = OllamaRuntimeClient(
        base_url="http://127.0.0.1:11434", profile=profile, transport=structured_transport
    )
    parsed = await s_client.chat_structured("Plan", _StubPlanOutput, system_prompt="Be concise")
    assert parsed.steps == ["step1", "step2"]
    assert structured_transport.posts[0][1]["options"]["num_ctx"] == 2048

    bad_transport = _FakeOllamaTransport(chat_reply={"message": {"content": '{"status": 123}'}})
    bad_client = OllamaRuntimeClient(
        base_url="http://127.0.0.1:11434", profile=profile, transport=bad_transport
    )
    with pytest.raises(ProviderError) as err:
        await bad_client.chat_structured("Bad", _StubPlanOutput)
    assert err.value.code == "invalid_structured_output"

    specs = SandboxToolRuntime().specs()
    tool_turn = await client.chat_with_tools("Inspect repo", specs, system_prompt="Safe only")
    assert [c.tool for c in tool_turn.proposed_calls] == ["workspace.list_files"]
    assert "powershell.exec" in tool_turn.rejected_calls
    assert "workspace.read_file:invalid_json_args" in tool_turn.rejected_calls
    assert tool_turn.all_proposals_safe is False

    assert await client.embed([]) == []
    vecs = await client.embed(["CeliaOS local memory"])
    assert len(vecs) == 1
    assert abs(sum(v * v for v in vecs[0]) - 1.0) < 1e-4
    assert transport.posts[-1][1]["keep_alive"] == 0

    offline = OllamaRuntimeClient(base_url="")
    assert offline.is_configured() is False
    for coro in (
        offline.list_tags(),
        offline.list_running(),
        offline.chat_structured("x", _StubPlanOutput),
        offline.chat_with_tools("x", specs),
        offline.embed(["x"]),
    ):
        with pytest.raises(ProviderError):
            await coro
    with pytest.raises(ValueError):
        await client.chat_structured("x", _StubPlanOutput, model="bad model!!")


async def test_httpx_ollama_transport_errors_and_streaming() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3.5:0.8b"}]})
        if path == "/api/chat":
            body = (
                '{"message":{"content":"Hel"}}\n'
                '{"message":{"content":"lo"},"done":false}\n'
                '{"done":true}\n'
            )
            return httpx.Response(200, content=body.encode("utf-8"))
        if path == "/api/post-ok":
            return httpx.Response(200, json={"ok": True})
        if path == "/api/bad-json":
            return httpx.Response(200, content=b"{invalid")
        if path == "/api/list-json":
            return httpx.Response(200, json=[1, 2, 3])
        if path == "/api/huge":
            return httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 5))
        if path == "/api/bad-status":
            return httpx.Response(503, json={"error": "busy"})
        if path == "/api/timeout":
            raise httpx.ReadTimeout("slow", request=request)
        if path == "/api/neterr":
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(404, json={"error": "missing"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as http_client:
        transport = HttpxOllamaTransport(client=http_client)
        res = await transport.get_json("http://127.0.0.1:11434/api/tags", headers={}, timeout_s=2.0)
        assert res.payload["models"][0]["name"] == "qwen3.5:0.8b"

        post_res = await transport.post_json(
            "http://127.0.0.1:11434/api/post-ok",
            headers={},
            json_body={"ping": True},
            timeout_s=2.0,
        )
        assert post_res.payload["ok"] is True

        for endpoint, expected_code, retryable in (
            ("/api/bad-status", "http_503", True),
            ("/api/missing", "http_404", False),
            ("/api/bad-json", "invalid_json", False),
            ("/api/list-json", "invalid_json", False),
            ("/api/huge", "response_too_large", False),
            ("/api/timeout", "timeout", True),
            ("/api/neterr", "network_error", True),
        ):
            with pytest.raises(ProviderError) as exc_info:
                await transport.get_json(
                    f"http://127.0.0.1:11434{endpoint}", headers={}, timeout_s=2.0
                )
            assert exc_info.value.code == expected_code
            assert exc_info.value.retryable is retryable

        client = OllamaRuntimeClient(base_url="http://127.0.0.1:11434", transport=transport)
        pieces: list[str] = []
        async for delta in client.stream_chat_deltas("Say hello", http_client=http_client):
            pieces.append(delta)
        assert "".join(pieces) == "Hello"


async def test_celia_memory_persistent_rag_bilingual_and_tool(tmp_path: Path) -> None:
    assert chunk_document("empty.md", "   \n  ") == []

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "celia_notes.md").write_text(
        "# CeliaOS Policy Gate\n\n"
        "Ollama generates embeddings locally while agi-system kernel verifies SHA-256.\n\n"
        "## العربية والذاكرة التراكمية\n\n"
        "تخزين الملاحظات في SQLite يضمن استرجاع السياق بعد إعادة التشغيل.\n",
        encoding="utf-8",
    )
    chunks = chunk_document("docs/celia_notes.md", (docs_dir / "celia_notes.md").read_text())
    assert len(chunks) >= 2

    db_file = tmp_path / "celia.sqlite3"
    fake_transport = _FakeOllamaTransport(
        embed_vectors=[[1.0, 0.0, 0.0, 0.0] for _ in range(len(chunks))]
    )
    client = OllamaRuntimeClient(base_url="http://127.0.0.1:11434", transport=fake_transport)
    backend = OllamaEmbeddingBackend(client)
    store = CeliaMemoryStore(db_file, backend=backend, allow_lexical_fallback=True)
    summary = await store.index_paths(tmp_path, ["docs"])
    assert summary.chunks_written == len(chunks)
    assert summary.is_neural is True
    assert summary.retrieval_mode == "neural_ollama"

    # Re-indexing same files reuses SHA-256 chunks cleanly
    second = await store.index_paths(tmp_path, ["docs"])
    assert second.chunks_written == 0
    assert second.chunks_reused == len(chunks)

    # Re-open SQLite offline -> proves disk persistence + honest fallback reporting
    reopened = CeliaMemoryStore(db_file, backend=None, allow_lexical_fallback=True)
    stats = await reopened.stats()
    assert stats.total_chunks == len(chunks)
    assert stats.neural_chunks == len(chunks)

    ar_hits = await reopened.search("استرجاع السياق SQLite", k=3)
    assert ar_hits and ar_hits[0].retrieval_mode == "lexical_fallback"
    assert ar_hits[0].is_neural is False

    # Strict mode refuses silent fallback when neural backend fails
    strict_store = CeliaMemoryStore(
        db_file,
        backend=OllamaEmbeddingBackend(OllamaRuntimeClient(base_url="")),
        allow_lexical_fallback=False,
    )
    with pytest.raises(ProviderError):
        await strict_store.search("SQLite")

    # Fallback mode degrades gracefully during indexing when Ollama is offline
    degraded_db = tmp_path / "celia_degraded.sqlite3"
    degraded_store = CeliaMemoryStore(
        degraded_db,
        backend=OllamaEmbeddingBackend(OllamaRuntimeClient(base_url="")),
        allow_lexical_fallback=True,
    )
    deg_sum = await degraded_store.index_paths(tmp_path, ["docs"])
    assert deg_sum.is_neural is False
    assert deg_sum.retrieval_mode == "lexical_fallback"
    deg_hits = await degraded_store.search("CeliaOS Policy Gate", k=2)
    assert deg_hits and deg_hits[0].is_neural is False

    # Neural query against neural chunks exercises direct neural cosine path
    neural_hits = await store.search("CeliaOS Policy Gate", k=2)
    assert neural_hits and neural_hits[0].is_neural is True
    assert neural_hits[0].retrieval_mode == "neural_ollama"

    with pytest.raises(SecurityValidationError):
        await reopened.search("   ")

    # Verify memory.rag_search tool via SandboxToolRuntime
    studio_dir = tmp_path / "studio"
    sess_dir = studio_dir / "sessions"
    sess_dir.mkdir(parents=True)
    ctx = ToolContext(
        session_id="sess12345678",
        workspace_root=tmp_path,
        sandbox_dir=sess_dir / "sandbox",
        ledger_path=sess_dir / "ledger.jsonl",
        sessions_root=sess_dir,
        kernel_bridge=KernelSandboxBridge(tmp_path),
        memory_bridge=MemoryBridge(
            workspace_root=tmp_path,
            db_path=studio_dir / "l1.db",
            sandbox_root=sess_dir / "sandbox",
        ),
        connectors_bridge=ConnectorsBridge(store_path=studio_dir / "approvals.json"),
        auditor=ProtocolAuditor(workspace_root=tmp_path),
    )
    tool_out = await SandboxToolRuntime().execute(
        ToolInvocation(
            tool="memory.rag_search",
            args={"query": "SHA-256 Policy Gate", "paths": ["docs"]},
        ),
        ctx,
    )
    assert tool_out.ok is True
    assert "celia_notes.md" in tool_out.output


async def test_phase0_readiness_and_structured_planner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snap = inspect_host_readiness(_REPO_ROOT)
    assert snap.cpu_count >= 1
    assert snap.free_disk_gb >= 0.0

    # Exercise low-RAM and low-disk warnings honestly
    monkeypatch.setattr("ai2027_protocol.ollama_readiness._read_ram_gb", lambda: (4.0, 0.9))
    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda _p: shutil._ntuple_diskusage(10 * 1024**3, 8 * 1024**3, 2 * 1024**3),
    )
    tight_snap = inspect_host_readiness(_REPO_ROOT)
    assert tight_snap.recommended_profile == "4gb-laptop"
    assert len(tight_snap.ram_advisories) >= 2

    monkeypatch.setattr("ai2027_protocol.ollama_readiness._read_ram_gb", lambda: (16.0, 9.5))
    big_snap = inspect_host_readiness(_REPO_ROOT)
    assert big_snap.recommended_profile == "8gb-standard"
    monkeypatch.undo()

    offline_rep = await verify_ollama_capabilities(
        client=OllamaRuntimeClient(base_url=""),
        workspace_root=_REPO_ROOT,
        run_live_inference=True,
    )
    assert offline_rep.verification_status == "LOCAL_GUARDS_ONLY"
    assert offline_rep.endpoint_reachable is False

    unreachable_rep = await verify_ollama_capabilities(
        client=OllamaRuntimeClient(
            base_url="http://127.0.0.1:11434",
            transport=_FakeOllamaTransport(fail_tags=True),
        ),
        workspace_root=_REPO_ROOT,
    )
    assert unreachable_rep.verification_status == "UNREACHABLE"

    failing_probe_rep = await verify_ollama_capabilities(
        client=OllamaRuntimeClient(
            base_url="http://127.0.0.1:11434",
            transport=_FakeOllamaTransport(fail_chat=True),
        ),
        workspace_root=_REPO_ROOT,
        run_live_inference=True,
    )
    assert failing_probe_rep.verification_status == "FAILED_PROBES"

    safe_transport = _FakeOllamaTransport(
        chat_reply={
            "message": {
                "content": json.dumps(
                    {
                        "status": "ok",
                        "language": "ar-en",
                        "summary": "Verified local Ollama probes cleanly",
                    }
                ),
                "tool_calls": [
                    {"function": {"name": "workspace.list_files", "arguments": {"path": "."}}}
                ],
            }
        }
    )
    live_rep = await verify_ollama_capabilities(
        client=OllamaRuntimeClient(base_url="http://127.0.0.1:11434", transport=safe_transport),
        workspace_root=_REPO_ROOT,
        run_live_inference=True,
    )
    assert live_rep.verification_status == "VERIFIED"
    assert live_rep.endpoint_reachable is True

    plan_transport = _FakeOllamaTransport(
        chat_reply={
            "message": {
                "content": json.dumps(
                    {
                        "thought": "Structured Ollama step",
                        "plan": ["Inspect"],
                        "actions": [],
                        "final_answer": "Structured Ollama complete",
                    }
                )
            }
        }
    )
    ol_client = OllamaRuntimeClient(base_url="http://127.0.0.1:11434", transport=plan_transport)
    planner = FreeFirstAgentPlanner(
        FreeFirstRouter(providers=[OllamaLocalProvider(base_url="http://127.0.0.1:11434")]),
        ollama_client=ol_client,
    )
    decision, prov, model, sim = await planner.decide(
        task="Inspect repo",
        step_num=1,
        max_steps=3,
        history_summary="",
        artifacts=(),
        memory_context="",
        tool_executor=SandboxToolRuntime(),
        preferred_provider=ProviderKind.OLLAMA,
        allow_remote=False,
    )
    assert prov == "ollama"
    assert model == "qwen3.5:0.8b"
    assert sim is False
    assert decision.final_answer == "Structured Ollama complete"


def test_server_and_cli_ollama_and_celia_rag(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "guide.md").write_text(
        "# Guide\nCelia Memory stores RAG vectors.\n", encoding="utf-8"
    )
    db_file = tmp_path / "cli_celia.sqlite3"

    assert main(["--root", str(tmp_path), "ollama-check"]) == 0
    assert main(["--root", str(tmp_path), "ollama-check", "--json"]) == 0
    rag_args = ["--root", str(tmp_path), "celia-memory"]
    assert main([*rag_args, "index", "--db", str(db_file), "--paths", "docs"]) == 0
    assert main([*rag_args, "search", "--db", str(db_file), "--query", "RAG vectors"]) == 0
    assert main([*rag_args, "stats", "--db", str(db_file)]) == 0

    app = create_app(workspace_root=tmp_path, studio_dir=tmp_path / "studio", read_only=False)
    with TestClient(app) as client:
        st = client.get("/v1/ollama/status")
        assert st.status_code == 200
        assert st.json()["verification_status"] == "LOCAL_GUARDS_ONLY"

        idx = client.post("/v1/memory/rag/index", json={"paths": ["docs"]})
        assert idx.status_code == 200
        assert idx.json()["total_chunks"] >= 1

        srch = client.post("/v1/memory/rag/search", json={"query": "RAG vectors", "k": 3})
        assert srch.status_code == 200
        assert len(srch.json()) >= 1

        rag_stats = client.get("/v1/memory/rag/stats")
        assert rag_stats.status_code == 200
        assert rag_stats.json()["total_chunks"] >= 1

    ro_app = create_app(workspace_root=tmp_path, studio_dir=tmp_path / "studio_ro", read_only=True)
    with TestClient(ro_app) as ro_client:
        assert ro_client.post("/v1/memory/rag/index", json={"paths": ["docs"]}).status_code == 403
