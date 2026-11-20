"""Bounded, read-only workspace inspection exposed by the single MCP tool."""

from __future__ import annotations

import asyncio
import hashlib
import os
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, BinaryIO

from .ir import describe_file as describe_file
from .ir_contracts import DocumentIR
from .mcp_contracts import McpTool, McpToolError, ProgressReporter
from .security import SecurityValidationError
from .security import resolve_safe_path as resolve_safe_path
from .verify import verify_file

MAX_INSPECT_FILE_BYTES = 8 * 1024 * 1024
MAX_DESCRIPTION_CHARS = 12_000
MAX_CONCURRENT_FILE_INSPECTIONS = 4
_FORBIDDEN_PATH_NAMES = frozenset(
    {
        "env.local",
        "local-pairings.json",
        ".git",
        ".ssh",
        ".aws",
        ".azure",
        ".kube",
        ".docker",
        ".git-credentials",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "credentials",
        "credentials.json",
        "credentials.yaml",
        "credentials.yml",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
        "application_default_credentials.json",
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
        "id_dsa",
    }
)
_FORBIDDEN_PATH_SUFFIXES = (".key", ".pem", ".p12", ".pfx", ".p8", ".ppk")


def _sha256_bounded(path: Path, *, max_bytes: int) -> str:
    """Hash a file incrementally and stop if concurrent growth exceeds the budget."""
    digest = hashlib.sha256()
    consumed = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            consumed += len(chunk)
            if consumed > max_bytes:
                raise McpToolError("File grew beyond the MCP inspection size limit.")
            digest.update(chunk)
    return digest.hexdigest()


def _is_forbidden_path(root: Path, target: Path) -> bool:
    """Block credential files and repository metadata, including symlink targets."""
    try:
        parts = target.relative_to(root).parts
    except ValueError:
        return True
    return any(
        part.lower().startswith(".env")
        or part.lower() in _FORBIDDEN_PATH_NAMES
        or part.lower().endswith(_FORBIDDEN_PATH_SUFFIXES)
        for part in parts
    )


def _open_resolved_file(root: Path, target: Path) -> BinaryIO:
    """Open the resolved workspace file without following replacement symlinks."""
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise SecurityValidationError(
            "Reading outside the configured workspace is forbidden."
        ) from exc
    if not relative.parts:
        raise McpToolError("path must identify a regular file inside the configured workspace.")

    base_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    non_blocking = getattr(os, "O_NONBLOCK", 0)
    file_descriptor: int | None = None
    if (
        os.name == "posix"
        and no_follow
        and getattr(os, "O_DIRECTORY", 0)
        and os.open in os.supports_dir_fd
    ):
        directory_flags = base_flags | os.O_DIRECTORY | no_follow
        directory_descriptors: list[int] = []
        try:
            directory_descriptors.append(os.open(root, directory_flags))
            for component in relative.parts[:-1]:
                directory_descriptors.append(
                    os.open(component, directory_flags, dir_fd=directory_descriptors[-1])
                )
            file_descriptor = os.open(
                relative.parts[-1],
                base_flags | no_follow | non_blocking,
                dir_fd=directory_descriptors[-1],
            )
        finally:
            for descriptor in reversed(directory_descriptors):
                os.close(descriptor)
    else:
        if target.resolve(strict=True) != target:
            raise SecurityValidationError("The workspace path changed during inspection.")
        file_descriptor = os.open(target, base_flags | no_follow | non_blocking)

    if file_descriptor is None:
        raise McpToolError("The file could not be opened for inspection.")
    try:
        handle = os.fdopen(file_descriptor, "rb")
    except OSError:
        os.close(file_descriptor)
        raise
    if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
        handle.close()
        raise McpToolError("path must identify a regular file inside the configured workspace.")
    return handle


def _copy_bounded_snapshot(source: BinaryIO, destination: BinaryIO) -> int:
    """Copy one stable file version to a capped temporary snapshot."""
    before = os.fstat(source.fileno())
    if not stat.S_ISREG(before.st_mode):
        raise McpToolError("path must identify a regular file inside the configured workspace.")
    if before.st_size > MAX_INSPECT_FILE_BYTES:
        raise McpToolError(
            f"File is {before.st_size:,} bytes; MCP inspection is capped at "
            f"{MAX_INSPECT_FILE_BYTES:,} bytes."
        )

    copied = 0
    while True:
        chunk = source.read(min(1024 * 1024, MAX_INSPECT_FILE_BYTES - copied + 1))
        if not chunk:
            break
        copied += len(chunk)
        if copied > MAX_INSPECT_FILE_BYTES:
            raise McpToolError("File grew beyond the MCP inspection size limit.")
        destination.write(chunk)

    after = os.fstat(source.fileno())
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if identity_before != identity_after or copied != before.st_size:
        raise McpToolError("File changed while MCP inspection was in progress.")
    destination.flush()
    return copied


def _safe_description(described: DocumentIR) -> str:
    """Summarize structure without returning user-controlled file text or names."""
    parts = [f"{described.kind} ({described.family.value})"]
    if described.text_index is not None:
        index = described.text_index
        parts.append(
            f"text structure: {len(index.sections)} heading(s), {index.total_lines} line(s), "
            f"{index.table_lines} table line(s), {index.code_blocks} code block(s)"
        )
    if described.tables:
        column_count = sum(len(table.columns) for table in described.tables)
        known_row_counts = sum(table.rows is not None for table in described.tables)
        parts.append(
            f"data structure: {len(described.tables)} table(s), {column_count} column(s), "
            f"{known_row_counts} row count(s) available"
        )
    if described.columns:
        row_state = "available" if described.rows is not None else "unavailable"
        parts.append(f"columnar schema: {len(described.columns)} column(s), row count {row_state}")
    for label, info in (
        ("image", described.media),
        ("PDF", described.pdf),
        ("media", described.media_info),
        ("geometry", described.geometry),
        ("OLE", described.ole),
    ):
        if info is not None:
            parts.append(f"{label} header readable: {info.readable}")
    if described.notes:
        parts.append(f"{len(described.notes)} parser note(s) omitted")
    parts.append("Free-form text, identifiers, and parser messages are not returned.")
    return "; ".join(parts)


def _inspect_snapshot(snapshot: Path, rel: str, size: int) -> dict[str, Any]:
    """Describe and independently verify only the immutable bounded snapshot."""
    described = describe_file(snapshot, display_path=rel)
    if described.family.value == "zip":
        # Directory metadata is inspected without inflation; skip verify_file's
        # deep CRC pass because it decompresses every member. The snapshot is
        # bounded and its compressed bytes get a streaming digest.
        verification_data: dict[str, Any] = {
            "ok": False,
            "sha256": _sha256_bounded(snapshot, max_bytes=MAX_INSPECT_FILE_BYTES),
            "checks": [
                {
                    "name": "SHA-256 computed",
                    "passed": True,
                    "detail": "streamed in 1 MiB chunks; digest is not a validity verdict",
                },
                {
                    "name": "archive member integrity",
                    "passed": False,
                    "detail": "not checked: MCP does not decompress archive members",
                },
            ],
            "checks_truncated": False,
            "notes": ["ZIP directory metadata only; compressed member payloads were not inflated."],
        }
    else:
        verification = verify_file(snapshot, display_path=rel, kind=described.kind)
        verification_data = {
            "ok": verification.ok,
            "sha256": verification.sha256,
            "checks": [
                {"name": check.name, "passed": check.passed} for check in verification.checks[:64]
            ],
            "checks_truncated": len(verification.checks) > 64,
            "notes_count": len(verification.notes),
        }
    description = _safe_description(described)
    was_truncated = len(description) > MAX_DESCRIPTION_CHARS
    if was_truncated:
        description = description[:MAX_DESCRIPTION_CHARS] + "\n… description truncated"
    return {
        "ok": True,
        "path": rel,
        "kind": described.kind,
        "family": described.family.value,
        "bytes_on_disk": size,
        "description": description,
        "description_truncated": was_truncated,
        "verification": verification_data,
        "scope_note": (
            "Verification is limited to the checks listed here; it does not prove "
            "semantic correctness."
        ),
    }


def _inspect_file_sync(root: Path, relative_path: str) -> dict[str, Any]:
    """Describe one stable, bounded workspace-file snapshot without modifying it."""
    rel = relative_path.strip()
    if not rel or len(rel) > 1024:
        raise McpToolError("path must be a non-empty relative path of at most 1024 characters.")
    workspace = root.resolve()
    target = resolve_safe_path(workspace, rel)
    if _is_forbidden_path(workspace, target):
        raise SecurityValidationError(
            "Reading credential or repository metadata paths is forbidden."
        )
    try:
        with _open_resolved_file(workspace, target) as source:
            with tempfile.TemporaryDirectory(prefix="ai2027-mcp-") as temporary_directory:
                snapshot = Path(temporary_directory) / "inspection.bin"
                with snapshot.open("wb") as destination:
                    size = _copy_bounded_snapshot(source, destination)
                return _inspect_snapshot(snapshot, rel, size)
    except (McpToolError, SecurityValidationError):
        raise
    except OSError as exc:
        raise McpToolError("The file could not be inspected safely.") from exc


def _build_tools(root: Path) -> tuple[McpTool, ...]:
    """Register only the read-only, size-bounded file inspection tool for B1."""
    inspection_slots = asyncio.Semaphore(MAX_CONCURRENT_FILE_INSPECTIONS)

    async def inspect_file(
        arguments: Mapping[str, Any], report_progress: ProgressReporter
    ) -> dict[str, Any]:
        """Inspect a relative workspace file and independently check its bytes."""
        if set(arguments) != {"path"} or not isinstance(arguments.get("path"), str):
            raise McpToolError("arguments must contain only a string 'path'.")
        await report_progress(0.1, "Checking workspace path and size limit")
        await inspection_slots.acquire()
        try:
            worker = asyncio.create_task(
                asyncio.to_thread(_inspect_file_sync, root, str(arguments["path"]))
            )
        except Exception:
            inspection_slots.release()
            raise
        worker.add_done_callback(lambda _task: inspection_slots.release())
        result = await asyncio.shield(worker)
        await report_progress(0.9, "Description and verification completed")
        return result

    return (
        McpTool(
            name="ai2027.inspect_file",
            description=(
                "Read-only inspection of one relative workspace file (up to 8 MiB). "
                "Returns safe structural counts (not free-form text or identifiers), "
                "verification checks, a SHA-256, and an HMAC-attested result. "
                "OLE stream payloads are not interpreted; "
                "ZIP members are not decompressed. A private bounded snapshot is used; "
                "no workspace files are modified."
            ),
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string", "minLength": 1, "maxLength": 1024}},
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=inspect_file,
            annotations={"readOnlyHint": True, "destructiveHint": False},
        ),
    )
