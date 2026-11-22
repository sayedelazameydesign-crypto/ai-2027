"""CELIA Skill Factory — reference core (Phase 1, design-verified).

Scope: event-log integrity, draft lifecycle, and the release gate.
NOT in scope: the factory engine, Nimna, the kernel, initial skills.

Every module here is standard-library-only ($0) and is exercised by
tests/test_*.py. Numbers reported in the report come from those runs.
"""
__version__ = "1.0.0"
__all__ = ["canonical", "eventlog", "drafts", "evidence"]
