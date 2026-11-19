from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_WORKER_DIR = Path(__file__).resolve().parents[1] / "workers" / "python-worker"
_WORKER_SRC = _WORKER_DIR / "src"
if str(_WORKER_SRC) not in sys.path:
    sys.path.insert(0, str(_WORKER_SRC))

from entry import CfResponse, WorkerEntrypoint, handle_worker_request  # noqa: E402
from worker_guard import read_bounded_request_body  # noqa: E402


async def test_python_worker_health_protocol_and_fail_closed_by_default() -> None:
    health = await handle_worker_request(method="GET", path="/health")
    assert health.status == 200
    assert health.body["ok"] is True
    assert health.body["cost_usd"] == 0.0
    assert health.body["runtime"] == "cloudflare-python-workers-314"
    assert health.headers["x-content-type-options"] == "nosniff"
    assert health.headers["traceparent"].startswith("00-")

    bad_health = await handle_worker_request(method="POST", path="/health")
    assert bad_health.status == 405

    proto = await handle_worker_request(method="GET", path="/v1/protocol")
    assert proto.status == 200
    assert proto.body["fallback_order"] == [
        "gemini",
        "nvidia",
        "groq",
        "openrouter",
        "deterministic",
    ]
    assert (await handle_worker_request(method="POST", path="/v1/protocol")).status == 405
    assert (await handle_worker_request(method="GET", path="/missing")).status == 404

    # P0 Fail-Closed: without WORKER_AUTH_TOKEN or explicit AI2027_PUBLIC_ROUTES, reject 401
    unauth_route = await handle_worker_request(
        method="POST",
        path="/v1/route",
        raw_body=json.dumps({"prompt": "hi"}),
        env={},
    )
    assert unauth_route.status == 401
    assert "Fail-closed" in unauth_route.body["error"]

    unauth_plan = await handle_worker_request(
        method="POST",
        path="/v1/agent/plan",
        raw_body=json.dumps({"task": "hi"}),
        env={},
    )
    assert unauth_plan.status == 401


async def test_python_worker_free_first_failover_and_retry() -> None:
    calls: list[str] = []

    async def fake_fetch(
        url: str, headers: Mapping[str, str], body_json: str, timeout_s: float
    ) -> tuple[int, dict[str, Any]]:
        calls.append(url)
        host = urlparse(url).hostname
        if host == "generativelanguage.googleapis.com":
            return 429, {"error": "quota"}
        if host == "api.groq.com":
            return 200, {"choices": [{"message": {"content": "groq worker reply"}}]}
        return 500, {}

    env = {
        "WORKER_AUTH_TOKEN": "tok-failover",
        "GEMINI_API_KEY": "AIza-fake-worker-key",
        "GROQ_API_KEY": "gsk-fake-worker-key",
        # Groq retired its free Llama IDs on 2026-08-16: the replacement model is
        # priced per token, so the free plan must be declared explicitly.
        "GROQ_MODEL": "openai/gpt-oss-20b",
        "AI2027_FREE_MODELS": "openai/gpt-oss-20b",
        "OPENROUTER_API_KEY": "sk-or-fake-worker-key",
    }
    res = await handle_worker_request(
        method="POST",
        path="/v1/route",
        headers={"Authorization": "Bearer tok-failover"},
        raw_body=json.dumps({"prompt": "Hello Python Worker"}),
        env=env,
        fetcher=fake_fetch,
    )
    assert res.status == 200
    assert res.body["provider"] == "groq"
    assert res.body["text"] == "groq worker reply"
    assert res.body["cost_usd"] == 0.0
    assert res.body["billing_tier"] == "free_tier"
    assert res.body["cost_known"] is False
    assert res.body["cost_verified_by_invoice"] is False
    assert len(res.body["attempts"]) == 3  # 2 Gemini retries + 1 Groq success


async def test_python_worker_budget_guard_blocks_unknown_cost_openrouter() -> None:
    fetched: list[str] = []

    async def spy_fetch(
        url: str, headers: Mapping[str, str], body_json: str, timeout_s: float
    ) -> tuple[int, dict[str, Any]]:
        fetched.append(url)
        return 200, {"choices": [{"message": {"content": "should-never-run"}}]}

    env = {
        "WORKER_AUTH_TOKEN": "tok-budget",
        "OPENROUTER_API_KEY": "sk-or-paid-key-present",
        "OPENROUTER_MODEL": "openai/gpt-4o",  # Missing :free -> UNKNOWN_COST!
        "AI2027_MAX_SPEND_USD": "0",
    }
    res = await handle_worker_request(
        method="POST",
        path="/v1/route",
        headers={"Authorization": "Bearer tok-budget"},
        raw_body=json.dumps({"prompt": "Test MAX_SPEND=0 block"}),
        env=env,
        fetcher=spy_fetch,
    )
    assert res.status == 200
    assert fetched == []  # Pre-flight block prevented outbound call!
    assert res.body["provider"] == "deterministic"
    assert res.body["billing_tier"] == "local"
    assert res.body["cost_known"] is True
    assert res.body["budget_summary"]["unknown_cost_blocked"] == 1
    assert res.body["attempts"][0]["error"] == "budget_blocked"


async def test_python_worker_blocks_retired_groq_model_without_calling() -> None:
    fetched: list[str] = []

    async def spy_fetch(
        url: str, headers: Mapping[str, str], body_json: str, timeout_s: float
    ) -> tuple[int, dict[str, Any]]:
        fetched.append(url)
        return 200, {"choices": [{"message": {"content": "should-never-run"}}]}

    env = {
        "WORKER_AUTH_TOKEN": "tok-retired",
        "GROQ_API_KEY": "gsk-fixture",
        "GROQ_MODEL": "llama-3.3-70b-versatile",  # retired 2026-08-16
        "AI2027_MAX_SPEND_USD": "0",
    }
    res = await handle_worker_request(
        method="POST",
        path="/v1/route",
        headers={"Authorization": "Bearer tok-retired"},
        raw_body=json.dumps({"prompt": "Retired model must not be called"}),
        env=env,
        fetcher=spy_fetch,
    )
    assert res.status == 200
    assert fetched == []
    assert res.body["provider"] == "deterministic"
    assert res.body["attempts"][0]["error"] == "model_retired"
    assert "docs/MODEL-CATALOG.md" in res.body["attempts"][0]["detail"]


async def test_python_worker_openrouter_free_and_deterministic_fallback() -> None:
    async def all_bad_fetch(
        url: str, headers: Mapping[str, str], body_json: str, timeout_s: float
    ) -> tuple[int, dict[str, Any]]:
        host = urlparse(url).hostname
        if host == "openrouter.ai":
            return 200, {"choices": [{"message": {"content": "openrouter reply"}}]}
        if host == "api.groq.com":
            raise TimeoutError("timed out")
        return 401, {"error": "bad key"}

    class _AttrEnv:
        AI2027_PUBLIC_ROUTES = "/v1/route,/v1/agent/plan"
        GEMINI_API_KEY = "AIza-fake"
        GROQ_API_KEY = "gsk-fake"
        OPENROUTER_API_KEY = "sk-or-fake"

    res = await handle_worker_request(
        method="POST",
        path="/v1/route",
        raw_body=json.dumps({"prompt": "Fall through to OpenRouter"}),
        env=_AttrEnv(),
        fetcher=all_bad_fetch,
    )
    assert res.status == 200
    assert res.body["provider"] == "openrouter"

    offline = await handle_worker_request(
        method="POST",
        path="/v1/route",
        raw_body=json.dumps({"prompt": "No keys at all", "allow_remote": False}),
        env={"AI2027_PUBLIC_ROUTES": "/v1/route"},
    )
    assert offline.status == 200
    assert offline.body["provider"] == "deterministic"
    assert offline.body["simulated"] is True


async def test_python_worker_bounded_body_pre_read_and_bearer_auth() -> None:
    env = {"WORKER_AUTH_TOKEN": "worker-token-fixture"}

    class _RejectTextRequest:
        url = "https://worker.local/v1/route"
        method = "POST"
        headers: Mapping[str, str] = {"Content-Length": "999999"}

        async def text(self) -> str:  # pragma: no cover - must never be called
            raise AssertionError("text() must not be called when Content-Length exceeds cap")

    text_out, status_cl, err_cl = await read_bounded_request_body(_RejectTextRequest())
    assert text_out is None and status_cl == 413 and "large" in err_cl.lower()

    bad_cl_req = _RejectTextRequest()
    bad_cl_req.headers = {"Content-Length": "abc"}
    text_bad, status_bad, _ = await read_bounded_request_body(bad_cl_req)
    assert text_bad is None and status_bad == 400

    class _FakeReader:
        def __init__(self) -> None:
            self.calls = 0
            self.cancelled = False

        async def read(self) -> dict[str, Any]:
            self.calls += 1
            if self.calls <= 3:
                return {"done": False, "value": b"x" * 20_000}
            return {"done": True, "value": b""}

        async def cancel(self) -> None:
            self.cancelled = True

        def releaseLock(self) -> None:  # noqa: N802
            pass

    reader = _FakeReader()

    class _FakeStream:
        def getReader(self) -> _FakeReader:  # noqa: N802
            return reader

    class _StreamingRequest:
        url = "https://worker.local/v1/route"
        method = "POST"
        headers: Mapping[str, str] = {}
        body = _FakeStream()

        async def text(self) -> str:  # pragma: no cover
            raise AssertionError("text() should not run when streaming reader is available")

    stream_res, stream_status, _ = await read_bounded_request_body(_StreamingRequest())
    assert stream_res is None and stream_status == 413 and reader.cancelled is True

    too_big = await handle_worker_request(
        method="POST",
        path="/v1/route",
        headers={"Authorization": "Bearer worker-token-fixture"},
        raw_body="x" * 40_000,
        env=env,
    )
    assert too_big.status == 413

    for bad_body in (
        "{not-json",
        "[]",
        json.dumps({"prompt": "   "}),
        json.dumps({"prompt": "ok", "max_tokens": 99999}),
        json.dumps({"prompt": "x" * 16_005}),
    ):
        bad = await handle_worker_request(
            method="POST",
            path="/v1/route",
            headers={"Authorization": "Bearer worker-token-fixture"},
            raw_body=bad_body,
            env=env,
        )
        assert bad.status == 400

    shim = CfResponse("ok", status=200, headers={"x-test": "1"})
    assert shim.status == 200
    assert WorkerEntrypoint.env is None


async def test_python_worker_gemini_direct_and_wrangler_py314_config() -> None:
    trace_parent = "00-11111111111111111111111111111111-2222222222222222-01"

    async def gemini_ok(
        url: str, headers: Mapping[str, str], body_json: str, timeout_s: float
    ) -> tuple[int, dict[str, Any]]:
        assert headers["x-goog-api-key"] == "AIza-direct-key"
        assert headers["traceparent"].startswith("00-11111111111111111111111111111111-")
        return 200, {"candidates": [{"content": {"parts": [{"text": "gemini direct ok"}]}}]}

    res = await handle_worker_request(
        method="POST",
        path="/v1/route",
        headers={
            "Authorization": "Bearer direct-tok",
            "traceparent": trace_parent,
        },
        raw_body=json.dumps({"prompt": "Gemini fast path"}),
        env={"WORKER_AUTH_TOKEN": "direct-tok", "GEMINI_API_KEY": "AIza-direct-key"},
        fetcher=gemini_ok,
    )
    assert res.status == 200
    assert res.body["provider"] == "gemini"
    assert res.body["trace_id"] == "11111111111111111111111111111111"

    plan_res = await handle_worker_request(
        method="POST",
        path="/v1/agent/plan",
        headers={"Authorization": "Bearer direct-tok"},
        raw_body=json.dumps({"task": "Inspect repo and verify SHA-256"}),
        env={"WORKER_AUTH_TOKEN": "direct-tok"},
    )
    assert plan_res.status == 200
    assert len(plan_res.body["steps"]) == 4

    wrangler_txt = (_WORKER_DIR / "wrangler.toml").read_text(encoding="utf-8")
    assert "python_workers_314" in wrangler_txt
    assert 'compatibility_date = "2026-09-08"' in wrangler_txt
    assert 'AI2027_MAX_SPEND_USD = "0"' in wrangler_txt
    pyproject_txt = (_WORKER_DIR / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.13"' in pyproject_txt
