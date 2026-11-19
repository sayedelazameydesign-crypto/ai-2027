"""Session persistence: atomic writes, one writer at a time, evidence on disk.

Extracted from ``agent.py`` when the session lease landed, so the persistence
layer keeps its own tests and the agent stays readable (the protocol auditor
enforces a 650-line ceiling per module).

Public API is unchanged: ``ai2027_protocol.agent.SessionStore`` and
``ai2027_protocol.SessionStore`` both point here.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .lease import (
    DEFAULT_TTL_S,
    Lease,
    LeaseError,
    LeaseHeldError,
    LeaseLostError,
    LeaseStore,
    SqliteLeaseStore,
    new_owner_id,
)
from .security import SecurityValidationError, redact_secrets, resolve_safe_path
from .session_state import AgentSessionState

LOG = logging.getLogger(__name__)
_SESSION_ID_RE = re.compile(r"^[a-z0-9_-]{8,48}$")


class SessionBusyError(RuntimeError):
    """Raised when another live writer already holds the session lease."""

    def __init__(self, session_id: str, owner: str, remaining_s: float) -> None:
        """Carry the holder and the time left so HTTP/CLI can answer precisely."""
        super().__init__(
            f"Session {session_id!r} is already running under {owner!r} "
            f"(lease expires in {remaining_s:.1f}s)."
        )
        self.session_id = session_id
        self.owner = owner
        self.remaining_s = remaining_s


class SessionStore:
    """Thread-safe filesystem store managing isolated session sandboxes and cancel flags."""

    #: ``off`` keeps the historical behaviour, ``warn`` records the refusal but
    #: still writes (only for debugging), ``enforce`` refuses the write.
    LEASE_MODES = ("off", "warn", "enforce")

    def __init__(
        self,
        studio_dir: Path,
        *,
        lease_mode: str | None = None,
        lease_store: LeaseStore | None = None,
        lease_ttl_s: float = DEFAULT_TTL_S,
    ) -> None:
        """Create session directories under studio_dir/sessions safely."""
        self._studio_dir = studio_dir.resolve()
        self._sessions_root = self._studio_dir / "sessions"
        self._sessions_root.mkdir(parents=True, exist_ok=True)
        self._cancel_flags: dict[str, asyncio.Event] = {}
        chosen_mode = (lease_mode or os.environ.get("AI2027_SESSION_LEASE", "off")).strip().lower()
        if chosen_mode not in self.LEASE_MODES:
            raise ValueError(
                f"Invalid session lease mode {chosen_mode!r}; use one of {self.LEASE_MODES}."
            )
        self.lease_mode = chosen_mode
        self.lease_ttl_s = max(1.0, float(lease_ttl_s))
        self._lease_store: LeaseStore | None = lease_store
        if chosen_mode != "off":
            self._lease_store = lease_store or SqliteLeaseStore(self._studio_dir / "leases.db")

    @property
    def leases_enforced(self) -> bool:
        """Return whether writes require a live lease (``enforce`` only)."""
        return self.lease_mode == "enforce"

    @property
    def lease_enabled(self) -> bool:
        """Return whether the store manages leases at all (``off`` disables them)."""
        return self._lease_store is not None

    def acquire_lease(self, session_id: str, *, owner: str | None = None) -> Lease:
        """Acquire the session lease or raise :class:`SessionBusyError`."""
        if self._lease_store is None:
            raise LeaseError("Session leases are disabled (AI2027_SESSION_LEASE=off).")
        clean = self._validate_session_id(session_id)
        try:
            return self._lease_store.acquire(clean, owner or new_owner_id(), ttl_s=self.lease_ttl_s)
        except LeaseHeldError as held:
            raise SessionBusyError(
                held.session_id, held.owner, max(0.0, held.expires_at - time.time())
            ) from held

    def renew_lease(self, lease: Lease) -> Lease:
        """Extend a held lease; raises :class:`LeaseLostError` when it is stale."""
        if self._lease_store is None:
            raise LeaseError("Session leases are disabled (AI2027_SESSION_LEASE=off).")
        return self._lease_store.renew(lease, ttl_s=self.lease_ttl_s)

    def current_lease(self, session_id: str) -> Lease | None:
        """Return the stored lease row (even expired), for diagnostics and tooling."""
        if self._lease_store is None:
            return None
        return self._lease_store.current(self._validate_session_id(session_id))

    def release_lease(self, lease: Lease | None) -> None:
        """Release a lease if one was taken; safe to call twice."""
        if lease is not None and self._lease_store is not None:
            self._lease_store.release(lease)

    @property
    def sessions_root(self) -> Path:
        """Return base directory housing all per-session sandboxes."""
        return self._sessions_root

    def _validate_session_id(self, session_id: str) -> str:
        clean = session_id.strip()
        if not _SESSION_ID_RE.fullmatch(clean):
            raise SecurityValidationError("Invalid session_id format.")
        return clean

    def session_dir(self, session_id: str) -> Path:
        """Return path-contained directory for an existing or newly created session."""
        clean = self._validate_session_id(session_id)
        return resolve_safe_path(self._sessions_root, clean)

    def create_session(self, *, title: str = "New Agent Session") -> AgentSessionState:
        """Allocate a fresh session with isolated sandbox directory and initial state."""
        session_id = f"sess_{uuid.uuid4().hex[:12]}"
        sdir = self.session_dir(session_id)
        (sdir / "sandbox").mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC).isoformat()
        state = AgentSessionState(
            session_id=session_id,
            title=redact_secrets(title.strip())[:100] or "Agent Session",
            created_at=now,
            updated_at=now,
        )
        self.save_sync(state)
        return state

    def save_sync(self, state: AgentSessionState, *, lease: Lease | None = None) -> None:
        """Atomically persist session.json, refusing to write without a live lease.

        The first write of a session is exempt (a fresh uuid cannot be contended);
        every later mutation needs the lease, and a writer that lost it is refused
        instead of silently clobbering newer state.
        """
        sdir = self.session_dir(state.session_id)
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / "sandbox").mkdir(parents=True, exist_ok=True)
        target = sdir / "session.json"
        self._check_lease(state.session_id, lease, target_exists=target.exists())

        # A per-write temp name: a shared ``session.tmp`` let one writer publish
        # another writer's half-finished file (measured torn reads in the audit).
        tmp = sdir / f".session.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
        try:
            tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
            tmp.replace(target)
        finally:
            tmp.unlink(missing_ok=True)

    def _check_lease(self, session_id: str, lease: Lease | None, *, target_exists: bool) -> None:
        """Enforce the session lease before a mutation is allowed to land."""
        if self._lease_store is None or not target_exists:
            return
        if lease is None:
            message = (
                f"Refusing to overwrite {session_id!r} without a session lease "
                "(acquire_lease first)."
            )
            if self.leases_enforced:
                raise LeaseLostError(session_id, 0, "no lease supplied for an existing session")
            LOG.warning("session lease missing for %s: %s", session_id, message)
            return
        try:
            self._lease_store.renew(lease, ttl_s=self.lease_ttl_s)
        except LeaseLostError:
            if self.leases_enforced:
                raise
            LOG.warning("session lease stale for %s at fence %s", session_id, lease.fence)

    async def save(self, state: AgentSessionState, *, lease: Lease | None = None) -> None:
        """Persist session state asynchronously without blocking the event loop."""
        await asyncio.to_thread(self.save_sync, state, lease=lease)

    def load_sync(self, session_id: str) -> AgentSessionState:
        """Load an existing session snapshot or raise FileNotFoundError."""
        sdir = self.session_dir(session_id)
        target = sdir / "session.json"
        if not target.is_file():
            raise FileNotFoundError(f"Session {session_id!r} not found.")
        return AgentSessionState.model_validate_json(target.read_text(encoding="utf-8"))

    async def load(self, session_id: str) -> AgentSessionState:
        """Load session snapshot asynchronously."""
        return await asyncio.to_thread(self.load_sync, session_id)

    async def list_sessions(self, *, limit: int = 25) -> list[AgentSessionState]:
        """List stored sessions ordered most recently updated first."""

        def _scan() -> list[AgentSessionState]:
            items: list[AgentSessionState] = []
            if not self._sessions_root.is_dir():
                return items
            for child in self._sessions_root.iterdir():
                meta = child / "session.json"
                if child.is_dir() and meta.is_file():
                    try:
                        raw_meta = meta.read_text(encoding="utf-8")
                        items.append(AgentSessionState.model_validate_json(raw_meta))
                    except (OSError, ValueError):
                        continue
            items.sort(key=lambda s: s.updated_at, reverse=True)
            return items[:limit]

        return await asyncio.to_thread(_scan)

    def get_cancel_flag(self, session_id: str) -> asyncio.Event:
        """Return or initialize the cooperative cancellation Event for session_id."""
        clean = self._validate_session_id(session_id)
        flag = self._cancel_flags.get(clean)
        if flag is None:
            flag = asyncio.Event()
            self._cancel_flags[clean] = flag
        return flag

    def request_abort(self, session_id: str) -> None:
        """Trigger cooperative cancellation for a running session."""
        self.get_cancel_flag(session_id).set()

    def clear_abort(self, session_id: str) -> None:
        """Reset cancellation before starting a new run on an existing session."""
        self.get_cancel_flag(session_id).clear()
