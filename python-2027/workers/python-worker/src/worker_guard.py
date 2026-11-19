"""Security, fail-closed auth, bounded stream reading, W3C trace, and $0 BudgetGuard helpers."""

from __future__ import annotations

import hmac
import re
import uuid
from collections.abc import Mapping
from typing import Protocol

MAX_BODY_BYTES = 32_768
PUBLIC_READ_ROUTES = frozenset({"/health", "/v1/protocol"})
_HEX32_RE = re.compile(r"^[0-9a-f]{32}$")
_TRACEPARENT_RE = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$")
_SECRET_SCRUB_RE = re.compile(
    r"(?:AIza[0-9A-Za-z_\-]{20,}|nvapi-[0-9A-Za-z_\-]{20,}|sk-[0-9A-Za-z_\-]{20,}|"
    r"gsk_[0-9A-Za-z_\-]{20,}|Bearer\s+[0-9A-Za-z._\-]{12,})"
)

FREE_MODELS: dict[str, frozenset[str]] = {
    "gemini": frozenset(
        {
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
            "gemini-1.5-flash",
            "gemini-1.5-flash-8b",
        }
    ),
    # Only IDs the provider still serves. A retired ID left in this map is how a
    # "free" worker check starts answering HTTP 404/410 while still claiming $0.
    "nvidia": frozenset({"meta/llama-3.3-70b-instruct"}),
    # Groq retired its free Llama models on 2026-08-16; GPT-OSS IDs are priced per
    # token, so they are admitted only through an explicit AI2027_FREE_MODELS entry.
    "groq": frozenset(),
}

#: Mirrors ai2027_protocol/model_catalog.py (a Worker cannot import the package).
RETIRED_MODELS: dict[str, dict[str, str]] = {
    "nvidia": {
        "meta/llama-3.1-8b-instruct": "meta/llama-3.3-70b-instruct",
    },
    "groq": {
        "llama-3.1-8b-instant": "openai/gpt-oss-20b",
        "llama-3.3-70b-versatile": "openai/gpt-oss-120b",
        "meta-llama/llama-4-scout-17b-16e-instruct": "openai/gpt-oss-120b",
        "qwen/qwen3-32b": "openai/gpt-oss-120b",
    },
}


class WorkerRequest(Protocol):
    """Structural interface for incoming Cloudflare FetchEvent requests."""

    url: str
    method: str
    headers: Mapping[str, str]

    async def text(self) -> str:
        """Read request body text asynchronously."""
        ...


def env_get(env: object, key: str, default: str = "") -> str:
    """Safely read and strip an environment binding from Mapping or attribute object."""
    if env is None:
        return default
    val = env.get(key, default) if isinstance(env, Mapping) else getattr(env, key, default)
    text = str(val).strip() if val is not None else ""
    return text or default


def header_get(headers: Mapping[str, str], target: str) -> str:
    """Case-insensitive header lookup."""
    low = target.lower()
    for key, value in headers.items():
        if str(key).lower() == low:
            return str(value).strip()
    return ""


def is_valid_key(value: str) -> bool:
    """Return True when key string is non-empty and not a placeholder."""
    return bool(value) and "placeholder" not in value.lower()


def scrub_text(text: str) -> str:
    """Redact common API key patterns from error diagnostics."""
    return _SECRET_SCRUB_RE.sub("[REDACTED]", text)


def resolve_trace_headers(req_headers: Mapping[str, str]) -> tuple[str, str]:
    """Propagate valid incoming W3C traceparent or X-Trace-Id, or mint a fresh pair."""
    lowered = {str(k).lower(): str(v).strip() for k, v in req_headers.items()}
    match = _TRACEPARENT_RE.fullmatch(lowered.get("traceparent", "").lower())
    if match and int(match.group(1), 16) != 0 and int(match.group(2), 16) != 0:
        tid = match.group(1)
        return tid, f"00-{tid}-{uuid.uuid4().hex[:16]}-01"
    raw_tid = lowered.get("x-trace-id", "").lower()
    valid_tid = _HEX32_RE.fullmatch(raw_tid) and int(raw_tid, 16) != 0
    trace_id = raw_tid if valid_tid else uuid.uuid4().hex
    return trace_id, f"00-{trace_id}-{uuid.uuid4().hex[:16]}-01"


def security_headers(trace_id: str, traceparent: str, auth_mode: str = "bearer") -> dict[str, str]:
    """Build strict no-store JSON security + trace response headers."""
    return {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
        "x-content-type-options": "nosniff",
        "x-trace-id": trace_id,
        "traceparent": traceparent,
        "x-ai2027-auth-mode": auth_mode,
    }


def authorize_request(
    path: str, headers: Mapping[str, str], env: object
) -> tuple[bool, int, str, str]:
    """Enforce Fail-Closed auth unless route is explicitly public or declared public."""
    if path in PUBLIC_READ_ROUTES:
        return True, 200, "", "public-read"
    expected_token = env_get(env, "WORKER_AUTH_TOKEN")
    if expected_token:
        auth = header_get(headers, "authorization")
        if not auth.lower().startswith("bearer "):
            return False, 401, "Unauthorized", "bearer"
        supplied = auth[7:].strip()
        ok = hmac.compare_digest(supplied.encode("utf-8"), expected_token.encode("utf-8"))
        return (True, 200, "", "bearer") if ok else (False, 401, "Unauthorized", "bearer")

    public_routes = {
        r.strip() for r in env_get(env, "AI2027_PUBLIC_ROUTES").split(",") if r.strip()
    }
    env_mode = (env_get(env, "AI2027_ENV") or env_get(env, "ENVIRONMENT") or "production").lower()
    allow_public = env_get(env, "AI2027_ALLOW_PUBLIC_UNAUTH").lower() in {"1", "true", "yes"}
    if path in public_routes or (allow_public and env_mode != "production"):
        return True, 200, "", "explicit-public"
    return (
        False,
        401,
        "Fail-closed: WORKER_AUTH_TOKEN is required (or declare path in AI2027_PUBLIC_ROUTES).",
        "fail-closed",
    )


def classify_worker_model(provider: str, model: str, env: object) -> tuple[str, bool, str]:
    """Classify billing tier (BudgetGuard) without treating API keys as free proof."""
    if provider == "deterministic":
        return "local", True, "proven_zero_local"
    norm = model.strip().lower()
    if provider == "openrouter":
        if norm.endswith(":free") or norm == "openrouter/free":
            return "free_tier", False, "declared_free_tier_unbilled"
        return "unknown_cost", False, "unknown_cost_blocked"
    if norm in RETIRED_MODELS.get(provider, {}):
        return "unknown_cost", False, "model_retired"
    extra = {m.strip().lower() for m in env_get(env, "AI2027_FREE_MODELS").split(",") if m.strip()}
    if norm in FREE_MODELS.get(provider, frozenset()) or norm in extra:
        return "free_tier", False, "declared_free_tier_unbilled"
    return "unknown_cost", False, "unknown_cost_blocked"


def max_spend_usd(env: object) -> float:
    """Return configured AI2027_MAX_SPEND_USD (defaults fail-closed to 0.0)."""
    try:
        return max(0.0, float(env_get(env, "AI2027_MAX_SPEND_USD", "0")))
    except ValueError:
        return 0.0


def _to_chunk_bytes(value: object) -> bytes:
    if value is None:
        return b""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    to_py = getattr(value, "to_py", None)
    if callable(to_py):
        return bytes(to_py())
    if isinstance(value, memoryview):
        return bytes(value)
    return bytes(str(value), encoding="utf-8")


async def read_bounded_request_body(
    request: WorkerRequest,
    *,
    max_bytes: int = MAX_BODY_BYTES,
) -> tuple[str | None, int, str]:
    """Enforce Content-Length and bounded stream reading before full body buffering."""
    cl_raw = header_get(getattr(request, "headers", {}) or {}, "content-length")
    if cl_raw:
        try:
            cl_val = int(cl_raw)
        except ValueError:
            return None, 400, "Invalid Content-Length header"
        if cl_val < 0:
            return None, 400, "Invalid Content-Length header"
        if cl_val > max_bytes:
            return None, 413, "Payload too large"

    body_stream = getattr(request, "body", None)
    get_reader = getattr(body_stream, "getReader", None) if body_stream is not None else None
    if callable(get_reader):
        reader = get_reader()
        chunks: list[bytes] = []
        total = 0
        try:
            while True:
                step = await reader.read()
                is_map = isinstance(step, Mapping)
                done = bool(step.get("done") if is_map else getattr(step, "done", False))
                if done:
                    break
                val = step.get("value") if is_map else getattr(step, "value", b"")
                raw_chunk = _to_chunk_bytes(val)
                total += len(raw_chunk)
                if total > max_bytes:
                    cancel = getattr(reader, "cancel", None)
                    if callable(cancel):
                        await cancel()
                    return None, 413, "Payload too large"
                chunks.append(raw_chunk)
        finally:
            rel = getattr(reader, "releaseLock", None)
            if callable(rel):
                rel()
        return b"".join(chunks).decode("utf-8", errors="replace"), 200, ""

    raw_text = await request.text()
    if len(raw_text.encode("utf-8")) > max_bytes:
        return None, 413, "Payload too large"
    return raw_text, 200, ""
