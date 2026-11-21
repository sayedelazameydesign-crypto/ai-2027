from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from substrate.kube import AgentRequestError, KubernetesAPI, KubernetesError


class Response:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, size=-1):
        return self.body if size < 0 else self.body[:size]


class FakeOpener:
    def __init__(self, body: bytes = b'{"ok":true}') -> None:
        self.body = body
        self.calls = []
        self.http_error = None

    def __call__(self, request, **kwargs):
        self.calls.append((request, kwargs))
        if self.http_error:
            raise self.http_error
        return Response(self.body)


class KubernetesAPITests(unittest.TestCase):
    def test_kubernetes_requests_use_scoped_path_and_bearer(self):
        opener = FakeOpener(b'{"metadata":{"name":"counter"}}')
        api = KubernetesAPI("http://127.0.0.1:8443", "kube-token", "agent-substrate", opener=opener)
        result = api.get_agent("counter")
        request, _ = opener.calls[0]
        self.assertEqual(result["metadata"]["name"], "counter")
        self.assertEqual(request.full_url, "http://127.0.0.1:8443/apis/substrate.ai/v1alpha1/namespaces/agent-substrate/agents/counter")
        self.assertEqual(request.get_header("Authorization"), "Bearer kube-token")

    def test_projected_service_account_token_is_reread_after_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / "token"
            token_file.write_text("first-service-account-token", encoding="utf-8")
            opener = FakeOpener(b'{"items":[]}')
            environment = {
                "SUBSTRATE_KUBERNETES_TOKEN_FILE": str(token_file),
                "SUBSTRATE_NAMESPACE": "agent-substrate",
                "SUBSTRATE_KUBERNETES_URL": "https://kubernetes.default.svc",
            }
            with patch.dict(os.environ, environment, clear=True):
                api = KubernetesAPI.from_environment()
                api._opener = opener
                api.list_agents()
                token_file.write_text("rotated-service-account-token", encoding="utf-8")
                api.list_agents()

            authorization = [request.get_header("Authorization") for request, _ in opener.calls]
            self.assertEqual(authorization, [
                "Bearer first-service-account-token",
                "Bearer rotated-service-account-token",
            ])

    def test_ipv6_kubernetes_service_address_is_bracketed(self):
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / "token"
            token_file.write_text("service-account-token", encoding="utf-8")
            environment = {
                "SUBSTRATE_KUBERNETES_TOKEN_FILE": str(token_file),
                "SUBSTRATE_NAMESPACE": "agent-substrate",
                "KUBERNETES_SERVICE_HOST": "fd00::1",
                "KUBERNETES_SERVICE_PORT": "443",
            }
            with patch.dict(os.environ, environment, clear=True):
                api = KubernetesAPI.from_environment()
            self.assertEqual(api.base_url, "https://[fd00::1]:443")

    def test_actor_requests_use_fixed_endpoint_and_per_actor_bearer(self):
        opener = FakeOpener(b'{"ok":true}')
        api = KubernetesAPI("http://127.0.0.1:8443", "kube-token", "agent-substrate", opener=opener)
        result = api.post_to_pod(
            {"status": {"podIP": "10.42.0.7"}},
            "/checkpoint",
            {},
            actor_token="per-actor-token-" + "p" * 32,
        )
        request, _ = opener.calls[0]
        self.assertEqual(result, {"ok": True})
        self.assertEqual(request.full_url, "http://10.42.0.7:8080/checkpoint")
        self.assertEqual(request.get_header("Authorization"), "Bearer per-actor-token-" + "p" * 32)

    def test_invalid_actor_endpoint_and_address_are_rejected(self):
        api = KubernetesAPI("http://127.0.0.1:8443", "kube-token", "agent-substrate", opener=FakeOpener())
        with self.assertRaises(ValueError):
            api.post_to_pod({"status": {"podIP": "10.0.0.2"}}, "/admin", {}, actor_token="x" * 40)
        with self.assertRaises(AgentRequestError):
            api.post_to_pod({"status": {"podIP": "not-an-ip"}}, "/invoke", {}, actor_token="x" * 40)

    def test_remote_kubernetes_api_requires_tls(self):
        with self.assertRaises(ValueError):
            KubernetesAPI("http://kube.example:8080", "token", "agent-substrate")

    def test_actor_error_body_does_not_escape(self):
        opener = FakeOpener()
        opener.http_error = HTTPError(
            "http://10.42.0.7:8080/invoke", 403, "Forbidden", {}, io.BytesIO(b"private actor detail"),
        )
        api = KubernetesAPI("http://127.0.0.1:8443", "kube-token", "agent-substrate", opener=opener)
        with self.assertRaises(AgentRequestError) as caught:
            api.post_to_pod(
                {"status": {"podIP": "10.42.0.7"}}, "/invoke", {}, actor_token="x" * 40,
            )
        self.assertNotIn("private actor detail", str(caught.exception))

    def test_kubernetes_error_body_is_not_included(self):
        opener = FakeOpener()
        opener.http_error = HTTPError(
            "http://127.0.0.1:8443/api", 403, "Forbidden", {}, io.BytesIO(b"secret-body"),
        )
        api = KubernetesAPI("http://127.0.0.1:8443", "kube-token", "agent-substrate", opener=opener)
        with self.assertRaises(KubernetesError) as caught:
            api.list_agents()
        self.assertNotIn("secret-body", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
