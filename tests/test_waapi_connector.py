#!/usr/bin/env python3
"""Hermetic tests for src/connectors/waapi.py (stdlib only, no network).

The transport attribute is swapped for a fake, so every branch — token
absence, shape refusals, 422, oversized bodies, redirects, network errors —
runs bare in CI. The token itself must never appear in any result, and no
test ever touches the real provider.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WAAPI_PATH = ROOT / "src" / "connectors" / "waapi.py"


def _load():
    spec = importlib.util.spec_from_file_location("waapi", WAAPI_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["waapi"] = module
    spec.loader.exec_module(module)
    return module


w = _load()

SECRET = "fixture-" + "sentinel-token"
INSTANCES_URL = w.BASE_URL
STATUS_URL = w.BASE_URL + "/777/client/status"
SEND_URL = w.BASE_URL + "/777/client/action/send-message"

INSTANCES_OK = {"status": "success", "instances": [{"id": 777}]}
STATUS_OK = {"status": "success",
             "clientStatus": {"status": "success", "instanceId": "777",
                              "instanceStatus": "ready"}}


class FakeTransport:
    """Records every call; serves canned responses per URL."""

    def __init__(self, responses=None, error=None):
        self.calls = []
        self.responses = responses or {}
        self.error = error

    def __call__(self, method, url, body, token):
        self.calls.append({"method": method, "url": url, "body": body,
                           "token": token})
        if self.error is not None:
            return self.error
        result = self.responses.get(url, w.TransportResult(
            "http_error", 404, None))
        return result


class EnvCase(unittest.TestCase):
    """Preserve the process environment around every test."""

    def setUp(self):
        self._saved = dict(os.environ)
        self._transport = w._transport
        os.environ["WAAPI_API_TOKEN"] = SECRET

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)
        w._transport = self._transport

    def ok_transport(self, responses):
        w._transport = FakeTransport(responses)


class PreflightTests(EnvCase):
    def test_preflight_ok_with_token(self):
        self.assertEqual(w.preflight(), {"ok": True, "stage": "ok"})

    def test_preflight_token_missing_is_local_and_fail_closed(self):
        os.environ.pop("WAAPI_API_TOKEN", None)
        w._transport = FakeTransport(error=AssertionError("must not call"))
        self.assertEqual(w.preflight(),
                         {"ok": False, "stage": "token_missing"})

    def test_send_without_token_never_touches_transport(self):
        os.environ.pop("WAAPI_API_TOKEN", None)
        w._transport = FakeTransport(error=AssertionError("must not call"))
        result = w.send_text("201100000000@c.us", "hello")
        self.assertEqual(result, {"ok": False, "stage": "token_missing"})


class HealthTests(EnvCase):
    def test_health_success(self):
        self.ok_transport({INSTANCES_URL: w.TransportResult("ok", 200,
                                                            INSTANCES_OK)})
        self.assertEqual(w.health(),
                         {"ok": True, "stage": "instances_ok", "count": 1})

    def test_health_rejects_html(self):
        self.ok_transport({INSTANCES_URL: w.TransportResult("ok", 200,
                                                            "<html>")})
        self.assertEqual(w.health(), {"ok": False, "stage": "shape_invalid"})

    def test_health_rejects_wrong_status_field(self):
        self.ok_transport({INSTANCES_URL: w.TransportResult(
            "ok", 200, {"status": "error", "instances": []})})
        self.assertEqual(w.health(), {"ok": False, "stage": "shape_invalid"})

    def test_health_maps_http_error_without_body(self):
        self.ok_transport({INSTANCES_URL: w.TransportResult("http_error",
                                                            401, None)})
        result = w.health()
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "http_error")
        self.assertEqual(result["http"], 401)

    def test_health_maps_network_error(self):
        self.ok_transport({INSTANCES_URL: w.TransportResult("network_error",
                                                            None, None)})
        self.assertEqual(w.health(),
                         {"ok": False, "stage": "network_error",
                          "http": None})

    def test_health_maps_redirect_refused(self):
        self.ok_transport({INSTANCES_URL: w.TransportResult(
            "redirect_refused", 302, None)})
        self.assertEqual(w.health()["stage"], "redirect_refused")

    def test_health_maps_too_large(self):
        self.ok_transport({INSTANCES_URL: w.TransportResult("too_large",
                                                            200, None)})
        self.assertEqual(w.health()["stage"], "too_large")

    def test_health_maps_not_json(self):
        self.ok_transport({INSTANCES_URL: w.TransportResult("not_json",
                                                            200, None)})
        self.assertEqual(w.health()["stage"], "not_json")


class InstanceTests(EnvCase):
    def test_single_instance_discovered(self):
        self.ok_transport({INSTANCES_URL: w.TransportResult("ok", 200,
                                                            INSTANCES_OK)})
        result = w.instance_id()
        self.assertTrue(result["ok"])
        self.assertEqual(result["instance_id"], "777")

    def test_env_override_wins_without_network(self):
        os.environ["WAAPI_INSTANCE_ID"] = "4242"
        w._transport = FakeTransport(error=AssertionError("must not call"))
        result = w.instance_id()
        self.assertEqual(result["instance_id"], "4242")
        self.assertEqual(result["stage"], "instance_from_env")

    def test_env_override_shape_checked(self):
        os.environ["WAAPI_INSTANCE_ID"] = "not-an-id"
        self.assertEqual(w.instance_id(),
                         {"ok": False, "stage": "instance_id_invalid"})

    def test_zero_instances_refused(self):
        self.ok_transport({INSTANCES_URL: w.TransportResult(
            "ok", 200, {"status": "success", "instances": []})})
        self.assertEqual(w.instance_id(),
                         {"ok": False, "stage": "instance_count_0"})

    def test_multiple_instances_refused(self):
        self.ok_transport({INSTANCES_URL: w.TransportResult(
            "ok", 200, {"status": "success",
                        "instances": [{"id": 1}, {"id": 2}]})})
        self.assertEqual(w.instance_id(),
                         {"ok": False, "stage": "instance_count_2"})

    def test_boolean_id_refused(self):
        self.ok_transport({INSTANCES_URL: w.TransportResult(
            "ok", 200, {"status": "success", "instances": [{"id": True}]})})
        self.assertEqual(w.instance_id(),
                         {"ok": False, "stage": "shape_invalid"})


class ClientStatusTests(EnvCase):
    def test_ready_status(self):
        self.ok_transport({STATUS_URL: w.TransportResult("ok", 200,
                                                         STATUS_OK)})
        result = w.client_status("777")
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "ready")

    def test_not_ready_is_reported_not_hidden(self):
        payload = json.loads(json.dumps(STATUS_OK))
        payload["clientStatus"]["instanceStatus"] = "qr"
        self.ok_transport({STATUS_URL: w.TransportResult("ok", 200,
                                                         payload)})
        result = w.client_status("777")
        self.assertTrue(result["ok"])         # the call succeeded…
        self.assertEqual(result["status"], "qr")  # …and says so honestly

    def test_instance_id_mismatch_refused(self):
        payload = json.loads(json.dumps(STATUS_OK))
        payload["clientStatus"]["instanceId"] = "999"
        self.ok_transport({STATUS_URL: w.TransportResult("ok", 200,
                                                         payload)})
        self.assertEqual(w.client_status("777"),
                         {"ok": False, "stage": "shape_invalid"})


class SendTests(EnvCase):
    def _responses(self, send_result, client_status=None):
        return {
            INSTANCES_URL: w.TransportResult("ok", 200, INSTANCES_OK),
            STATUS_URL: w.TransportResult(
                "ok", 200, client_status or STATUS_OK),
            SEND_URL: send_result,
        }

    def _posts(self):
        return [c for c in w._transport.calls if c["method"] == "POST"]

    def test_send_posts_only_after_client_status_ready(self):
        self.ok_transport(self._responses(
            w.TransportResult("ok", 200, {"status": "success"})))
        result = w.send_text("201100000000@c.us", "hello")
        self.assertEqual(result, {"ok": True, "stage": "sent"})
        # Both GETs happened before the POST: list -> status -> send.
        methods = [c["method"] for c in w._transport.calls]
        self.assertEqual(methods, ["GET", "GET", "POST"])

    def test_not_ready_never_posts(self):
        # Review action item: qr / disconnected must refuse BEFORE any POST.
        for observed in ("qr", "disconnected", "booting", "auth_failure"):
            status = json.loads(json.dumps(STATUS_OK))
            status["clientStatus"]["instanceStatus"] = observed
            w._transport = FakeTransport(self._responses(
                w.TransportResult("ok", 200, {"status": "success"}),
                client_status=status))
            result = w.send_text("201100000000@c.us", "hello")
            self.assertEqual(result,
                             {"ok": False, "stage": "not_ready",
                              "status": observed}, observed)
            self.assertEqual(self._posts(), [], observed)

    def test_status_shape_failure_never_posts(self):
        bad = json.loads(json.dumps(STATUS_OK))
        bad["clientStatus"]["instanceId"] = "999"
        self.ok_transport(self._responses(
            w.TransportResult("ok", 200, {"status": "success"}),
            client_status=bad))
        result = w.send_text("201100000000@c.us", "hello")
        self.assertEqual(result, {"ok": False, "stage": "shape_invalid"})
        self.assertEqual(self._posts(), [])

    def test_status_transport_failure_never_posts(self):
        responses = self._responses(
            w.TransportResult("ok", 200, {"status": "success"}))
        responses[STATUS_URL] = w.TransportResult("http_error", 503, None)
        self.ok_transport(responses)
        result = w.send_text("201100000000@c.us", "hello")
        self.assertEqual(result,
                         {"ok": False, "stage": "http_error", "http": 503})
        self.assertEqual(self._posts(), [])

    def test_send_success(self):
        self.ok_transport(self._responses(
            w.TransportResult("ok", 200, {"status": "success"})))
        result = w.send_text("201100000000@c.us", "hello")
        self.assertEqual(result, {"ok": True, "stage": "sent"})
        call = w._transport.calls[-1]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], SEND_URL)
        self.assertEqual(call["body"],
                         {"chatId": "201100000000@c.us", "message": "hello"})
        self.assertEqual(call["token"], SECRET)

    def test_422_becomes_refused_422(self):
        self.ok_transport(self._responses(
            w.TransportResult("http_error", 422, None)))
        self.assertEqual(w.send_text("201100000000@c.us", "hello"),
                         {"ok": False, "stage": "refused_422"})

    def test_invalid_chat_id_refused_before_network(self):
        self.ok_transport(self._responses(
            w.TransportResult("ok", 200, {"status": "success"})))
        result = w.send_text("not-a-chat", "hello")
        self.assertEqual(result, {"ok": False, "stage": "chat_id_invalid"})
        self.assertEqual(w._transport.calls, [])

    def test_empty_and_oversized_message_refused(self):
        self.ok_transport(self._responses(
            w.TransportResult("ok", 200, {"status": "success"})))
        self.assertEqual(w.send_text("201100000000@c.us", "   "),
                         {"ok": False, "stage": "message_invalid"})
        self.assertEqual(w.send_text("201100000000@c.us", "x" * 4097),
                         {"ok": False, "stage": "message_invalid"})
        self.assertEqual(w._transport.calls, [])

    def test_send_shape_invalid(self):
        self.ok_transport(self._responses(
            w.TransportResult("ok", 200, {"status": "queued"})))
        self.assertEqual(w.send_text("201100000000@c.us", "hello"),
                         {"ok": False, "stage": "send_shape_invalid"})

    def test_token_never_appears_in_any_failure_result(self):
        self.ok_transport(self._responses(
            w.TransportResult("http_error", 401, None)))
        result = w.send_text("201100000000@c.us", "hello")
        self.assertNotIn(SECRET, json.dumps(result))


class SendHandlerTests(EnvCase):
    def _responses(self, send_result, client_status=None):
        return {
            INSTANCES_URL: w.TransportResult("ok", 200, INSTANCES_OK),
            STATUS_URL: w.TransportResult(
                "ok", 200, client_status or STATUS_OK),
            SEND_URL: send_result,
        }

    def test_handler_returns_delivery_on_success(self):
        self.ok_transport(self._responses(
            w.TransportResult("ok", 200, {"status": "success"})))
        handler = w.make_send_handler()
        self.assertEqual(handler({"chat_id": "201100000000@c.us",
                                  "message": "hi"}),
                         {"delivered": True, "stage": "sent"})

    def test_handler_raises_stage_only_error(self):
        self.ok_transport(self._responses(
            w.TransportResult("http_error", 422, None)))
        handler = w.make_send_handler()
        with self.assertRaises(w.WaapiSendError) as ctx:
            handler({"chat_id": "201100000000@c.us", "message": "hi"})
        self.assertEqual(str(ctx.exception), "waapi_refused_422")
        self.assertNotIn(SECRET, str(ctx.exception))

    def test_handler_raises_not_ready_without_post(self):
        status = json.loads(json.dumps(STATUS_OK))
        status["clientStatus"]["instanceStatus"] = "qr"
        self.ok_transport(self._responses(
            w.TransportResult("ok", 200, {"status": "success"}),
            client_status=status))
        handler = w.make_send_handler()
        with self.assertRaises(w.WaapiSendError) as ctx:
            handler({"chat_id": "201100000000@c.us", "message": "hi"})
        self.assertEqual(str(ctx.exception), "waapi_not_ready")
        posts = [c for c in w._transport.calls if c["method"] == "POST"]
        self.assertEqual(posts, [])


class NotifyTests(EnvCase):
    def setUp(self):
        super().setUp()
        os.environ["PA_WAAPI_NOTIFY"] = "1"
        os.environ["WAAPI_OWNER_CHAT_ID"] = "01100000000"

    def test_notify_disabled_by_default_flag(self):
        os.environ["PA_WAAPI_NOTIFY"] = "0"
        self.assertEqual(w.notify_approval({"action": "a"}, "https://v"),
                         {"ok": False, "stage": "notify_disabled"})

    def test_notify_normalises_owner_number_and_sends(self):
        self.ok_transport({
            INSTANCES_URL: w.TransportResult("ok", 200, INSTANCES_OK),
            STATUS_URL: w.TransportResult("ok", 200, STATUS_OK),
            SEND_URL: w.TransportResult("ok", 200, {"status": "success"}),
        })
        result = w.notify_approval({"action": "agent.plan_day"},
                                   "https://vote?approval=X")
        self.assertEqual(result, {"ok": True, "stage": "sent"})
        call = w._transport.calls[-1]
        self.assertEqual(call["body"]["chatId"], "201100000000@c.us")
        self.assertIn("agent.plan_day", call["body"]["message"])
        self.assertIn("https://vote?approval=X", call["body"]["message"])

    def test_notify_missing_owner_chat_fail_closed(self):
        os.environ.pop("WAAPI_OWNER_CHAT_ID", None)
        w._transport = FakeTransport(error=AssertionError("must not call"))
        self.assertEqual(w.notify_approval({"action": "a"}, "https://v"),
                         {"ok": False, "stage": "owner_chat_missing"})

    def test_notify_missing_vote_url_fail_closed(self):
        w._transport = FakeTransport(error=AssertionError("must not call"))
        self.assertEqual(w.notify_approval({"action": "a"}, ""),
                         {"ok": False, "stage": "vote_url_missing"})

    def test_notify_token_missing_fail_closed(self):
        os.environ.pop("WAAPI_API_TOKEN", None)
        w._transport = FakeTransport(error=AssertionError("must not call"))
        self.assertEqual(w.notify_approval({"action": "a"}, "https://v"),
                         {"ok": False, "stage": "token_missing"})


class ReadinessTests(EnvCase):
    """readiness() = pre-consume gate used by the runner."""

    def test_ready_passes(self):
        self.ok_transport({
            INSTANCES_URL: w.TransportResult("ok", 200, INSTANCES_OK),
            STATUS_URL: w.TransportResult("ok", 200, STATUS_OK),
        })
        self.assertEqual(w.readiness(), {"ok": True, "stage": "ready"})

    def test_not_ready_refused_with_status(self):
        status = json.loads(json.dumps(STATUS_OK))
        status["clientStatus"]["instanceStatus"] = "disconnected"
        self.ok_transport({
            INSTANCES_URL: w.TransportResult("ok", 200, INSTANCES_OK),
            STATUS_URL: w.TransportResult("ok", 200, status),
        })
        self.assertEqual(w.readiness(),
                         {"ok": False, "stage": "not_ready",
                          "status": "disconnected"})

    def test_token_missing_is_local(self):
        os.environ.pop("WAAPI_API_TOKEN", None)
        w._transport = FakeTransport(error=AssertionError("must not call"))
        self.assertEqual(w.readiness(),
                         {"ok": False, "stage": "token_missing"})


class NormalisationTests(unittest.TestCase):
    def test_egyptian_shapes(self):
        cases = {
            "01100000000": "201100000000",
            "+20 11 0000 0000": "201100000000",
            "00201234567890": "201234567890",
            "201012345678": "201012345678",
            "not-a-number": "",
            "011": "",
            "201999999999": "",      # not an Egyptian mobile prefix
        }
        for raw, expected in cases.items():
            self.assertEqual(w.normalize_egyptian(raw), expected, raw)

    def test_owner_chat_id_accepts_full_chat_shape(self):
        os.environ["WAAPI_OWNER_CHAT_ID"] = "201100000000@c.us"
        self.assertEqual(w.owner_chat_id(), "201100000000@c.us")

    def test_owner_chat_id_empty_when_unset(self):
        os.environ.pop("WAAPI_OWNER_CHAT_ID", None)
        self.assertEqual(w.owner_chat_id(), "")


if __name__ == "__main__":
    unittest.main()
