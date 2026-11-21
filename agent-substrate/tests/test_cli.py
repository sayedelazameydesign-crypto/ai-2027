from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import ProxyHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from substrate.cli import _request


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size: int) -> bytes:
        return self.body


class CLIRequestTests(unittest.TestCase):
    def _run(self, origin: str, timeout: float = 4.5):
        environment = {
            "SUBSTRATE_GATEWAY_URL": origin,
            "SUBSTRATE_API_TOKEN": "t" * 40,
        }
        with patch.dict(os.environ, environment, clear=True):
            with patch("substrate.cli.build_opener") as build_opener:
                build_opener.return_value.open.return_value = FakeResponse(b'{"ok":true}')
                result = _request("/v1/agents/counter", method="GET", timeout=timeout)
        return result, build_opener

    def test_loopback_port_forward_bypasses_environment_proxies(self):
        result, build_opener = self._run("http://127.0.0.1:8080")
        self.assertEqual(result, {"ok": True})
        handlers = build_opener.call_args.args
        proxies = [handler for handler in handlers if isinstance(handler, ProxyHandler)]
        self.assertEqual(len(proxies), 1)
        self.assertEqual(proxies[0].proxies, {})
        self.assertEqual(build_opener.return_value.open.call_args.kwargs["timeout"], 4.5)

    def test_remote_gateway_keeps_default_proxy_behavior(self):
        result, build_opener = self._run("https://gateway.example")
        self.assertEqual(result, {"ok": True})
        handlers = build_opener.call_args.args
        self.assertFalse(any(isinstance(handler, ProxyHandler) for handler in handlers))
        self.assertEqual(build_opener.return_value.open.call_args.kwargs["timeout"], 4.5)


if __name__ == "__main__":
    unittest.main()
