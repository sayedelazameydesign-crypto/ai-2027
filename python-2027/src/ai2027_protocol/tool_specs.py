"""Canonical immutable tool descriptor catalog."""

from __future__ import annotations

from .contracts import ToolSpec

_TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="workspace.list_files",
        description="List files inside a workspace or sandbox subdirectory.",
        read_only=True,
        parameters_hint='{"path": ".", "scope": "workspace|sandbox", "limit": 60}',
    ),
    ToolSpec(
        name="workspace.read_file",
        description="Read text lines from a workspace or sandbox file safely.",
        read_only=True,
        parameters_hint='{"path": "README.md", "scope": "workspace|sandbox", "max_lines": 120}',
    ),
    ToolSpec(
        name="workspace.index_file",
        description=(
            "Map a file's structure (sections, tables, code blocks) without "
            "reading its content. Costs a fraction of the file at any size; "
            "use it before read_file on anything long."
        ),
        read_only=True,
        parameters_hint='{"path": "report.md", "scope": "workspace|sandbox"}',
    ),
    ToolSpec(
        name="workspace.describe_file",
        description=(
            "Describe any file family — database schema, container directory, "
            "columnar footer, text sections — without loading it. Use before "
            "read_file on anything that is not plain text."
        ),
        read_only=True,
        parameters_hint='{"path": "sales.db", "scope": "workspace|sandbox"}',
    ),
    ToolSpec(
        name="sandbox.validate_artifact",
        description=(
            "Re-open a sandbox artifact and prove it: ZIP members re-read for "
            "CRC, OOXML parts present, parquet footer consistent, text really "
            "decodes. Different from verify_artifact, which checks the hash."
        ),
        read_only=True,
        parameters_hint='{"path": "report.docx", "kind": "docx"}',
    ),
    ToolSpec(
        name="sandbox.plan_delivery",
        description=(
            "Decide how to hand an artifact over: the original file when the "
            "user's own tool can open it, a table when it cannot, a report when "
            "it failed validation. Decided from the bytes, not the extension."
        ),
        read_only=True,
        parameters_hint='{"path": "report.docx"}',
    ),
    ToolSpec(
        name="workspace.grep",
        description="Search repository source files concurrently for a pattern.",
        read_only=True,
        parameters_hint='{"query": "Kernel", "path": ".", "limit": 25}',
    ),
    ToolSpec(
        name="sandbox.write_artifact",
        description="Write HTML/SVG/Markdown/Python/JSON deliverable verified by L0 SHA-256.",
        read_only=False,
        parameters_hint='{"path": "reports/summary.html", "content": "..."}',
    ),
    ToolSpec(
        name="sandbox.edit_file",
        description="Replace a unique substring inside an existing sandbox artifact.",
        read_only=False,
        parameters_hint='{"path": "app.py", "old_text": "...", "new_text": "..."}',
    ),
    ToolSpec(
        name="sandbox.run_python",
        description="Execute an isolated Python 3 script inside the sandbox with scrubbed env.",
        read_only=False,
        parameters_hint='{"path": "analyze.py", "code": "print(1+1)", "timeout_s": 5.0}',
    ),
    ToolSpec(
        name="sandbox.run_tests",
        description="Execute unittest tests inside the isolated sandbox with scrubbed env.",
        read_only=False,
        parameters_hint='{"path": ".", "pattern": "test_*.py", "timeout_s": 8.0}',
    ),
    ToolSpec(
        name="sandbox.verify_artifact",
        description="Independently re-read artifact bytes, SHA-256 hash, and L0 ledger chain.",
        read_only=True,
        parameters_hint='{"path": "deliverable.html"}',
    ),
    ToolSpec(
        name="workspace.git_diff",
        description="Inspect repository git status and diff --stat cleanly (read-only).",
        read_only=True,
        parameters_hint='{"path": "."}',
    ),
    ToolSpec(
        name="memory.search",
        description="Query L1 EvidenceMemoryStore for verified past findings.",
        read_only=True,
        parameters_hint='{"query": "agi-system verification", "k": 5}',
    ),
    ToolSpec(
        name="memory.rag_search",
        description="Search Celia Memory persistent SQLite RAG across docs and notes.",
        read_only=True,
        parameters_hint='{"query": "Ollama 4GB profile", "k": 5, "paths": ["docs", "README.md"]}',
    ),
    ToolSpec(
        name="repo.audit",
        description="Audit a relative repository module against the 10 Python 2027 rules.",
        read_only=True,
        parameters_hint='{"path": "python-2027"}',
    ),
    ToolSpec(
        name="connector.inspect",
        description="List L5 workspace connectors and sanitized readiness without secrets.",
        read_only=True,
        parameters_hint="{}",
    ),
    ToolSpec(
        name="connector.search",
        description="Search a configured L5 connector (read-only, bounded).",
        read_only=True,
        parameters_hint='{"connector": "github", "query": "ai-2027", "limit": 5}',
    ),
    ToolSpec(
        name="connector.read",
        description="Read bounded UTF-8 metadata/content for one L5 connector resource.",
        read_only=True,
        parameters_hint='{"connector": "github", "resource_id": "owner/repo"}',
    ),
    ToolSpec(
        name="connector.propose_action",
        description="Queue an external connector mutation for explicit human approval.",
        read_only=False,
        parameters_hint=(
            '{"connector": "gmail", "summary": "...", "method": "POST", '
            '"path": "/drafts", "payload": {}}'
        ),
    ),
)
