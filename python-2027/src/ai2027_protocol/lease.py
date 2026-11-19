"""Per-session execution leases: one writer at a time, with a fencing token.

``SessionStore`` used to be described as thread-safe while holding no lock at all:
two writers shared one ``session.tmp`` filename, so saves were lost and half-written
files were published (measured: 23-61 lost writes out of 240, up to 13 torn reads —
see ``docs/PAP-2027-RESILIENCE-AUDIT.md`` §5).

This module is the missing piece and nothing more:

* a **lease** is a row in SQLite guarded by ``BEGIN IMMEDIATE``, so acquisition is
  atomic across threads *and* processes on the same machine;
* every acquisition or takeover increments a **fencing token**, so a writer that
  lost the lease cannot overwrite newer state — its ``renew``/``save`` fails;
  the token never resets, not even when a lease is released (a released row
  expires instead of disappearing), because a fence that restarted at 1 would
  let a pre-release writer pass the check;
* leases **expire**, so a crashed process does not keep a session locked forever:
  the next writer takes over and the fence moves on;
* the storage is behind a :class:`LeaseStore` protocol, so a distributed backend
  can replace SQLite without touching the agent.

SQLite is stdlib, matches ``ADR-0002``, and is already the pattern used by the L1
memory store and the connector approvals ledger.
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

DEFAULT_TTL_S = 60.0
_SCHEMA = """
CREATE TABLE IF NOT EXISTS session_leases (
    session_id  TEXT PRIMARY KEY,
    owner       TEXT NOT NULL,
    fence       INTEGER NOT NULL,
    acquired_at REAL NOT NULL,
    renewed_at  REAL NOT NULL,
    expires_at  REAL NOT NULL
);
"""


class LeaseError(RuntimeError):
    """Base class for lease refusals; never a silent fallback."""


class LeaseHeldError(LeaseError):
    """Another live writer owns the session."""

    def __init__(self, session_id: str, owner: str, expires_at: float) -> None:
        """Report who holds the lease and when it expires."""
        remaining = max(0.0, expires_at - time.time())
        super().__init__(
            f"Session {session_id!r} is leased by {owner!r} for another {remaining:.1f}s."
        )
        self.session_id = session_id
        self.owner = owner
        self.expires_at = expires_at


class LeaseLostError(LeaseError):
    """This writer's lease expired or was taken over by a newer one."""

    def __init__(self, session_id: str, fence: int, reason: str) -> None:
        """Report which fence was rejected and why."""
        super().__init__(f"Lease for {session_id!r} (fence {fence}) is no longer valid: {reason}.")
        self.session_id = session_id
        self.fence = fence
        self.reason = reason


@dataclass(frozen=True)
class Lease:
    """A held lease: who holds it, until when, and at which fencing token."""

    session_id: str
    owner: str
    fence: int
    expires_at: float

    @property
    def remaining_s(self) -> float:
        """Return seconds left before the lease expires (never negative)."""
        return max(0.0, self.expires_at - time.time())

    @property
    def is_live(self) -> bool:
        """Return whether the lease is currently held (a released one is not)."""
        return time.time() < self.expires_at


@runtime_checkable
class LeaseStore(Protocol):
    """Replaceable lease backend; SQLite today, anything transactional later."""

    def acquire(self, session_id: str, owner: str, *, ttl_s: float = DEFAULT_TTL_S) -> Lease:
        """Acquire or take over an expired lease, or raise :class:`LeaseHeldError`."""
        ...

    def renew(self, lease: Lease, *, ttl_s: float = DEFAULT_TTL_S) -> Lease:
        """Extend a live lease, or raise :class:`LeaseLostError` if it is stale."""
        ...

    def release(self, lease: Lease) -> None:
        """Drop the lease if this writer still owns it."""
        ...

    def current(self, session_id: str) -> Lease | None:
        """Return the current lease row (even if expired), for diagnostics."""
        ...


def new_owner_id() -> str:
    """Return a process-unique owner identity for lease ownership."""
    import socket

    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class SqliteLeaseStore:
    """SQLite-backed leases with transactional acquisition and monotonic fences."""

    def __init__(self, path: Path) -> None:
        """Create the lease database (WAL, bounded busy timeout) at ``path``."""
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute(_SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=10.0, isolation_level=None)
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    def current(self, session_id: str) -> Lease | None:
        """Return the stored lease row for ``session_id`` if any."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT session_id, owner, fence, expires_at FROM session_leases"
                " WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return Lease(session_id=row[0], owner=row[1], fence=int(row[2]), expires_at=float(row[3]))

    def acquire(self, session_id: str, owner: str, *, ttl_s: float = DEFAULT_TTL_S) -> Lease:
        """Take the lease atomically, or raise :class:`LeaseHeldError` with the holder."""
        now = time.time()
        expires = now + ttl_s
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT owner, fence, expires_at FROM session_leases WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                fence = 1
                conn.execute(
                    "INSERT INTO session_leases"
                    " (session_id, owner, fence, acquired_at, renewed_at, expires_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (session_id, owner, fence, now, now, expires),
                )
            else:
                holder, held_fence, held_expires = row[0], int(row[1]), float(row[2])
                if held_expires > now and holder != owner:
                    conn.rollback()
                    raise LeaseHeldError(session_id, holder, held_expires)
                fence = held_fence + 1
                conn.execute(
                    "UPDATE session_leases SET owner = ?, fence = ?, acquired_at = ?,"
                    " renewed_at = ?, expires_at = ? WHERE session_id = ?",
                    (owner, fence, now, now, expires, session_id),
                )
            conn.commit()
        finally:
            conn.close()
        return Lease(session_id=session_id, owner=owner, fence=fence, expires_at=expires)

    def renew(self, lease: Lease, *, ttl_s: float = DEFAULT_TTL_S) -> Lease:
        """Extend the lease only if this exact writer still holds it."""
        now = time.time()
        expires = now + ttl_s
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT owner, fence, expires_at FROM session_leases WHERE session_id = ?",
                (lease.session_id,),
            ).fetchone()
            if row is None:
                conn.rollback()
                raise LeaseLostError(lease.session_id, lease.fence, "no lease row")
            owner, fence, held_expires = row[0], int(row[1]), float(row[2])
            if owner != lease.owner or fence != lease.fence:
                conn.rollback()
                raise LeaseLostError(
                    lease.session_id,
                    lease.fence,
                    f"taken over by {owner!r} at fence {fence}",
                )
            if held_expires <= now:
                conn.rollback()
                raise LeaseLostError(lease.session_id, lease.fence, "expired")
            conn.execute(
                "UPDATE session_leases SET renewed_at = ?, expires_at = ?"
                " WHERE session_id = ? AND owner = ? AND fence = ?",
                (now, expires, lease.session_id, lease.owner, lease.fence),
            )
            conn.commit()
        finally:
            conn.close()
        return Lease(
            session_id=lease.session_id,
            owner=lease.owner,
            fence=lease.fence,
            expires_at=expires,
        )

    def release(self, lease: Lease) -> None:
        """Free the session, keeping the fence monotonic for the next writer.

        The row stays behind with ``expires_at = 0`` rather than being deleted:
        deleting it would restart the fence at 1 and hand a stale writer a token
        that looks current.
        """
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE session_leases SET expires_at = 0, renewed_at = ?"
                " WHERE session_id = ? AND owner = ? AND fence = ?",
                (time.time(), lease.session_id, lease.owner, lease.fence),
            )
        finally:
            conn.close()
