"""Agent lifecycle reconciler: one restricted Pod and durable workspace per actor."""
from __future__ import annotations

import base64
import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .kube import AgentRequestError, AlreadyExists, KubernetesAPI, KubernetesError
from .security import ActorTokenManager


LOG = logging.getLogger("agent_substrate.controller")
_AGENT_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
_RUNTIME_CLASS = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,61}[a-z0-9])?$")
_STORAGE_SIZE = re.compile(r"^[1-9][0-9]*(?:Mi|Gi|Ti)$")


def validate_agent_name(name: str) -> str:
    if not isinstance(name, str) or len(name) > 40 or not _AGENT_NAME.fullmatch(name):
        raise ValueError("Agent name must be a DNS label of at most 40 characters")
    return name


def _uid_suffix(uid: str) -> str:
    if not isinstance(uid, str) or not uid:
        raise ValueError("Agent UID is required for owned resources")
    return hashlib.sha256(uid.encode("utf-8")).hexdigest()[:8]


def pod_name_for(name: str, uid: str) -> str:
    return f"as-agent-{validate_agent_name(name)}-{_uid_suffix(uid)}"


def pvc_name_for(name: str, uid: str) -> str:
    return f"as-workspace-{validate_agent_name(name)}-{_uid_suffix(uid)}"


def actor_secret_name_for(name: str, uid: str) -> str:
    return f"as-token-{validate_agent_name(name)}-{_uid_suffix(uid)}"


def pod_is_ready(pod: dict[str, Any]) -> bool:
    if pod.get("metadata", {}).get("deletionTimestamp"):
        return False
    status = pod.get("status", {})
    return status.get("phase") == "Running" and any(
        item.get("type") == "Ready" and item.get("status") == "True"
        for item in status.get("conditions", [])
        if isinstance(item, dict)
    )


def _owner_reference(agent: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = agent.get("metadata", {})
    uid = metadata.get("uid")
    if not uid:
        return []
    return [{
        "apiVersion": "substrate.ai/v1alpha1",
        "kind": "Agent",
        "name": metadata["name"],
        "uid": uid,
        "controller": True,
        "blockOwnerDeletion": False,
    }]


def _owned_by_agent(resource: dict[str, Any], agent: dict[str, Any]) -> bool:
    metadata = agent.get("metadata", {})
    references = resource.get("metadata", {}).get("ownerReferences", [])
    if not isinstance(references, list):
        return False
    return any(
        isinstance(ref, dict)
        and ref.get("apiVersion") == "substrate.ai/v1alpha1"
        and ref.get("kind") == "Agent"
        and ref.get("name") == metadata.get("name")
        and ref.get("uid") == metadata.get("uid")
        for ref in references
    )


def build_workspace_pvc(agent: dict[str, Any]) -> dict[str, Any]:
    metadata = agent.get("metadata", {})
    name = validate_agent_name(metadata.get("name", ""))
    uid = metadata.get("uid", "")
    storage = agent.get("spec", {}).get("workspaceStorage", "1Gi")
    if not isinstance(storage, str) or not _STORAGE_SIZE.fullmatch(storage):
        raise ValueError("workspaceStorage must be an integer Mi, Gi, or Ti quantity")
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": pvc_name_for(name, uid),
            "labels": {
                "app.kubernetes.io/managed-by": "agent-substrate",
                "agent-substrate.io/agent": name,
            },
            "ownerReferences": _owner_reference(agent),
        },
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "volumeMode": "Filesystem",
            "resources": {"requests": {"storage": storage}},
        },
    }


def build_actor_secret(agent: dict[str, Any], token: str) -> dict[str, Any]:
    metadata = agent.get("metadata", {})
    name = validate_agent_name(metadata.get("name", ""))
    uid = metadata.get("uid", "")
    if len(token) < 32:
        raise ValueError("Derived actor token is too short")
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": actor_secret_name_for(name, uid),
            "labels": {
                "app.kubernetes.io/managed-by": "agent-substrate",
                "agent-substrate.io/agent": name,
            },
            "ownerReferences": _owner_reference(agent),
        },
        "type": "Opaque",
        "immutable": True,
        "stringData": {"token": token},
    }


def build_agent_pod(agent: dict[str, Any]) -> dict[str, Any]:
    metadata = agent.get("metadata", {})
    spec = agent.get("spec", {})
    name = validate_agent_name(metadata.get("name", ""))
    uid = metadata.get("uid", "")
    image = spec.get("image")
    runtime_class = spec.get("runtimeClassName", "gvisor")
    if not isinstance(image, str) or not image or len(image) > 512 or any(c.isspace() for c in image):
        raise ValueError("Agent image must be a non-empty image reference")
    if not isinstance(runtime_class, str) or not _RUNTIME_CLASS.fullmatch(runtime_class):
        raise ValueError("runtimeClassName must be a valid Kubernetes name")

    labels = {
        "app.kubernetes.io/name": "agent-substrate-actor",
        "agent-substrate.io/managed": "true",
        "agent-substrate.io/agent": name,
    }
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": pod_name_for(name, uid),
            "labels": labels,
            "ownerReferences": _owner_reference(agent),
        },
        "spec": {
            "runtimeClassName": runtime_class,
            "automountServiceAccountToken": False,
            "enableServiceLinks": False,
            "restartPolicy": "Always",
            "terminationGracePeriodSeconds": 20,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 10001,
                "runAsGroup": 10001,
                "fsGroup": 10001,
                "fsGroupChangePolicy": "OnRootMismatch",
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [{
                "name": "actor",
                "image": image,
                "imagePullPolicy": "IfNotPresent",
                "ports": [{"name": "http", "containerPort": 8080, "protocol": "TCP"}],
                "env": [
                    {"name": "SUBSTRATE_WORKSPACE", "value": "/workspace"},
                    {
                        "name": "SUBSTRATE_ACTOR_TOKEN",
                        "valueFrom": {
                            "secretKeyRef": {
                                "name": actor_secret_name_for(name, uid),
                                "key": "token",
                            },
                        },
                    },
                ],
                "resources": {
                    "requests": {"cpu": "100m", "memory": "128Mi"},
                    "limits": {"cpu": "1", "memory": "512Mi"},
                },
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "readOnlyRootFilesystem": True,
                    "runAsNonRoot": True,
                    "runAsUser": 10001,
                    "runAsGroup": 10001,
                    "capabilities": {"drop": ["ALL"]},
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "readinessProbe": {
                    "httpGet": {"path": "/healthz", "port": "http"},
                    "periodSeconds": 3,
                    "failureThreshold": 10,
                },
                "livenessProbe": {
                    "httpGet": {"path": "/healthz", "port": "http"},
                    "periodSeconds": 10,
                    "failureThreshold": 3,
                },
                "volumeMounts": [
                    {"name": "workspace", "mountPath": "/workspace"},
                    {"name": "tmp", "mountPath": "/tmp"},
                ],
            }],
            "volumes": [
                {"name": "workspace", "persistentVolumeClaim": {"claimName": pvc_name_for(name, uid)}},
                {"name": "tmp", "emptyDir": {"medium": "Memory", "sizeLimit": "64Mi"}},
            ],
        },
    }


def _isoformat(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class Reconciler:
    def __init__(
        self,
        api: KubernetesAPI,
        actor_tokens: ActorTokenManager,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.api = api
        self.actor_tokens = actor_tokens
        self.clock = clock

    def reconcile_all(self) -> None:
        for agent in self.api.list_agents():
            name = agent.get("metadata", {}).get("name", "<unknown>")
            try:
                self.reconcile(agent)
            except Exception:
                # A malformed Agent or one failed API operation must not starve
                # the remaining namespace-scoped actors on every poll.
                LOG.exception("Failed to reconcile Agent %s", name)

    def reconcile(self, agent: dict[str, Any]) -> None:
        metadata = agent.get("metadata", {})
        name = validate_agent_name(metadata.get("name", ""))
        uid = metadata.get("uid", "")
        # Include the Kubernetes UID so a delete/recreate cannot accidentally
        # attach the new actor to the previous actor's Pod, token Secret, or PVC.
        pod_name = pod_name_for(name, uid)
        spec = agent.get("spec", {})
        desired = spec.get("desiredState", "Running")
        pod = self.api.get_pod(pod_name)
        if pod is not None and not _owned_by_agent(pod, agent):
            self._set_status(
                agent, "Failed", pod_name=pod_name,
                reason="Actor pod ownership does not match this Agent",
            )
            return

        if desired == "Sleeping":
            if pod is not None and pod.get("metadata", {}).get("deletionTimestamp"):
                self._set_status(agent, "Sleeping", pod_name="")
                return
            self._suspend(agent, pod)
            return
        if desired != "Running":
            self._set_status(agent, "Failed", pod_name="", reason="Unsupported desired state")
            return

        if not self._ensure_workspace(agent):
            self._set_status(
                agent, "Failed", pod_name=pod_name,
                reason="Workspace PVC ownership does not match this Agent",
            )
            return
        self._ensure_actor_secret(agent)
        if pod is None:
            try:
                self.api.create_pod(build_agent_pod(agent))
            except AlreadyExists:
                pass
            self._set_status(agent, "Starting", pod_name=pod_name)
            return

        phase = pod.get("status", {}).get("phase")
        if phase == "Failed":
            self._set_status(agent, "Failed", pod_name=pod_name, reason="Actor pod failed; inspect pod events")
            return
        if not pod_is_ready(pod):
            self._set_status(agent, "Starting", pod_name=pod_name)
            return

        status = agent.get("status", {})
        last_activity = status.get("lastActivityTime")
        # A resumed actor may have been sleeping longer than its idle timeout.
        # Start a fresh idle window when it first becomes ready; otherwise the
        # reconciler can suspend it again before the waking invocation arrives.
        if status.get("phase") != "Running" or not last_activity:
            self._set_status(agent, "Running", pod_name=pod_name, last_activity=_isoformat(self.clock()))
            return
        idle_seconds = spec.get("idleTimeoutSeconds", 300)
        last = _parse_timestamp(last_activity)
        if idle_seconds > 0 and last is not None and self.clock() - last >= timedelta(seconds=idle_seconds):
            self.api.patch_agent(name, {"spec": {"desiredState": "Sleeping"}})
            agent = {**agent, "spec": {**spec, "desiredState": "Sleeping"}}
            self._suspend(agent, pod)
            return
        self._set_status(agent, "Running", pod_name=pod_name)

    def _ensure_workspace(self, agent: dict[str, Any]) -> bool:
        metadata = agent.get("metadata", {})
        name = validate_agent_name(metadata.get("name", ""))
        uid = metadata.get("uid", "")
        pvc_name = pvc_name_for(name, uid)
        existing = self.api.get_pvc(pvc_name)
        if existing is not None:
            return _owned_by_agent(existing, agent)
        try:
            self.api.create_pvc(build_workspace_pvc(agent))
            return True
        except AlreadyExists:
            existing = self.api.get_pvc(pvc_name)
            return existing is not None and _owned_by_agent(existing, agent)

    def _ensure_actor_secret(self, agent: dict[str, Any]) -> None:
        metadata = agent.get("metadata", {})
        name = validate_agent_name(metadata.get("name", ""))
        uid = metadata.get("uid", "")
        namespace = metadata.get("namespace", self.api.namespace)
        token = self.actor_tokens.token_for(namespace, name, uid)
        secret_name = actor_secret_name_for(name, uid)
        secret = self.api.get_secret(secret_name)
        expected = base64.b64encode(token.encode("utf-8")).decode("ascii")
        if secret is None:
            try:
                self.api.create_secret(build_actor_secret(agent, token))
                return
            except AlreadyExists:
                # Another reconcile may have won the create race. Re-read rather
                # than blindly patching a Secret that may not belong to this Agent.
                secret = self.api.get_secret(secret_name)
                if secret is None:
                    raise KubernetesError("Actor token Secret disappeared during creation")

        owns_secret = _owned_by_agent(secret, agent)
        if secret.get("data", {}).get("token") == expected and owns_secret and secret.get("immutable") is True:
            return
        if not owns_secret:
            raise KubernetesError("Actor token Secret is not owned by this Agent")
        if secret.get("immutable") is True:
            raise KubernetesError("Actor token Secret is immutable and does not match the configured key")

        desired = build_actor_secret(agent, token)
        self.api.patch_secret(secret_name, {
            "metadata": {
                "labels": desired["metadata"]["labels"],
                "ownerReferences": desired["metadata"]["ownerReferences"],
            },
            "type": "Opaque",
            "immutable": True,
            "stringData": {"token": token},
        })

    def _suspend(self, agent: dict[str, Any], pod: dict[str, Any] | None) -> None:
        metadata = agent.get("metadata", {})
        name = validate_agent_name(metadata.get("name", ""))
        uid = metadata.get("uid", "")
        pod_name = pod_name_for(name, uid)
        if pod is None:
            self._set_status(agent, "Sleeping", pod_name="")
            return
        if not pod_is_ready(pod):
            self._set_status(
                agent, "CheckpointFailed", pod_name=pod_name,
                reason="Actor is not ready; keeping its pod to avoid losing state",
            )
            return
        namespace = metadata.get("namespace", self.api.namespace)
        actor_token = self.actor_tokens.token_for(namespace, name, uid)
        try:
            self.api.post_to_pod(pod, "/checkpoint", {}, actor_token=actor_token)
        except (AgentRequestError, KubernetesError, TimeoutError, OSError):
            self._set_status(
                agent, "CheckpointFailed", pod_name=pod_name,
                reason="Checkpoint failed; actor pod was kept running",
            )
            return
        self.api.delete_pod(pod_name)
        self._set_status(agent, "Sleeping", pod_name="")

    def _set_status(
        self,
        agent: dict[str, Any],
        phase: str,
        *,
        pod_name: str,
        reason: str | None = None,
        last_activity: str | None = None,
    ) -> None:
        status: dict[str, Any] = {
            "phase": phase,
            "podName": pod_name,
            "observedGeneration": agent.get("metadata", {}).get("generation", 0),
            "reason": reason,
        }
        if last_activity is not None:
            status["lastActivityTime"] = last_activity
        current = agent.get("status", {})
        if all(current.get(key) == value for key, value in status.items()):
            return
        self.api.patch_agent_status(agent["metadata"]["name"], status)
