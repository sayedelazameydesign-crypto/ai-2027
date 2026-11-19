"""Cloudflare Python Worker entrypoint for Fail-Closed Free-First AI routing at $0."""

from __future__ import annotations

import asyncio
import importlib
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from worker_guard import (
    MAX_BODY_BYTES,
    WorkerRequest,
    authorize_request,
    classify_worker_model,
    env_get,
    is_valid_key,
    max_spend_usd,
    read_bounded_request_body,
    resolve_trace_headers,
    scrub_text,
    security_headers,
)

_IN_WORKERD = False
_js_module: Any = None


class WorkerEntrypoint:
    """Local base class matching Cloudflare Workers Python WorkerEntrypoint."""

    env: object = None


class CfResponse:
    """Local HTTP response class matching Cloudflare Workers Response."""

    def __init__(
        self,
        body: str,
        *,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """Store body, status, and headers for local unit testing."""
        self.body = body
        self.status = status
        self.headers = dict(headers or {})


try:  # pragma: no cover - only active inside Cloudflare workerd runtime
    _workers_mod = importlib.import_module("workers")
    if hasattr(_workers_mod, "WorkerEntrypoint") and hasattr(_workers_mod, "Response"):
        WorkerEntrypoint = _workers_mod.WorkerEntrypoint  # type: ignore[misc]
        CfResponse = _workers_mod.Response  # type: ignore[misc]
        _js_module = importlib.import_module("js")
        _IN_WORKERD = True
except ImportError:
    pass


AsyncFetcher = Callable[
    [str, Mapping[str, str], str, float],
    Awaitable[tuple[int, dict[str, Any]]],
]


@dataclass(frozen=True)
class WorkerHttpResult:
    """Normalized HTTP response returned by the pure-Python Worker handler."""

    status: int
    headers: dict[str, str]
    body: dict[str, Any]


async def _default_workerd_fetcher(  # pragma: no cover - Pyodide js.fetch bridge
    url: str,
    headers: Mapping[str, str],
    body_json: str,
    timeout_s: float,
) -> tuple[int, dict[str, Any]]:
    if not _IN_WORKERD:
        raise RuntimeError("No fetch implementation available outside Cloudflare workerd.")
    init = {"method": "POST", "headers": dict(headers), "body": body_json}
    async with asyncio.timeout(timeout_s):
        resp = await _js_module.fetch(url, init)
        text = await resp.text()
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = {}
    return int(resp.status), parsed if isinstance(parsed, dict) else {}


def _attempt_entry(
    provider: str,
    model: str,
    tier: str,
    cost_known: bool,
    cost_status: str,
    attempt: int,
    *,
    ok: bool,
    latency_ms: float | None = None,
    error: str | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "billing_tier": tier,
        "cost_known": cost_known,
        "cost_status": cost_status,
        "attempt": attempt,
        "ok": ok,
    }
    if latency_ms is not None:
        row["latency_ms"] = latency_ms
    if error is not None:
        row["error"] = error
    if detail is not None:
        row["detail"] = detail
    return row


async def _call_with_retry(
    *,
    provider: str,
    model: str,
    billing_tier: str,
    cost_known: bool,
    cost_status: str,
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, Any],
    extract_fn: Callable[[dict[str, Any]], str | None],
    fetcher: AsyncFetcher,
    timeout_s: float = 6.0,
    max_retries: int = 1,
) -> tuple[str | None, list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    serialized = json.dumps(payload)
    for attempt in range(1, max_retries + 2):
        start = time.monotonic()
        try:
            async with asyncio.timeout(timeout_s):
                status, data = await fetcher(url, headers, serialized, timeout_s)
        except TimeoutError:
            attempts.append(
                _attempt_entry(
                    provider,
                    model,
                    billing_tier,
                    cost_known,
                    cost_status,
                    attempt,
                    ok=False,
                    error="timeout",
                )
            )
            continue
        except Exception as exc:
            attempts.append(
                _attempt_entry(
                    provider,
                    model,
                    billing_tier,
                    cost_known,
                    cost_status,
                    attempt,
                    ok=False,
                    error=scrub_text(type(exc).__name__),
                )
            )
            continue

        elapsed_ms = round((time.monotonic() - start) * 1000.0, 2)
        if status >= 500 or status in {408, 429}:
            attempts.append(
                _attempt_entry(
                    provider,
                    model,
                    billing_tier,
                    cost_known,
                    cost_status,
                    attempt,
                    ok=False,
                    latency_ms=elapsed_ms,
                    error=f"http_{status}",
                )
            )
            if attempt <= max_retries:
                await asyncio.sleep(0.02 * attempt)
            continue
        if status >= 400:
            attempts.append(
                _attempt_entry(
                    provider,
                    model,
                    billing_tier,
                    cost_known,
                    cost_status,
                    attempt,
                    ok=False,
                    latency_ms=elapsed_ms,
                    error=f"http_{status}",
                )
            )
            break
        text = extract_fn(data)
        if text:
            attempts.append(
                _attempt_entry(
                    provider,
                    model,
                    billing_tier,
                    cost_known,
                    cost_status,
                    attempt,
                    ok=True,
                    latency_ms=elapsed_ms,
                )
            )
            return text, attempts
        attempts.append(
            _attempt_entry(
                provider,
                model,
                billing_tier,
                cost_known,
                cost_status,
                attempt,
                ok=False,
                latency_ms=elapsed_ms,
                error="invalid_payload",
            )
        )
        break
    return None, attempts


def _extract_gemini(data: dict[str, Any]) -> str | None:
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        return None
    parts = candidates[0].get("content", {}).get("parts")
    if not isinstance(parts, list) or not parts or not isinstance(parts[0], dict):
        return None
    text = parts[0].get("text")
    return text.strip() if isinstance(text, str) and text.strip() else None


def _extract_openai(data: dict[str, Any]) -> str | None:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    msg = choices[0].get("message")
    text = msg.get("content") if isinstance(msg, dict) else None
    return text.strip() if isinstance(text, str) and text.strip() else None


def _provider_specs(
    env: object, prompt: str, max_tokens: int, traceparent: str
) -> list[
    tuple[
        str,
        str,
        str,
        str,
        dict[str, str],
        dict[str, Any],
        Callable[[dict[str, Any]], str | None],
    ]
]:
    def _oai_payload(model: str) -> dict[str, Any]:
        return {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        }

    gem_model = env_get(env, "GEMINI_MODEL", "gemini-2.5-flash")
    nv_model = env_get(env, "NVIDIA_MODEL", "meta/llama-3.3-70b-instruct")
    groq_model = env_get(env, "GROQ_MODEL", "openai/gpt-oss-20b")
    or_model = env_get(env, "OPENROUTER_MODEL", "openrouter/free")

    return [
        (
            "gemini",
            env_get(env, "GEMINI_API_KEY"),
            gem_model,
            f"https://generativelanguage.googleapis.com/v1beta/models/{gem_model}:generateContent",
            {
                "content-type": "application/json",
                "x-goog-api-key": env_get(env, "GEMINI_API_KEY"),
                "traceparent": traceparent,
            },
            {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": max_tokens},
            },
            _extract_gemini,
        ),
        (
            "nvidia",
            env_get(env, "NVIDIA_API_KEY"),
            nv_model,
            "https://integrate.api.nvidia.com/v1/chat/completions",
            {
                "content-type": "application/json",
                "authorization": f"Bearer {env_get(env, 'NVIDIA_API_KEY')}",
                "traceparent": traceparent,
            },
            _oai_payload(nv_model),
            _extract_openai,
        ),
        (
            "groq",
            env_get(env, "GROQ_API_KEY"),
            groq_model,
            "https://api.groq.com/openai/v1/chat/completions",
            {
                "content-type": "application/json",
                "authorization": f"Bearer {env_get(env, 'GROQ_API_KEY')}",
                "traceparent": traceparent,
            },
            _oai_payload(groq_model),
            _extract_openai,
        ),
        (
            "openrouter",
            env_get(env, "OPENROUTER_API_KEY"),
            or_model,
            "https://openrouter.ai/api/v1/chat/completions",
            {
                "content-type": "application/json",
                "authorization": f"Bearer {env_get(env, 'OPENROUTER_API_KEY')}",
                "traceparent": traceparent,
            },
            _oai_payload(or_model),
            _extract_openai,
        ),
    ]


async def handle_worker_request(
    *,
    method: str,
    path: str,
    headers: Mapping[str, str] | None = None,
    raw_body: str = "",
    env: object = None,
    fetcher: AsyncFetcher | None = None,
) -> WorkerHttpResult:
    """Process an HTTP request inside Cloudflare Python Workers or local test harness."""
    req_headers = headers or {}
    trace_id, traceparent = resolve_trace_headers(req_headers)
    upper_method = method.upper()
    authorized, auth_status, auth_err, auth_mode = authorize_request(path, req_headers, env)
    resp_headers = security_headers(trace_id, traceparent, auth_mode)

    if path == "/health":
        if upper_method not in {"GET", "HEAD"}:
            return WorkerHttpResult(405, resp_headers, {"error": "Method not allowed"})
        return WorkerHttpResult(
            200,
            resp_headers,
            {
                "ok": True,
                "service": "ai2027-python-worker",
                "runtime": "cloudflare-python-workers-314",
                "auth_mode": "bearer" if env_get(env, "WORKER_AUTH_TOKEN") else "fail-closed",
                "max_spend_usd": max_spend_usd(env),
                "cost_usd": 0.0,
                "cost_known": True,
                "trace_id": trace_id,
            },
        )

    if path == "/v1/protocol":
        if upper_method != "GET":
            return WorkerHttpResult(405, resp_headers, {"error": "Method not allowed"})
        return WorkerHttpResult(
            200,
            resp_headers,
            {
                "protocol": "Python 2027 Software Excellence Protocol",
                "tier": "Tier 2 · Fail-Closed Edge Worker (py314 target)",
                "fallback_order": ["gemini", "nvidia", "groq", "openrouter", "deterministic"],
                "agent_endpoints": ["/v1/route", "/v1/agent/plan"],
                "max_spend_usd": max_spend_usd(env),
                "trace_id": trace_id,
            },
        )

    if path not in {"/v1/agent/plan", "/v1/route"}:
        return WorkerHttpResult(404, resp_headers, {"error": "Not found"})
    if upper_method != "POST":
        return WorkerHttpResult(405, resp_headers, {"error": "Method not allowed"})
    if not authorized:
        return WorkerHttpResult(auth_status, resp_headers, {"error": auth_err})
    if len(raw_body.encode("utf-8")) > MAX_BODY_BYTES:
        return WorkerHttpResult(413, resp_headers, {"error": "Payload too large"})

    try:
        body = json.loads(raw_body or "{}")
    except ValueError:
        return WorkerHttpResult(400, resp_headers, {"error": "Invalid JSON body"})

    if path == "/v1/agent/plan":
        task = body.get("task") or body.get("prompt") if isinstance(body, dict) else None
        if not isinstance(task, str) or not task.strip() or "\x00" in task:
            return WorkerHttpResult(400, resp_headers, {"error": "task must be non-empty string"})
        return WorkerHttpResult(
            200,
            resp_headers,
            {
                "task": task.strip()[:2000],
                "mode": "edge-plan",
                "billing_tier": "local",
                "cost_known": True,
                "cost_status": "proven_zero_local",
                "cost_usd": 0.0,
                "trace_id": trace_id,
                "steps": [
                    {"step": 1, "tool": "workspace.list_files", "purpose": "Inspect repo tree"},
                    {"step": 2, "tool": "memory.search", "purpose": "Recall L1 verified hits"},
                    {"step": 3, "tool": "sandbox.run_python", "purpose": "Run isolated compute"},
                    {"step": 4, "tool": "sandbox.write_artifact", "purpose": "Verify SHA-256"},
                ],
                "studio_hint": "Execute full multi-step sandbox locally via ai2027-py27 studio",
            },
        )

    if not isinstance(body, dict):
        return WorkerHttpResult(400, resp_headers, {"error": "JSON root must be an object"})
    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or "\x00" in prompt:
        return WorkerHttpResult(400, resp_headers, {"error": "prompt must be a non-empty string"})
    clean_prompt = prompt.strip()
    if len(clean_prompt) > 16_000:
        return WorkerHttpResult(400, resp_headers, {"error": "prompt exceeds 16000 chars"})
    max_tokens_raw = body.get("max_tokens", 256)
    if (
        not isinstance(max_tokens_raw, int)
        or isinstance(max_tokens_raw, bool)
        or not (1 <= max_tokens_raw <= 4096)
    ):
        return WorkerHttpResult(400, resp_headers, {"error": "max_tokens must be int in 1..4096"})

    max_spend = max_spend_usd(env)
    allow_remote = bool(body.get("allow_remote", True))
    active_fetcher = fetcher or _default_workerd_fetcher
    all_attempts: list[dict[str, Any]] = []
    unknown_blocked = 0

    if allow_remote:
        for prov, key, model, url, req_hdrs, payload, extractor in _provider_specs(
            env, clean_prompt, max_tokens_raw, traceparent
        ):
            if not is_valid_key(key):
                continue
            tier, cost_known, cost_status = classify_worker_model(prov, model, env)
            if tier == "unknown_cost" and max_spend <= 0.0:
                unknown_blocked += 1
                retired = cost_status == "model_retired"
                all_attempts.append(
                    _attempt_entry(
                        prov,
                        model,
                        tier,
                        False,
                        "unknown_cost_blocked",
                        1,
                        ok=False,
                        error="model_retired" if retired else "budget_blocked",
                        detail=(
                            "Model ID was retired by the provider; update the *_MODEL "
                            "variable (see docs/MODEL-CATALOG.md)."
                            if retired
                            else "API key does not prove $0; unknown cost blocked at MAX_SPEND=0."
                        ),
                    )
                )
                continue
            text, attempts = await _call_with_retry(
                provider=prov,
                model=model,
                billing_tier=tier,
                cost_known=cost_known,
                cost_status=cost_status,
                url=url,
                headers=req_hdrs,
                payload=payload,
                extract_fn=extractor,
                fetcher=active_fetcher,
            )
            all_attempts.extend(attempts)
            if text is not None:
                return WorkerHttpResult(
                    200,
                    resp_headers,
                    {
                        "provider": prov,
                        "model": model,
                        "text": text,
                        "simulated": False,
                        "cost_usd": 0.0,
                        "billing_tier": tier,
                        "cost_known": cost_known,
                        "cost_status": cost_status,
                        "cost_verified_by_invoice": False,
                        "max_spend_usd": max_spend,
                        "budget_summary": {
                            "known_zero_local": 0,
                            "declared_free_tier": 1,
                            "unknown_cost_blocked": unknown_blocked,
                        },
                        "trace_id": trace_id,
                        "attempts": all_attempts,
                    },
                )

    all_attempts.append(
        _attempt_entry(
            "deterministic",
            "worker-deterministic-v1",
            "local",
            True,
            "proven_zero_local",
            1,
            ok=True,
        )
    )
    preview = " ".join(clean_prompt.split())[:140]
    return WorkerHttpResult(
        200,
        resp_headers,
        {
            "provider": "deterministic",
            "model": "worker-deterministic-v1",
            "text": f"[worker-deterministic-fallback | simulated=true | cost=$0.00] {preview}",
            "simulated": True,
            "cost_usd": 0.0,
            "billing_tier": "local",
            "cost_known": True,
            "cost_status": "proven_zero_local",
            "cost_verified_by_invoice": False,
            "max_spend_usd": max_spend,
            "budget_summary": {
                "known_zero_local": 1,
                "declared_free_tier": 0,
                "unknown_cost_blocked": unknown_blocked,
            },
            "trace_id": trace_id,
            "attempts": all_attempts,
        },
    )


class Default(WorkerEntrypoint):
    """Cloudflare Workers Python entrypoint delegating fetch requests safely."""

    async def fetch(self, request: WorkerRequest) -> CfResponse:  # pragma: no cover
        """Authenticate and enforce bounded body limits before buffering request payloads."""
        raw_url = str(getattr(request, "url", "https://worker.local/"))
        path = "/" + raw_url.split("://", 1)[-1].split("/", 1)[-1].split("?", 1)[0]
        method = str(getattr(request, "method", "GET"))
        raw_headers = getattr(request, "headers", {}) or {}
        headers = {str(k): str(v) for k, v in dict(raw_headers).items()}
        env = getattr(self, "env", None)
        trace_id, traceparent = resolve_trace_headers(headers)

        authorized, auth_status, auth_err, auth_mode = authorize_request(path, headers, env)
        if not authorized:
            return CfResponse(
                json.dumps({"error": auth_err}, ensure_ascii=False),
                status=auth_status,
                headers=security_headers(trace_id, traceparent, auth_mode),
            )

        body = ""
        if method.upper() == "POST":
            body_text, body_status, body_err = await read_bounded_request_body(
                request, max_bytes=MAX_BODY_BYTES
            )
            if body_text is None:
                return CfResponse(
                    json.dumps({"error": body_err}, ensure_ascii=False),
                    status=body_status,
                    headers=security_headers(trace_id, traceparent, auth_mode),
                )
            body = body_text

        result = await handle_worker_request(
            method=method,
            path=path,
            headers=headers,
            raw_body=body,
            env=env,
        )
        return CfResponse(
            json.dumps(result.body, ensure_ascii=False),
            status=result.status,
            headers=result.headers,
        )
