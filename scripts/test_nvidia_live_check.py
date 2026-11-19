#!/usr/bin/env python3
"""Offline tests for the NVIDIA live-check script; no key and no network."""

from __future__ import annotations

import contextlib
import io
import json
import unittest
import urllib.error
from email.message import Message
from typing import Self

import nvidia_live_check as nlc


class FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self.body if size < 0 else self.body[:size]


class NvidiaLiveCheckTests(unittest.TestCase):
    def test_one_small_request_uses_fixed_https_endpoint_and_header_key(self) -> None:
        key = "unit-test-nvidia-key"
        requests = []

        def opener(request, *, timeout):
            requests.append(request)
            self.assertEqual(request.full_url, nlc.ENDPOINT)
            self.assertTrue(request.full_url.startswith("https://"))
            self.assertNotIn(key, request.full_url)
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(request.get_header("Authorization"), f"Bearer {key}")
            payload = json.loads(request.data)
            self.assertNotIn(key, json.dumps(payload))
            self.assertEqual(payload["model"], "meta/example-instruct")
            self.assertEqual(payload["messages"][0]["content"], nlc.PROMPT)
            self.assertEqual(payload["max_tokens"], 16)
            self.assertEqual(payload["temperature"], 0)
            self.assertFalse(payload["stream"])
            self.assertEqual(timeout, 20.0)
            return FakeResponse(
                200,
                json.dumps(
                    {
                        "choices": [{"message": {"content": "pong"}}],
                        "usage": {"prompt_tokens": 9, "completion_tokens": 2},
                    }
                ).encode(),
            )

        result = nlc.check(model="meta/example-instruct", key=key, opener=opener)
        self.assertEqual(len(requests), 1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["credential"], "present")
        self.assertEqual(result["response_chars"], 4)
        self.assertEqual(result["usage"], {"prompt_tokens": 9, "completion_tokens": 2})
        self.assertNotIn(key, json.dumps(result))

    def test_missing_key_skips_without_network(self) -> None:
        calls = []
        result = nlc.check(
            model="meta/example-instruct",
            key="",
            opener=lambda *args, **kwargs: calls.append(args),
        )
        self.assertEqual(result["error_kind"], "no_key")
        self.assertEqual(result["credential"], "absent")
        self.assertEqual(calls, [])

    def test_invalid_model_is_rejected_before_network(self) -> None:
        calls = []
        result = nlc.check(
            model="https://attacker.invalid/model",
            key="unit-test-nvidia-key",
            opener=lambda *args, **kwargs: calls.append(args),
        )
        self.assertEqual(result["error_kind"], "invalid_model")
        self.assertEqual(calls, [])

    def test_provider_error_body_is_never_read_or_reported(self) -> None:
        key = "unit-test-nvidia-key"
        headers = Message()
        body = (key + " must not leak").encode()

        def opener(request, *, timeout):
            raise urllib.error.HTTPError(
                nlc.ENDPOINT,
                401,
                "Unauthorized",
                headers,
                io.BytesIO(body),
            )

        result = nlc.check(model="meta/example-instruct", key=key, opener=opener)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 401)
        self.assertEqual(result["error_kind"], "auth_or_permission")
        self.assertNotIn(key, json.dumps(result))

    def test_response_size_is_bounded(self) -> None:
        def opener(request, *, timeout):
            return FakeResponse(200, b"x" * (nlc.MAX_RESPONSE_BYTES + 1))

        result = nlc.check(
            model="meta/example-instruct",
            key="unit-test-nvidia-key",
            opener=opener,
        )
        self.assertEqual(result["error_kind"], "response_too_large")
        self.assertFalse(result["ok"])

    def test_cli_json_never_contains_the_key(self) -> None:
        key = "unit-test-nvidia-key"

        def opener(request, *, timeout):
            return FakeResponse(
                200,
                b'{"choices":[{"message":{"content":"pong"}}]}',
            )

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = nlc.main(
                ["--json"],
                env={"NVIDIA_API_KEY": key, "NVIDIA_MODEL": "meta/example-instruct"},
                opener=opener,
            )
        self.assertEqual(result, 0)
        self.assertNotIn(key, output.getvalue())
        self.assertTrue(json.loads(output.getvalue())["ok"])

    def test_http_410_is_classified_as_model_retired(self) -> None:
        key = "unit-test-nvidia-key"

        def opener(request, *, timeout):
            raise urllib.error.HTTPError(
                nlc.ENDPOINT,
                410,
                "Gone",
                Message(),
                io.BytesIO(b"gone"),
            )

        result = nlc.check(model="meta/example-instruct", key=key, opener=opener)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 410)
        self.assertEqual(result["error_kind"], "model_retired")

    def test_reasoning_content_fallback_when_content_empty(self) -> None:
        def opener(request, *, timeout):
            return FakeResponse(
                200,
                json.dumps(
                    {"choices": [{"message": {"content": None, "reasoning_content": "pong"}}]}
                ).encode(),
            )

        result = nlc.check(
            model="meta/example-instruct",
            key="unit-test-nvidia-key",
            opener=opener,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["response_chars"], 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
