#!/usr/bin/env python3
"""One small, manual NVIDIA NIM smoke test for GitHub Actions.

This is intentionally separate from the local Connectors Hub keyring path:
GitHub supplies ``NVIDIA_API_KEY`` through a step environment assignment. The
script makes one fixed, tiny request, disables redirects and environment
proxies, and never prints provider error bodies or the credential.

Exit codes: 0 = answered; 1 = attempted and failed; 2 = no key (skipped);
3 = invalid model input. No retries and no user prompt/content are accepted.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable

ENDPOINT = "https://integrate.api.nvidia.com/v1/chat/completions"
PROMPT = "Reply with exactly the word: pong"
MAX_OUTPUT_TOKENS = 16
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MODEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,119}")
OK, FAILED, NO_KEY, INVALID_MODEL = 0, 1, 2, 3


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so an authorization header cannot follow another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open_direct(request: urllib.request.Request, *, timeout: float):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
    )
    return opener.open(request, timeout=timeout)


def _post(
    url: str,
    headers: dict[str, str],
    payload: dict,
    timeout: float,
    opener: Callable | None = None,
) -> tuple[int | None, bytes, str | None]:
    """Return status and bounded body; never return exception/provider text."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    open_fn = opener or _open_direct
    try:
        with open_fn(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                return status, b"", "response_too_large"
            return status, raw, None
    except urllib.error.HTTPError as exc:
        # Do not read the body: provider diagnostics can echo authorization data.
        exc.close()
        return int(exc.code), b"", "http_error"
    except (urllib.error.URLError, OSError, ValueError):
        # Exception text can contain URLs, headers, or other sensitive details.
        return None, b"", "network_error"


def _classify(status: int | None, error: str | None) -> str:
    if error == "response_too_large":
        return "response_too_large"
    if status is None:
        return "network"
    if 300 <= status < 400:
        return "redirect_blocked"
    if status in {401, 403}:
        return "auth_or_permission"
    if status == 404:
        return "model_not_found"
    if status == 429:
        return "quota_or_rate_limit"
    if status == 400:
        return "bad_request"
    if status >= 500:
        return "upstream_error"
    return "http_error"


def _parse_text(raw: bytes) -> tuple[str | None, dict[str, int]]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, {}
    if not isinstance(data, dict):
        return None, {}
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None, {}
    message = choices[0].get("message")
    text = message.get("content") if isinstance(message, dict) else None
    usage_data = data.get("usage")
    usage = (
        {
            name: value
            for name, value in usage_data.items()
            if isinstance(name, str) and isinstance(value, int) and value >= 0
        }
        if isinstance(usage_data, dict)
        else {}
    )
    return (text if isinstance(text, str) and text.strip() else None), usage


def check(
    *,
    model: str,
    key: str,
    timeout: float = 20.0,
    opener: Callable | None = None,
) -> dict:
    """Perform one live request; return only safe, non-secret metadata."""
    key = key.strip()
    result = {
        "provider": "nvidia_nim",
        "model": model,
        "credential": "present" if key else "absent",
        "ok": False,
        "status": None,
        "error_kind": None,
        "latency_ms": None,
        "response_chars": 0,
        "usage": {},
        "claim": (
            "one small NVIDIA NIM endpoint check only; this does not prove product "
            "integration, output quality, or parity with another assistant"
        ),
    }
    if not key:
        result["error_kind"] = "no_key"
        return result
    if not isinstance(model, str) or not MODEL_PATTERN.fullmatch(model):
        result["error_kind"] = "invalid_model"
        return result

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "temperature": 0,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "stream": False,
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    }
    started = time.monotonic()
    status, raw, error = _post(ENDPOINT, headers, payload, timeout, opener=opener)
    result["status"] = status
    result["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
    if status is None or not 200 <= status < 300:
        result["error_kind"] = _classify(status, error)
        return result
    if error:
        result["error_kind"] = _classify(status, error)
        return result

    text, usage = _parse_text(raw)
    if text is None:
        result["error_kind"] = "invalid_response"
        return result
    result["ok"] = True
    result["response_chars"] = len(text)
    result["usage"] = usage
    return result


def main(
    argv: list[str] | None = None,
    env: dict[str, str] | None = None,
    opener: Callable | None = None,
) -> int:
    env = os.environ if env is None else env
    parser = argparse.ArgumentParser(description="One tiny NVIDIA NIM live smoke test.")
    parser.add_argument("--model", default=env.get("NVIDIA_MODEL", ""))
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = check(
        model=args.model,
        key=env.get("NVIDIA_API_KEY", ""),
        timeout=args.timeout,
        opener=opener,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("NVIDIA NIM live check — one fixed, minimal request")
        print(f"  model      : {result['model'] or '(missing)'}")
        print(f"  credential : {result['credential']}")
        if result["ok"]:
            print(f"  result     : answered ({result['response_chars']} chars)")
            print(
                f"  latency    : {result['latency_ms']} ms · status {result['status']}"
            )
        else:
            print(f"  result     : {result['error_kind']}")
            if result["status"] is not None:
                print(f"  status     : {result['status']}")
        print(result["claim"])

    if result["ok"]:
        return OK
    if result["error_kind"] == "no_key":
        return NO_KEY
    if result["error_kind"] == "invalid_model":
        return INVALID_MODEL
    return FAILED


if __name__ == "__main__":
    raise SystemExit(main())
