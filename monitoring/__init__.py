"""Monitoring package (Phase 2A watchdog + Phase 2B alerting)."""
try:
    from .phase2b import *  # noqa: F401,F403
except Exception:
    pass
