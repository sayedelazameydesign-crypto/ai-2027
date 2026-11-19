"""AST metric scanner used by the Python 2027 protocol auditor."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "dist",
        "build",
        ".wrangler",
    }
)
_BLOCKING_CALL_NAMES = frozenset({"sleep", "urlopen"})
_BLOCKING_HTTP_ATTRS = frozenset({"get", "post", "request", "urlopen"})
_SECRET_LITERAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    re.compile(r"nvapi-[0-9A-Za-z_\-]{20,}"),
    re.compile(r"gsk_[0-9A-Za-z_\-]{20,}"),
    re.compile(r"gh[pousr]_[0-9A-Za-z]{36,}"),
    re.compile(r"sk-(?:proj-)?[0-9A-Za-z_\-]{32,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
_ALLOWLIST_HINTS = (
    "example",
    "placeholder",
    "redacted",
    "dummy",
    "fake",
    "allowlist-secret",
)


@dataclass(frozen=True)
class AstScanMetrics:
    """Aggregated static metrics collected from a project's Python AST."""

    python_files: int
    total_loc: int
    max_file_loc: int
    public_defs: int
    public_funcs: int
    typed_public_funcs: int
    documented_public_defs: int
    protocol_classes: int
    async_defs: int
    uses_taskgroup: bool
    blocking_in_async: int
    uses_pydantic_v2: bool
    uses_structlog: bool
    uses_otel: bool
    uses_hypothesis: bool
    uses_pytest: bool
    uses_perf_counter: bool
    uses_timeout_retry: bool
    uses_free_providers: bool
    secret_hits: int


def iter_python_files(project_dir: Path) -> list[Path]:
    """Return sorted Python files in project_dir excluding virtualenvs and caches."""
    files: list[Path] = []
    for path in sorted(project_dir.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.relative_to(project_dir).parts):
            continue
        files.append(path)
    return files


def is_test_file(path: Path, project_dir: Path) -> bool:
    """Return whether path belongs to a unit/property test module."""
    rel = path.relative_to(project_dir)
    return path.name.startswith("test_") or path.name.endswith("_test.py") or "tests" in rel.parts


def _has_blocking_call(async_node: ast.AsyncFunctionDef) -> int:
    hits = 0
    for node in ast.walk(async_node):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                attr = node.func.attr
                owner = node.func.value.id if isinstance(node.func.value, ast.Name) else ""
                if (owner == "time" and attr == "sleep") or (
                    owner in {"requests", "urllib"} and attr in _BLOCKING_HTTP_ATTRS
                ):
                    hits += 1
            elif isinstance(node.func, ast.Name) and node.func.id in _BLOCKING_CALL_NAMES:
                hits += 1
    return hits


def _is_fully_typed(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    args = [
        a
        for a in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
        if a.arg not in {"self", "cls"}
    ]
    return node.returns is not None and all(a.annotation is not None for a in args)


def scan_project_ast(project_dir: Path) -> AstScanMetrics:
    """Parse every Python file in project_dir and collect protocol compliance metrics."""
    py_files = iter_python_files(project_dir)
    total_loc = 0
    max_file_loc = 0
    public_defs = 0
    public_funcs = 0
    typed_public_funcs = 0
    documented_public_defs = 0
    protocol_classes = 0
    async_defs = 0
    uses_taskgroup = False
    blocking_in_async = 0
    uses_pydantic_v2 = False
    uses_structlog = False
    uses_otel = False
    uses_hypothesis = False
    uses_pytest = False
    uses_perf_counter = False
    uses_timeout_retry = False
    uses_free_providers = False
    secret_hits = 0

    for file_path in py_files:
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover - unreadable permissions
            continue
        lines = text.splitlines()
        loc = len(lines)
        total_loc += loc
        max_file_loc = max(max_file_loc, loc)

        for raw_line in lines:
            low_line = raw_line.lower()
            if any(hint in low_line for hint in _ALLOWLIST_HINTS):
                continue
            for rx in _SECRET_LITERAL_PATTERNS:
                if rx.search(raw_line):
                    secret_hits += 1

        try:
            tree = ast.parse(text, filename=str(file_path))
        except SyntaxError:
            continue

        is_test = is_test_file(file_path, project_dir)
        lower_text = text.lower()
        if "taskgroup" in lower_text:
            uses_taskgroup = True
        if "perf_counter" in lower_text:
            uses_perf_counter = True
        if ("timeout" in lower_text and "retry" in lower_text) or "max_retries" in lower_text:
            uses_timeout_retry = True
        if any(
            name in lower_text
            for name in ("ollama", "gemini", "nvidia", "groq", "openrouter", "free-first")
        ):
            uses_free_providers = True

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_mod = alias.name.split(".")[0]
                    if root_mod == "pydantic":
                        uses_pydantic_v2 = True
                    elif root_mod == "structlog":
                        uses_structlog = True
                    elif root_mod == "opentelemetry":
                        uses_otel = True
                    elif root_mod == "hypothesis":
                        uses_hypothesis = True
                    elif root_mod == "pytest":
                        uses_pytest = True
            elif isinstance(node, ast.ImportFrom) and node.module:
                root_mod = node.module.split(".")[0]
                if root_mod == "pydantic":
                    uses_pydantic_v2 = True
                elif root_mod == "structlog":
                    uses_structlog = True
                elif root_mod == "opentelemetry":
                    uses_otel = True
                elif root_mod == "hypothesis":
                    uses_hypothesis = True
                elif root_mod == "pytest":
                    uses_pytest = True
            elif isinstance(node, ast.AsyncFunctionDef):
                async_defs += 1
                blocking_in_async += _has_blocking_call(node)
            elif isinstance(node, ast.ClassDef):
                for base in node.bases:
                    if isinstance(base, ast.Name) and base.id == "Protocol":
                        protocol_classes += 1
                    elif isinstance(base, ast.Attribute) and base.attr == "Protocol":
                        protocol_classes += 1
                    elif isinstance(base, ast.Name) and base.id in {"BaseModel", "StrictModel"}:
                        uses_pydantic_v2 = True

        if not is_test:
            for top in tree.body:
                if isinstance(top, ast.ClassDef) and not top.name.startswith("_"):
                    public_defs += 1
                    if ast.get_docstring(top):
                        documented_public_defs += 1
                    for member in top.body:
                        if isinstance(
                            member, ast.FunctionDef | ast.AsyncFunctionDef
                        ) and not member.name.startswith("_"):
                            public_defs += 1
                            public_funcs += 1
                            if ast.get_docstring(member):
                                documented_public_defs += 1
                            if _is_fully_typed(member):
                                typed_public_funcs += 1
                elif isinstance(
                    top, ast.FunctionDef | ast.AsyncFunctionDef
                ) and not top.name.startswith("_"):
                    public_defs += 1
                    public_funcs += 1
                    if ast.get_docstring(top):
                        documented_public_defs += 1
                    if _is_fully_typed(top):
                        typed_public_funcs += 1

    return AstScanMetrics(
        python_files=len(py_files),
        total_loc=total_loc,
        max_file_loc=max_file_loc,
        public_defs=public_defs,
        public_funcs=public_funcs,
        typed_public_funcs=typed_public_funcs,
        documented_public_defs=documented_public_defs,
        protocol_classes=protocol_classes,
        async_defs=async_defs,
        uses_taskgroup=uses_taskgroup,
        blocking_in_async=blocking_in_async,
        uses_pydantic_v2=uses_pydantic_v2,
        uses_structlog=uses_structlog,
        uses_otel=uses_otel,
        uses_hypothesis=uses_hypothesis,
        uses_pytest=uses_pytest,
        uses_perf_counter=uses_perf_counter,
        uses_timeout_retry=uses_timeout_retry,
        uses_free_providers=uses_free_providers,
        secret_hits=secret_hits,
    )
