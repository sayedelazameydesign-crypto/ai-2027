"""Local, opt-in SQLite bookkeeping for the read-only proactive watcher."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path


class WatcherState:
    """Store only hashed identifiers, with atomic baseline and duplicate counting.

    This is not an outbox, an audit ledger, or a distributed lock.
    """

    def __init__(self, path: Path, account_scope: str) -> None:
        """Open a private database in an existing owner-only directory."""
        if not path.is_absolute() or not account_scope.strip():
            raise ValueError("absolute state path and account scope required")
        parent = path.parent.stat()
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.getuid()
            or parent.st_mode & 0o077
        ):
            raise ValueError("state directory must be owned by the user and private")
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError("state file must be owned by the user and private")
        finally:
            os.close(fd)
        self.path = path
        self._scope = hashlib.sha256(account_scope.encode()).hexdigest()
        with closing(self._connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            db.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY)")
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT value FROM meta WHERE key = 'scope'").fetchone()
            if current is None:
                db.execute("INSERT INTO meta VALUES ('scope', ?)", (self._scope,))
            elif current[0] != self._scope:
                raise ValueError("state database belongs to a different account scope")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def observe(self, ids: set[str]) -> tuple[bool, int]:
        """Atomically commit a complete successful snapshot and return its delta."""
        if any(len(item) != 64 or any(c not in "0123456789abcdef" for c in item) for item in ids):
            raise ValueError("invalid source identifier")
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            scope = db.execute("SELECT value FROM meta WHERE key='scope'").fetchone()
            if scope is None or scope[0] != self._scope:
                raise ValueError("state database account scope mismatch")
            baseline = db.execute("SELECT 1 FROM meta WHERE key='baseline'").fetchone() is None
            before = db.total_changes
            db.executemany("INSERT OR IGNORE INTO seen (id) VALUES (?)", ((item,) for item in ids))
            added = db.total_changes - before
            db.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('baseline', '1')")
            return baseline, added
