"""Alias for monitoring.phase2b (Phase 2B)."""
try:
    from .phase2b import *  # noqa: F401,F403
except Exception:
    import phase2b  # type: ignore
    from phase2b import *  # noqa: F401,F403
