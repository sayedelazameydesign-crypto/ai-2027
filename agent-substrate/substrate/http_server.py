"""Small HTTP/JSON API with bearer auth and strict request-size limits."""
from __future__ import annotations

import hmac
import json
import logging
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Type
from urllib.parse import urlsplit

from .gateway import AgentGateway, GatewayError
from .kube import KubernetesAPI, KubernetesError


LOG = logging.getLogger("agent_substrate.http")
MAX_REQUEST_BYTES = 64 * 1024
_AGENT_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")


def make_handler(gateway: AgentGateway, api: KubernetesAPI, api_token: str) -> Type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "AgentSubstrate/0.1"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def _send_json(self, status: int, value: dict[str, Any]) -> None:
            raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.close_connection = True
            self.end_headers()
            self.wfile.write(raw)

        def _authorized(self) -> bool:
            header = self.headers.get("Authorization", "")
            scheme, _, supplied = header.partition(" ")
            return (
                scheme.lower() == "bearer"
                and bool(supplied)
                and hmac.compare_digest(supplied.encode("utf-8"), api_token.encode("utf-8"))
            )

        def _read_json(self) -> dict[str, Any]:
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                raise ValueError("Content-Length is required")
            try:
                length = int(raw_length)
            except ValueError:
                raise ValueError("Invalid Content-Length") from None
            if length < 0 or length > MAX_REQUEST_BYTES:
                raise OverflowError("Request body is too large")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                value = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ValueError("Body must be valid JSON") from None
            if not isinstance(value, dict):
                raise ValueError("Body must be a JSON object")
            return value

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            path = urlsplit(self.path).path
            if path == "/healthz":
                self._send_json(200, {"status": "ok"})
                return
            if path == "/readyz":
                try:
                    api.list_agents()
                except KubernetesError:
                    self._send_json(503, {"status": "not-ready"})
                    return
                self._send_json(200, {"status": "ready"})
                return
            if not self._authorized():
                self._send_json(401, {"error": "authentication required"})
                return
            parts = path.split("/")
            if len(parts) == 4 and parts[:3] == ["", "v1", "agents"] and _AGENT_NAME.fullmatch(parts[3]):
                try:
                    self._send_json(200, gateway.get_status(parts[3]))
                except GatewayError as exc:
                    self._send_json(exc.status_code, {"error": exc.public_message})
                return
            self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if not self._authorized():
                self._send_json(401, {"error": "authentication required"})
                return
            path = urlsplit(self.path).path
            parts = path.split("/")
            if len(parts) != 5 or parts[:3] != ["", "v1", "agents"] or not _AGENT_NAME.fullmatch(parts[3]):
                self._send_json(404, {"error": "not found"})
                return
            action = parts[4]
            if action not in {"invoke", "suspend", "resume"}:
                self._send_json(404, {"error": "not found"})
                return
            try:
                body = self._read_json()
            except OverflowError:
                self._send_json(413, {"error": "request body too large"})
                return
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            try:
                if action == "invoke":
                    self._send_json(200, gateway.invoke(parts[3], body))
                elif action == "suspend":
                    self._send_json(202, gateway.set_desired_state(parts[3], "Sleeping"))
                else:
                    self._send_json(202, gateway.set_desired_state(parts[3], "Running"))
            except GatewayError as exc:
                self._send_json(exc.status_code, {"error": exc.public_message})
            except Exception:
                LOG.exception("Request failed")
                self._send_json(500, {"error": "internal error"})

        def log_message(self, fmt: str, *args: Any) -> None:
            LOG.info("http %s - %s", self.address_string(), fmt % args)

    return Handler


def make_server(address: tuple[str, int], gateway: AgentGateway, api: KubernetesAPI, api_token: str) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(address, make_handler(gateway, api, api_token))
    server.daemon_threads = True
    server.allow_reuse_address = True
    return server
