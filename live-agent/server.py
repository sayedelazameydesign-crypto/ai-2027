#!/usr/bin/env python3
"""AI-2027 Live Agent Experience — Phase 1.

A zero-dependency web wrapper around the REAL agi-system kernel
(projects/agi-system). No code is copied: the kernel is imported in place,
so its architecture stays untouched.

Every run gets:
  * an isolated per-run sandbox directory (the kernel policy enforces
    sandbox confinement and DENIES path traversal),
  * a persisted DecisionRecord (the evidence), downloadable as JSON,
  * a file browser over the run sandbox for downloading produced artifacts.

Honest scope (see README.md): the planner is deterministic and rule-based.
There is NO LLM in this loop yet — the value demonstrated here is the
evidence-first execution discipline: Plan -> Policy -> Execute -> Verify ->
Evidence -> Report, with independent verification and a tamper-evident
ledger. A local/free LLM brain is Phase 2 (see docs/KIMI-K3-UTILIZATION.md).

Every VERIFIED run is also offered to the evidence-gated memory store
(memory/store.py): the kernel's own verifier decides what may be remembered,
and a DENIED or unverified run is refused with a reason. Search is lexical,
not neural — `GET /api/memory/search?q=...` returns the measured score and an
`above_noise` flag derived from the corpus noise floor.

Run:  python3 server.py [--port 8090]
Stdlib only. Python 3.10+.
"""

from __future__ import annotations

import argparse
import contextvars
import hashlib
import hmac
import json
import logging
import math
import os
import re
import shutil
import stat
import sys
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# --------------------------------------------------------------------------- #
# Import the kernel in place — projects/ stays untouched.
# --------------------------------------------------------------------------- #
HERE = Path(__file__).resolve().parent
AGI_DIR = (HERE.parent / "projects" / "agi-system").resolve()
if not (AGI_DIR / "core" / "kernel.py").is_file():
    sys.stderr.write(f"FATAL: agi-system kernel not found at {AGI_DIR}\n")
    sys.exit(1)
sys.path.insert(0, str(AGI_DIR))

from core.kernel import Kernel, KernelConfig  # noqa: E402
from core.models import Goal  # noqa: E402
from core.planner import Planner, SequentialPlanner  # noqa: E402

sys.path.insert(0, str(HERE.parent))
from memory.store import EvidenceMemoryStore  # noqa: E402

RUNS_DIR = HERE / "runs"
SANDBOXES_DIR = HERE / "sandboxes"
MEMORY_DB = HERE / "memory.db"
STATIC_DIR = HERE / "static"
MAX_CONTENT_BYTES = 64 * 1024          # goal param payloads
MAX_PARAM_STR = 4096                   # any single string param
SUPPORTED_GOALS = SequentialPlanner().supported_goal_types

_lock = threading.Lock()               # the kernel is single-run per instance
_run_index: dict[str, dict] = {}       # run_id -> {record, sandbox, ts}
_BOOT_TS = time.time()
_memory: EvidenceMemoryStore | None = None
_memory_lock = threading.Lock()
_READ_TOKEN_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_READ_TOKEN_SECRET_RE = re.compile(r"[A-Za-z0-9_-]{32,256}\Z")
_READ_TOKEN_HASH_RE = re.compile(r"[0-9a-fA-F]{64}\Z")
_READ_TOKEN_HASHES: dict[str, str] = {}
_PRIVATE_DIR_MODE = 0o700
_PRIVATE_FILE_MODE = 0o600
_SQLITE_SUFFIXES = ("", "-wal", "-shm", "-journal")
_WRITE_TOKEN = ""
_READ_ONLY = False
_REQUEST_ID_CONTEXT: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ai2027_request_id", default=None
)


class RequestRateLimiter:
    """Bound API requests per observed peer IP in a process-local time window."""

    def __init__(self, max_requests: int, window_seconds: int, max_clients: int = 4096):
        if max_requests < 1 or window_seconds < 1 or max_clients < 1:
            raise ValueError("rate limiter bounds must be positive")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.max_clients = max_clients
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, client: str, now: float | None = None) -> tuple[bool, int]:
        now = time.monotonic() if now is None else now
        cutoff = now - self.window_seconds
        with self._lock:
            events = self._events.get(client)
            if events is not None:
                while events and events[0] <= cutoff:
                    events.popleft()
                if not events:
                    self._events.pop(client, None)
                    events = None

            if events is None:
                if len(self._events) >= self.max_clients:
                    for peer, peer_events in list(self._events.items()):
                        while peer_events and peer_events[0] <= cutoff:
                            peer_events.popleft()
                        if not peer_events:
                            self._events.pop(peer, None)
                    if len(self._events) >= self.max_clients:
                        return False, self.window_seconds
                events = deque()
                self._events[client] = events

            if len(events) >= self.max_requests:
                retry_after = max(1, math.ceil(events[0] + self.window_seconds - now))
                return False, retry_after
            events.append(now)
            return True, 0


_API_READ_RATE_LIMITER = RequestRateLimiter(120, 60)
_API_WRITE_RATE_LIMITER = RequestRateLimiter(10, 60)


def configure_api_access_from_env() -> None:
    """Load per-principal read-token hashes and local API rate bounds.

    Clients send ``Bearer <token-id>.<random-secret>``. Only SHA-256 digests
    are kept in service configuration; malformed input fails closed without
    echoing any configured value.
    """
    global _READ_TOKEN_HASHES, _WRITE_TOKEN, _READ_ONLY
    global _API_READ_RATE_LIMITER, _API_WRITE_RATE_LIMITER
    raw_hashes = os.environ.get("AI2027_READ_TOKEN_HASHES", "").strip()
    if len(raw_hashes) > 32_768:
        raise ValueError("AI2027_READ_TOKEN_HASHES exceeds the configuration limit")
    if raw_hashes:
        try:
            parsed = json.loads(raw_hashes)
        except (TypeError, ValueError) as exc:
            raise ValueError("AI2027_READ_TOKEN_HASHES must be a JSON object") from exc
        if not isinstance(parsed, dict) or len(parsed) > 256:
            raise ValueError("AI2027_READ_TOKEN_HASHES must contain at most 256 token hashes")
        hashes: dict[str, str] = {}
        for token_id, digest in parsed.items():
            if not isinstance(token_id, str) or not _READ_TOKEN_ID_RE.fullmatch(token_id):
                raise ValueError("AI2027_READ_TOKEN_HASHES contains an invalid token id")
            if not isinstance(digest, str) or not _READ_TOKEN_HASH_RE.fullmatch(digest):
                raise ValueError("AI2027_READ_TOKEN_HASHES values must be SHA-256 hex digests")
            hashes[token_id] = digest.lower()
    else:
        hashes = {}

    write_token = os.environ.get("AI2027_WRITE_TOKEN", "")
    if write_token and (
        not 32 <= len(write_token) <= 512
        or write_token.strip() != write_token
        or any(char.isspace() for char in write_token)
    ):
        raise ValueError("AI2027_WRITE_TOKEN must be 32-512 characters with no whitespace")

    try:
        read_limit = int(os.environ.get("AI2027_READ_RATE_LIMIT", "120"))
        read_window = int(os.environ.get("AI2027_READ_RATE_WINDOW_SECONDS", "60"))
        write_limit = int(os.environ.get("AI2027_WRITE_RATE_LIMIT", "10"))
        write_window = int(os.environ.get("AI2027_WRITE_RATE_WINDOW_SECONDS", "60"))
    except ValueError as exc:
        raise ValueError("API rate settings must be integers") from exc
    if not 1 <= read_limit <= 10_000:
        raise ValueError("AI2027_READ_RATE_LIMIT must be between 1 and 10000")
    if not 1 <= read_window <= 86_400:
        raise ValueError("AI2027_READ_RATE_WINDOW_SECONDS must be between 1 and 86400")
    if not 1 <= write_limit <= 10_000:
        raise ValueError("AI2027_WRITE_RATE_LIMIT must be between 1 and 10000")
    if not 1 <= write_window <= 86_400:
        raise ValueError("AI2027_WRITE_RATE_WINDOW_SECONDS must be between 1 and 86400")

    _READ_TOKEN_HASHES = hashes
    _WRITE_TOKEN = write_token
    _READ_ONLY = os.environ.get("AI2027_READ_ONLY", "") == "1"
    _API_READ_RATE_LIMITER = RequestRateLimiter(read_limit, read_window)
    _API_WRITE_RATE_LIMITER = RequestRateLimiter(write_limit, write_window)


def _read_principal(authorization: str | None) -> str | None:
    if not authorization or len(authorization) > 512:
        return None
    scheme, separator, credential = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer":
        return None
    token_id, separator, secret = credential.partition(".")
    if (
        not separator
        or not _READ_TOKEN_ID_RE.fullmatch(token_id)
        or not _READ_TOKEN_SECRET_RE.fullmatch(secret)
    ):
        return None
    supplied = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    expected = _READ_TOKEN_HASHES.get(token_id, "0" * 64)
    matched = hmac.compare_digest(supplied, expected)
    return token_id if matched and token_id in _READ_TOKEN_HASHES else None


def read_access_configured() -> bool:
    return bool(_READ_TOKEN_HASHES)


def _write_token_matches(authorization: str | None) -> bool:
    if not _WRITE_TOKEN or not authorization or len(authorization) > 1024:
        return False
    scheme, separator, supplied = authorization.partition(" ")
    if (
        not separator
        or scheme.casefold() != "bearer"
        or not supplied
        or any(char.isspace() for char in supplied)
    ):
        return False
    return hmac.compare_digest(supplied.encode("utf-8"), _WRITE_TOKEN.encode("utf-8"))


def current_request_id() -> str | None:
    return _REQUEST_ID_CONTEXT.get()


def _is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def _canonical_route(path: str) -> str:
    if path in ("/", "/index.html"):
        return "/"
    if path in {
        "/api/health", "/api/agent-card", "/api/metrics", "/api/memory",
        "/api/memory/search", "/api/runs", "/api/file", "/api/run",
    }:
        return path
    if path.startswith("/api/run/"):
        return "/api/run/{id}"
    if _is_api_path(path):
        return "/api/{other}"
    return "/static/{other}"


def _safe_method(method: str | None) -> str:
    return method if method and re.fullmatch(r"[A-Z]{1,12}", method) else "OTHER"


def _configure_kernel_log_suppression() -> None:
    """Keep core exception text out of service stdout/stderr without editing core."""
    logger = logging.getLogger("agi.kernel")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False


def _reject_symlink_components(path: str | Path) -> Path:
    """Return an absolute path only if no existing component traverses a symlink."""
    raw = Path(path).expanduser()
    if ".." in raw.parts:
        raise ValueError("runtime paths must not contain parent traversal")
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    candidate = Path(os.path.abspath(os.fspath(raw)))
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current = current / part
        try:
            if current.is_symlink():
                raise ValueError("runtime paths must not traverse symlinks")
        except OSError as exc:
            raise ValueError("runtime path could not be inspected") from exc
    return candidate


def _validate_runtime_layout(runs_dir: Path | None = None,
                             sandboxes_dir: Path | None = None,
                             memory_db: Path | None = None) -> None:
    """Refuse runtime directory/file links before creating or deleting data."""
    runs = RUNS_DIR if runs_dir is None else Path(runs_dir)
    sandboxes = SANDBOXES_DIR if sandboxes_dir is None else Path(sandboxes_dir)
    database = Path(MEMORY_DB if memory_db is None else memory_db)
    paths = (
        runs,
        sandboxes,
        database.parent,
        runs / "ledgers",
        *(Path(str(database) + suffix) for suffix in _SQLITE_SUFFIXES),
    )
    for path in paths:
        _reject_symlink_components(path)


def _set_private_directory_mode(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("runtime storage layout is not a real directory")
    if os.name == "posix":
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(path, flags)
        try:
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise ValueError("runtime storage layout is not a real directory")
            os.fchmod(descriptor, _PRIVATE_DIR_MODE)
        finally:
            os.close(descriptor)


def _set_private_file_mode(path: Path, *, create: bool = False) -> bool:
    """Validate a regular runtime file and make it owner-only on POSIX."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if not create or os.name != "posix":
            return False
        create_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        create_flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor = os.open(path, create_flags, _PRIVATE_FILE_MODE)
        except FileExistsError:
            metadata = path.lstat()
        else:
            try:
                os.fchmod(descriptor, _PRIVATE_FILE_MODE)
            finally:
                os.close(descriptor)
            metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("runtime storage files must be regular files")
    if os.name != "posix":
        return True

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("runtime storage files must be regular files")
        os.fchmod(descriptor, _PRIVATE_FILE_MODE)
        return True
    finally:
        os.close(descriptor)


def _secure_runtime_tree(root: Path) -> None:
    """Tighten existing runtime data without following or accepting links."""
    try:
        metadata = root.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("runtime storage root must be a real directory")
    _set_private_directory_mode(root)

    def fail_walk(error: OSError) -> None:
        raise ValueError("runtime storage could not be inspected") from error

    for current, directories, filenames in os.walk(
        root, topdown=True, followlinks=False, onerror=fail_walk
    ):
        parent = Path(current)
        for name in directories:
            _set_private_directory_mode(parent / name)
        for name in filenames:
            _set_private_file_mode(parent / name)


def _secure_sqlite_files(*, create_database: bool = False) -> None:
    """Reject linked SQLite files and tighten the database/sidecar modes."""
    database = Path(MEMORY_DB)
    present = [
        _set_private_file_mode(database.with_name(database.name + suffix),
                               create=(create_database and not suffix))
        for suffix in _SQLITE_SUFFIXES
    ]
    if not present[0] and any(present[1:]):
        raise ValueError("runtime SQLite sidecar exists without its database")


def _restrict_runtime_permissions() -> None:
    """Apply a private process umask and owner-only modes to durable runtime data."""
    if os.name == "posix":
        previous = os.umask(0o077)
        os.umask(previous | 0o077)
    _validate_runtime_layout()
    for directory in (RUNS_DIR, SANDBOXES_DIR):
        _secure_runtime_tree(Path(directory))
    database_parent = Path(MEMORY_DB).parent
    if database_parent.name == "state" and database_parent != HERE:
        _secure_runtime_tree(database_parent)
    _secure_sqlite_files()


def _configure_runtime(runtime_dir: str | Path) -> None:
    """Redirect this process to an explicit, isolated runtime directory."""
    global RUNS_DIR, SANDBOXES_DIR, MEMORY_DB, _memory
    root = _reject_symlink_components(runtime_dir)
    runs = root / "runs"
    sandboxes = root / "sandboxes"
    database = root / "state" / "memory.db"
    _validate_runtime_layout(runs, sandboxes, database)
    root_was_missing = not root.exists()
    root.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIR_MODE)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("runtime root must be a real directory")
    if root_was_missing and root != HERE:
        _set_private_directory_mode(root)
    RUNS_DIR, SANDBOXES_DIR, MEMORY_DB = runs, sandboxes, database
    with _memory_lock:
        _memory = None


def memory() -> EvidenceMemoryStore:
    """Lazy store: sandbox artifacts must exist before admission is checked."""
    global _memory
    with _memory_lock:
        if _memory is None:
            _secure_sqlite_files(create_database=True)
            store = EvidenceMemoryStore(MEMORY_DB, sandbox_root=SANDBOXES_DIR)
            _secure_sqlite_files()
            _memory = store
        return _memory


def _fresh_state() -> None:
    """Destructively clear local runtime data; callers must opt into this."""
    global _memory
    _validate_runtime_layout()
    for d in (RUNS_DIR, SANDBOXES_DIR):
        if d.exists() and not d.is_dir():
            raise ValueError("runtime storage layout is not a directory")
    memory_files = [Path(str(MEMORY_DB) + suffix) for suffix in _SQLITE_SUFFIXES]
    if any(path.exists() and not path.is_file() for path in memory_files):
        raise ValueError("runtime memory layout is not a regular file")
    for d in (RUNS_DIR, SANDBOXES_DIR):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIR_MODE)
        _set_private_directory_mode(d)
    database_parent = Path(MEMORY_DB).parent
    database_parent.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIR_MODE)
    if database_parent.name == "state" and database_parent != HERE:
        _set_private_directory_mode(database_parent)
    _run_index.clear()
    with _memory_lock:
        _memory = None
        for path in memory_files:
            if path.exists():
                path.unlink()


def _ensure_state() -> None:
    """Create runtime directories without deleting prior runs or memory."""
    _validate_runtime_layout()
    for d in (RUNS_DIR, SANDBOXES_DIR, Path(MEMORY_DB).parent, RUNS_DIR / "ledgers"):
        d.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIR_MODE)
        if d.is_symlink() or not d.is_dir():
            raise ValueError("runtime storage layout is not a directory")
        if d in (RUNS_DIR, SANDBOXES_DIR, RUNS_DIR / "ledgers") or (
            d.name == "state" and d != HERE
        ):
            _set_private_directory_mode(d)
    _secure_sqlite_files()


def _restore_run_index() -> list[str]:
    """Rebuild the in-memory recent-run index from durable DecisionRecords."""
    _run_index.clear()
    warnings: list[str] = []
    for path in sorted(RUNS_DIR.glob("run_*.json")):
        if path.is_symlink() or not re.fullmatch(r"run_\d+_[0-9a-f]{8}\.json", path.name):
            continue
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(rec, dict) or rec.get("run_id") != path.stem:
                raise ValueError("record id does not match its filename")
            run_id = rec["run_id"]
            goal = rec.get("goal") if isinstance(rec.get("goal"), dict) else {}
            stamp = rec.get("finished_at") or rec.get("started_at") or path.stat().st_mtime
            _run_index[run_id] = {
                "ts": float(stamp),
                "status": str(rec.get("status") or "UNKNOWN"),
                "goal_type": str(goal.get("goal_type") or "unknown"),
            }
        except (OSError, UnicodeError, ValueError, TypeError, AttributeError) as exc:
            warnings.append(f"could not restore {path.name}: {type(exc).__name__}")
    return warnings


def _prepare_state(fresh: bool = False) -> list[str]:
    """Default to preservation; destructive cleanup requires `fresh=True`."""
    if fresh:
        _fresh_state()
        return []
    _ensure_state()
    return _restore_run_index()


def _sanitize_params(params: object) -> dict:
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    clean: dict[str, object] = {}
    for k, v in params.items():
        if not isinstance(k, str) or len(k) > 64:
            raise ValueError("bad param key")
        if isinstance(v, str) and len(v) > MAX_PARAM_STR:
            raise ValueError(f"param {k!r} too long (>{MAX_PARAM_STR})")
        if not isinstance(v, (str, int, float, bool)) and v is not None:
            raise ValueError(f"param {k!r}: unsupported type")
        clean[k] = v
    return clean


def run_goal(goal_type: str, params: dict, *, planner: Planner | None = None) -> dict:
    """Execute one goal through the real kernel and persist the evidence.

    ``planner`` is an in-process test/extension seam only; the HTTP API does not
    expose provider configuration. With no planner supplied, the existing
    deterministic planner remains the default.
    """
    if goal_type not in SUPPORTED_GOALS:
        raise ValueError(
            f"unsupported goal_type {goal_type!r}; supported={SUPPORTED_GOALS}"
        )
    params = _sanitize_params(params)
    run_id = f"run_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    sandbox = SANDBOXES_DIR / run_id
    sandbox.mkdir(mode=_PRIVATE_DIR_MODE)
    _set_private_directory_mode(sandbox)

    _configure_kernel_log_suppression()
    with _lock:
        kernel = Kernel(
            sandbox_dir=sandbox,
            # Keep the append-only audit ledger outside the model-accessible sandbox.
            ledger_path=RUNS_DIR / "ledgers" / f"{run_id}.jsonl",
            planner=planner,
            config=KernelConfig(),
        )
        # Use the public run ID in the kernel ledger too, so the sandbox,
        # persisted record, and append-only ledger share one verifiable ID.
        record = kernel.run(Goal(goal_type=goal_type, params=params), run_id=run_id)

    payload = record.to_dict()
    payload["run_id"] = run_id
    payload["sandbox"] = run_id  # public handle for the sandbox dir
    files = []
    for p in sorted(sandbox.rglob("*")):
        if p.is_file() and p.name != "ledger.jsonl":
            files.append(str(p.relative_to(sandbox)))
    payload["sandbox_files"] = files

    # Evidence-gated memory: only VERIFIED runs with a real, hash-matching
    # artifact are admitted; refusals are logged with their reason.
    try:
        verdict = memory().ingest(payload)
        payload["memory"] = {"admitted": bool(verdict.admitted),
                             "reason": verdict.reason,
                             "memory_id": verdict.memory_id,
                             "trust": verdict.trust,
                             "artifact_verified": bool(verdict.artifact_verified)}
    except Exception:                            # memory must never break a run
        payload["memory"] = {
            "admitted": False,
            "reason": "memory store unavailable",
            "error_code": "MEMORY_STORE_ERROR",
        }

    _persist_run_record(payload)
    _run_index[run_id] = {"ts": time.time(), "status": payload["status"],
                          "goal_type": goal_type}
    return payload


def _persist_run_record(payload: dict) -> None:
    """Atomically persist a validated run record without logging its contents."""
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"run_\d+_[0-9a-f]{8}", run_id):
        raise ValueError("invalid run id")
    target = RUNS_DIR / f"{run_id}.json"
    temporary = RUNS_DIR / f".{run_id}.{uuid.uuid4().hex}.tmp"
    descriptor: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(temporary, flags, _PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None  # fdopen owns it from here
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":
            os.chmod(temporary, _PRIVATE_FILE_MODE)
        os.replace(temporary, target)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def read_run(run_id: str) -> dict | None:
    f = RUNS_DIR / f"{run_id}.json"
    if f.is_file():
        return json.loads(f.read_text(encoding="utf-8"))
    return None


def sandbox_file(run_id: str, rel: str) -> tuple[bytes, str] | None:
    """Serve a file from a run sandbox — traversal-safe (defense in depth:
    the kernel policy is the first wall, this is the second)."""
    if not re.fullmatch(r"run_\d+_[0-9a-f]{8}", run_id or ""):
        return None
    root = (SANDBOXES_DIR / run_id).resolve()
    if not root.is_dir():
        return None
    target = (root / rel).resolve()
    if root not in target.parents and target != root:
        return None
    if not target.is_file() or target.name == "ledger.jsonl":
        return None
    data = target.read_bytes()
    if len(data) > 4 * 1024 * 1024:
        return None
    return data, target.name


# --------------------------------------------------------------------------- #
# HTTP layer
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "AI2027LiveAgent/1.0"

    def __init__(self, *args, **kwargs):
        self.request_id = uuid.uuid4().hex
        self._request_started = time.monotonic()
        self._response_status: int | None = None
        self._request_log_written = False
        super().__init__(*args, **kwargs)

    def send_response(self, code: int, message: str | None = None) -> None:
        self._response_status = int(code)
        super().send_response(code, message)
        self.send_header("X-Request-ID", self.request_id)

    def log_request(self, code="-", size="-") -> None:
        try:
            self._response_status = int(code)
        except (TypeError, ValueError):
            self._response_status = None

    def log_message(self, _fmt: str, *_args) -> None:
        # BaseHTTPRequestHandler may pass the raw request line or parse error.
        # Deliberately discard it; the structured log below contains no inputs.
        return None

    def send_error(self, code: int, message=None, explain=None) -> None:
        """Return a generic error body instead of reflecting malformed input."""
        self._response_status = int(code)
        self.close_connection = True
        body = json.dumps({
            "error": "request rejected",
            "request_id": self.request_id,
        }).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        if getattr(self, "command", None) != "HEAD":
            self.wfile.write(body)

    def _write_access_log(self) -> None:
        if self._request_log_written:
            return
        self._request_log_written = True
        route = _canonical_route(urlparse(getattr(self, "path", "")).path)
        record = {
            "request_id": self.request_id,
            "method": _safe_method(getattr(self, "command", None)),
            "route": route,
            "status": self._response_status,
            "duration_ms": round(max(0.0, time.monotonic() - self._request_started) * 1000, 3),
        }
        try:
            sys.stderr.write(json.dumps(record, separators=(",", ":")) + "\n")
            sys.stderr.flush()
        except OSError:
            pass

    def finish(self) -> None:
        try:
            super().finish()
        finally:
            self._write_access_log()

    def _json(self, obj: object, status: int = 200,
              headers: dict[str, str] | None = None) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _text(self, body: str, content_type: str = "text/plain; charset=utf-8",
              status: int = 200) -> None:
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _static(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _check_api_rate_limit(self) -> bool:
        allowed, retry_after = _API_READ_RATE_LIMITER.allow(str(self.client_address[0]))
        if allowed:
            return True
        self._json(
            {"error": "rate limit exceeded"},
            429,
            {"Retry-After": str(retry_after)},
        )
        return False

    def _require_read_access(self) -> bool:
        principal = _read_principal(self.headers.get("Authorization"))
        if principal is None:
            self._json(
                {"error": "authentication required"},
                401,
                {"WWW-Authenticate": 'Bearer realm="ai-2027-live-agent"'},
            )
            return False
        self._read_principal_id = principal
        return True

    def _check_write_rate_limit(self) -> bool:
        allowed, retry_after = _API_WRITE_RATE_LIMITER.allow(str(self.client_address[0]))
        if allowed:
            return True
        self._json(
            {"error": "rate limit exceeded"},
            429,
            {"Retry-After": str(retry_after)},
        )
        return False

    def _require_write_access(self) -> bool:
        if _READ_ONLY:
            self._json({"error": "writes are disabled"}, 403)
            return False
        if not _WRITE_TOKEN:
            self._json({"error": "write endpoint unavailable"}, 503)
            return False
        if not _write_token_matches(self.headers.get("Authorization")):
            self._json(
                {"error": "authentication required"},
                401,
                {"WWW-Authenticate": 'Bearer realm="ai-2027-live-agent"'},
            )
            return False
        return True

    # ------------------------------ GET ---------------------------------- #
    def do_GET(self) -> None:
        try:
            return self._do_GET()
        except Exception:
            return self._json(
                {"error": "internal server error", "request_id": self.request_id}, 500
            )

    def _do_GET(self) -> None:
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._static(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        if _is_api_path(u.path):
            if not self._check_api_rate_limit():
                return
            if u.path != "/api/health" and not self._require_read_access():
                return
        if u.path == "/api/health":
            return self._json({"ok": True, "service": "ai-2027-live-agent"})
        if u.path == "/api/agent-card":
            card_path = HERE.parent / "agent-card.json"
            if not card_path.is_file():
                return self._json({"error": "agent-card.json missing"}, 404)
            card = json.loads(card_path.read_text(encoding="utf-8"))
            card["served_by"] = "live-agent/server.py"
            card["live"] = {"runs": len(_run_index),
                            "memories": memory().stats()["memories"]}
            return self._json(card)
        if u.path == "/api/metrics":
            # Prometheus text format. Counters only — no derived "health score"
            # that could flatter us; the smoke test decides pass/fail.
            runs = list(_run_index.values())
            st = memory().stats()
            lines = [
                "# HELP ai2027_runs_total Kernel runs by final status.",
                "# TYPE ai2027_runs_total counter",
            ]
            for status in ("VERIFIED", "DENIED", "FAILED", "ABORTED", "INCONCLUSIVE"):
                n = sum(1 for r in runs if r.get("status") == status)
                lines.append(f'ai2027_runs_total{{status="{status}"}} {n}')
            lines += [
                "# HELP ai2027_memories Evidence-gated memories admitted.",
                "# TYPE ai2027_memories gauge",
                f"ai2027_memories {st['memories']}",
                "# HELP ai2027_memory_refusals Runs refused by the evidence gate.",
                "# TYPE ai2027_memory_refusals counter",
                f"ai2027_memory_refusals {st['refused_events']}",
                "# HELP ai2027_uptime_seconds Seconds since this process booted.",
                "# TYPE ai2027_uptime_seconds gauge",
                f"ai2027_uptime_seconds {round(time.time() - _BOOT_TS, 3)}",
            ]
            return self._text("\n".join(lines) + "\n")
        if u.path == "/api/memory":
            return self._json(memory().stats())
        if u.path == "/api/memory/search":
            q = parse_qs(u.query).get("q", [""])[0].strip()
            if not q:
                return self._json({"error": "missing q"}, 400)
            try:
                k = max(1, min(20, int(parse_qs(u.query).get("k", ["5"])[0])))
            except ValueError:
                return self._json({"error": "bad k"}, 400)
            res = memory().search(q, k=k)
            res["honest_limits"] = [
                "Lexical, not neural: true synonyms are missed (measured, not assumed).",
                "Only VERIFIED runs with a hash-matching artifact are remembered.",
            ]
            return self._json(res)
        if u.path == "/api/runs":
            runs = sorted(
                ({"run_id": k, **v} for k, v in _run_index.items()),
                key=lambda r: r["ts"], reverse=True,
            )[:50]
            return self._json({"runs": runs})
        if u.path.startswith("/api/run/"):
            rec = read_run(u.path.rsplit("/", 1)[-1])
            return self._json(rec) if rec else self._json({"error": "not found"}, 404)
        if u.path == "/api/file":
            q = parse_qs(u.query)
            got = sandbox_file(q.get("run", [""])[0], q.get("path", [""])[0])
            if not got:
                return self._json({"error": "not found or not allowed"}, 404)
            data, name = got
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition",
                             f"attachment; filename=\"{name}\"")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        return self._json({"error": "not found"}, 404)

    # ------------------------------ POST --------------------------------- #
    def do_POST(self) -> None:
        context_token = _REQUEST_ID_CONTEXT.set(self.request_id)
        try:
            return self._do_POST()
        finally:
            _REQUEST_ID_CONTEXT.reset(context_token)

    def _do_POST(self) -> None:
        u = urlparse(self.path)
        if not _is_api_path(u.path):
            return self._json({"error": "not found"}, 404)
        if not self._check_write_rate_limit():
            return
        if not self._require_write_access():
            return
        if u.path != "/api/run":
            return self._json({"error": "not found"}, 404)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._json({"error": "bad content-length"}, 400)
        if length <= 0 or length > MAX_CONTENT_BYTES:
            return self._json({"error": "payload too large or empty"}, 413)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            goal_type = body.get("goal_type", "")
            params = body.get("params", {})
            if not isinstance(goal_type, str):
                raise ValueError("goal_type must be a string")
            payload = run_goal(goal_type, params)
            return self._json(payload, 200)
        except ValueError as exc:
            message = str(exc)
            if message.startswith("run budget exhausted ("):
                return self._json({
                    "error": message,
                    "error_code": "RUN_BUDGET_EXHAUSTED",
                    "request_id": self.request_id,
                }, 400)
            return self._json({"error": "invalid request", "request_id": self.request_id}, 400)
        except Exception:
            return self._json(
                {"error": "internal server error", "request_id": self.request_id}, 500
            )


class SafeThreadingHTTPServer(ThreadingHTTPServer):
    """Suppress socketserver tracebacks that can include request-derived text."""

    def handle_error(self, _request, _client_address) -> None:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address (loopback by default; use deployment/live_server.py for hosting)")
    ap.add_argument("--runtime-dir", default=os.environ.get("AI2027_LIVE_RUNTIME_DIR"),
                    help="isolate persistent runs/sandboxes/memory under this directory")
    ap.add_argument("--fresh", action="store_true",
                    help="DESTRUCTIVE: delete this service's runs, sandboxes, and memory before starting")
    args = ap.parse_args()
    try:
        configure_api_access_from_env()
    except ValueError:
        sys.stderr.write("FATAL: invalid API access configuration\n")
        return 2
    _configure_kernel_log_suppression()
    if args.runtime_dir:
        _configure_runtime(args.runtime_dir)
    _restrict_runtime_permissions()
    restore_warnings = _prepare_state(fresh=args.fresh)
    print(f"AI-2027 Live Agent Experience — http://{args.host}:{args.port}")
    print("kernel: agi-system imported in place")
    print(f"runtime mode: {'fresh' if args.fresh else 'preserving existing evidence'}")
    print(f"private API: {'per-principal reader tokens configured' if read_access_configured() else 'fail-closed; no reader configured'}")
    print(f"API read rate limit: {_API_READ_RATE_LIMITER.max_requests} per {_API_READ_RATE_LIMITER.window_seconds}s per peer IP")
    write_state = "read-only" if _READ_ONLY else ("operator token required" if _WRITE_TOKEN else "fail-closed; no writer configured")
    print(f"API writes: {write_state}")
    if restore_warnings:
        print(f"runtime restore warnings: {len(restore_warnings)}", file=sys.stderr)
    SafeThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
