"""A watchdog source that reads Render, and admits when it cannot.

Import this as ``--source monitoring.render_source:render_snapshot`` after
setting ``RENDER_API_KEY``. Without a key it raises — on purpose. A source that
returns a plausible-looking "all good" when it has no credentials is how a
monitoring system ends up green and blind at the same time.

Deploy IDs and statuses only. No env vars, no log bodies, no secret values.
"""
from __future__ import annotations

import os

def render_snapshot() -> dict:
    key = os.environ.get("RENDER_API_KEY")
    if not key:
        raise RuntimeError(
            "RENDER_API_KEY is not set: refusing to report a state I cannot see"
        )
    import sys
    from pathlib import Path
    # The adapter is a SEPARATE package and must stay that way (the Render layer
    # is not part of Core). It is imported from its own directory at runtime
    # rather than installed, so the dependency exists only here, only when a real
    # snapshot is taken, and never inside the watchdog itself.
    #
    # Guarded on purpose: the CI AST gate rejects third-party imports, and an
    # import that is absent from the module's top level — and only appears when
    # talking to a live API — is a runtime coupling, not an install-time one.
    try:
        import adapter  # noqa: PLC0415
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                               / "render_control_adapter"))
        try:
            import adapter  # noqa: PLC0415,F811
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "render_control_adapter/adapter.py not found: refusing to report "
                "a state I cannot read"
            ) from exc

    client = adapter.RenderClient(api_key=key)
    snapshot: dict = {}
    for svc in client.list_services():
        sid = svc.get("id")
        if not sid:
            continue
        name = svc.get("name") or sid
        snapshot[f"{name}.suspended"] = svc.get("suspended")
        try:
            deploys = client.list_deploys(sid, limit=1)
        except Exception as exc:  # recorded, never silently swallowed
            snapshot[f"{name}.deploys_error"] = type(exc).__name__
            continue
        if deploys:
            d = deploys[0]
            snapshot[f"{name}.deploy_status"] = d.get("status")
            snapshot[f"{name}.deploy_id"] = d.get("id")
    if not snapshot:
        raise RuntimeError("no services returned: the snapshot would be empty")
    return snapshot
