"""Hostile path and content probes for the bounded MCP file inspector."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai2027_protocol import mcp_inspector as mcp_inspector_module
from ai2027_protocol import mcp_server as mcp_server_module
from ai2027_protocol.mcp_server import McpToolError
from ai2027_protocol.security import SecurityValidationError

_CONTENT_MARKER = "MCP_REDTEAM_CONTENT_SENTINEL_DO_NOT_DISCLOSE_83917"
_BINARY_MARKER = b"MCP_BINARY_SECRET_SENTINEL_DO_NOT_DISCLOSE_28461"

_FORBIDDEN_PATHS = (
    ".env",
    ".env.local",
    "env.local",
    ".ENV",
    ".env.production",
    ".env.test",
    "config/.env",
    "private/.env.local",
    "local-pairings.json",
    ".git/config",
    ".git/HEAD",
    ".GIT/config",
    "nested/.git/config",
    ".ssh/id_rsa",
    ".aws/credentials",
    ".azure/accessTokens.json",
    ".kube/config",
    ".docker/config.json",
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
    "application_default_credentials.json",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "id_dsa",
    "keys/server.key",
    "keys/server.pem",
    "keys/cert.p12",
    "keys/cert.pfx",
    "keys/client.p8",
    "keys/client.ppk",
)

_TRAVERSAL_PATHS = (
    "../outside.txt",
    "../../outside.txt",
    "../../../outside.txt",
    "../../../../outside.txt",
    "../../../../../outside.txt",
    "../../../../../../outside.txt",
    "../../../../../../../outside.txt",
    "../../../../../../../../outside.txt",
    "nested/../../outside.txt",
    "nested/../../../outside.txt",
    "nested/deeper/../../../../outside.txt",
    "./../outside.txt",
    "./../../outside.txt",
    "folder/./../../outside.txt",
    "folder//../../outside.txt",
    "/etc/passwd",
    "//etc/passwd",
    "C:/Windows/win.ini",
    "C:\\Windows\\win.ini",
    "\\\\server\\share\\secret.txt",
    "..\\outside.txt",
    "nested\\..\\outside.txt",
    "%2e%2e%2foutside.txt",
    "%2E%2E%2Foutside.txt",
    "%2e%2e%5coutside.txt",
    "%2E%2E%5Coutside.txt",
    "nested/%2e%2e/%2e%2e/outside.txt",
    "%252e%252e%252foutside.txt",
    "%252E%252E%255Coutside.txt",
    "file://etc/passwd",
)


def _hostile_paths(workspace: Path, outside: Path) -> list[str]:
    """Create local fixtures and return exactly 100 distinct attack paths."""
    for relative in _FORBIDDEN_PATHS:
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_CONTENT_MARKER, encoding="utf-8")

    outside_file = outside / "outside.txt"
    outside_file.write_text(_CONTENT_MARKER, encoding="utf-8")
    symlink_paths: list[str] = []
    for index in range(10):
        if index == 0:
            relative = "escape-directory/outside.txt"
            (workspace / "escape-directory").symlink_to(outside, target_is_directory=True)
        else:
            relative = f"escape-{index}/secret.txt"
            link = workspace / relative
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(outside_file)
        symlink_paths.append(relative)

    large_file = workspace / "oversized.bin"
    large_file.write_bytes(b"x" * (mcp_server_module.MAX_INSPECT_FILE_BYTES + 1))
    large_paths = ["oversized.bin"]
    for index in range(9):
        relative = f"large-alias-{index}.bin"
        (workspace / relative).symlink_to(large_file)
        large_paths.append(relative)

    binary_paths: list[str] = []
    for index in range(11):
        relative = f"binary-{index}.dat"
        (workspace / relative).write_bytes(b"\x00\xff" + _BINARY_MARKER + bytes([index]))
        binary_paths.append(relative)

    paths = [
        *_TRAVERSAL_PATHS,
        *_FORBIDDEN_PATHS,
        *symlink_paths,
        *large_paths,
        *binary_paths,
    ]
    assert len(paths) == 100
    assert len(set(paths)) == 100
    return paths


@pytest.fixture(scope="module")
def hostile_workspace(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, list[str]]:
    """Build the shared hostile corpus once; the oversized fixture is 8 MiB."""
    root = tmp_path_factory.mktemp("mcp-redteam")
    workspace = root / "workspace"
    outside = root / "outside"
    workspace.mkdir()
    outside.mkdir()
    return workspace, _hostile_paths(workspace, outside)


@pytest.mark.parametrize("attack_index", range(100), ids=lambda index: f"attack-{index + 1:03d}")
def test_inspect_file_blocks_or_safely_describes_100_hostile_paths(
    hostile_workspace: tuple[Path, list[str]], attack_index: int
) -> None:
    """No attack may escape the workspace, disclose sentinels, or exceed limits."""
    workspace, attack_paths = hostile_workspace
    attack_path = attack_paths[attack_index]

    try:
        result = mcp_inspector_module._inspect_file_sync(workspace, attack_path)
    except (McpToolError, SecurityValidationError, FileNotFoundError):
        return

    rendered = json.dumps(result, ensure_ascii=False, sort_keys=True)
    assert _CONTENT_MARKER not in rendered, f"secret path disclosed: {attack_path!r}"
    assert _BINARY_MARKER.decode("ascii") not in rendered, (
        f"binary content disclosed: {attack_path!r}"
    )
    assert len(rendered.encode("utf-8")) <= mcp_server_module.MAX_RESULT_BYTES
    assert attack_path.startswith("binary-") and attack_path.endswith(".dat"), (
        f"unexpected hostile path was accepted: {attack_path!r}"
    )
    assert result["path"] == attack_path
    assert result["bytes_on_disk"] <= mcp_server_module.MAX_INSPECT_FILE_BYTES
