#!/usr/bin/env python3
"""Credential-routing tests: local test credentials must never reach remote hosts."""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import live_agent_auth as auth  # noqa: E402


class LiveAgentCredentialRoutingTests(unittest.TestCase):
    def test_public_health_needs_no_header_and_remote_hosts_get_no_local_fallback(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(auth.authorization_header(
                "http://127.0.0.1:8090", "/api/health", "GET"))
            self.assertIsNone(auth.authorization_header(
                "https://api.example.invalid", "/api/runs", "GET"))
            self.assertIsNone(auth.authorization_header(
                "https://api.example.invalid", "/api/run", "POST"))

    def test_authenticated_redirects_are_refused(self):
        request = urllib.request.Request(
            "https://api.example.invalid/api/run",
            headers={"Authorization": "Bearer " + "synthetic-token-" + "x" * 40},
        )
        redirect = auth._NoAuthenticatedRedirect()
        self.assertIsNone(redirect.redirect_request(
            request, None, 302, "Found", {}, "https://other.example.invalid/capture"))

    def test_open_url_refuses_a_bearer_request_to_remote_http(self):
        request = urllib.request.Request(
            "http://api.example.invalid/api/runs",
            headers={"Authorization": "Bearer " + "synthetic-token-" + "x" * 40},
        )
        with self.assertRaisesRegex(ValueError, "insecure transport"):
            auth.open_url(request, timeout=1)

    def test_open_url_does_not_forward_bearer_to_redirect_target(self):
        captured = []

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/start":
                    self.send_response(302)
                    self.send_header("Location", "/capture")
                    self.end_headers()
                    return
                captured.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *_args):
                return None

        server = HTTPServer(("127.0.0.1", 0), RedirectHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/start",
            headers={"Authorization": "Bearer " + "synthetic-token-" + "x" * 40},
        )
        try:
            with self.assertRaises(urllib.error.HTTPError) as context:
                auth.open_url(request, timeout=2)
            self.assertEqual(context.exception.code, 302)
            context.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(captured, [])

    def test_loopback_bearer_request_bypasses_configured_http_proxy(self):
        captured = []

        class CaptureHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                captured.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"local")

            def log_message(self, *_args):
                return None

        server = HTTPServer(("127.0.0.1", 0), CaptureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/runs",
            headers={"Authorization": "Bearer " + "synthetic-token-" + "x" * 40},
        )
        try:
            with patch.object(urllib.request, "getproxies",
                              return_value={"http": "http://127.0.0.1:1"}), \
                    patch.object(urllib.request, "proxy_bypass", return_value=False):
                with auth.open_url(request, timeout=2) as response:
                    self.assertEqual(response.read(), b"local")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(captured, [request.get_header("Authorization")])

    def test_explicit_environment_credentials_can_target_an_approved_remote(self):
        with patch.dict(os.environ, {
            "AI2027_READ_TOKEN_ID": "test-reader",
            "AI2027_READ_TOKEN_SECRET": "synthetic-read-secret-" + "r" * 40,
            "AI2027_WRITE_TOKEN": "synthetic-write-secret-" + "w" * 40,
        }, clear=True):
            self.assertEqual(
                auth.authorization_header("https://api.example.invalid", "/api/runs", "GET"),
                "Bearer test-reader." + "synthetic-read-secret-" + "r" * 40,
            )
            self.assertEqual(
                auth.authorization_header("https://api.example.invalid", "/api/run", "POST"),
                "Bearer synthetic-write-secret-" + "w" * 40,
            )
            self.assertIsNone(auth.authorization_header(
                "https://api.example.invalid", "/api/memory", "POST"))
            self.assertIsNone(auth.authorization_header(
                "https://api.example.invalid", "/api/run", "DELETE"))
            self.assertIsNone(auth.authorization_header(
                "http://api.example.invalid", "/api/runs", "GET"))
            self.assertIsNone(auth.authorization_header(
                "http://api.example.invalid", "/api/run", "POST"))
            self.assertIsNone(auth.authorization_header(
                "https://reader:password@api.example.invalid", "/api/runs", "GET"))
            self.assertEqual(
                auth.authorization_header("http://127.0.0.1:8090", "/api/runs", "GET"),
                "Bearer test-reader." + "synthetic-read-secret-" + "r" * 40,
            )

    def test_operator_tokens_accept_symbols_and_only_the_shared_boundaries(self):
        valid = ("+/=" + "A" * 29, "+/=" + "A" * 509)
        self.assertEqual(tuple(map(len, valid)), (32, 512))
        invalid = (
            "A" * 31,
            "A" * 513,
            "A" * 16 + " " + "A" * 16,
            "A" * 16 + "\t" + "A" * 16,
            "A" * 32 + "\n",
            "A" * 31 + "é",
        )
        with patch.dict(os.environ, {}, clear=True):
            for token in valid:
                with self.subTest(valid_length=len(token)):
                    with patch.dict(os.environ, {"AI2027_WRITE_TOKEN": token}):
                        self.assertEqual(
                            auth.authorization_header(
                                "https://api.example.invalid", "/api/run", "POST"),
                            f"Bearer {token}",
                        )
            for token in invalid:
                with self.subTest(invalid_length=len(token)):
                    with patch.dict(os.environ, {"AI2027_WRITE_TOKEN": token}):
                        self.assertIsNone(auth.authorization_header(
                            "https://api.example.invalid", "/api/run", "POST"))

    def test_saved_credentials_are_ignored_on_non_posix_platforms(self):
        with patch.object(auth.os, "name", "nt"), \
                patch.dict(os.environ, {}, clear=True), \
                patch.object(auth, "_LOCAL_CREDENTIALS") as credentials_path:
            self.assertIsNone(auth.authorization_header(
                "http://127.0.0.1:8090", "/api/runs", "GET"))
            credentials_path.is_symlink.assert_not_called()
            credentials_path.open.assert_not_called()

    def test_private_loopback_credentials_require_owner_only_file_metadata(self):
        if os.name != "posix":
            self.skipTest("Windows ACL privacy cannot be verified with the stdlib helper")
        with tempfile.TemporaryDirectory(prefix="ai2027-auth-helper-") as tmp:
            state = Path(tmp) / ".ember-up"
            state.mkdir(mode=0o700)
            state.chmod(0o700)
            credential_path = state / "api-credentials.json"
            reader_id = "local-test-reader"
            reader_secret = "synthetic-local-read-secret-" + "r" * 40
            writer = "+/=" + "w" * 29
            fixture = {
                "read_id": reader_id,
                "read_secret": reader_secret,
                "write_token": writer,
                "read_hashes": {reader_id: hashlib.sha256(reader_secret.encode()).hexdigest()},
            }
            # Exercise ownership and mode checks against a real private file,
            # but supply its synthetic contents in memory so no test token is
            # ever serialized to disk.
            credential_path.touch(mode=0o600)
            credential_path.chmod(0o600)
            with patch.object(auth, "_LOCAL_CREDENTIALS", credential_path), \
                    patch.object(auth.json, "load", return_value=fixture), \
                    patch.dict(os.environ, {}, clear=True):
                self.assertEqual(auth.authorization_header(
                    "http://127.0.0.1:8090", "/api/runs", "GET"),
                    f"Bearer {reader_id}.{reader_secret}")
                self.assertEqual(auth.authorization_header(
                    "http://localhost:8090", "/api/run", "POST"), f"Bearer {writer}")
                self.assertIsNone(auth.authorization_header(
                    "https://api.example.invalid", "/api/runs", "GET"))
                self.assertEqual(auth.authorization_header(
                    "http://127.0.0.1:8090", "/api/file", "GET"),
                    f"Bearer {reader_id}.{reader_secret}")
                with patch.object(auth.json, "load", return_value={
                    **fixture, "write_token": "W" * 31,
                }):
                    self.assertIsNone(auth.authorization_header(
                        "http://127.0.0.1:8090", "/api/run", "POST"))
                credential_path.chmod(0o644)
                self.assertIsNone(auth.authorization_header(
                    "http://127.0.0.1:8090", "/api/runs", "GET"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
