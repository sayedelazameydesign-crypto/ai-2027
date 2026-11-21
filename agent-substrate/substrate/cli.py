"""Local CLI client for the authenticated Agent Substrate gateway."""
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _origin() -> str:
    raw = os.environ.get("SUBSTRATE_GATEWAY_URL", "http://127.0.0.1:8080").rstrip("/")
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("SUBSTRATE_GATEWAY_URL must be an HTTP(S) origin")
    if parts.path not in {"", "/"} or parts.query or parts.fragment:
        raise ValueError("SUBSTRATE_GATEWAY_URL must not include a path or query")
    if parts.scheme == "http" and parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Use HTTPS for non-loopback gateway URLs")
    return raw


def _request(path: str, *, method: str, payload: dict | None = None, timeout: float = 30.0) -> dict:
    token = os.environ.get("SUBSTRATE_API_TOKEN", "")
    if len(token) < 32:
        raise ValueError("Set SUBSTRATE_API_TOKEN to the gateway token (at least 32 characters)")
    data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    origin = _origin()
    request = Request(origin + path, data=data, headers=headers, method=method)
    handlers = [_NoRedirect]
    if urlsplit(origin).hostname in {"localhost", "127.0.0.1", "::1"}:
        # Port-forward traffic should not be sent through HTTP(S)_PROXY, but
        # keep the normal proxy behavior for remote gateway URLs.
        handlers.append(ProxyHandler({}))
    try:
        with build_opener(*handlers).open(request, timeout=timeout) as response:
            raw = response.read(64 * 1024 + 1)
    except HTTPError as exc:
        try:
            error = json.loads(exc.read(4096)).get("error", f"HTTP {exc.code}")
        except Exception:
            error = f"HTTP {exc.code}"
        raise RuntimeError(str(error)) from None
    except (URLError, TimeoutError, OSError):
        raise RuntimeError("Gateway is unreachable") from None
    if len(raw) > 64 * 1024:
        raise RuntimeError("Gateway response exceeded the size limit")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("Gateway returned invalid JSON") from None
    if not isinstance(value, dict):
        raise RuntimeError("Gateway returned an invalid response")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-substrate", description="Operate actors through the local Substrate gateway")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "suspend", "resume"):
        sub = commands.add_parser(name)
        sub.add_argument("agent")
    invoke = commands.add_parser("invoke")
    invoke.add_argument("agent")
    invoke.add_argument("--op", choices=["increment"], default="increment")
    invoke.add_argument("--by", type=int, default=1)
    invoke.add_argument("--request-id", required=True, help="stable id used to make retries idempotent")
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            result = _request(f"/v1/agents/{args.agent}", method="GET")
        elif args.command == "suspend":
            result = _request(f"/v1/agents/{args.agent}/suspend", method="POST", payload={})
        elif args.command == "resume":
            result = _request(f"/v1/agents/{args.agent}/resume", method="POST", payload={})
        else:
            result = _request(
                f"/v1/agents/{args.agent}/invoke",
                method="POST",
                payload={"op": args.op, "by": args.by, "requestId": args.request_id},
            )
    except (ValueError, RuntimeError) as exc:
        print(f"agent-substrate: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
