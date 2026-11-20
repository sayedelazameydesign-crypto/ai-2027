"""A real HTTP Ollama server, serving scripted answers and no intelligence.

Why this exists
---------------
``ScriptedBackend`` proves the fleet can consume a completion, but it steps over
the entire HTTP layer: endpoint validation, loopback allowance, request shape,
JSON decoding, ``message.content`` extraction. A bug in any of those would never
be seen by a test that never opens a socket.

This server closes that gap honestly. It speaks the real ``/api/chat`` contract
over a real TCP socket on loopback, so ``OllamaBackend`` is exercised end to end
— the same object that would talk to a real Ollama. What it does **not** do is
think: it returns scripted text. It is named ``Fake...`` for that reason, and it
is documented as such everywhere it appears.

The distinction that matters
----------------------------
* It **is** evidence that the Ollama path works: validate → POST → decode →
  extract → write → verify.
* It **is not** evidence about any model's quality. A scripted answer has no
  quality. Any report built on this server records ``model_called=True`` and a
  backend name of ``fake-ollama``, so nobody can mistake one for the other.

Runs on port 0 by default, so tests never collide, and it is stdlib-only —
``http.server`` and ``threading`` — because ``fleet2027`` must not gain a
dependency to test its own transport.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


@dataclass
class FakeOllamaServer:
    """Loopback HTTP server implementing Ollama's ``/api/chat`` contract.

    ``responses`` is consumed in order; the last one repeats so a retry does not
    exhaust it. Every request body is recorded in ``requests`` so a test can
    assert what the client actually sent — that is how the prompt path is
    verified rather than assumed.
    """

    responses: list[str] = field(default_factory=list)
    model: str = "fake-model:1b"
    requests: list[dict[str, Any]] = field(default_factory=list)
    url: str = ""
    _server: HTTPServer | None = None
    _thread: threading.Thread | None = None

    # -- lifecycle ------------------------------------------------------- #

    def start(self) -> str:
        """Start serving on a free loopback port and return the base URL."""
        if self._server is not None:
            return self.url
        handler = _handler_factory(self)
        self._server = HTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._server.server_address[:2]
        self.url = f"http://{host}:{port}"
        return self.url

    def stop(self) -> None:
        """Stop serving and release the port."""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> FakeOllamaServer:
        """Start on entry so ``with`` blocks cannot leak a server.

        Calling ``start()`` again on an already-running server returns the
        existing URL rather than opening a second socket — a context manager
        that leaks a port per use would make the tests flaky.
        """
        if self._server is None:
            self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        """Stop on exit, whatever happened."""
        self.stop()

    # -- behaviour ------------------------------------------------------- #

    def next_response(self) -> str:
        """Return the next scripted answer, repeating the last one."""
        if not self.responses:
            return "```text\nno scripted response\n```"
        index = min(len(self.requests) - 1, len(self.responses) - 1)
        return self.responses[max(index, 0)]


def _handler_factory(server: FakeOllamaServer) -> type[BaseHTTPRequestHandler]:
    """Build a handler class bound to one server instance."""

    class Handler(BaseHTTPRequestHandler):
        """Answers ``/api/chat`` and ``/api/tags``; everything else is a 404."""

        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003
            """Silence the default stderr logging; tests do not need a request log."""

        def do_POST(self) -> None:  # noqa: N802 - http.server's required name
            """Handle a chat completion request."""
            if not self.path.rstrip("/").endswith("/api/chat"):
                self._send(404, {"error": f"unknown path {self.path}"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self._send(400, {"error": "body is not json"})
                return
            if not isinstance(payload, dict):
                self._send(400, {"error": "body is not an object"})
                return
            server.requests.append(payload)
            # The real contract: a message object with a string content field.
            self._send(
                200,
                {
                    "model": payload.get("model", server.model),
                    "created_at": "2026-01-01T00:00:00Z",
                    "message": {"role": "assistant", "content": server.next_response()},
                    "done": True,
                },
            )

        def do_GET(self) -> None:  # noqa: N802 - http.server's required name
            """Expose ``/api/tags`` so readiness probes can pass."""
            if self.path.rstrip("/").endswith("/api/tags"):
                self._send(200, {"models": [{"name": server.model}]})
                return
            self._send(404, {"error": f"unknown path {self.path}"})

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            """Write one JSON response with a correct length."""
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler
