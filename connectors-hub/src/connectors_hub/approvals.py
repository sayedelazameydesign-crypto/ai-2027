"""Encrypted pending-action queue and local-only approval/execution path."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import unicodedata
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

from .catalog import SPECS
from .config import Settings
from .credentials import CredentialVault
from .crypto import PayloadCipher
from .http_api import ProviderError, _assert_trusted_base, provider_headers
from .models import ActionProposalRequest, ActionStatusResponse, ConnectorId


class ActionError(RuntimeError):
    """Safe, user-facing action queue error."""


@dataclass(frozen=True)
class ActionPreview:
    id: str
    connector: ConnectorId
    summary: str
    method: str
    path: str
    query: dict[str, str]
    payload: dict[str, Any] | list[Any] | None
    status: str
    request_hash: str
    created_at: str
    expires_at: str


def _now() -> datetime:
    return datetime.now(UTC)


def _validate_action_id(action_id: str) -> None:
    if not _ACTION_ID.fullmatch(action_id):
        raise ActionError("Action ID is invalid")


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


_ACTION_ID = re.compile(r"^[0-9a-f]{32}$")
_SECRET_FIELD = re.compile(
    r"(?:password|passwd|secret|service[_-]?role|authorization|access[_-]?token|"
    r"refresh[_-]?token|api[_-]?key|client[_-]?secret|private[_-]?key)",
    re.IGNORECASE,
)


def _validate_path(path: str) -> None:
    if not path.startswith("/") or path.startswith("//"):
        raise ActionError(
            "Path must be an absolute path within the selected provider API"
        )
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ActionError("Path must not contain a host, query string, or fragment")
    if re.search(r"%(?:2f|5c)", path, flags=re.IGNORECASE):
        raise ActionError("Encoded path separators are not accepted")

    decoded = path
    for _ in range(5):
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            break
        decoded = next_decoded
    if decoded.startswith("//") or "\\" in decoded or "\x00" in decoded:
        raise ActionError("Path contains a forbidden traversal component")
    if any(part in {".", ".."} for part in decoded.split("/")):
        raise ActionError("Path contains a forbidden traversal component")
    if any(unicodedata.category(ch) in {"Cc", "Cf", "Zl", "Zp"} for ch in decoded):
        raise ActionError(
            "Control and bidirectional formatting characters are not accepted in paths"
        )
    if re.search(r"%(?:2f|5c|00|0[0-9a-f]|1[0-9a-f])", decoded, flags=re.IGNORECASE):
        raise ActionError(
            "Repeatedly encoded separators or control characters are not accepted"
        )


def _check_payload_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if _SECRET_FIELD.search(str(key)):
                raise ActionError(
                    "Secret-like fields are not accepted in agent-proposed provider payloads"
                )
            _check_payload_keys(nested)
    elif isinstance(value, list):
        for item in value:
            _check_payload_keys(item)


def _check_query_keys(query: dict[str, str]) -> None:
    if any(_SECRET_FIELD.search(key) for key in query):
        raise ActionError(
            "Secret-like query parameters are not accepted in agent-proposed provider requests"
        )


def _to_status(row: sqlite3.Row) -> ActionStatusResponse:
    return ActionStatusResponse(
        id=row["id"],
        connector=ConnectorId(row["connector"]),
        summary=row["summary"],
        method=row["method"],
        path=row["path"],
        status=row["status"],
        request_hash=row["request_hash"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        response_status=row["response_status"],
        completed_at=row["completed_at"],
    )


class ApprovalStore:
    """SQLite queue. Payloads are encrypted; service responses are not retained."""

    def __init__(self, database_path: Path, vault: CredentialVault):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._cipher = PayloadCipher(vault)
        self._vault = vault
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    def _initialize(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = FULL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS actions (
                    id TEXT PRIMARY KEY,
                    connector TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    method TEXT NOT NULL,
                    path TEXT NOT NULL,
                    encrypted_request TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    approved_at TEXT,
                    completed_at TEXT,
                    response_status INTEGER,
                    error_code TEXT
                );
                CREATE INDEX IF NOT EXISTS actions_status_created
                    ON actions(status, created_at);
                CREATE TABLE IF NOT EXISTS audit_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    action_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    at TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(action_id) REFERENCES actions(id)
                );
                """
            )
        if os.name != "nt":
            try:
                os.chmod(self.database_path, 0o600)
                wal = Path(str(self.database_path) + "-wal")
                shm = Path(str(self.database_path) + "-shm")
                if wal.exists():
                    os.chmod(wal, 0o600)
                if shm.exists():
                    os.chmod(shm, 0o600)
            except OSError:
                # Permission hardening is best-effort; the OS profile directory
                # remains the primary boundary on platforms with ACLs.
                pass

    def propose(self, request: ActionProposalRequest) -> ActionStatusResponse:
        _validate_path(request.path)
        if len(_canonical(request.payload)) > 64 * 1024:
            raise ActionError("Proposed payload exceeds the 64 KiB safety limit")
        _check_payload_keys(request.payload)
        _check_query_keys(request.query)
        if any(len(k) > 100 or len(v) > 2000 for k, v in request.query.items()):
            raise ActionError("Query parameter exceeds the safety limit")
        if any(
            unicodedata.category(ch) in {"Cf", "Zl", "Zp"} for ch in request.summary
        ):
            raise ActionError("Summary must not contain hidden formatting characters")
        summary = " ".join(request.summary.split())
        if len(summary) < 8:
            raise ActionError(
                "Action summary must contain at least eight visible characters"
            )
        action_id = uuid.uuid4().hex
        created = _now()
        expires = created + timedelta(minutes=request.expires_in_minutes)
        record = {
            "query": request.query,
            "payload": request.payload,
        }
        request_material = {
            "connector": request.connector.value,
            "summary": summary,
            "method": request.method,
            "path": request.path,
            "request": record,
        }
        request_hash = hashlib.sha256(_canonical(request_material)).hexdigest()
        encrypted = self._cipher.encrypt_json(
            record, associated_data=action_id.encode("ascii")
        )
        created_iso, expires_iso = created.isoformat(), expires.isoformat()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT INTO actions
                   (id,connector,summary,method,path,encrypted_request,request_hash,status,created_at,expires_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    action_id,
                    request.connector.value,
                    summary,
                    request.method,
                    request.path,
                    encrypted,
                    request_hash,
                    "pending_approval",
                    created_iso,
                    expires_iso,
                ),
            )
            self._audit(conn, action_id, "proposed", {"request_hash": request_hash})
            conn.commit()
        return self.get_status(action_id)

    def get_status(self, action_id: str) -> ActionStatusResponse:
        _validate_action_id(action_id)
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM actions WHERE id=?", (action_id,)
            ).fetchone()
        if row is None:
            raise ActionError("Action was not found")
        return _to_status(row)

    def list_pending(self, limit: int = 100) -> list[ActionStatusResponse]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM actions WHERE status='pending_approval' ORDER BY created_at LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
        return [_to_status(row) for row in rows]

    def _preview_from_row(self, row: sqlite3.Row) -> ActionPreview:
        action_id = row["id"]
        record = self._cipher.decrypt_json(
            row["encrypted_request"], associated_data=action_id.encode("ascii")
        )
        if not isinstance(record, dict) or not isinstance(
            record.get("query", {}), dict
        ):
            raise ActionError(
                "Action payload is malformed; refusing to display or execute it"
            )
        _validate_path(row["path"])
        _check_query_keys(record.get("query", {}))
        _check_payload_keys(record.get("payload"))
        request_material = {
            "connector": row["connector"],
            "summary": row["summary"],
            "method": row["method"],
            "path": row["path"],
            "request": record,
        }
        verified_hash = hashlib.sha256(_canonical(request_material)).hexdigest()
        if not secrets_compare(verified_hash, row["request_hash"]):
            raise ActionError(
                "Action integrity check failed; refusing to display or execute it"
            )
        try:
            connector = ConnectorId(row["connector"])
        except ValueError as exc:
            raise ActionError("Action references an unsupported connector") from exc
        return ActionPreview(
            id=action_id,
            connector=connector,
            summary=row["summary"],
            method=row["method"],
            path=row["path"],
            query=record.get("query", {}),
            payload=record.get("payload"),
            status=row["status"],
            request_hash=row["request_hash"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )

    def get_preview(self, action_id: str) -> ActionPreview:
        _validate_action_id(action_id)
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM actions WHERE id=?", (action_id,)
            ).fetchone()
        if row is None:
            raise ActionError("Action was not found")
        if row["status"] != "pending_approval":
            raise ActionError("Only pending actions can be previewed for approval")
        if _now() >= datetime.fromisoformat(row["expires_at"]):
            self._set_terminal(action_id, "expired", "expired")
            raise ActionError("Action approval window expired")
        return self._preview_from_row(row)

    def execute_after_local_approval(
        self,
        action_id: str,
        confirmation: str,
        *,
        settings: Settings,
        client: httpx.Client | None = None,
    ) -> ActionStatusResponse:
        _validate_action_id(action_id)
        expected = f"APPROVE-{action_id[:8].upper()}"
        if not secrets_compare(confirmation, expected):
            raise ActionError("Approval phrase did not match; no request was sent")
        preview = self.get_preview(action_id)
        spec = SPECS[preview.connector]
        try:
            base = _assert_trusted_base(spec, settings)
            headers = provider_headers(preview.connector, self._vault)
        except ProviderError as exc:
            raise ActionError(f"{exc}; the action remains pending") from exc

        owned_client = client is None
        url = f"{base}{preview.path}"

        # Claim atomically and re-verify the encrypted payload while holding the
        # write lock. No changed request can be executed after the CLI preview.
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM actions WHERE id=?", (action_id,)
            ).fetchone()
            if row is None or row["status"] != "pending_approval":
                conn.rollback()
                raise ActionError("Action is no longer pending")
            if _now() >= datetime.fromisoformat(row["expires_at"]):
                conn.execute(
                    "UPDATE actions SET status='expired', completed_at=? WHERE id=?",
                    (_now().isoformat(), action_id),
                )
                self._audit(conn, action_id, "expired", {})
                conn.commit()
                raise ActionError("Action approval window expired")
            locked_preview = self._preview_from_row(row)
            if not secrets_compare(locked_preview.request_hash, preview.request_hash):
                conn.rollback()
                raise ActionError("Action changed after review; no request was sent")
            cursor = conn.execute(
                "UPDATE actions SET status='executing', approved_at=? WHERE id=? AND status='pending_approval' AND request_hash=?",
                (_now().isoformat(), action_id, preview.request_hash),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                raise ActionError("Action is no longer pending")
            self._audit(
                conn,
                action_id,
                "approved_locally",
                {"request_hash": row["request_hash"]},
            )
            conn.commit()

        try:
            if client is None:
                client = httpx.Client(
                    timeout=httpx.Timeout(20.0, connect=5.0), follow_redirects=False
                )
            with client.stream(
                preview.method,
                url,
                headers=headers,
                params=preview.query,
                json=preview.payload,
                follow_redirects=False,
            ) as response:
                response_status = response.status_code
            if 200 <= response_status < 300:
                status, error_code = "executed", None
            elif 300 <= response_status < 400 or response_status >= 500:
                # A redirect or server error may follow a partial write. Keep the
                # outcome explicitly uncertain and never retry automatically.
                status, error_code = "unknown", "delivery_unknown"
            else:
                status, error_code = "failed", "provider_rejected"
            self._finish(action_id, status, response_status, error_code)
        except (httpx.TimeoutException, httpx.RequestError):
            # The provider may have applied a write before the connection failed.
            # Do not retry; the operator must inspect provider state first.
            self._finish(action_id, "unknown", None, "delivery_unknown")
        except Exception:  # noqa: BLE001 - once claimed, unknown failures must never leave a retryable action.
            # Any unexpected transport/client failure after the one-time claim is
            # treated as uncertain. An action is never automatically retried.
            self._finish(action_id, "unknown", None, "delivery_unknown")
        finally:
            if owned_client and client is not None:
                client.close()
        return self.get_status(action_id)

    def cancel(self, action_id: str) -> ActionStatusResponse:
        _validate_action_id(action_id)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM actions WHERE id=?", (action_id,)
            ).fetchone()
            if row is None:
                conn.rollback()
                raise ActionError("Action was not found")
            if row["status"] != "pending_approval":
                conn.rollback()
                raise ActionError("Only a pending action can be cancelled")
            now = _now().isoformat()
            conn.execute(
                "UPDATE actions SET status='cancelled', completed_at=? WHERE id=?",
                (now, action_id),
            )
            self._audit(conn, action_id, "cancelled", {})
            conn.commit()
        return self.get_status(action_id)

    def audit_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT action_id,event,at,details_json FROM audit_events ORDER BY sequence DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [
            {
                "action_id": row["action_id"],
                "event": row["event"],
                "at": row["at"],
                "details": json.loads(row["details_json"]),
            }
            for row in rows
        ]

    def _finish(
        self,
        action_id: str,
        status: str,
        response_status: int | None,
        error_code: str | None,
    ) -> None:
        now = _now().isoformat()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE actions SET status=?,response_status=?,completed_at=?,error_code=? WHERE id=? AND status='executing'",
                (status, response_status, now, error_code, action_id),
            )
            self._audit(
                conn,
                action_id,
                status,
                {"response_status": response_status, "error_code": error_code},
            )
            conn.commit()

    def _set_terminal(self, action_id: str, status: str, event: str) -> None:
        now = _now().isoformat()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE actions SET status=?,completed_at=? WHERE id=? AND status='pending_approval'",
                (status, now, action_id),
            )
            self._audit(conn, action_id, event, {})
            conn.commit()

    @staticmethod
    def _audit(
        conn: sqlite3.Connection, action_id: str, event: str, details: dict[str, Any]
    ) -> None:
        conn.execute(
            "INSERT INTO audit_events(action_id,event,at,details_json) VALUES (?,?,?,?)",
            (
                action_id,
                event,
                _now().isoformat(),
                json.dumps(details, sort_keys=True, separators=(",", ":")),
            ),
        )


def secrets_compare(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
