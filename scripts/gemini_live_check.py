#!/usr/bin/env python3
"""One real Gemini call — so "LLM wiring works" is a measurement, not a claim.

Context this file exists in: every proven path in this repository is a
*deterministic planner* with **no model** (the live agent says so on
`/api/health`). `GEMINI_API_KEY` is now a repository secret, which makes exactly
one new claim possible: *the credential works and the endpoint answers.* This
script makes that claim falsifiable — one request, one tiny response, no retry
loop, no prompt library.

Security rules, enforced here rather than documented:

1. the key is read from ``GEMINI_API_KEY`` **only** — never from argv, so it
   never lands in a process list or a shell history;
2. the key travels in the ``x-goog-api-key`` **header**, never as ``?key=`` in
   the URL — URLs are copied into proxy logs, error pages and bug reports;
3. the key is never printed, logged, or written to the JSON result — the report
   says only that a credential is present, and how long it is;
4. the request is deliberately tiny (``maxOutputTokens=24``, ``temperature=0``)
   because a smoke test that costs money is a smoke test people disable.

Exit codes (distinct on purpose — "no credential" is not "broken"):
    0  the endpoint answered with text
    1  the call was attempted and failed (classified in the report)
    2  no key present → SKIPPED, never reported as a failure
    3  no general egress → BLOCKED (see scripts/network_preflight.py)

    python3 scripts/gemini_live_check.py                 # human output
    python3 scripts/gemini_live_check.py --json          # for CI
    GEMINI_MODEL=gemini-2.0-flash python3 scripts/gemini_live_check.py

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

DEFAULT_MODEL = "gemini-2.0-flash"
ENDPOINT = ("https://generativelanguage.googleapis.com/v1beta/models/"
            "{model}:generateContent")
PROMPT = "Reply with exactly the word: pong"
HOST = "https://generativelanguage.googleapis.com/"

OK, FAILED, NO_KEY, BLOCKED = 0, 1, 2, 3

#: A provider's error body can quote the key back at you (`API key not valid:
#: AIza…`). Storing that message verbatim would publish the secret in a CI log —
#: measured with test_14, not assumed.
_KEY_SHAPES = (re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),)


def scrub(text, key: str = "") -> str:
    """Remove the key (and anything shaped like one) from operator-facing text."""
    if not text:
        return text
    out = str(text)
    if key:
        out = out.replace(key, "[redacted-key]")
    for rx in _KEY_SHAPES:
        out = rx.sub("[redacted-key]", out)
    return out


def credential_label(key: str) -> str:
    """Say that a credential is present — and nothing else about it.

    Three iterations, each rejected by a reviewer (one of them CodeQL):

    * `AIza…(39 chars)` — a prefix. `AIza` is identical for every Google key, so it
      identified nothing while being key material in a log;
    * `sha256:<12 hex>` — better, but still a derivation of the secret, and CodeQL
      was right that deriving an identifier from a credential invites the next
      mistake (the weak-hash alert it raised is the same family of problem);
    * **present / absent + length** — derivable without exporting anything.

    Rotation does not need a fingerprint: the live call either succeeds or it does
    not, and that is the measurement this file exists to make.
    """
    return f"present ({len(key)} chars)" if key else "(none)"


def classify(status: int | None, error: str | None = None) -> str:
    """What kind of failure is this? Distinct causes deserve distinct labels."""
    if status is None:
        return "network"
    if status in (401, 403):
        return "auth_or_permission"
    if status == 429:
        return "quota_or_rate_limit"
    if status == 404:
        return "model_not_found"
    if status == 400:
        return "bad_request"
    if 500 <= status < 600:
        return "upstream_error"
    if error:
        return "failed"
    return "failed"


def _post(url: str, headers: dict, payload: dict, timeout: float, opener=None):
    """(status, body_bytes, error_text). Any HTTP status is an answer, not an exception."""
    open_fn = opener or urllib.request.urlopen
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with open_fn(req, timeout=timeout) as resp:
            return int(getattr(resp, "status", 200)), resp.read(), None
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except Exception:      # a fixture-less HTTPError has no readable body
            body = b""
        return int(exc.code), body, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return None, b"", str(reason)[:160]


def parse_answer(raw: bytes) -> tuple[str | None, dict]:
    """Pull the text out of a generateContent response, or say why it is empty."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, {"problem": "response is not JSON"}
    if not isinstance(data, dict):
        return None, {"problem": "response is not an object"}
    if "error" in data:
        err = data["error"] if isinstance(data["error"], dict) else {}
        return None, {"problem": "api_error", "api_status": err.get("status"),
                      "api_message": scrub(str(err.get("message", "")))[:200]}
    candidates = data.get("candidates") or []
    usage = data.get("usageMetadata") or {}
    if not candidates:
        # A safety block returns a promptFeedback and zero candidates. Saying
        # "empty response" would hide the actual reason.
        feedback = data.get("promptFeedback") or {}
        return None, {"problem": "no_candidates",
                      "block_reason": feedback.get("blockReason")}
    parts = ((candidates[0].get("content") or {}).get("parts") or [])
    text = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict))
    return (text or None), {"usage": usage,
                            "finish_reason": candidates[0].get("finishReason")}


def check(model: str = DEFAULT_MODEL, timeout: float = 20.0, opener=None,
          key: str | None = None, require_reachability: bool = True) -> dict:
    """The whole check, as data. No printing, no exiting — that is `main`'s job."""
    key = (key if key is not None else "").strip()
    started = time.time()
    result = {
        "model": model,
        "credential": credential_label(key),
        "key_present": bool(key),
        "ok": False,
        "status": None,
        "error_kind": None,
        "latency_ms": None,
        "text_chars": 0,
        "text_preview": "",
        "usage": {},
        "claim": "the credential works and the endpoint answers; this is NOT a "
                 "statement that any agent uses a model",
    }
    if not key:
        result["error_kind"] = "no_key"
        return result

    if require_reachability:
        try:
            import network_preflight
            report = network_preflight.cached_preflight()
            if not report.allows(HOST):
                result["error_kind"] = "blocked"
                result["blocked_by"] = f"egress={report.egress}"
                return result
        except Exception as exc:  # a broken preflight must not fake a result
            result["preflight_error"] = type(exc).__name__

    url = ENDPOINT.format(model=model)
    payload = {"contents": [{"parts": [{"text": PROMPT}]}],
               "generationConfig": {"temperature": 0, "maxOutputTokens": 24}}
    headers = {"Content-Type": "application/json", "x-goog-api-key": key}
    status, raw, error = _post(url, headers, payload, timeout, opener=opener)
    result["status"] = status
    result["latency_ms"] = round((time.time() - started) * 1000, 1)

    if status is None or not (200 <= status < 300):
        result["error_kind"] = classify(status, error)
        result["error"] = scrub(error, key)
        try:
            text, meta = parse_answer(raw)
            if meta.get("api_status") or meta.get("api_message"):
                result["api_status"] = meta.get("api_status")
                result["api_message"] = scrub(meta.get("api_message"), key)
        except Exception:  # diagnostics must never mask the original failure
            pass
        return result

    text, meta = parse_answer(raw)
    result.update({k: v for k, v in meta.items() if k != "problem"})
    if text is None:
        result["error_kind"] = meta.get("problem", "empty_response")
        result["block_reason"] = meta.get("block_reason")
        return result
    result.update({"ok": True, "text_chars": len(text),
                   "text_preview": text.strip()[:80]})
    return result


def main(argv: list[str] | None = None, env: dict | None = None,
         opener=None) -> int:
    import os

    env = os.environ if env is None else env
    ap = argparse.ArgumentParser(description="One real Gemini call (tiny, cheap, masked).")
    ap.add_argument("--model", default=env.get("GEMINI_MODEL") or DEFAULT_MODEL)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-preflight", action="store_true",
                    help="skip the egress gate (not recommended: it turns blindness into failure)")
    args = ap.parse_args(argv)

    result = check(model=args.model, timeout=args.timeout,
                   key=env.get("GEMINI_API_KEY", ""),
                   require_reachability=not args.no_preflight,
                   opener=opener)

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("GEMINI LIVE CHECK — one real call, nothing simulated")
        print("=" * 66)
        print(f"  model      : {result['model']}")
        print(f"  credential : {result['credential']}")
        if result["ok"]:
            print(f"  answer     : {result['text_preview']!r} "
                  f"({result['text_chars']} chars)")
            print(f"  latency    : {result['latency_ms']} ms · status {result['status']}")
        else:
            print(f"  result     : {result['error_kind']}")
            for k in ("status", "api_status", "api_message", "blocked_by", "error"):
                if result.get(k):
                    print(f"  {k:<10} : {result[k]}")
        print("=" * 66)
        print(result["claim"])

    if result["ok"]:
        return OK
    if result["error_kind"] == "no_key":
        return NO_KEY
    if result["error_kind"] == "blocked":
        return BLOCKED
    return FAILED


if __name__ == "__main__":
    raise SystemExit(main())
