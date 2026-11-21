from __future__ import annotations

import base64
import copy
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from substrate.controller import (
    Reconciler,
    actor_secret_name_for,
    build_agent_pod,
    build_workspace_pvc,
    pod_is_ready,
    pod_name_for,
    pvc_name_for,
)
from substrate.kube import AgentRequestError, AlreadyExists, KubernetesError
from substrate.security import ActorTokenManager


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
TOKENS = ActorTokenManager("actor-master-key-for-tests-" + "m" * 32)


def agent(*, desired: str = "Running", status: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "apiVersion": "substrate.ai/v1alpha1",
        "kind": "Agent",
        "metadata": {"name": "counter", "namespace": "agent-substrate", "uid": "uid-1", "generation": 3},
        "spec": {
            "image": "registry.example/counter@sha256:" + "a" * 64,
            "desiredState": desired,
            "runtimeClassName": "gvisor",
            "workspaceStorage": "2Gi",
            "idleTimeoutSeconds": 300,
        },
        "status": status or {},
    }


def ready_pod() -> dict[str, Any]:
    return {
        "metadata": {
            "name": pod_name_for("counter", "uid-1"),
            "ownerReferences": [{
                "apiVersion": "substrate.ai/v1alpha1",
                "kind": "Agent",
                "name": "counter",
                "uid": "uid-1",
            }],
        },
        "status": {
            "phase": "Running",
            "podIP": "10.42.0.7",
            "conditions": [{"type": "Ready", "status": "True"}],
        },
    }


class FakeAPI:
    def __init__(self, resource: dict[str, Any], *, pod: dict[str, Any] | None = None) -> None:
        self.namespace = resource["metadata"]["namespace"]
        self.agent = copy.deepcopy(resource)
        self.pods = {pod["metadata"]["name"]: copy.deepcopy(pod)} if pod else {}
        self.pvcs: dict[str, dict[str, Any]] = {}
        self.secrets: dict[str, dict[str, Any]] = {}
        self.actions: list[tuple[str, Any]] = []
        self.fail_checkpoint = False

    def list_agents(self):
        return [self.agent]

    def get_pod(self, name):
        return self.pods.get(name)

    def create_pod(self, value):
        self.actions.append(("create_pod", value))
        if value["metadata"]["name"] in self.pods:
            raise AlreadyExists("exists", 409)
        value = copy.deepcopy(value)
        value["status"] = {"phase": "Pending"}
        self.pods[value["metadata"]["name"]] = value
        return value

    def delete_pod(self, name):
        self.actions.append(("delete_pod", name))
        self.pods.pop(name, None)

    def get_pvc(self, name):
        return self.pvcs.get(name)

    def create_pvc(self, value):
        self.actions.append(("create_pvc", value))
        if value["metadata"]["name"] in self.pvcs:
            raise AlreadyExists("exists", 409)
        self.pvcs[value["metadata"]["name"]] = copy.deepcopy(value)
        return value

    def get_secret(self, name):
        return self.secrets.get(name)

    def create_secret(self, value):
        self.actions.append(("create_secret", value))
        name = value["metadata"]["name"]
        if name in self.secrets:
            raise AlreadyExists("exists", 409)
        stored = copy.deepcopy(value)
        token = stored.pop("stringData", {}).get("token", "")
        stored["data"] = {"token": base64.b64encode(token.encode()).decode()}
        self.secrets[name] = stored
        return stored

    def patch_secret(self, name, patch):
        self.actions.append(("patch_secret", patch))
        stored = self.secrets.setdefault(name, {"metadata": {}, "data": {}})
        stored.setdefault("metadata", {}).update(patch.get("metadata", {}))
        stored["type"] = patch.get("type", stored.get("type"))
        stored["immutable"] = patch.get("immutable", stored.get("immutable"))
        token = patch.get("stringData", {}).get("token")
        if token is not None:
            stored.setdefault("data", {})["token"] = base64.b64encode(token.encode()).decode()
        return stored

    def patch_agent(self, name, patch):
        self.actions.append(("patch_agent", copy.deepcopy(patch)))
        self.agent.setdefault("spec", {}).update(patch.get("spec", {}))
        return self.agent

    def patch_agent_status(self, name, status):
        self.actions.append(("patch_status", copy.deepcopy(status)))
        self.agent.setdefault("status", {}).update(status)
        return self.agent

    def post_to_pod(self, pod, endpoint, payload, *, actor_token):
        self.actions.append(("post", {"endpoint": endpoint, "actor_token": actor_token}))
        if self.fail_checkpoint:
            raise AgentRequestError("checkpoint failed")
        return {"ok": True}


class PodBuilderTests(unittest.TestCase):
    def test_pod_uses_sandbox_and_restricted_security_defaults(self):
        pod = build_agent_pod(agent())
        spec = pod["spec"]
        container = spec["containers"][0]
        self.assertEqual(spec["runtimeClassName"], "gvisor")
        self.assertFalse(spec["automountServiceAccountToken"])
        self.assertTrue(spec["securityContext"]["runAsNonRoot"])
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertFalse(container["securityContext"]["allowPrivilegeEscalation"])
        self.assertEqual(container["securityContext"]["capabilities"]["drop"], ["ALL"])
        self.assertEqual(container["resources"]["limits"], {"cpu": "1", "memory": "512Mi"})
        self.assertEqual(spec["volumes"][0]["persistentVolumeClaim"]["claimName"], pvc_name_for("counter", "uid-1"))
        env = {item["name"]: item for item in container["env"]}
        self.assertNotIn("value", env["SUBSTRATE_ACTOR_TOKEN"])
        self.assertEqual(
            env["SUBSTRATE_ACTOR_TOKEN"]["valueFrom"]["secretKeyRef"]["name"],
            actor_secret_name_for("counter", "uid-1"),
        )
        self.assertEqual(pod["metadata"]["ownerReferences"][0]["uid"], "uid-1")

    def test_pvc_is_owned_and_has_requested_durable_size(self):
        pvc = build_workspace_pvc(agent())
        self.assertEqual(pvc["spec"]["resources"]["requests"]["storage"], "2Gi")
        self.assertEqual(pvc["spec"]["accessModes"], ["ReadWriteOnce"])
        self.assertEqual(pvc["metadata"]["ownerReferences"][0]["kind"], "Agent")

    def test_invalid_names_and_storage_sizes_fail_closed(self):
        resource = agent()
        resource["metadata"]["name"] = "../bad"
        with self.assertRaises(ValueError):
            build_agent_pod(resource)
        resource = agent()
        resource["spec"]["workspaceStorage"] = "0Gi"
        with self.assertRaises(ValueError):
            build_workspace_pvc(resource)

    def test_uid_scopes_owned_resources_to_one_agent_incarnation(self):
        first = agent()
        second = agent()
        second["metadata"]["uid"] = "uid-2"
        self.assertNotEqual(pod_name_for("counter", "uid-1"), pod_name_for("counter", "uid-2"))
        self.assertNotEqual(pvc_name_for("counter", "uid-1"), pvc_name_for("counter", "uid-2"))
        self.assertNotEqual(
            actor_secret_name_for("counter", first["metadata"]["uid"]),
            actor_secret_name_for("counter", second["metadata"]["uid"]),
        )


class ReconcilerTests(unittest.TestCase):
    def setUp(self):
        self.tokens = TOKENS

    def test_running_agent_creates_pvc_secret_and_restricted_pod(self):
        api = FakeAPI(agent())
        reconciler = Reconciler(api, self.tokens, clock=lambda: NOW)
        reconciler.reconcile(api.agent)
        pod_name = pod_name_for("counter", "uid-1")
        pvc_name = pvc_name_for("counter", "uid-1")
        secret_name = actor_secret_name_for("counter", "uid-1")
        self.assertIn(pvc_name, api.pvcs)
        self.assertIn(secret_name, api.secrets)
        self.assertIn(pod_name, api.pods)
        self.assertEqual(api.agent["status"]["phase"], "Starting")
        secret_token = base64.b64decode(api.secrets[secret_name]["data"]["token"]).decode()
        self.assertEqual(secret_token, self.tokens.token_for("agent-substrate", "counter", "uid-1"))
        self.assertEqual([name for name, _ in api.actions].count("patch_secret"), 0)
        created = next(value for action, value in api.actions if action == "create_pod")
        self.assertEqual(created["spec"]["runtimeClassName"], "gvisor")

    def test_ready_agent_sets_running_and_first_activity_timestamp(self):
        api = FakeAPI(agent(), pod=ready_pod())
        Reconciler(api, self.tokens, clock=lambda: NOW).reconcile(api.agent)
        self.assertEqual(api.agent["status"]["phase"], "Running")
        self.assertEqual(api.agent["status"]["lastActivityTime"], "2026-01-02T03:04:05Z")

    def test_woken_agent_gets_a_fresh_idle_window_before_running(self):
        old_activity = (NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        api = FakeAPI(
            agent(desired="Running", status={"phase": "Sleeping", "lastActivityTime": old_activity}),
            pod=ready_pod(),
        )
        Reconciler(api, self.tokens, clock=lambda: NOW).reconcile(api.agent)
        self.assertEqual(api.agent["spec"]["desiredState"], "Running")
        self.assertEqual(api.agent["status"]["phase"], "Running")
        self.assertEqual(api.agent["status"]["lastActivityTime"], "2026-01-02T03:04:05Z")
        self.assertNotIn("post", [name for name, _ in api.actions])
        self.assertNotIn("delete_pod", [name for name, _ in api.actions])

    def test_immutable_actor_secret_mismatch_fails_closed(self):
        api = FakeAPI(agent())
        name = actor_secret_name_for("counter", "uid-1")
        api.secrets[name] = {
            "metadata": {
                "ownerReferences": [{
                    "apiVersion": "substrate.ai/v1alpha1",
                    "kind": "Agent",
                    "name": "counter",
                    "uid": "uid-1",
                }],
            },
            "data": {"token": base64.b64encode(b"different-token").decode()},
            "immutable": True,
        }
        with self.assertRaises(KubernetesError):
            Reconciler(api, self.tokens, clock=lambda: NOW).reconcile(api.agent)
        self.assertNotIn(pod_name_for("counter", "uid-1"), api.pods)
        self.assertNotIn("patch_secret", [name for name, _ in api.actions])

    def test_terminating_pod_is_not_treated_as_ready_or_checkpointed_again(self):
        pod = ready_pod()
        pod["metadata"]["deletionTimestamp"] = "2026-01-02T03:04:04Z"
        self.assertFalse(pod_is_ready(pod))
        api = FakeAPI(agent(desired="Sleeping"), pod=pod)
        Reconciler(api, self.tokens, clock=lambda: NOW).reconcile(api.agent)
        self.assertEqual(api.agent["status"]["phase"], "Sleeping")
        self.assertNotIn("post", [name for name, _ in api.actions])
        self.assertNotIn("delete_pod", [name for name, _ in api.actions])

    def test_unowned_pod_is_neither_invoked_nor_deleted(self):
        pod = ready_pod()
        pod["metadata"]["ownerReferences"][0]["uid"] = "different-agent-uid"
        api = FakeAPI(agent(desired="Sleeping"), pod=pod)
        Reconciler(api, self.tokens, clock=lambda: NOW).reconcile(api.agent)
        self.assertIn(pod_name_for("counter", "uid-1"), api.pods)
        self.assertEqual(api.agent["status"]["phase"], "Failed")
        self.assertNotIn("post", [name for name, _ in api.actions])
        self.assertNotIn("delete_pod", [name for name, _ in api.actions])

    def test_unowned_workspace_pvc_fails_closed(self):
        api = FakeAPI(agent())
        name = pvc_name_for("counter", "uid-1")
        api.pvcs[name] = {
            "metadata": {
                "ownerReferences": [{
                    "apiVersion": "substrate.ai/v1alpha1",
                    "kind": "Agent",
                    "name": "counter",
                    "uid": "different-agent-uid",
                }],
            },
        }
        Reconciler(api, self.tokens, clock=lambda: NOW).reconcile(api.agent)
        self.assertEqual(api.agent["status"]["phase"], "Failed")
        self.assertNotIn(pod_name_for("counter", "uid-1"), api.pods)
        self.assertNotIn("create_pod", [name for name, _ in api.actions])

    def test_suspend_checkpoints_before_deleting_pod_and_keeps_pvc(self):
        api = FakeAPI(agent(desired="Sleeping"), pod=ready_pod())
        api.pvcs[pvc_name_for("counter", "uid-1")] = {"metadata": {"name": pvc_name_for("counter", "uid-1")}}
        Reconciler(api, self.tokens, clock=lambda: NOW).reconcile(api.agent)
        actions = [name for name, _ in api.actions]
        self.assertLess(actions.index("post"), actions.index("delete_pod"))
        self.assertEqual(api.agent["status"]["phase"], "Sleeping")
        self.assertNotIn(pod_name_for("counter", "uid-1"), api.pods)
        self.assertIn(pvc_name_for("counter", "uid-1"), api.pvcs)

    def test_checkpoint_failure_keeps_pod_running(self):
        api = FakeAPI(agent(desired="Sleeping"), pod=ready_pod())
        api.fail_checkpoint = True
        Reconciler(api, self.tokens, clock=lambda: NOW).reconcile(api.agent)
        self.assertIn(pod_name_for("counter", "uid-1"), api.pods)
        self.assertNotIn("delete_pod", [name for name, _ in api.actions])
        self.assertEqual(api.agent["status"]["phase"], "CheckpointFailed")

    def test_idle_timeout_changes_desired_state_then_suspends(self):
        last = (NOW - timedelta(seconds=301)).isoformat().replace("+00:00", "Z")
        api = FakeAPI(agent(status={"phase": "Running", "lastActivityTime": last}), pod=ready_pod())
        Reconciler(api, self.tokens, clock=lambda: NOW).reconcile(api.agent)
        self.assertEqual(api.agent["spec"]["desiredState"], "Sleeping")
        self.assertEqual(api.agent["status"]["phase"], "Sleeping")
        actions = [name for name, _ in api.actions]
        self.assertLess(actions.index("patch_agent"), actions.index("post"))
        self.assertLess(actions.index("post"), actions.index("delete_pod"))

    def test_sleeping_without_a_pod_is_idempotent(self):
        api = FakeAPI(agent(desired="Sleeping"))
        Reconciler(api, self.tokens, clock=lambda: NOW).reconcile(api.agent)
        self.assertEqual(api.agent["status"]["phase"], "Sleeping")
        self.assertEqual(api.agent["status"]["podName"], "")

    def test_reconcile_failure_does_not_starve_later_agents(self):
        api = FakeAPI(agent())
        invalid = copy.deepcopy(api.agent)
        invalid["metadata"]["name"] = "../invalid"
        api.list_agents = lambda: [invalid, api.agent]
        reconciler = Reconciler(api, self.tokens, clock=lambda: NOW)
        with self.assertLogs("agent_substrate.controller", level="ERROR"):
            reconciler.reconcile_all()
        self.assertIn(pod_name_for("counter", "uid-1"), api.pods)


if __name__ == "__main__":
    unittest.main()
