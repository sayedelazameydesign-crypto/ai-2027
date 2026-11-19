from __future__ import annotations

from pathlib import Path

import httpx

from ai2027_protocol.contracts import PromptRequest, ProviderKind
from ai2027_protocol.router import FreeFirstRouter
from ai2027_protocol.server import create_app

_REPO_ROOT = Path(__file__).resolve().parents[2]


class _FailingProvider:
    kind = ProviderKind.GEMINI
    model = "broken"
    is_remote = False

    def is_configured(self) -> bool:
        return True

    async def complete(self, request: PromptRequest, *, timeout_s: float) -> str:
        raise RuntimeError("hard failure")


async def test_fastapi_server_endpoints() -> None:
    app = create_app(workspace_root=_REPO_ROOT)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        health = await client.get("/health")
        assert health.status_code == 200
        assert health.json()["ok"] is True

        proto = await client.get("/v1/protocol")
        assert proto.status_code == 200
        assert len(proto.json()["rules"]) == 10

        provs = await client.get("/v1/providers")
        assert provs.status_code == 200
        assert any(p["provider"] == "nvidia" for p in provs.json())

        routed = await client.post(
            "/v1/route", json={"prompt": "Hello server", "allow_remote": False}
        )
        assert routed.status_code == 200
        assert routed.headers.get("x-trace-id")
        assert routed.json()["cost_usd"] == 0.0

        batch = await client.post(
            "/v1/route/batch",
            json={"requests": [{"prompt": "A"}, {"prompt": "B"}], "max_concurrency": 2},
        )
        assert batch.status_code == 200
        assert len(batch.json()) == 2

        audit_ok = await client.post("/v1/audit", json={"target_path": "python-2027"})
        assert audit_ok.status_code == 200
        assert audit_ok.json()["scanned_projects"] == 1

        audit_bad = await client.post("/v1/audit", json={"target_path": "../../etc"})
        assert audit_bad.status_code == 400

        audit_missing = await client.post("/v1/audit", json={"target_path": "no_such_dir_xyz"})
        assert audit_missing.status_code == 404

        traces = await client.get("/v1/traces")
        assert traces.status_code == 200
        assert len(traces.json()) >= 1

        bench = await client.get("/v1/bench")
        assert bench.status_code == 200
        assert len(bench.json()["comparisons"]) == 2


async def test_fastapi_server_read_only_and_503_handling() -> None:
    ro_app = create_app(read_only=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=ro_app), base_url="http://testserver"
    ) as client:
        r1 = await client.post("/v1/route", json={"prompt": "blocked"})
        assert r1.status_code == 403
        r2 = await client.post("/v1/route/batch", json={"requests": [{"prompt": "blocked"}]})
        assert r2.status_code == 403

    fail_router = FreeFirstRouter(providers=[_FailingProvider()])
    fail_app = create_app(router=fail_router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fail_app), base_url="http://testserver"
    ) as client:
        r3 = await client.post("/v1/route", json={"prompt": "will fail"})
        assert r3.status_code == 503
        r4 = await client.post("/v1/route/batch", json={"requests": [{"prompt": "will fail"}]})
        assert r4.status_code == 503
