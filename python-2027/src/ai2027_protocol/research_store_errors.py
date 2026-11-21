"""Typed fail-closed errors shared by the Research Radar SQLite components."""


class ResearchStoreError(RuntimeError):
    """Base class for local registry refusals."""


class ResearchNotFoundError(ResearchStoreError):
    """A verified paper is not present in the local registry."""


class ResearchConflictError(ResearchStoreError):
    """A source returned conflicting metadata for an already registered identifier."""


class ResearchTransitionError(ResearchStoreError):
    """A requested triage status transition lacks required evidence or is terminal."""


class ResearchIntegrityError(ResearchStoreError):
    """The database rows or audit chain do not match their recorded digests."""
