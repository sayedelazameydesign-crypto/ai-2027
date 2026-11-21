from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

APP = Path(__file__).resolve().parents[1] / "examples" / "counter" / "app.py"
SPEC = importlib.util.spec_from_file_location("substrate_counter_app", APP)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CounterStore = MODULE.CounterStore
make_server = MODULE.make_server


class CounterStoreTests(unittest.TestCase):
    def test_state_survives_restart_and_request_id_prevents_duplicate_increment(self):
        with tempfile.TemporaryDirectory() as directory:
            first = CounterStore(directory)
            self.assertEqual(first.increment("request-1", 3), {"value": 3, "duplicate": False})
            restarted = CounterStore(directory)
            self.assertEqual(restarted.increment("request-1", 3), {"value": 3, "duplicate": True})
            self.assertEqual(restarted.increment("request-2", 2), {"value": 5, "duplicate": False})
            self.assertEqual(restarted.checkpoint(), {"ok": True, "value": 5})
            self.assertEqual(CounterStore(directory).increment("request-3", -1)["value"], 4)

    def test_invalid_request_id_or_delta_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CounterStore(directory)
            with self.assertRaises(ValueError):
                store.increment("bad id", 1)
            with self.assertRaises(ValueError):
                store.increment("request-1", True)
            with self.assertRaises(ValueError):
                store.increment("request-2", 1001)
            self.assertFalse((Path(directory) / "counter-state.json").exists())


class CounterHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.token = "counter-token-" + "c" * 40
        with patch.dict(os.environ, {
            "SUBSTRATE_ACTOR_TOKEN": self.token,
            "SUBSTRATE_WORKSPACE": self.tmp.name,
        }):
            self.server = make_server("127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.root = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def test_health_is_public_but_mutations_require_actor_token(self):
        with urlopen(self.root + "/healthz", timeout=3) as response:
            self.assertEqual(response.status, 200)
        body = json.dumps({"op": "increment", "by": 2, "requestId": "http-1"}).encode()
        req = Request(self.root + "/invoke", data=body, headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(HTTPError) as caught:
            urlopen(req, timeout=3)
        self.assertEqual(caught.exception.code, 401)
        req = Request(
            self.root + "/invoke",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
            method="POST",
        )
        with urlopen(req, timeout=3) as response:
            result = json.loads(response.read())
        self.assertEqual(result["value"], 2)


if __name__ == "__main__":
    unittest.main()
