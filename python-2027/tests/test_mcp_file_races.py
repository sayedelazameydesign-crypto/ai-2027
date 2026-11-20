"""Race-focused security tests for resolved MCP inspection paths."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, BinaryIO, cast

import pytest

from ai2027_protocol import mcp_inspector as mcp_inspector_module
from ai2027_protocol.mcp_contracts import McpToolError
from ai2027_protocol.security import SecurityValidationError


@pytest.mark.parametrize("replace_parent", [False, True])
def test_inspector_refuses_symlink_replacement_after_path_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replace_parent: bool
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "secret.txt"
    sentinel = "SYMLINK_REPLACEMENT_SECRET_SENTINEL"
    outside_file.write_text(sentinel, encoding="utf-8")

    source_directory: Path | None = None
    moved_directory: Path | None = None
    if replace_parent:
        source_directory = tmp_path / "safe"
        source_directory.mkdir()
        source = source_directory / "report.txt"
        moved_directory = tmp_path / "safe-original"
        relative_path = "safe/report.txt"
    else:
        source = tmp_path / "report.txt"
        relative_path = "report.txt"
    source.write_text("safe initial content", encoding="utf-8")

    resolve_path = mcp_inspector_module.resolve_safe_path

    def resolve_then_replace(root: Path, rel: str) -> Path:
        target = resolve_path(root, rel)
        if replace_parent:
            assert source_directory is not None and moved_directory is not None
            source_directory.rename(moved_directory)
            source_directory.symlink_to(outside, target_is_directory=True)
        else:
            source.unlink()
            source.symlink_to(outside_file)
        return target

    monkeypatch.setattr(mcp_inspector_module, "resolve_safe_path", resolve_then_replace)
    with pytest.raises((McpToolError, SecurityValidationError)) as error:
        mcp_inspector_module._inspect_file_sync(tmp_path, relative_path)
    assert sentinel not in str(error.value)


def test_snapshot_stops_when_source_grows_past_the_inspection_cap(tmp_path: Path) -> None:
    source = tmp_path / "growing.txt"
    source.write_bytes(b"initial")
    snapshot = tmp_path / "snapshot.bin"

    class GrowingReader:
        def __init__(self, handle: BinaryIO) -> None:
            self.handle = handle
            self.grew = False

        def fileno(self) -> int:
            return self.handle.fileno()

        def read(self, size: int = -1) -> bytes:
            if not self.grew:
                with source.open("ab") as writer:
                    writer.write(b"x" * (mcp_inspector_module.MAX_INSPECT_FILE_BYTES + 1))
                self.grew = True
            return self.handle.read(size)

    with source.open("rb") as reader, snapshot.open("wb") as destination:
        with pytest.raises(McpToolError, match="size limit"):
            mcp_inspector_module._copy_bounded_snapshot(
                cast(BinaryIO, GrowingReader(reader)), destination
            )
    assert snapshot.stat().st_size <= mcp_inspector_module.MAX_INSPECT_FILE_BYTES


def test_inspector_uses_bounded_snapshot_when_source_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "race.md"
    original = b"# Stable inspection snapshot\n"
    replacement = b"REPLACED_SOURCE_CONTENT " + b"x" * mcp_inspector_module.MAX_INSPECT_FILE_BYTES
    source.write_bytes(original)
    describe_snapshot = mcp_inspector_module.describe_file

    def replace_source(snapshot: Path, *, display_path: str = "") -> Any:
        source.write_bytes(replacement)
        return describe_snapshot(snapshot, display_path=display_path)

    monkeypatch.setattr(mcp_inspector_module, "describe_file", replace_source)
    result = mcp_inspector_module._inspect_file_sync(tmp_path, "race.md")
    assert source.stat().st_size > mcp_inspector_module.MAX_INSPECT_FILE_BYTES
    assert result["bytes_on_disk"] == len(original)
    assert result["verification"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert "REPLACED_SOURCE_CONTENT" not in str(result)
