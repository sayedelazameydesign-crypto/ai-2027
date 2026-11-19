from __future__ import annotations

from pathlib import Path

import httpx

from ai2027_protocol.server import create_app

_REPO_ROOT = Path(__file__).resolve().parents[2]


async def test_studio_api_end_to_end_lifecycle(tmp_path: Path) -> None:
    app = create_app(workspace_root=_REPO_ROOT, studio_dir=tmp_path / "studio")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        home = await client.get("/")
        assert home.status_code == 200
        assert "AI-2027" in home.text

        studio_page = await client.get("/studio")
        assert studio_page.status_code == 200

        status = await client.get("/v1/studio/status")
        assert status.status_code == 200
        st_json = status.json()
        assert st_json["kernel_available"] is True
        assert st_json["cost_usd"] == 0.0

        created = await client.post("/v1/studio/sessions", json={"title": "API Run"})
        assert created.status_code == 200
        sid = created.json()["session_id"]

        listed = await client.get("/v1/studio/sessions")
        assert listed.status_code == 200
        assert any(s["session_id"] == sid for s in listed.json())

        run_resp = await client.post(
            f"/v1/studio/sessions/{sid}/run",
            json={
                "task": "Build SVG chart and propose a Notion update",
                "max_steps": 4,
                "preferred_provider": "deterministic",
            },
        )
        assert run_resp.status_code == 200
        assert run_resp.headers.get("x-trace-id")
        run_data = run_resp.json()
        assert run_data["status"] == "completed"
        assert len(run_data["artifacts"]) >= 2
        assert len(run_data["approvals"]) == 1
        approval_id = run_data["approvals"][0]

        fetched = await client.get(f"/v1/studio/sessions/{sid}")
        assert fetched.status_code == 200
        assert fetched.json()["session_id"] == sid

        arts = await client.get(f"/v1/studio/sessions/{sid}/artifacts")
        assert arts.status_code == 200
        assert arts.json()["ledger_events"] >= 1

        html_art = await client.get(f"/v1/studio/sessions/{sid}/artifacts/deliverable.html")
        assert html_art.status_code == 200
        assert html_art.headers.get("content-security-policy") == "sandbox allow-scripts"
        assert "L0 SHA-256 Verified Artifact" in html_art.text

        traversal = await client.get(f"/v1/studio/sessions/{sid}/artifacts/..%2f..%2fetc/passwd")
        assert traversal.status_code == 400

        missing_art = await client.get(f"/v1/studio/sessions/{sid}/artifacts/missing.txt")
        assert missing_art.status_code == 404

        stream_resp = await client.post(
            f"/v1/studio/sessions/{sid}/stream",
            json={
                "task": "Inspect python-2027",
                "max_steps": 3,
                "preferred_provider": "deterministic",
            },
        )
        assert stream_resp.status_code == 200
        assert "text/event-stream" in stream_resp.headers["content-type"]
        assert "event: final" in stream_resp.text

        stop_resp = await client.post(f"/v1/studio/sessions/{sid}/stop")
        assert stop_resp.status_code == 200
        assert stop_resp.json()["abort_requested"] is True

        apprs = await client.get("/v1/studio/approvals", params={"session_id": sid})
        assert apprs.status_code == 200
        assert len(apprs.json()) == 1

        ok_appr = await client.post(
            f"/v1/studio/approvals/{approval_id}/approve",
            json={"note": "Verified by reviewer"},
        )
        assert ok_appr.status_code == 200
        assert ok_appr.json()["status"] == "approved"

        rej_appr = await client.post(
            f"/v1/studio/approvals/{approval_id}/reject",
            json={"note": "Changed mind"},
        )
        assert rej_appr.status_code == 200
        assert rej_appr.json()["status"] == "rejected"

        mem_search = await client.get("/v1/studio/memory/search", params={"q": "deliverable"})
        assert mem_search.status_code == 200


async def test_studio_api_read_only_and_error_guards(tmp_path: Path) -> None:
    ro_app = create_app(workspace_root=_REPO_ROOT, studio_dir=tmp_path / "ro", read_only=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=ro_app), base_url="http://testserver"
    ) as client:
        blocked_create = await client.post("/v1/studio/sessions", json={"title": "blocked"})
        assert blocked_create.status_code == 403
        assert (await client.get("/v1/studio/sessions/nonexistent_01")).status_code == 404
        assert (await client.get("/v1/studio/sessions/bad!id")).status_code == 400
        blocked_run = await client.post(
            "/v1/studio/sessions/sess_missing01/run",
            json={"task": "blocked"},
        )
        assert blocked_run.status_code == 403
        blocked_stream = await client.post(
            "/v1/studio/sessions/sess_missing01/stream",
            json={"task": "blocked"},
        )
        assert blocked_stream.status_code == 403
        blocked_approve = await client.post("/v1/studio/approvals/0123456789abcdef/approve")
        assert blocked_approve.status_code == 403
        blocked_reject = await client.post("/v1/studio/approvals/0123456789abcdef/reject")
        assert blocked_reject.status_code == 403
