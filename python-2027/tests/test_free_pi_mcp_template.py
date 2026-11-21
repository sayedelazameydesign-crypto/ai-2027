"""Safety checks for the optional, disabled-by-default Free-Pi MCP template."""

from __future__ import annotations

import json
from pathlib import Path


def test_free_pi_mcp_template_is_valid_and_disabled() -> None:
    """Keep the example runnable in shape without enabling it or embedding a key."""
    repository_root = Path(__file__).resolve().parents[2]
    config_path = repository_root / ".pi" / "mcp.json.example"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    server = config["mcpServers"]["ai2027-inspector"]
    ignore_rules = (repository_root / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert ".pi/mcp.json" in ignore_rules
    assert server["enabled"] is False
    assert server["command"] == "python"
    assert server["args"] == ["-m", "ai2027_protocol", "--root", ".", "mcp"]
    assert server["cwd"] == "python-2027"
    assert server["env"]["PYTHONPATH"] == "src"
    assert server["env"]["AI2027_MCP_SIGNING_KEY"] == "${AI2027_MCP_SIGNING_KEY}"
    description = server["description"].lower()
    assert "read-only" in description and "stdio" in description
