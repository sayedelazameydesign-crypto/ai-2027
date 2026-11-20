"""Data models and replaceable ports shared by the system bridges."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import Field

from .contracts import StrictModel

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
