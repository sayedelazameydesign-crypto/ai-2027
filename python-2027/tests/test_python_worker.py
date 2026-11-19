from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_WORKER_SRC = Path(__file__).resolve().parents[1] / "workers" / "python-worker" / "src"
if str(_WORKER_SRC) not in sys.path:
    sys.path.insert(0, str(_WORKER_SRC))

from entry import CfResponse, WorkerEntrypoint, handle_worker_request  # noqa: E402


async def test_python_worker_health_and_protocol() -> None:
    health = await handle_worker_request(method="GET", path="/health")
    assert health.status == 200
    assert health.body["ok"] is True
    assert health.body["cost_usd"] == 0.0
    assert health.headers["x-content-type-options"] == "nosniff"

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

    bad_proto = await handle_worker_request(method="POST", path="/v1/protocol")
    assert bad_proto.status == 405

    not_found = await handle_worker_request(method="GET", path="/missing")
    assert not_found.status == 404


async def test_python_worker_free_first_failover_and_retry() -> None:
    calls: list[str] = []

    async def fake_fetch(
        url: str, headers: Mapping[str, str], body_json: str, timeout_s: float
    ) -> tuple[int, dict[str, Any]]:
        calls.append(url)
        if "googleapis.com" in url:
            return 429, {"error": "quota"}
        if "groq.com" in url:
            return 200, {"choices": [{"message": {"content": "groq worker reply"}}]}
        return 500, {}

    env = {
        "GEMINI_API_KEY": "AIza-fake-worker-key",
        "GROQ_API_KEY": "gsk-fake-worker-key",
        "OPENROUTER_API_KEY": "sk-or-fake-worker-key",
    }
    res = await handle_worker_request(
        method="POST",
        path="/v1/route",
        raw_body=json.dumps({"prompt": "Hello Python Worker"}),
        env=env,
        fetcher=fake_fetch,
    )
    assert res.status == 200
    assert res.body["provider"] == "groq"
    assert res.body["text"] == "groq worker reply"
    assert res.body["cost_usd"] == 0.0
    assert len(res.body["attempts"]) == 3  # 2 Gemini retries + 1 Groq success


async def test_python_worker_openrouter_and_deterministic_fallback() -> None:
    async def all_bad_fetch(
        url: str, headers: Mapping[str, str], body_json: str, timeout_s: float
    ) -> tuple[int, dict[str, Any]]:
        if "openrouter.ai" in url:
            return 200, {"choices": [{"message": {"content": "openrouter reply"}}]}
        if "groq.com" in url:
            raise TimeoutError("timed out")
        return 401, {"error": "bad key"}

    class _AttrEnv:
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
    )
    assert offline.status == 200
    assert offline.body["provider"] == "deterministic"
    assert offline.body["simulated"] is True


async def test_python_worker_validation_and_bearer_auth() -> None:
    env = {"WORKER_AUTH_TOKEN": "worker-token-fixture"}
    unauth = await handle_worker_request(
        method="POST",
        path="/v1/route",
        raw_body=json.dumps({"prompt": "hi"}),
        env=env,
    )
    assert unauth.status == 401

    wrong_method = await handle_worker_request(method="GET", path="/v1/route", env=env)
    assert wrong_method.status == 405

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


async def test_python_worker_gemini_direct_and_malformed_payloads() -> None:
    async def gemini_ok(
        url: str, headers: Mapping[str, str], body_json: str, timeout_s: float
    ) -> tuple[int, dict[str, Any]]:
        assert headers["x-goog-api-key"] == "AIza-direct-key"
        return 200, {"candidates": [{"content": {"parts": [{"text": "gemini direct ok"}]}}]}

    res = await handle_worker_request(
        method="POST",
        path="/v1/route",
        raw_body=json.dumps({"prompt": "Gemini fast path"}),
        env={"GEMINI_API_KEY": "AIza-direct-key"},
        fetcher=gemini_ok,
    )
    assert res.status == 200
    assert res.body["provider"] == "gemini"
    assert res.body["text"] == "gemini direct ok"

    async def broken_json(
        url: str, headers: Mapping[str, str], body_json: str, timeout_s: float
    ) -> tuple[int, dict[str, Any]]:
        if "groq.com" in url:
            raise OSError("network dropped")
        return 200, {"unexpected": "shape"}

    fallback = await handle_worker_request(
        method="POST",
        path="/v1/route",
        raw_body=json.dumps({"prompt": "Bad payloads all around"}),
        env={
            "GEMINI_API_KEY": "AIza-direct-key",
            "NVIDIA_API_KEY": "nvapi-direct-key",
            "GROQ_API_KEY": "gsk-direct-key",
            "OPENROUTER_API_KEY": "sk-or-direct-key",
        },
        fetcher=broken_json,
    )
    assert fallback.status == 200
    assert fallback.body["provider"] == "deterministic"

    async def nvidia_ok(
        url: str, headers: Mapping[str, str], body_json: str, timeout_s: float
    ) -> tuple[int, dict[str, Any]]:
        assert "nvidia.com" in url
        assert headers["authorization"] == "Bearer nvapi-worker-key"
        return 200, {"choices": [{"message": {"content": "nvidia worker ok"}}]}

    nvidia_res = await handle_worker_request(
        method="POST",
        path="/v1/route",
        raw_body=json.dumps({"prompt": "NVIDIA Actions key route"}),
        env={"NVIDIA_API_KEY": "nvapi-worker-key", "NVIDIA_MODEL": ""},
        fetcher=nvidia_ok,
    )
    assert nvidia_res.status == 200
    assert nvidia_res.body["provider"] == "nvidia"
    assert nvidia_res.body["text"] == "nvidia worker ok"
