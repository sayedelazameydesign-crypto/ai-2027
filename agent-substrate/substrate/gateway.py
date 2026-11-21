"""Authenticated gateway for waking, invoking, and suspending Agent resources."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

from .controller import pod_is_ready, pod_name_for, validate_agent_name
from .kube import AgentRequestError, KubernetesAPI, KubernetesError
from .security import ActorTokenManager


class GatewayError(RuntimeError):
    def __init__(self, status_code: int, public_message: str) -> None:
        super().__init__(public_message)
        self.status_code = status_code
        self.public_message = public_message


class AgentGateway:
    def __init__(
        self,
        api: KubernetesAPI,
        actor_tokens: ActorTokenManager,
        *,
        wake_timeout: float = 60.0,
        poll_interval: float = 0.25,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api = api
        self.actor_tokens = actor_tokens
        self.wake_timeout = wake_timeout
        self.poll_interval = poll_interval
        self.clock = clock
        self.monotonic = monotonic
        self.sleep = sleep

    def get_status(self, name: str) -> dict[str, Any]:
        name = validate_agent_name(name)
        try:
            agent = self.api.get_agent(name)
        except KubernetesError as exc:
            raise GatewayError(404 if exc.status_code == 404 else 503, "Agent status is unavailable") from None
        spec = agent.get("spec", {})
        status = agent.get("status", {})
        return {
            "name": name,
            "desiredState": spec.get("desiredState", "Running"),
            "phase": status.get("phase", "Pending"),
            "lastActivityTime": status.get("lastActivityTime"),
            "reason": status.get("reason"),
        }

    def set_desired_state(self, name: str, desired_state: str) -> dict[str, Any]:
        name = validate_agent_name(name)
        if desired_state not in {"Running", "Sleeping"}:
            raise GatewayError(400, "Unsupported desired state")
        try:
            self.api.get_agent(name)
            updated = self.api.patch_agent(name, {"spec": {"desiredState": desired_state}})
        except KubernetesError as exc:
            raise GatewayError(404 if exc.status_code == 404 else 503, "Could not update agent state") from None
        spec = updated.get("spec", {})
        return {"name": name, "desiredState": spec.get("desiredState", desired_state)}

    def invoke(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        name = validate_agent_name(name)
        if not isinstance(payload, dict):
            raise GatewayError(400, "Request body must be a JSON object")
        try:
            agent = self.api.get_agent(name)
        except KubernetesError as exc:
            raise GatewayError(404 if exc.status_code == 404 else 503, "Agent is unavailable") from None
        metadata = agent.get("metadata", {})
        uid = metadata.get("uid", "")
        namespace = metadata.get("namespace", self.api.namespace)
        if not uid:
            raise GatewayError(503, "Agent identity is not ready")

        if agent.get("spec", {}).get("desiredState", "Running") == "Sleeping":
            try:
                self.api.patch_agent(name, {"spec": {"desiredState": "Running"}})
            except KubernetesError:
                raise GatewayError(503, "Agent could not be resumed") from None

        pod = self._wait_for_ready_pod(name, uid)
        try:
            # Mark activity before proxying. A failed response must not trigger an
            # immediate suspend while an actor may still be processing the call.
            self.api.patch_agent_status(name, {
                "phase": "Running",
                "podName": pod_name_for(name, uid),
                "lastActivityTime": self.clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "reason": None,
            })
        except KubernetesError:
            raise GatewayError(503, "Agent activity could not be recorded") from None
        actor_token = self.actor_tokens.token_for(namespace, name, uid)
        try:
            return self.api.post_to_pod(pod, "/invoke", payload, actor_token=actor_token)
        except AgentRequestError:
            raise GatewayError(502, "Agent invocation failed") from None
        except KubernetesError:
            raise GatewayError(503, "Agent invocation is temporarily unavailable") from None

    def _wait_for_ready_pod(self, name: str, uid: str) -> dict[str, Any]:
        pod_name = pod_name_for(name, uid)
        deadline = self.monotonic() + self.wake_timeout
        while True:
            try:
                pod = self.api.get_pod(pod_name)
            except KubernetesError:
                raise GatewayError(503, "Agent pod status is unavailable") from None
            if pod is not None and pod_is_ready(pod):
                return pod
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                raise GatewayError(504, "Agent did not become ready in time")
            self.sleep(min(self.poll_interval, remaining))
