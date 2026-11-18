#!/usr/bin/env python3
"""Tests for the Gemini live check — the key must never appear in the output.

No network: every request is driven by an injected opener.
Run: python3 scripts/test_gemini_live_check.py -v
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gemini_live_check as glc  # noqa: E402

KEY = "AIza" + "TesT" + "k" * 30          # 39 chars, shaped like a real key
SECRET_LIKE = KEY


class _Resp:
    def __init__(self, status: int, raw: bytes):
        self.status = status
        self._raw = raw

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener_returning(status: int, payload: dict | bytes):
    calls = []

    def _open(req, timeout=None):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        calls.append({"url": req.full_url, "headers": dict(req.header_items()),
                      "body": req.data, "method": req.get_method()})
        if status >= 400:
            # an HTTPError carries a body; a provider's error JSON lives there
            raise urllib.error.HTTPError(req.full_url, status, "err", {},
                                         io.BytesIO(raw))
        return _Resp(status, raw)
    _open.calls = calls
    return _open


GOOD = {"candidates": [{"content": {"parts": [{"text": "pong"}]},
                        "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 8, "candidatesTokenCount": 1}}


class SecurityTests(unittest.TestCase):
    key = KEY

    def test_01_the_key_never_appears_in_the_result(self):
        out = glc.check(key=KEY, opener=opener_returning(200, GOOD),
                        require_reachability=False)
        self.assertTrue(out["ok"])
        self.assertNotIn(KEY, json.dumps(out))

    def test_02_the_key_never_appears_on_stdout(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            glc.main(["--no-preflight"], env={"GEMINI_API_KEY": KEY},
                     opener=opener_returning(200, GOOD))
        self.assertNotIn(KEY, buf.getvalue())
        self.assertIn("sha256:", buf.getvalue())

    def test_03_the_key_is_sent_as_a_header_never_in_the_url(self):
        """A key in a URL ends up in proxy logs, error pages and bug reports."""
        opener = opener_returning(200, GOOD)
        glc.check(key=KEY, opener=opener, require_reachability=False)
        call = opener.calls[0]
        self.assertNotIn(KEY, call["url"])
        self.assertNotIn("key=", call["url"])
        headers = {k.lower(): v for k, v in call["headers"].items()}
        self.assertEqual(headers.get("x-goog-api-key"), KEY)

    def test_04_the_request_is_deliberately_tiny_and_deterministic(self):
        opener = opener_returning(200, GOOD)
        glc.check(key=KEY, opener=opener, require_reachability=False)
        body = json.loads(opener.calls[0]["body"].decode())
        cfg = body["generationConfig"]
        self.assertEqual(cfg["temperature"], 0)
        self.assertLessEqual(cfg["maxOutputTokens"], 32)

    def test_05_the_fingerprint_is_a_hash_never_a_prefix(self):
        """`AIza` is the same for every Google key: a prefix identifies nothing and
        is still key material in a log. CodeQL flagged the first version."""
        fp = glc.fingerprint(KEY)
        self.assertTrue(fp.startswith("sha256:"))
        self.assertIn(str(len(KEY)), fp)
        self.assertNotIn(KEY[:4], fp)
        self.assertNotIn(KEY[4:], fp)
        # deterministic (so rotation can compare) and different keys differ
        self.assertEqual(fp, glc.fingerprint(KEY))
        self.assertNotEqual(fp, glc.fingerprint(KEY + "x"))
        self.assertEqual(glc.fingerprint(""), "(none)")


class ClassificationTests(unittest.TestCase):
    def _kind(self, status, payload=b"", key=KEY):
        return glc.check(key=key, opener=opener_returning(status, payload),
                         require_reachability=False)["error_kind"]

    def test_10_each_http_failure_has_its_own_name(self):
        self.assertEqual(self._kind(401, b"{}"), "auth_or_permission")
        self.assertEqual(self._kind(403, b"{}"), "auth_or_permission")
        self.assertEqual(self._kind(429, b"{}"), "quota_or_rate_limit")
        self.assertEqual(self._kind(404, b"{}"), "model_not_found")
        self.assertEqual(self._kind(400, b"{}"), "bad_request")
        self.assertEqual(self._kind(503, b"{}"), "upstream_error")

    def test_11_a_safety_block_is_named_not_called_empty(self):
        payload = {"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []}
        out = glc.check(key=KEY, opener=opener_returning(200, payload),
                        require_reachability=False)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error_kind"], "no_candidates")
        self.assertEqual(out["block_reason"], "SAFETY")

    def test_12_a_non_json_answer_is_reported_as_such(self):
        out = glc.check(key=KEY, opener=opener_returning(200, b"<html>oops</html>"),
                        require_reachability=False)
        self.assertEqual(out["error_kind"], "response is not JSON")

    def test_13_a_transport_failure_is_network_not_a_fake_verdict(self):
        def boom(req, timeout=None):
            raise urllib.error.URLError("connection reset")

        out = glc.check(key=KEY, opener=boom, require_reachability=False)
        self.assertEqual(out["error_kind"], "network")
        self.assertIsNone(out["status"])

    def test_14_an_api_error_body_is_surfaced_without_the_key(self):
        payload = {"error": {"status": "PERMISSION_DENIED",
                             "message": f"API key not valid: {KEY}"}}
        out = glc.check(key=KEY, opener=opener_returning(403, payload),
                        require_reachability=False)
        self.assertEqual(out["error_kind"], "auth_or_permission")
        self.assertEqual(out["api_status"], "PERMISSION_DENIED")
        # the upstream message quotes the key — it must be scrubbed before printing
        self.assertNotIn(KEY, json.dumps(out))


class ExitCodeTests(unittest.TestCase):
    def _run(self, argv, env, opener=None):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = glc.main(argv, env=env, opener=opener)
        return code, buf.getvalue()

    def test_20_no_key_is_skipped_not_failed(self):
        code, out = self._run(["--no-preflight"], env={})
        self.assertEqual(code, glc.NO_KEY)
        self.assertIn("no_key", out)

    def test_21_success_is_zero(self):
        code, _ = self._run(["--no-preflight", "--json"], env={"GEMINI_API_KEY": KEY},
                            opener=opener_returning(200, GOOD))
        self.assertEqual(code, glc.OK)

    def test_22_a_real_failure_is_one(self):
        code, _ = self._run(["--no-preflight"], env={"GEMINI_API_KEY": KEY},
                            opener=opener_returning(429, b"{}"))
        self.assertEqual(code, glc.FAILED)

    def test_23_blindness_is_three_not_one(self):
        import network_preflight as npf
        original = npf.cached_preflight
        npf.cached_preflight = lambda *a, **k: npf.Report(npf.ALLOWLISTED_ONLY, ())
        try:
            code, out = self._run(["--json"], env={"GEMINI_API_KEY": KEY})
        finally:
            npf.cached_preflight = original
        self.assertEqual(code, glc.BLOCKED)
        self.assertIn("blocked", out)

    def test_24_the_model_can_be_overridden_and_is_recorded(self):
        opener = opener_returning(200, GOOD)
        code, out = self._run(["--no-preflight", "--json", "--model", "gemini-x"],
                              env={"GEMINI_API_KEY": KEY}, opener=opener)
        self.assertEqual(code, glc.OK)
        self.assertEqual(json.loads(out)["model"], "gemini-x")
        self.assertIn("gemini-x", opener.calls[0]["url"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
