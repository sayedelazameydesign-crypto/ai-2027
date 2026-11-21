"""Controller and gateway process entry point."""
from __future__ import annotations

import logging
import os
import signal
import threading
from http.server import ThreadingHTTPServer

from .controller import Reconciler
from .gateway import AgentGateway
from .http_server import make_server
from .kube import KubernetesAPI
from .security import ActorTokenManager


LOG = logging.getLogger("agent_substrate")


def _positive_float(name: str, default: str, *, maximum: float) -> float:
    raw = os.environ.get(name, default)
    try:
        value = float(raw)
    except ValueError:
        raise RuntimeError(f"{name} must be numeric") from None
    if value <= 0 or value > maximum:
        raise RuntimeError(f"{name} is outside the allowed range")
    return value


def run() -> None:
    api_token = os.environ.get("SUBSTRATE_API_TOKEN", "")
    if len(api_token) < 32:
        raise RuntimeError("SUBSTRATE_API_TOKEN must be at least 32 characters")
    actor_master = os.environ.get("SUBSTRATE_ACTOR_MASTER_KEY", "")
    try:
        actor_tokens = ActorTokenManager(actor_master)
    except ValueError as exc:
        raise RuntimeError("SUBSTRATE_ACTOR_MASTER_KEY must be at least 32 characters") from exc
    interval = _positive_float("SUBSTRATE_RECONCILE_INTERVAL_SECONDS", "2", maximum=60)
    wake_timeout = _positive_float("SUBSTRATE_WAKE_TIMEOUT_SECONDS", "60", maximum=600)
    port_text = os.environ.get("SUBSTRATE_PORT", "8080")
    try:
        port = int(port_text)
    except ValueError:
        raise RuntimeError("SUBSTRATE_PORT must be an integer") from None
    if not 1 <= port <= 65535:
        raise RuntimeError("SUBSTRATE_PORT is outside the allowed range")

    api = KubernetesAPI.from_environment()
    reconciler = Reconciler(api, actor_tokens)
    gateway = AgentGateway(api, actor_tokens, wake_timeout=wake_timeout)
    stopping = threading.Event()

    def reconcile_loop() -> None:
        while not stopping.is_set():
            try:
                reconciler.reconcile_all()
            except Exception:
                LOG.exception("Reconciliation iteration failed")
            stopping.wait(interval)

    thread = threading.Thread(target=reconcile_loop, name="agent-reconciler", daemon=True)
    thread.start()
    server: ThreadingHTTPServer = make_server(("0.0.0.0", port), gateway, api, api_token)

    def stop(_signum: int, _frame: object) -> None:
        stopping.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    LOG.info("Agent Substrate control plane listening on port %s", port)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        stopping.set()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    run()
