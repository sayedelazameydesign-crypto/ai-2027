"""Tiny durable example actor implementing the Agent Substrate HTTP contract."""
from __future__ import annotations

import hmac
import json
import os
import re
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

MAX_BODY = 8 * 1024
MAX_REQUEST_IDS = 4096
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class CounterStore:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace)
        self.path = self.workspace / "counter-state.json"
        self.lock = threading.Lock()

    def _load_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"value": 0, "requestIds": []}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("persistent counter state is unreadable") from exc
        if (
            not isinstance(value, dict)
            or type(value.get("value")) is not int
            or not isinstance(value.get("requestIds"), list)
            or any(not isinstance(item, str) for item in value["requestIds"])
        ):
            raise RuntimeError("persistent counter state is invalid")
        return {"value": value["value"], "requestIds": value["requestIds"][-MAX_REQUEST_IDS:]}

    def _save_unlocked(self, state: dict[str, Any]) -> None:
        self.workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".counter-", suffix=".tmp", dir=self.workspace)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(state, stream, separators=(",", ":"), sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.workspace, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def increment(self, request_id: str, amount: int) -> dict[str, Any]:
        if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
            raise ValueError("requestId is invalid")
        if type(amount) is not int or not -1000 <= amount <= 1000:
            raise ValueError("by must be an integer between -1000 and 1000")
        with self.lock:
            state = self._load_unlocked()
            duplicate = request_id in state["requestIds"]
            if not duplicate:
                state["value"] += amount
                state["requestIds"].append(request_id)
                state["requestIds"] = state["requestIds"][-MAX_REQUEST_IDS:]
                self._save_unlocked(state)
            return {"value": state["value"], "duplicate": duplicate}

    def checkpoint(self) -> dict[str, Any]:
        with self.lock:
            state = self._load_unlocked()
            self._save_unlocked(state)
            return {"ok": True, "value": state["value"]}


class Handler(BaseHTTPRequestHandler):
    server_version = "SubstrateCounter/0.1"
    sys_version = ""

    @property
    def store(self) -> CounterStore:
        return self.server.store  # type: ignore[attr-defined]

    @property
    def actor_token(self) -> str:
        return self.server.actor_token  # type: ignore[attr-defined]

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        scheme, _, supplied = header.partition(" ")
        return (
            scheme.lower() == "bearer"
            and bool(supplied)
            and hmac.compare_digest(supplied.encode("utf-8"), self.actor_token.encode("utf-8"))
        )

    def _json(self, status: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        self.wfile.write(raw)

    def _read(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError:
            raise ValueError("Invalid Content-Length") from None
        if length < 0 or length > MAX_BODY:
            raise OverflowError("Request body too large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Body must be valid JSON") from None
        if not isinstance(value, dict):
            raise ValueError("Body must be a JSON object")
        return value

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/healthz":
            self._json(404, {"error": "not found"})
            return
        self._json(200, {"status": "ok"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._authorized():
            self._json(401, {"error": "authentication required"})
            return
        if self.path not in {"/invoke", "/checkpoint"}:
            self._json(404, {"error": "not found"})
            return
        try:
            body = self._read()
        except OverflowError:
            self._json(413, {"error": "request body too large"})
            return
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        try:
            if self.path == "/checkpoint":
                self._json(200, self.store.checkpoint())
                return
            if body.get("op") != "increment":
                self._json(400, {"error": "unsupported operation"})
                return
            result = self.store.increment(body.get("requestId"), body.get("by", 1))
            self._json(200, result)
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except RuntimeError:
            self._json(500, {"error": "persistent state unavailable"})

    def log_message(self, fmt: str, *args: Any) -> None:
        # Do not log request bodies or arbitrary actor inputs.
        print("counter-http " + fmt % args, flush=True)


def make_server(host: str = "0.0.0.0", port: int = 8080) -> ThreadingHTTPServer:
    workspace = os.environ.get("SUBSTRATE_WORKSPACE", "/workspace")
    actor_token = os.environ.get("SUBSTRATE_ACTOR_TOKEN", "")
    if len(actor_token) < 32:
        raise RuntimeError("SUBSTRATE_ACTOR_TOKEN is unavailable")
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.store = CounterStore(workspace)  # type: ignore[attr-defined]
    server.actor_token = actor_token  # type: ignore[attr-defined]
    return server


if __name__ == "__main__":
    make_server().serve_forever()
