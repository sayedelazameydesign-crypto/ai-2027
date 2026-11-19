"""Cloudflare Python Worker entrypoint for Free-First AI routing at $0."""

from __future__ import annotations

import asyncio
import hmac
import importlib
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

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


_MAX_BODY_BYTES = 32_768
_SECRET_SCRUB_RE = re.compile(
    r"(?:AIza[0-9A-Za-z_\-]{20,}|nvapi-[0-9A-Za-z_\-]{20,}|sk-[0-9A-Za-z_\-]{20,}|"
    r"gsk_[0-9A-Za-z_\-]{20,}|Bearer\s+[0-9A-Za-z._\-]{12,})"
)

AsyncFetcher = Callable[
    [str, Mapping[str, str], str, float],
    Awaitable[tuple[int, dict[str, Any]]],
]


class WorkerRequest(Protocol):
    """Structural interface for incoming Cloudflare FetchEvent requests."""

    url: str
    method: str
    headers: Mapping[str, str]

    async def text(self) -> str:
        """Read request body text asynchronously."""
        ...


@dataclass(frozen=True)
class WorkerHttpResult:
    """Normalized HTTP response returned by the pure-Python Worker handler."""

    status: int
    headers: dict[str, str]
    body: dict[str, Any]


def _security_headers(trace_id: str) -> dict[str, str]:
    return {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
        "x-content-type-options": "nosniff",
        "x-trace-id": trace_id,
    }


def _env_get(env: object, key: str, default: str = "") -> str:
    if env is None:
        return default
    if isinstance(env, Mapping):
        val = env.get(key, default)
    else:
        val = getattr(env, key, default)
    text = str(val).strip() if val is not None else ""
    return text or default


def _is_valid_key(value: str) -> bool:
    return bool(value) and "placeholder" not in value.lower()


def _scrub(text: str) -> str:
    return _SECRET_SCRUB_RE.sub("[REDACTED]", text)


def _check_bearer(headers: Mapping[str, str], expected_token: str) -> bool:
    if not expected_token:
        return True
    auth = ""
    for key, value in headers.items():
        if key.lower() == "authorization":
            auth = value.strip()
            break
    if not auth.lower().startswith("bearer "):
        return False
    supplied = auth[7:].strip()
    return hmac.compare_digest(supplied.encode("utf-8"), expected_token.encode("utf-8"))


async def _default_workerd_fetcher(  # pragma: no cover - Pyodide js.fetch bridge
    url: str,
    headers: Mapping[str, str],
    body_json: str,
    timeout_s: float,
) -> tuple[int, dict[str, Any]]:
    if not _IN_WORKERD:
        raise RuntimeError("No fetch implementation available outside Cloudflare workerd.")
    init = {
        "method": "POST",
        "headers": dict(headers),
        "body": body_json,
    }
    async with asyncio.timeout(timeout_s):
        resp = await _js_module.fetch(url, init)
        text = await resp.text()
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = {}
    return int(resp.status), parsed if isinstance(parsed, dict) else {}


async def _call_with_retry(
    *,
    provider: str,
    model: str,
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
                {
                    "provider": provider,
                    "model": model,
                    "attempt": attempt,
                    "ok": False,
                    "error": "timeout",
                }
            )
            continue
        except Exception as exc:
            attempts.append(
                {
                    "provider": provider,
                    "model": model,
                    "attempt": attempt,
                    "ok": False,
                    "error": _scrub(type(exc).__name__),
                }
            )
            continue

        elapsed_ms = round((time.monotonic() - start) * 1000.0, 2)
        if status >= 500 or status in {408, 429}:
            attempts.append(
                {
                    "provider": provider,
                    "model": model,
                    "attempt": attempt,
                    "ok": False,
                    "latency_ms": elapsed_ms,
                    "error": f"http_{status}",
                }
            )
            if attempt <= max_retries:
                await asyncio.sleep(0.02 * attempt)
            continue
        if status >= 400:
            attempts.append(
                {
                    "provider": provider,
                    "model": model,
                    "attempt": attempt,
                    "ok": False,
                    "latency_ms": elapsed_ms,
                    "error": f"http_{status}",
                }
            )
            break
        text = extract_fn(data)
        if text:
            attempts.append(
                {
                    "provider": provider,
                    "model": model,
                    "attempt": attempt,
                    "ok": True,
                    "latency_ms": elapsed_ms,
                }
            )
            return text, attempts
        attempts.append(
            {
                "provider": provider,
                "model": model,
                "attempt": attempt,
                "ok": False,
                "latency_ms": elapsed_ms,
                "error": "invalid_payload",
            }
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
    trace_id = uuid.uuid4().hex
    resp_headers = _security_headers(trace_id)
    req_headers = headers or {}
    upper_method = method.upper()

    if path == "/health":
        if upper_method not in {"GET", "HEAD"}:
            return WorkerHttpResult(405, resp_headers, {"error": "Method not allowed"})
        return WorkerHttpResult(
            200,
            resp_headers,
            {
                "ok": True,
                "service": "ai2027-python-worker",
                "runtime": "cloudflare-python-workers",
                "cost_usd": 0.0,
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
                "tier": "$0 Free-First Edge Worker",
                "fallback_order": ["gemini", "nvidia", "groq", "openrouter", "deterministic"],
                "agent_endpoints": ["/v1/route", "/v1/agent/plan"],
                "trace_id": trace_id,
            },
        )

    if path == "/v1/agent/plan":
        if upper_method != "POST":
            return WorkerHttpResult(405, resp_headers, {"error": "Method not allowed"})
        if not _check_bearer(req_headers, _env_get(env, "WORKER_AUTH_TOKEN")):
            return WorkerHttpResult(401, resp_headers, {"error": "Unauthorized"})
        try:
            body = json.loads(raw_body or "{}")
        except ValueError:
            return WorkerHttpResult(400, resp_headers, {"error": "Invalid JSON body"})
        task = body.get("task") or body.get("prompt") if isinstance(body, dict) else None
        if not isinstance(task, str) or not task.strip() or "\x00" in task:
            return WorkerHttpResult(400, resp_headers, {"error": "task must be non-empty string"})
        clean_task = task.strip()[:2000]
        return WorkerHttpResult(
            200,
            resp_headers,
            {
                "task": clean_task,
                "mode": "edge-plan",
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

    if path == "/v1/route":
        if upper_method != "POST":
            return WorkerHttpResult(405, resp_headers, {"error": "Method not allowed"})
        auth_token = _env_get(env, "WORKER_AUTH_TOKEN")
        if not _check_bearer(req_headers, auth_token):
            return WorkerHttpResult(401, resp_headers, {"error": "Unauthorized"})
        if len(raw_body.encode("utf-8")) > _MAX_BODY_BYTES:
            return WorkerHttpResult(413, resp_headers, {"error": "Payload too large"})
        try:
            body = json.loads(raw_body or "{}")
        except ValueError:
            return WorkerHttpResult(400, resp_headers, {"error": "Invalid JSON body"})
        if not isinstance(body, dict):
            return WorkerHttpResult(400, resp_headers, {"error": "JSON root must be an object"})
        prompt = body.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or "\x00" in prompt:
            return WorkerHttpResult(
                400, resp_headers, {"error": "prompt must be a non-empty string"}
            )
        clean_prompt = prompt.strip()
        if len(clean_prompt) > 16_000:
            return WorkerHttpResult(400, resp_headers, {"error": "prompt exceeds 16000 chars"})

        max_tokens_raw = body.get("max_tokens", 256)
        valid_tokens = (
            isinstance(max_tokens_raw, int)
            and not isinstance(max_tokens_raw, bool)
            and 1 <= max_tokens_raw <= 4096
        )
        if not valid_tokens:
            return WorkerHttpResult(
                400, resp_headers, {"error": "max_tokens must be int in 1..4096"}
            )

        allow_remote = bool(body.get("allow_remote", True))
        active_fetcher = fetcher or _default_workerd_fetcher
        all_attempts: list[dict[str, Any]] = []

        if allow_remote:
            gemini_key = _env_get(env, "GEMINI_API_KEY")
            if _is_valid_key(gemini_key):
                model = _env_get(env, "GEMINI_MODEL", "gemini-2.5-flash")
                gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                text, attempts = await _call_with_retry(
                    provider="gemini",
                    model=model,
                    url=gemini_url,
                    headers={"content-type": "application/json", "x-goog-api-key": gemini_key},
                    payload={
                        "contents": [{"role": "user", "parts": [{"text": clean_prompt}]}],
                        "generationConfig": {"maxOutputTokens": max_tokens_raw},
                    },
                    extract_fn=_extract_gemini,
                    fetcher=active_fetcher,
                )
                all_attempts.extend(attempts)
                if text is not None:
                    return WorkerHttpResult(
                        200,
                        resp_headers,
                        {
                            "provider": "gemini",
                            "model": model,
                            "text": text,
                            "simulated": False,
                            "cost_usd": 0.0,
                            "trace_id": trace_id,
                            "attempts": all_attempts,
                        },
                    )

            nvidia_key = _env_get(env, "NVIDIA_API_KEY")
            if _is_valid_key(nvidia_key):
                model = _env_get(env, "NVIDIA_MODEL", "meta/llama-3.1-8b-instruct")
                text, attempts = await _call_with_retry(
                    provider="nvidia",
                    model=model,
                    url="https://integrate.api.nvidia.com/v1/chat/completions",
                    headers={
                        "content-type": "application/json",
                        "authorization": f"Bearer {nvidia_key}",
                    },
                    payload={
                        "model": model,
                        "messages": [{"role": "user", "content": clean_prompt}],
                        "max_tokens": max_tokens_raw,
                    },
                    extract_fn=_extract_openai,
                    fetcher=active_fetcher,
                )
                all_attempts.extend(attempts)
                if text is not None:
                    return WorkerHttpResult(
                        200,
                        resp_headers,
                        {
                            "provider": "nvidia",
                            "model": model,
                            "text": text,
                            "simulated": False,
                            "cost_usd": 0.0,
                            "trace_id": trace_id,
                            "attempts": all_attempts,
                        },
                    )

            groq_key = _env_get(env, "GROQ_API_KEY")
            if _is_valid_key(groq_key):
                model = _env_get(env, "GROQ_MODEL", "llama-3.3-70b-versatile")
                text, attempts = await _call_with_retry(
                    provider="groq",
                    model=model,
                    url="https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "content-type": "application/json",
                        "authorization": f"Bearer {groq_key}",
                    },
                    payload={
                        "model": model,
                        "messages": [{"role": "user", "content": clean_prompt}],
                        "max_tokens": max_tokens_raw,
                    },
                    extract_fn=_extract_openai,
                    fetcher=active_fetcher,
                )
                all_attempts.extend(attempts)
                if text is not None:
                    return WorkerHttpResult(
                        200,
                        resp_headers,
                        {
                            "provider": "groq",
                            "model": model,
                            "text": text,
                            "simulated": False,
                            "cost_usd": 0.0,
                            "trace_id": trace_id,
                            "attempts": all_attempts,
                        },
                    )

            openrouter_key = _env_get(env, "OPENROUTER_API_KEY")
            if _is_valid_key(openrouter_key):
                model = _env_get(env, "OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
                text, attempts = await _call_with_retry(
                    provider="openrouter",
                    model=model,
                    url="https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "content-type": "application/json",
                        "authorization": f"Bearer {openrouter_key}",
                    },
                    payload={
                        "model": model,
                        "messages": [{"role": "user", "content": clean_prompt}],
                        "max_tokens": max_tokens_raw,
                    },
                    extract_fn=_extract_openai,
                    fetcher=active_fetcher,
                )
                all_attempts.extend(attempts)
                if text is not None:
                    return WorkerHttpResult(
                        200,
                        resp_headers,
                        {
                            "provider": "openrouter",
                            "model": model,
                            "text": text,
                            "simulated": False,
                            "cost_usd": 0.0,
                            "trace_id": trace_id,
                            "attempts": all_attempts,
                        },
                    )

        all_attempts.append(
            {
                "provider": "deterministic",
                "model": "worker-deterministic-v1",
                "attempt": 1,
                "ok": True,
            }
        )
        preview = " ".join(clean_prompt.split())[:140]
        return WorkerHttpResult(
            200,
            resp_headers,
            {
                "provider": "deterministic",
                "model": "worker-deterministic-v1",
                "text": (
                    f"[worker-deterministic-fallback | simulated=true | cost=$0.00] {preview}"
                ),
                "simulated": True,
                "cost_usd": 0.0,
                "trace_id": trace_id,
                "attempts": all_attempts,
            },
        )

    return WorkerHttpResult(404, resp_headers, {"error": "Not found"})


class Default(WorkerEntrypoint):
    """Cloudflare Workers Python entrypoint delegating fetch requests safely."""

    async def fetch(self, request: WorkerRequest) -> CfResponse:  # pragma: no cover
        """Handle incoming Worker FetchEvent requests."""
        raw_url = str(getattr(request, "url", "https://worker.local/"))
        path = "/" + raw_url.split("://", 1)[-1].split("/", 1)[-1].split("?", 1)[0]
        method = str(getattr(request, "method", "GET"))
        raw_headers = getattr(request, "headers", {}) or {}
        headers = {str(k): str(v) for k, v in dict(raw_headers).items()}
        body = await request.text() if method.upper() == "POST" else ""
        result = await handle_worker_request(
            method=method,
            path=path,
            headers=headers,
            raw_body=body,
            env=getattr(self, "env", None),
        )
        return CfResponse(
            json.dumps(result.body, ensure_ascii=False),
            status=result.status,
            headers=result.headers,
        )
