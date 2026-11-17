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
    # The adapter lives beside this package; import it without installing it.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "render_control_adapter"))
    import adapter  # noqa: PLC0415

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
