from __future__ import annotations

import json
import sys
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from substrate.http_server import MAX_REQUEST_BYTES, make_server


TOKEN = "test-token-" + "x" * 40


class FakeGateway:
    def get_status(self, name):
        return {"name": name, "phase": "Running", "desiredState": "Running"}

    def invoke(self, name, payload):
        return {"name": name, "accepted": True, "payload": payload}

    def set_desired_state(self, name, state):
        return {"name": name, "desiredState": state}


class FakeAPI:
    def list_agents(self):
        return []


class HTTPServerTests(unittest.TestCase):
    def setUp(self):
        self.server = make_server(("127.0.0.1", 0), FakeGateway(), FakeAPI(), TOKEN)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.root = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, *, method="GET", body=None, token=None):
        raw = None if body is None else json.dumps(body).encode("utf-8")
        headers = {}
        if raw is not None:
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        req = Request(self.root + path, data=raw, headers=headers, method=method)
        try:
            with urlopen(req, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_health_is_public_but_agent_status_requires_bearer(self):
        status, body = self.request("/healthz")
        self.assertEqual((status, body["status"]), (200, "ok"))
        status, _ = self.request("/v1/agents/counter")
        self.assertEqual(status, 401)
        status, body = self.request("/v1/agents/counter", token=TOKEN)
        self.assertEqual(status, 200)
        self.assertEqual(body["phase"], "Running")

    def test_invoke_proxies_json_and_suspend_returns_accepted(self):
        status, body = self.request(
            "/v1/agents/counter/invoke",
            method="POST",
            body={"op": "increment", "by": 4},
            token=TOKEN,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["payload"]["by"], 4)
        status, body = self.request(
            "/v1/agents/counter/suspend", method="POST", body={}, token=TOKEN,
        )
        self.assertEqual(status, 202)
        self.assertEqual(body["desiredState"], "Sleeping")

    def test_oversized_request_is_rejected(self):
        raw = b"x" * (MAX_REQUEST_BYTES + 1)
        req = Request(
            self.root + "/v1/agents/counter/invoke",
            data=raw,
            headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(req, timeout=3)
        self.assertEqual(caught.exception.code, 413)

    def test_unknown_route_does_not_call_gateway(self):
        status, body = self.request("/v1/agents/counter/delete", method="POST", body={}, token=TOKEN)
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "not found")


if __name__ == "__main__":
    unittest.main()
