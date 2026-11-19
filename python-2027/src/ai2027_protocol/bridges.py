"""Bridges uniting L0 Kernel (agi-system), L1 Evidence Memory, and L5 Connectors."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import re
import sys
import threading
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import Field

from .ai_providers import credential_label
from .contracts import StrictModel
from .security import SecurityValidationError, redact_secrets, scrub_mapping

_CORE_IMPORT_LOCK = threading.Lock()
_MEMORY_IMPORT_LOCK = threading.Lock()
_ACTION_ID_RE = re.compile(r"^[a-f0-9]{16,32}$")

_CONNECTOR_METADATA: tuple[tuple[str, str, str, str, tuple[str, ...]], ...] = (
    ("gmail", "Gmail", "google", "GOOGLE_ACCESS_TOKEN", ("Email triage & draft proposals",)),
    (
        "google_drive",
        "Google Drive",
        "google",
        "GOOGLE_ACCESS_TOKEN",
        ("Read-only file discovery; writes require approval",),
    ),
    (
        "google_docs",
        "Google Docs",
        "google",
        "GOOGLE_ACCESS_TOKEN",
        ("Document extraction & safe append proposals",),
    ),
    (
        "google_sheets",
        "Google Sheets",
        "google",
        "GOOGLE_ACCESS_TOKEN",
        ("Spreadsheet inspection & structured cell proposals",),
    ),
    (
        "google_slides",
        "Google Slides",
        "google",
        "GOOGLE_ACCESS_TOKEN",
        ("Slide outline retrieval & deck updates",),
    ),
    (
        "google_calendar",
        "Google Calendar",
        "google",
        "GOOGLE_ACCESS_TOKEN",
        ("Schedule query & event proposal queue",),
    ),
    (
        "dropbox",
        "Dropbox",
        "dropbox",
        "DROPBOX_ACCESS_TOKEN",
        ("File metadata search & scoped downloads",),
    ),
    ("github", "GitHub", "github", "GITHUB_TOKEN", ("Repo search, PR checks & issue proposals",)),
    ("linear", "Linear", "linear", "LINEAR_API_KEY", ("Issue tracking & state transition queue",)),
    ("notion", "Notion", "notion", "NOTION_TOKEN", ("Workspace search & page update proposals",)),
    (
        "supabase",
        "Supabase",
        "supabase",
        "SUPABASE_URL",
        ("Explicit allowlisted table reads; no blind global search",),
    ),
    ("vercel", "Vercel", "vercel", "VERCEL_TOKEN", ("Deployment status & preview metadata",)),
)


class KernelWriteResult(StrictModel):
    """Normalized outcome from running L0 Policy + Executor + SHA-256 Verifier."""

    run_id: str
    status: str
    rel_path: str
    sha256: str | None = None
    byte_size: int = 0
    checks: dict[str, bool] = Field(default_factory=dict)
    denial_reasons: tuple[str, ...] = ()
    error: str | None = None
    admitted_to_memory: bool = False
    memory_verdict: str = ""


class MemoryHit(StrictModel):
    """Evidence-gated memory search hit returned from L1 store."""

    memory_id: str
    run_id: str
    goal_type: str
    text: str
    score: float
    above_noise: bool
    sha256: str | None = None
    artifact_path: str | None = None
    trust: float = 0.0


class MemoryStats(StrictModel):
    """Operational telemetry for the L1 EvidenceMemoryStore."""

    available: bool
    total_memories: int = 0
    verified_artifacts: int = 0
    refusals: int = 0
    noise_floor_p95: float = 0.0


class ConnectorDescriptor(StrictModel):
    """Sanitized status descriptor for one L5 external workspace connector."""

    connector_id: str
    name: str
    auth_group: str
    configured: bool
    credential_state: str
    credential_env_hint: str
    read_supported: bool = True
    write_requires_approval: bool = True
    notes: tuple[str, ...] = ()


class ApprovalProposal(StrictModel):
    """Human-in-the-loop pending action proposal matching connectors-hub discipline."""

    action_id: str
    session_id: str
    connector: str
    summary: str
    method: Literal["POST", "PUT", "PATCH", "DELETE"] = "POST"
    path: str
    payload_preview: dict[str, Any] = Field(default_factory=dict)
    request_sha256: str
    status: Literal["pending", "approved", "rejected"] = "pending"
    created_at: str
    decided_at: str | None = None
    decision_note: str | None = None


@runtime_checkable
class KernelSandboxPort(Protocol):
    """Replaceable contract for L0 policy-guarded sandbox execution."""

    async def write_verified_file(
        self,
        *,
        sandbox_dir: Path,
        ledger_path: Path,
        rel_path: str,
        content: str,
        session_id: str = "",
        sessions_root: Path | None = None,
    ) -> tuple[KernelWriteResult, dict[str, Any]]:
        """Execute a file write via L0 Kernel and return verified evidence + raw record."""
        ...


@runtime_checkable
class MemoryStorePort(Protocol):
    """Replaceable contract for L1 evidence-gated memory admission and retrieval."""

    async def admit_record(self, raw_record: dict[str, Any]) -> tuple[bool, str]:
        """Offer a kernel DecisionRecord to the evidence gate."""
        ...

    async def search(self, query: str, *, k: int = 5) -> list[MemoryHit]:
        """Search verified memories ranked by similarity."""
        ...

    async def stats(self) -> MemoryStats:
        """Return memory store counts and measured noise floor."""
        ...


@runtime_checkable
class ConnectorBridgePort(Protocol):
    """Replaceable contract for L5 connector inspection and human approval gating."""

    async def list_connectors(self) -> list[ConnectorDescriptor]:
        """List workspace connectors and sanitized credential readiness."""
        ...

    async def propose_action(
        self,
        *,
        session_id: str,
        connector: str,
        summary: str,
        method: Literal["POST", "PUT", "PATCH", "DELETE"],
        path: str,
        payload: Mapping[str, Any],
    ) -> ApprovalProposal:
        """Queue an external mutation requiring explicit human approval."""
        ...

    async def list_approvals(self, *, session_id: str | None = None) -> list[ApprovalProposal]:
        """List pending and decided proposals."""
        ...

    async def decide_approval(
        self,
        *,
        action_id: str,
        approve: bool,
        note: str | None = None,
    ) -> ApprovalProposal:
        """Approve or reject a queued proposal."""
        ...


def _load_agi_core(workspace_root: Path) -> tuple[Any, Any, Any] | None:
    """Import projects/agi-system/core in-place without modifying its stdlib package."""
    agi_dir = (workspace_root / "projects" / "agi-system").resolve()
    if not (agi_dir / "core" / "kernel.py").is_file():
        return None
    with _CORE_IMPORT_LOCK:
        if str(agi_dir) not in sys.path:
            sys.path.insert(0, str(agi_dir))
        kernel_mod = importlib.import_module("core.kernel")
        models_mod = importlib.import_module("core.models")
        return kernel_mod.Kernel, kernel_mod.KernelConfig, models_mod.Goal


class _EvidenceVerdictLike(Protocol):
    admitted: bool
    reason: str


class _EvidenceStoreLike(Protocol):
    def __init__(self, db_path: str | Path, sandbox_root: str | Path | None = None) -> None: ...
    def ingest(self, rec: dict[str, Any]) -> _EvidenceVerdictLike: ...
    def search(self, query: str, k: int = 5) -> dict[str, Any]: ...
    def stats(self) -> dict[str, Any]: ...


def _load_memory_store_cls(workspace_root: Path) -> type[_EvidenceStoreLike] | None:
    """Import memory/store.py EvidenceMemoryStore in-place without altering memory/."""
    mem_file = (workspace_root / "memory" / "store.py").resolve()
    if not mem_file.is_file():
        return None
    with _MEMORY_IMPORT_LOCK:
        if str(workspace_root) not in sys.path:
            sys.path.insert(0, str(workspace_root))
        mem_mod = importlib.import_module("memory.store")
        cls: type[_EvidenceStoreLike] = mem_mod.EvidenceMemoryStore
        return cls


class KernelSandboxBridge:
    """Wraps projects/agi-system Kernel for SHA-256 verified sandbox writes."""

    def __init__(self, workspace_root: Path) -> None:
        """Initialize the bridge targeting workspace_root/projects/agi-system."""
        self._workspace_root = workspace_root.resolve()

    @property
    def is_available(self) -> bool:
        """Return whether projects/agi-system/core is present on disk."""
        return (self._workspace_root / "projects" / "agi-system" / "core" / "kernel.py").is_file()

    def fsck_ledger_sync(self, ledger_path: Path) -> dict[str, Any] | None:
        """Verify L0 EventLedger hash-chain integrity when core.event_ledger is present."""
        if not ledger_path.is_file() or not self.is_available:
            return None
        _load_agi_core(self._workspace_root)
        ledger_mod = importlib.import_module("core.event_ledger")
        result: dict[str, Any] = ledger_mod.EventLedger(ledger_path).fsck()
        return result

    def write_verified_file_sync(
        self,
        *,
        sandbox_dir: Path,
        ledger_path: Path,
        rel_path: str,
        content: str,
        session_id: str = "",
        sessions_root: Path | None = None,
    ) -> tuple[KernelWriteResult, dict[str, Any]]:
        """Run one file write through L0 Kernel synchronously and collect evidence."""
        sandbox_dir.mkdir(parents=True, exist_ok=True)
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        loaded = _load_agi_core(self._workspace_root)
        if loaded is None:
            raw_bytes = content.encode("utf-8")
            sha = hashlib.sha256(raw_bytes).hexdigest()
            return (
                KernelWriteResult(
                    run_id=f"fallback_{uuid.uuid4().hex[:8]}",
                    status="FAILED",
                    rel_path=rel_path,
                    sha256=sha,
                    byte_size=len(raw_bytes),
                    error="projects/agi-system kernel unavailable in workspace_root",
                ),
                {"status": "FAILED", "error": "kernel unavailable"},
            )

        kernel_cls, kernel_config_cls, goal_cls = loaded
        run_id = f"run_{uuid.uuid4().hex[:10]}"
        kernel = kernel_cls(
            sandbox_dir=sandbox_dir,
            ledger_path=ledger_path,
            config=kernel_config_cls(),
        )
        record = kernel.run(
            goal_cls(goal_type="create_file", params={"path": rel_path, "content": content}),
            run_id=run_id,
        )
        raw: dict[str, Any] = record.to_dict()
        raw["run_id"] = run_id
        if sessions_root is not None and session_id:
            raw["sandbox"] = f"{session_id}/sandbox"
        else:
            raw["sandbox"] = sandbox_dir.name

        checks: dict[str, bool] = {}
        evidence_list = raw.get("evidence") or []
        if evidence_list and isinstance(evidence_list[0], dict):
            raw_checks = evidence_list[0].get("checks") or {}
            checks = {str(k): bool(v) for k, v in raw_checks.items()}

        sha_val: str | None = None
        byte_size = 0
        for obs in raw.get("observations") or []:
            data = obs.get("data") or {}
            if isinstance(data.get("sha256"), str):
                sha_val = data["sha256"]
                byte_size = int(data.get("bytes", 0))
                break

        result = KernelWriteResult(
            run_id=run_id,
            status=str(raw.get("status", "UNKNOWN")),
            rel_path=rel_path,
            sha256=sha_val,
            byte_size=byte_size,
            checks=checks,
            denial_reasons=tuple(str(r) for r in (raw.get("denial_reasons") or [])),
            error=raw.get("error"),
        )
        return result, raw

    async def write_verified_file(
        self,
        *,
        sandbox_dir: Path,
        ledger_path: Path,
        rel_path: str,
        content: str,
        session_id: str = "",
        sessions_root: Path | None = None,
    ) -> tuple[KernelWriteResult, dict[str, Any]]:
        """Execute L0 Kernel write asynchronously without blocking the event loop."""
        return await asyncio.to_thread(
            self.write_verified_file_sync,
            sandbox_dir=sandbox_dir,
            ledger_path=ledger_path,
            rel_path=rel_path,
            content=content,
            session_id=session_id,
            sessions_root=sessions_root,
        )


class MemoryBridge:
    """Wraps memory/store.py EvidenceMemoryStore for verified recall and admission."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        db_path: Path,
        sandbox_root: Path,
    ) -> None:
        """Create an evidence-gated memory store bound to db_path and sandbox_root."""
        self._workspace_root = workspace_root.resolve()
        self._db_path = db_path
        self._sandbox_root = sandbox_root
        self._store: _EvidenceStoreLike | None = None
        self._init_lock = threading.Lock()

    def _get_store(self) -> _EvidenceStoreLike | None:
        with self._init_lock:
            if self._store is not None:
                return self._store
            cls = _load_memory_store_cls(self._workspace_root)
            if cls is None:
                return None
            self._store = cls(self._db_path, sandbox_root=self._sandbox_root)
            return self._store

    async def admit_record(self, raw_record: dict[str, Any]) -> tuple[bool, str]:
        """Offer one L0 DecisionRecord to the L1 evidence gate."""

        def _admit() -> tuple[bool, str]:
            store = self._get_store()
            if store is None:
                return False, "L1 memory store module unavailable"
            verdict = store.ingest(raw_record)
            return bool(verdict.admitted), str(verdict.reason)

        return await asyncio.to_thread(_admit)

    async def search(self, query: str, *, k: int = 5) -> list[MemoryHit]:
        """Search verified L1 memories and return ranked similarity hits."""

        def _search() -> list[MemoryHit]:
            store = self._get_store()
            if store is None:
                return []
            payload = store.search(query, k=max(1, min(k, 20)))
            raw_hits = payload.get("results", []) if isinstance(payload, dict) else payload
            hits: list[MemoryHit] = []
            for row in raw_hits:
                if not isinstance(row, dict):
                    continue
                hits.append(
                    MemoryHit(
                        memory_id=str(row.get("memory_id", "")),
                        run_id=str(row.get("run_id") or row.get("memory_id") or ""),
                        goal_type=str(row.get("goal_type", "")),
                        text=redact_secrets(str(row.get("text", ""))),
                        score=round(float(row.get("score", 0.0)), 4),
                        above_noise=bool(row.get("above_noise", False)),
                        sha256=row.get("sha256"),
                        artifact_path=row.get("artifact_path"),
                        trust=round(float(row.get("trust", 0.0)), 2),
                    )
                )
            return hits

        return await asyncio.to_thread(_search)

    async def stats(self) -> MemoryStats:
        """Inspect L1 memory table counts and calibrated noise floor."""

        def _stats() -> MemoryStats:
            store = self._get_store()
            if store is None:
                return MemoryStats(available=False)
            data = store.stats()
            floor_obj = data.get("noise_floor")
            p95 = (
                float(floor_obj.get("p95") or 0.0)
                if isinstance(floor_obj, dict)
                else float(data.get("noise_floor_p95") or 0.0)
            )
            return MemoryStats(
                available=True,
                total_memories=int(data.get("memories", 0)),
                verified_artifacts=int(
                    data.get("admitted_events", data.get("verified_artifacts", 0))
                ),
                refusals=int(data.get("refused_events", data.get("refusals", 0))),
                noise_floor_p95=round(p95, 4),
            )

        return await asyncio.to_thread(_stats)


class ConnectorsBridge:
    """Manages L5 connector readiness inspection and Human-in-the-Loop approvals."""

    def __init__(self, *, store_path: Path) -> None:
        """Persist queued external action proposals safely inside store_path."""
        self._store_path = store_path
        self._lock = threading.Lock()
        self._store_path.parent.mkdir(parents=True, exist_ok=True)

    def _load_raw(self) -> list[dict[str, Any]]:
        if not self._store_path.is_file():
            return []
        try:
            data = json.loads(self._store_path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _save_raw(self, items: list[dict[str, Any]]) -> None:
        tmp = self._store_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._store_path)

    async def list_connectors(self) -> list[ConnectorDescriptor]:
        """Return sanitized connection state for every supported external connector."""

        def _collect() -> list[ConnectorDescriptor]:
            descriptors: list[ConnectorDescriptor] = []
            for cid, name, auth_group, env_var, notes in _CONNECTOR_METADATA:
                val = os.environ.get(env_var, "")
                label = credential_label(val)
                descriptors.append(
                    ConnectorDescriptor(
                        connector_id=cid,
                        name=name,
                        auth_group=auth_group,
                        configured=label != "absent",
                        credential_state=label,
                        credential_env_hint=env_var,
                        notes=notes,
                    )
                )
            return descriptors

        return await asyncio.to_thread(_collect)

    async def propose_action(
        self,
        *,
        session_id: str,
        connector: str,
        summary: str,
        method: Literal["POST", "PUT", "PATCH", "DELETE"],
        path: str,
        payload: Mapping[str, Any],
    ) -> ApprovalProposal:
        """Create a pending human-approval entry without executing any external write."""
        valid_ids = {item[0] for item in _CONNECTOR_METADATA}
        if connector not in valid_ids:
            raise SecurityValidationError(f"Unsupported connector {connector!r}.")
        clean_path = path.strip()
        if not clean_path.startswith("/") or clean_path.startswith("//") or ".." in clean_path:
            raise SecurityValidationError("Connector action path must be a safe API path.")

        scrubbed_payload = scrub_mapping(dict(payload))
        canonical = json.dumps(
            {
                "connector": connector,
                "method": method,
                "path": clean_path,
                "payload": scrubbed_payload,
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
        digest = hashlib.sha256(canonical).hexdigest()
        proposal = ApprovalProposal(
            action_id=uuid.uuid4().hex[:24],
            session_id=session_id,
            connector=connector,
            summary=redact_secrets(summary.strip())[:240] or "External connector action",
            method=method,
            path=clean_path,
            payload_preview=scrubbed_payload,
            request_sha256=digest,
            status="pending",
            created_at=datetime.now(UTC).isoformat(),
        )

        def _append() -> None:
            with self._lock:
                items = self._load_raw()
                items.append(proposal.model_dump(mode="json"))
                self._save_raw(items[-200:])

        await asyncio.to_thread(_append)
        return proposal

    async def list_approvals(self, *, session_id: str | None = None) -> list[ApprovalProposal]:
        """List stored approval proposals ordered newest first."""

        def _read() -> list[ApprovalProposal]:
            with self._lock:
                raw_items = self._load_raw()
            parsed = [ApprovalProposal.model_validate(item) for item in raw_items]
            if session_id:
                parsed = [item for item in parsed if item.session_id == session_id]
            return list(reversed(parsed))

        return await asyncio.to_thread(_read)

    async def decide_approval(
        self,
        *,
        action_id: str,
        approve: bool,
        note: str | None = None,
    ) -> ApprovalProposal:
        """Record an explicit human approval or rejection for a pending action."""
        if not _ACTION_ID_RE.fullmatch(action_id):
            raise SecurityValidationError("Invalid approval action_id.")

        def _update() -> ApprovalProposal:
            with self._lock:
                items = self._load_raw()
                updated: ApprovalProposal | None = None
                new_items: list[dict[str, Any]] = []
                for item in items:
                    if item.get("action_id") == action_id:
                        existing = ApprovalProposal.model_validate(item)
                        decided = existing.model_copy(
                            update={
                                "status": "approved" if approve else "rejected",
                                "decided_at": datetime.now(UTC).isoformat(),
                                "decision_note": (
                                    redact_secrets(note.strip())[:240]
                                    if note and note.strip()
                                    else (
                                        "Approved via Studio" if approve else "Rejected via Studio"
                                    )
                                ),
                            }
                        )
                        updated = decided
                        new_items.append(decided.model_dump(mode="json"))
                    else:
                        new_items.append(item)
                if updated is None:
                    raise FileNotFoundError(f"Approval proposal {action_id!r} not found.")
                self._save_raw(new_items)
                return updated

        return await asyncio.to_thread(_update)
