"""Small stdlib-only Kubernetes API client used by the substrate control plane."""
from __future__ import annotations

import ipaddress
import json
import os
import ssl
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)


class KubernetesError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class NotFound(KubernetesError):
    pass


class AlreadyExists(KubernetesError):
    pass


class AgentRequestError(RuntimeError):
    """A request to an actor pod failed; its response body is never exposed."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward Kubernetes or per-actor credentials to a Location host.
        return None


class KubernetesAPI:
    """Namespace-scoped Kubernetes client with bounded responses and safe errors."""

    SERVICE_ACCOUNT_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
    MAX_API_RESPONSE = 2 * 1024 * 1024
    MAX_AGENT_RESPONSE = 64 * 1024

    def __init__(
        self,
        base_url: str,
        token: str,
        namespace: str,
        *,
        ca_file: str | None = None,
        timeout: float = 6.0,
        opener: Callable[..., Any] | None = None,
        token_provider: Callable[[], str] | None = None,
    ) -> None:
        parts = urlsplit(base_url)
        if parts.scheme not in {"https", "http"} or not parts.netloc or parts.path not in {"", "/"}:
            raise ValueError("Kubernetes URL must be an absolute HTTP(S) origin")
        if parts.query or parts.fragment or parts.username or parts.password:
            raise ValueError("Kubernetes URL must not contain credentials or a path")
        if parts.scheme == "http" and parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Kubernetes API URLs must use HTTPS except on loopback")
        if not token or not namespace:
            raise ValueError("Kubernetes API token and namespace are required")
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._token_provider = token_provider
        self.namespace = namespace
        self.timeout = timeout
        self._opener = opener
        self._ssl_context = ssl.create_default_context(cafile=ca_file) if parts.scheme == "https" else None
        handlers: list[Any] = [ProxyHandler({}), _NoRedirect()]
        if self._ssl_context is not None:
            handlers.append(HTTPSHandler(context=self._ssl_context))
        self._safe_opener = build_opener(*handlers)

    def _open(self, request: Request, *, timeout: float) -> Any:
        if self._opener is None:
            return self._safe_opener.open(request, timeout=timeout)
        kwargs: dict[str, Any] = {"timeout": timeout}
        if self._ssl_context is not None:
            kwargs["context"] = self._ssl_context
        return self._opener(request, **kwargs)

    def _authorization_token(self) -> str:
        if self._token_provider is None:
            return self._token
        try:
            token = self._token_provider()
        except (OSError, UnicodeError):
            raise KubernetesError("Kubernetes service-account token is unavailable") from None
        if not token:
            raise KubernetesError("Kubernetes service-account token is unavailable")
        return token

    @classmethod
    def from_environment(cls) -> "KubernetesAPI":
        service_dir = cls.SERVICE_ACCOUNT_DIR
        token_file = Path(os.environ.get("SUBSTRATE_KUBERNETES_TOKEN_FILE", service_dir / "token"))
        token = os.environ.get("SUBSTRATE_KUBERNETES_TOKEN")
        token_provider: Callable[[], str] | None = None
        if token is None:
            try:
                token = token_file.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise RuntimeError("Kubernetes service-account token is unavailable") from exc
            # Projected service-account tokens rotate on disk; read the current
            # value for each API request instead of retaining an expiring token.
            token_provider = lambda: token_file.read_text(encoding="utf-8").strip()

        namespace = os.environ.get("SUBSTRATE_NAMESPACE")
        if not namespace:
            try:
                namespace = (service_dir / "namespace").read_text(encoding="utf-8").strip()
            except OSError:
                namespace = "default"

        base_url = os.environ.get("SUBSTRATE_KUBERNETES_URL")
        if not base_url:
            host = os.environ.get("KUBERNETES_SERVICE_HOST")
            port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
            if not host:
                raise RuntimeError("Kubernetes API address is unavailable")
            if ":" in host and not (host.startswith("[") and host.endswith("]")):
                host = f"[{host}]"
            base_url = f"https://{host}:{port}"

        ca_file = os.environ.get("SUBSTRATE_KUBERNETES_CA_FILE")
        default_ca = service_dir / "ca.crt"
        if ca_file is None and default_ca.is_file():
            ca_file = str(default_ca)
        return cls(base_url, token, namespace, ca_file=ca_file, token_provider=token_provider)

    @staticmethod
    def _q(value: str) -> str:
        return quote(value, safe="")

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._authorization_token()}",
        }
        if data is not None:
            headers["Content-Type"] = "application/merge-patch+json" if method == "PATCH" else "application/json"
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with self._open(request, timeout=self.timeout) as response:
                raw = response.read(self.MAX_API_RESPONSE + 1)
        except HTTPError as exc:
            if exc.code == 404:
                raise NotFound("Kubernetes resource not found", 404) from None
            if exc.code == 409:
                raise AlreadyExists("Kubernetes resource already exists", 409) from None
            raise KubernetesError(f"Kubernetes API returned HTTP {exc.code}", exc.code) from None
        except (URLError, TimeoutError, OSError) as exc:
            raise KubernetesError("Kubernetes API request failed") from exc
        if len(raw) > self.MAX_API_RESPONSE:
            raise KubernetesError("Kubernetes API response exceeded the size limit")
        if not raw:
            return {}
        try:
            decoded = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise KubernetesError("Kubernetes API returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise KubernetesError("Kubernetes API returned an invalid object")
        return decoded

    def _agents_path(self) -> str:
        return f"/apis/substrate.ai/v1alpha1/namespaces/{self._q(self.namespace)}/agents"

    def list_agents(self) -> list[dict[str, Any]]:
        result = self._request("GET", self._agents_path())
        items = result.get("items", [])
        if not isinstance(items, list):
            raise KubernetesError("Kubernetes returned an invalid Agent list")
        return [item for item in items if isinstance(item, dict)]

    def get_agent(self, name: str) -> dict[str, Any]:
        return self._request("GET", f"{self._agents_path()}/{self._q(name)}")

    def patch_agent(self, name: str, patch: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", f"{self._agents_path()}/{self._q(name)}", patch)

    def patch_agent_status(self, name: str, status: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", f"{self._agents_path()}/{self._q(name)}/status", {"status": status})

    def _core_path(self, resource: str, name: str | None = None) -> str:
        path = f"/api/v1/namespaces/{self._q(self.namespace)}/{resource}"
        return path if name is None else f"{path}/{self._q(name)}"

    def get_pod(self, name: str) -> dict[str, Any] | None:
        try:
            return self._request("GET", self._core_path("pods", name))
        except NotFound:
            return None

    def create_pod(self, pod: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", self._core_path("pods"), pod)

    def delete_pod(self, name: str) -> None:
        self._request("DELETE", self._core_path("pods", name), {"gracePeriodSeconds": 20})

    def get_pvc(self, name: str) -> dict[str, Any] | None:
        try:
            return self._request("GET", self._core_path("persistentvolumeclaims", name))
        except NotFound:
            return None

    def create_pvc(self, pvc: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", self._core_path("persistentvolumeclaims"), pvc)

    def get_secret(self, name: str) -> dict[str, Any] | None:
        try:
            return self._request("GET", self._core_path("secrets", name))
        except NotFound:
            return None

    def create_secret(self, secret: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", self._core_path("secrets"), secret)

    def patch_secret(self, name: str, patch: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", self._core_path("secrets", name), patch)

    def post_to_pod(
        self,
        pod: dict[str, Any],
        endpoint: str,
        payload: dict[str, Any],
        *,
        actor_token: str,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        if endpoint not in {"/invoke", "/checkpoint"}:
            raise ValueError("Unsupported actor endpoint")
        pod_ip = pod.get("status", {}).get("podIP")
        try:
            parsed_ip = ipaddress.ip_address(pod_ip)
        except (ValueError, TypeError):
            raise AgentRequestError("Actor pod has no valid IP address") from None
        authority = f"[{parsed_ip.compressed}]" if parsed_ip.version == 6 else parsed_ip.compressed
        request = Request(
            f"http://{authority}:8080{endpoint}",
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {actor_token}",
            },
            method="POST",
        )
        try:
            with self._open(request, timeout=timeout) as response:
                raw = response.read(self.MAX_AGENT_RESPONSE + 1)
                status = response.status
        except HTTPError as exc:
            raise AgentRequestError(f"Actor endpoint returned HTTP {exc.code}") from None
        except (URLError, TimeoutError, OSError) as exc:
            raise AgentRequestError("Actor endpoint is unreachable") from exc
        if status < 200 or status >= 300:
            raise AgentRequestError(f"Actor endpoint returned HTTP {status}")
        if len(raw) > self.MAX_AGENT_RESPONSE:
            raise AgentRequestError("Actor response exceeded the size limit")
        try:
            decoded = json.loads(raw) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise AgentRequestError("Actor returned invalid JSON") from None
        if not isinstance(decoded, dict):
            raise AgentRequestError("Actor returned an invalid response")
        return decoded
