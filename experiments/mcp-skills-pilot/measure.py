#!/usr/bin/env python3
"""Reproduce the MCP→Skill context measurement (stdlib only).

BEFORE: what an MCP-native agent keeps in context for every model turn —
        the full ``tools/list`` payloads of every runnable server in
        ``projects/ui/.mcp.json`` plus this repo's own ``ai2027 mcp`` server.
AFTER:  the mcpskills-cli pattern — skill frontmatter (always resident)
        plus SKILL.md loaded on demand (measured on the generated skill in
        ``skills/filesystem-mcp/``).

Token numbers are an explicit heuristic: est_tokens = len(text) // 4
(chars-per-token ~4 for mixed English+JSON). tiktoken could not be used:
its BPE vocab downloads from a host this sandbox cannot reach. Ratios
between BEFORE/AFTER are the meaningful figure; absolutes are estimates.

Run:  python3 experiments/mcp-skills-pilot/measure.py
      --venv-python PATH  (interpreter of python-2027; default: its .venv)
      --fs-root PATH      (filesystem-server root; default: /tmp/mcp-fs-root)
      --ai-root PATH      (ai2027 mcp root;     default: /tmp/mcp-audit-root)

Platform note (F-06): the default venv path is POSIX (…/.venv/bin/python);
on a native Windows checkout pass --venv-python explicitly. Every subprocess
must exit 0 before its output is accepted.
"""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY27 = REPO / "python-2027" / ".venv" / "bin" / "python"
SKILL_MD = Path(__file__).parent / "skills" / "filesystem-mcp" / "SKILL.md"
SKILL_RO_MD = Path(__file__).parent / "skills" / "filesystem-mcp-readonly" / "SKILL.md"
DEFAULT_FS_ROOT = "/tmp/mcp-fs-root"
DEFAULT_AI_ROOT = "/tmp/mcp-audit-root"

INIT = {
    "jsonrpc": "2.0", "id": 0, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "measure", "version": "0"}},
}
NOTIF = {"jsonrpc": "2.0", "method": "notifications/initialized"}
LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}


def _lines(*msgs: dict) -> str:
    return "".join(json.dumps(m, separators=(",", ":")) + "\n" for m in msgs)


def est_tokens(nbytes: int) -> int:
    """Heuristic: ~4 bytes per token for mixed English+JSON text."""
    return nbytes // 4


def run_stdio(cmd: list[str], payload: str, *, env: dict | None = None,
              timeout: int = 120) -> list[dict]:
    proc = subprocess.run(
        cmd, input=payload, capture_output=True, text=True,
        timeout=timeout, env=env, cwd=str(REPO),
    )
    if proc.returncode != 0:
        # F-06: never accept parsed output from a failed process.
        tail = proc.stderr.strip()[-400:]
        raise RuntimeError(
            f"command exited {proc.returncode}: {' '.join(cmd[:4])}… — {tail}"
        )
    out = []
    for line in proc.stdout.splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def tools_payload(messages: list[dict]) -> tuple[str, int]:
    """Return (canonical tools/list result JSON, tool count)."""
    for msg in messages:
        if msg.get("id") == 1 and "result" in msg:
            result = msg["result"]
            tools = result.get("tools", [])
            return json.dumps(result, ensure_ascii=False, separators=(",", ":")), len(tools)
    raise RuntimeError(f"no tools/list response; got: {messages!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP→Skill context measurement")
    parser.add_argument("--venv-python", default=str(PY27),
                        help="interpreter used to run ai2027_protocol.cli")
    parser.add_argument("--fs-root", default=DEFAULT_FS_ROOT,
                        help="root directory for the filesystem MCP server")
    parser.add_argument("--ai-root", default=DEFAULT_AI_ROOT,
                        help="root directory for the ai2027 mcp server")
    cli = parser.parse_args()

    # --- BEFORE part 1: this repo's own MCP server (ai2027.inspect_file) ---
    env = {"AI2027_MCP_SIGNING_KEY": secrets.token_hex(32), "PATH": "/usr/bin:/bin:/usr/local/bin"}
    env.update({k: v for k, v in __import__("os").environ.items()
                if k in ("HOME", "PATH", "NODE_PATH", "npm_config_cache")})
    ai_msgs = run_stdio(
        [cli.venv_python, "-m", "ai2027_protocol.cli", "--root", cli.ai_root, "mcp"],
        _lines(INIT, NOTIF, LIST), env=env,
    )
    ai_payload, ai_n = tools_payload(ai_msgs)

    # --- BEFORE part 2: filesystem server declared in projects/ui/.mcp.json ---
    fs_msgs = run_stdio(
        ["npx", "-y", "@modelcontextprotocol/server-filesystem@2.4.0", cli.fs_root],
        _lines(INIT, NOTIF, LIST),
    )
    fs_payload, fs_n = tools_payload(fs_msgs)

    # --- BEFORE variant: same server with --read-only (write tools unregistered) ---
    ro_msgs = run_stdio(
        ["npx", "-y", "@modelcontextprotocol/server-filesystem@2.4.0",
         "--read-only", cli.fs_root],
        _lines(INIT, NOTIF, LIST),
    )
    ro_payload, ro_n = tools_payload(ro_msgs)

    # --- AFTER: generated skills ---
    skill = SKILL_MD.read_text(encoding="utf-8")
    frontmatter = skill.split("---\n")[1]
    frontmatter_block = f"---\n{frontmatter}---\n"
    skill_ro = SKILL_RO_MD.read_text(encoding="utf-8")
    fm_ro = skill_ro.split("---\n")[1]
    fm_ro_block = f"---\n{fm_ro}---\n"

    before_text = fs_payload + ai_payload
    before_bytes = len(before_text.encode())
    after_always = frontmatter_block
    after_ondemand = skill

    rows = [
        ("BEFORE: tools/list filesystem@2.4.0 (16 tools, pinned npx handshake)",
         len(fs_payload.encode()), fs_n),
        ("BEFORE: tools/list ai2027.inspect_file (real stdio handshake)",
         len(ai_payload.encode()), ai_n),
        ("BEFORE total — resident EVERY model turn (MCP-native)",
         before_bytes, fs_n + ai_n),
        ("AFTER always-on — skill frontmatter only",
         len(after_always.encode()), 0),
        ("AFTER on-demand — SKILL.md (first filesystem task of session)",
         len(after_ondemand.encode()), 0),
        ("RO server tools/list (--read-only, write tools unregistered)",
         len(ro_payload.encode()), ro_n),
        ("RO skill frontmatter (always-on, DEFAULT path)",
         len(fm_ro_block.encode()), 0),
        ("RO skill SKILL.md (on-demand)",
         len(skill_ro.encode()), 0),
    ]

    print(f"{'scenario':<62} {'bytes':>8} {'est_tok':>8} {'tools':>6}")
    print("-" * 88)
    for label, nbytes, ntools in rows:
        print(f"{label:<62} {nbytes:>8} {est_tokens(nbytes):>8} {ntools:>6}")

    print()
    always_before = est_tokens(before_bytes)
    always_after = est_tokens(len(after_always.encode()))
    body = est_tokens(len(after_ondemand.encode()))
    print(f"Resident-per-turn saving: {before_bytes}B -> {len(after_always.encode())}B "
          f"({always_before} -> {always_after} est tok, "
          f"{100 * (1 - len(after_always.encode()) / before_bytes):.1f}% less per turn)")
    for turns in (1, 5, 10, 20):
        b = always_before * turns
        a = always_after * turns + body  # skill body paid once per session
        print(f"  session of {turns:>2} tool-using turns: BEFORE~{b:>6} tok  "
              f"AFTER~{a:>6} tok  saved~{b - a:>6} "
              f"({100 * (b - a) / b:.1f}%)")

    # --- READ-ONLY path: independent comparison (default skill, tiny surface) ---
    ro_always = est_tokens(len(fm_ro_block.encode()))
    ro_body = est_tokens(len(skill_ro.encode()))
    ro_schemas = est_tokens(len(ro_payload.encode()))
    print()
    print(f"READ-ONLY path (--read-only server, {ro_n} tools, write tools unregistered):")
    print(f"  schemas~{ro_schemas} tok vs RO skill frontmatter~{ro_always} tok "
          f"per turn ({100 * (1 - len(fm_ro_block.encode()) / len(ro_payload.encode())):.1f}% less)")
    for turns in (1, 10):
        b = ro_schemas * turns
        a = ro_always * turns + ro_body
        print(f"  RO session of {turns:>2} turns: BEFORE~{b:>6} tok  "
              f"AFTER~{a:>6} tok  saved~{b - a:>6} ({100 * (b - a) / b:.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
