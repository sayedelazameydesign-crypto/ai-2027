from __future__ import annotations

import copy
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from substrate.controller import pod_name_for
from substrate.gateway import AgentGateway, GatewayError
from substrate.kube import AgentRequestError
from substrate.security import ActorTokenManager

TOKENS = ActorTokenManager("gateway-test-master-" + "k" * 40)


class FakeTime:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, duration: float) -> None:
        self.value += duration


class FakeAPI:
    namespace = "agent-substrate"

    def __init__(self, desired: str = "Sleeping", *, become_ready: bool = True) -> None:
        self.agent = {
            "metadata": {"name": "counter", "namespace": self.namespace, "uid": "uid-1", "generation": 2},
            "spec": {"desiredState": desired, "image": "private/image:tag"},
            "status": {"phase": "Sleeping", "lastActivityTime": None},
        }
        self.pod: dict[str, Any] | None = None
        self.become_ready = become_ready
        self.actions: list[str] = []
        self.endpoint_token = ""

    def get_agent(self, name):
        return copy.deepcopy(self.agent)

    def patch_agent(self, name, patch):
        self.actions.append("patch_agent")
        self.agent["spec"].update(patch.get("spec", {}))
        return copy.deepcopy(self.agent)

    def patch_agent_status(self, name, status):
        self.actions.append("patch_status")
        self.agent["status"].update(status)
        return copy.deepcopy(self.agent)

    def get_pod(self, name):
        if self.pod is None and self.agent["spec"]["desiredState"] == "Running" and self.become_ready:
            self.pod = {
                "metadata": {"name": pod_name_for("counter", "uid-1")},
                "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}], "podIP": "10.42.0.8"},
            }
        if self.pod and self.pod["metadata"]["name"] != name:
            return None
        return copy.deepcopy(self.pod)

    def post_to_pod(self, pod, endpoint, payload, *, actor_token):
        self.actions.append("invoke")
        self.endpoint_token = actor_token
        return {"value": payload.get("by", 0), "requestId": payload.get("requestId")}


class GatewayTests(unittest.TestCase):
    def test_invocation_wakes_actor_before_proxying_and_records_activity(self):
        api = FakeAPI()
        fake_time = FakeTime()
        gateway = AgentGateway(
            api,
            TOKENS,
            wake_timeout=2,
            poll_interval=0.1,
            clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
            monotonic=fake_time.monotonic,
            sleep=fake_time.sleep,
        )
        result = gateway.invoke("counter", {"op": "increment", "by": 2, "requestId": "t-1"})
        self.assertEqual(result["value"], 2)
        self.assertEqual(api.agent["spec"]["desiredState"], "Running")
        self.assertEqual(api.agent["status"]["lastActivityTime"], "2026-01-01T00:00:00Z")
        self.assertEqual(api.endpoint_token, TOKENS.token_for("agent-substrate", "counter", "uid-1"))
        self.assertLess(api.actions.index("patch_agent"), api.actions.index("patch_status"))
        self.assertLess(api.actions.index("patch_status"), api.actions.index("invoke"))

    def test_wake_timeout_is_reported_without_proxying(self):
        api = FakeAPI(become_ready=False)
        fake_time = FakeTime()
        gateway = AgentGateway(
            api,
            TOKENS,
            wake_timeout=0.5,
            poll_interval=0.25,
            monotonic=fake_time.monotonic,
            sleep=fake_time.sleep,
        )
        with self.assertRaises(GatewayError) as caught:
            gateway.invoke("counter", {"requestId": "t-2"})
        self.assertEqual(caught.exception.status_code, 504)
        self.assertNotIn("invoke", api.actions)

    def test_gateway_does_not_invoke_a_pod_that_is_terminating(self):
        api = FakeAPI(desired="Running")
        api.pod = {
            "metadata": {
                "name": pod_name_for("counter", "uid-1"),
                "deletionTimestamp": "2026-01-01T00:00:00Z",
            },
            "status": {
                "phase": "Running",
                "conditions": [{"type": "Ready", "status": "True"}],
                "podIP": "10.42.0.8",
            },
        }
        fake_time = FakeTime()
        gateway = AgentGateway(
            api,
            TOKENS,
            wake_timeout=0.5,
            poll_interval=0.25,
            monotonic=fake_time.monotonic,
            sleep=fake_time.sleep,
        )
        with self.assertRaises(GatewayError) as caught:
            gateway.invoke("counter", {"requestId": "terminating"})
        self.assertEqual(caught.exception.status_code, 504)
        self.assertNotIn("invoke", api.actions)

    def test_actor_error_is_mapped_without_exposing_detail(self):
        api = FakeAPI(desired="Running")

        def fail(_pod, _endpoint, _payload, *, actor_token):
            raise AgentRequestError("internal address and detail should not escape")

        api.post_to_pod = fail
        gateway = AgentGateway(api, TOKENS, wake_timeout=1, monotonic=lambda: 1, sleep=lambda _: None)
        with self.assertRaises(GatewayError) as caught:
            gateway.invoke("counter", {"requestId": "t-3"})
        self.assertEqual(caught.exception.status_code, 502)
        self.assertEqual(caught.exception.public_message, "Agent invocation failed")

    def test_status_never_returns_image_configuration(self):
        api = FakeAPI(desired="Running")
        result = AgentGateway(api, TOKENS).get_status("counter")
        self.assertNotIn("image", result)
        self.assertEqual(result["name"], "counter")


if __name__ == "__main__":
    unittest.main()
