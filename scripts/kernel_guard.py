#!/usr/bin/env python3
"""kernel_guard.py — stop reliability primitives from being re-implemented.

The repository's real problem was never "we lack a helper"; it was that every
engine wrote its own copy, so improvements stopped at the engine boundary. This
guard is the mechanism that makes the shared kernel stick:

1. **Banned duplication.** Each promoted primitive has a signature regex. A file
   outside `kernel-2027/` that *defines* one of them fails the guard, with the
   kernel replacement named in the message.
2. **Ratchet.** Adoption (how many engines import `kernel2027`) is recorded in
   `kernel-2027/adoption.json`. The count may rise, never fall: a merge that
   drops an engine back to a private copy is refused.
3. **Honest reporting.** Files listed in the baseline are not silently forgiven —
   they are counted and printed, so the remaining work is always visible.

Escape hatch, deliberately narrow: an engine that must keep a specialised
implementation can add the comment `# kernel-exempt: <reason>` on or above the
offending `def`, and the guard reports it as *exempt* rather than passing it
silently. Exemptions are printed on every run so they cannot accumulate unseen.

Usage:
    python3 scripts/kernel_guard.py            # check (exit 1 on failure)
    python3 scripts/kernel_guard.py --report   # show adoption status only
    python3 scripts/kernel_guard.py --freeze   # record the current baseline
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KERNEL = ROOT / "kernel-2027" / "kernel2027"
BASELINE = ROOT / "kernel-2027" / "adoption.json"
EXEMPT_MARKER = "# kernel-exempt:"

#: Directories that are not engines (docs, generated, vendored) and are skipped.
SKIP_DIRS = {
    ".git", ".github", "node_modules", "__pycache__", ".venv", "venv", "build",
    "dist", "site-packages", ".pytest_cache", "kernel-2027", "docs", "launch",
    "packaging", "patches", "website", "projects",
}

#: An engine = a top-level unit that may import the kernel.
ENGINE_ROOTS = (
    "fleet-2027", "python-2027", "memory", "monitoring", "deployment",
    "live-agent", "command-hud", "connectors-hub", "browser_skill_adapter",
    "render_control_adapter", "scripts",
)


def _is_docstring(stmt: ast.AST) -> bool:
    """Whether a statement is a bare string literal (a docstring)."""
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


@dataclass(frozen=True)
class Primitive:
    """One promoted primitive: how to recognise a private copy, and the fix."""

    name: str
    pattern: re.Pattern[str]
    replacement: str
    note: str = ""
    names: tuple[str, ...] = ()
    #: Where a definition counts as a re-implementation.
    #: ``"module"`` — only a top-level def/class. A *method* that records or
    #: forwards an approval is transport/persistence, not a second policy, and
    #: flagging it would punish correct layering.
    #: ``"any"`` — a method can genuinely duplicate the primitive too (e.g. a
    #: staticmethod hashing a key the same way the kernel does).
    scope: str = "module"

    def matches(self, node: ast.AST, *, nested: bool) -> bool:
        """Whether ``node`` re-declares this primitive, matched on the AST name.

        Matching on the declared name rather than surrounding text is what keeps
        the guard honest: an unrelated method a few lines below a copy must never
        be blamed for it.
        """
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return False
        if not (bool(self.names) and node.name in self.names):
            return False
        # An interface declaration (``...`` / ``pass`` only) implements nothing,
        # so it cannot drift from the kernel. Only real bodies are findings.
        body = [stmt for stmt in getattr(node, "body", []) if not _is_docstring(stmt)]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not body:
                return False
            if all(isinstance(stmt, ast.Pass) for stmt in body):
                return False
            if len(body) == 1 and isinstance(body[0], ast.Expr):
                value = body[0].value
                if isinstance(value, ast.Constant) and value.value is Ellipsis:
                    return False
        return True


PRIMITIVES: tuple[Primitive, ...] = (
    Primitive(
        "secret redaction",
        re.compile(r"^def\s+(redact_secrets|redact|scrub_secrets)\s*\("),
        "kernel2027.redact / redact_mapping",
        "one pattern list, or a credential regex misses in one engine only",
        names=("redact_secrets", "redact", "scrub_secrets",),
    ),
    Primitive(
        "hashing/integrity",
        re.compile(r"^def\s+(sha256_text|sha256_bytes|sha256_file|sha256_hex)\s*\("),
        "kernel2027.sha256_text / sha256_bytes / sha256_file",
        names=("sha256_text", "sha256_bytes", "sha256_file", "sha256_hex",),
    ),
    Primitive(
        "canonical json",
        re.compile(r"^def\s+canonical_json\s*\("),
        "kernel2027.canonical_json",
        "a different serialisation silently breaks every stored digest",
        names=("canonical_json",),
    ),
    Primitive(
        "retry policy",
        re.compile(r"^def\s+(backoff_delay|retry_with_backoff|compute_backoff)\s*\("),
        "kernel2027.RetryPolicy + with_retry",
        names=("backoff_delay", "retry_with_backoff", "compute_backoff",),
    ),
    Primitive(
        "circuit breaker",
        re.compile(r"^class\s+(CircuitBreaker|BreakerPool)\s*[:(]"),
        "kernel2027.CircuitBreaker / BreakerPool",
        names=("CircuitBreaker", "BreakerPool",),
    ),
    Primitive(
        "approval decision",
        re.compile(r"^def\s+(decide_approval|require_approval|approve_action)\s*\("),
        "kernel2027.decide / require_approval",
        "approval is a security boundary and must fail closed identically",
        names=("decide_approval", "require_approval", "approve_action",),
    ),
    Primitive(
        "idempotency key",
        re.compile(r"^def\s+(effect_key|idempotency_key|make_idempotency_key)\s*\("),
        "kernel2027.effect_key",
        names=("effect_key", "idempotency_key", "make_idempotency_key",),
        scope="any",
    ),
    Primitive(
        "audit chain",
        re.compile(r"^def\s+(audit_link|verify_chain|append_audit_row)\s*\("),
        "kernel2027.audit_link / verify_chain",
        names=("audit_link", "verify_chain", "append_audit_row",),
        scope="any",
    ),
    Primitive(
        "endpoint safety",
        re.compile(r"^def\s+(validate_endpoint|check_endpoint|validate_provider_url)\s*\("),
        "kernel2027.validate_endpoint",
        names=("validate_endpoint", "check_endpoint", "validate_provider_url",),
    ),
)

#: Files that may legitimately define a primitive: the kernel itself, adapter
#: shims that re-export, and pre-migration baseline entries.
ALLOW_SUFFIXES = ("kernel2027/",)


@dataclass
class Finding:
    """One duplicated definition, with everything needed to act on it."""

    path: Path
    lineno: int
    primitive: str
    replacement: str
    exempt: bool = False
    reason: str = ""


@dataclass
class Report:
    """Result of one guard run."""

    applied: list[Finding] = field(default_factory=list)
    exemptions: list[Finding] = field(default_factory=list)
    adopting: list[str] = field(default_factory=list)
    engines: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def _iter_engine_files() -> list[Path]:
    files: list[Path] = []
    for root_name in ENGINE_ROOTS:
        root = ROOT / root_name
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            files.append(path)
    return sorted(files)


def _kernel_names(tree: ast.AST) -> set[str]:
    """Every name by which this module refers to a kernel2027 object.

    Both the local alias (``kernel_redact``) and the original name (``redact``)
    are included, because a delegation is equally compliant either way.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] == "kernel2027":
                for alias in node.names:
                    names.add(alias.asname or alias.name)
                    names.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "kernel2027":
                    names.add((alias.asname or alias.name).split(".")[0])
    return names



def _delegates_to_kernel(node: ast.AST, kernel_names: set[str]) -> bool:
    """True when a definition is a thin shim over a kernel primitive.

    A wrapper that validates its input and then calls the kernel is *compliance*:
    the rule still lives in exactly one place. Only a re-implementation — doing
    the work locally — is a finding. This distinction is what stops the guard
    from punishing the very refactor it is asking for.
    """
    if isinstance(node, ast.ClassDef):
        for base in node.bases:
            target = base.func if isinstance(base, ast.Call) else base
            if isinstance(target, ast.Name) and target.id in kernel_names:
                return True
        return False

    called: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                called.add(func.id)
            elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                called.add(f"{func.value.id}.{func.attr}")
    return bool(called & kernel_names)


def _exempt_reason(lines: list[str], lineno: int) -> str | None:
    """Find the exemption marker in the contiguous comment block above ``def``.

    Scanning the whole block (not just one line) matters because a real reason is
    usually longer than one line — and an exemption with no room for its reason
    is an exemption nobody will ever review.
    """
    index = lineno - 2  # 0-based index of the line directly above the def
    while index >= 0:
        text = lines[index].strip()
        if not text or text.startswith("@"):
            index -= 1
            continue
        if not text.startswith("#"):
            return None
        if EXEMPT_MARKER in text:
            reason = text.split(EXEMPT_MARKER, 1)[1].strip()
            trailing = [
                lines[i].strip().lstrip("# ").strip()
                for i in range(index + 1, lineno - 1)
                if lines[i].strip().startswith("#")
            ]
            joined = " ".join([part for part in [reason, *trailing] if part])
            return joined or "no reason given"
        index -= 1
    return None


def _scan_file(path: Path, baseline: set[str]) -> tuple[list[Finding], list[Finding]]:
    """Return (applied findings, exemption findings) for one file."""
    try:
        text = path.read_text(encoding="utf-8")
        module = ast.parse(text, filename=str(path))
    except (UnicodeDecodeError, OSError, SyntaxError):  # pragma: no cover - defensive
        return [], []
    lines = text.splitlines()
    relative = path.relative_to(ROOT).as_posix()
    kernel_names = _kernel_names(module)
    top_level = {
        id(child) for child in module.body
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    applied: list[Finding] = []
    exemptions: list[Finding] = []
    for node in ast.walk(module):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        nested = id(node) not in top_level
        matched = next(
            (prim for prim in PRIMITIVES if prim.matches(node, nested=nested)), None
        )
        if matched is None:
            continue
        reason = _exempt_reason(lines, node.lineno)
        if reason is None and _delegates_to_kernel(node, kernel_names):
            continue  # compliant shim: the rule itself lives in the kernel
        finding = Finding(
            path=path,
            lineno=node.lineno,
            primitive=matched.name,
            replacement=matched.replacement,
            exempt=reason is not None,
            reason=reason or "",
        )
        if reason is not None:
            exemptions.append(finding)
        elif relative in baseline:
            finding.exempt = True
            finding.reason = "baseline (pre-migration)"
            exemptions.append(finding)
        else:
            applied.append(finding)
    return applied, exemptions


def _adopting_engines(files: list[Path]) -> tuple[list[str], list[str]]:
    """Which engine roots import the kernel, and which do not yet."""
    importing: set[str] = set()
    present: set[str] = set()
    for path in files:
        try:
            source = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):  # pragma: no cover
            continue
        relative = path.relative_to(ROOT)
        root = relative.parts[0]
        present.add(root)
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:  # pragma: no cover - a broken file is caught elsewhere
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name.split(".")[0] == "kernel2027" for alias in node.names):
                    importing.add(root)
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.split(".")[0] == "kernel2027"
            ):
                importing.add(root)
    return sorted(importing), sorted(present)


def _load_baseline() -> dict:
    if not BASELINE.is_file():
        return {"engines_importing": [], "accepted_duplicates": []}
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def check(*, freeze: bool = False, verbose: bool = True) -> Report:
    """Run the guard. With ``freeze=True`` rewrite the baseline instead."""
    baseline = _load_baseline()
    accepted = set(baseline.get("accepted_duplicates", []))
    report = Report()
    files = _iter_engine_files()
    for path in files:
        applied, exemptions = _scan_file(path, accepted)
        report.applied.extend(applied)
        report.exemptions.extend(exemptions)
    report.adopting, report.engines = _adopting_engines(files)
    report.engines = [name for name in report.engines if name in ENGINE_ROOTS]

    if freeze:
        duplicates = sorted(
            {finding.path.relative_to(ROOT).as_posix() for finding in report.applied}
        )
        accepted.update(duplicates)
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(
            json.dumps(
                {
                    "engines_importing": report.adopting,
                    "engines_present": report.engines,
                    "accepted_duplicates": sorted(accepted),
                    "note": "Ratchet file: engines_importing may only grow. "
                            "accepted_duplicates are visible debt, not permissions.",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if verbose:
            print(f"baseline written: {len(accepted)} accepted duplicate file(s)")

    for finding in report.applied:
        relative = finding.path.relative_to(ROOT).as_posix()
        report.failures.append(
            f"{relative}:{finding.lineno}: re-implements {finding.primitive} "
            f"→ use {finding.replacement}"
        )

    # Ratchet: adoption must not shrink.
    required = set(baseline.get("engines_importing", []))
    lost = sorted(required - set(report.adopting))
    if lost:
        report.failures.append(
            "kernel adoption regressed: these engines stopped importing kernel2027 "
            f"→ {lost}"
        )
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Enforce kernel-2027 adoption.")
    parser.add_argument("--freeze", action="store_true", help="record the current baseline")
    parser.add_argument("--report", action="store_true", help="print adoption, do not fail")
    args = parser.parse_args(argv)

    report = check(freeze=args.freeze)
    present = len(report.engines) or 1
    print(
        f"kernel-2027 adoption: {len(report.adopting)}/{present} engines import the kernel"
    )
    print(f"  adopting: {', '.join(report.adopting) or '(none)'}")
    pending = [name for name in report.engines if name not in report.adopting]
    if pending:
        print(f"  not yet:  {', '.join(pending)}")
    if report.exemptions:
        print(f"  {len(report.exemptions)} accepted duplicate(s) still visible:")
        for finding in report.exemptions[:10]:
            relative = finding.path.relative_to(ROOT).as_posix()
            print(f"    · {relative}:{finding.lineno} {finding.primitive} ({finding.reason})")
        if len(report.exemptions) > 10:
            print(f"    … and {len(report.exemptions) - 10} more")

    if args.report:
        return 0
    if report.failures:
        print("\nFAIL: reliability primitives must come from kernel-2027.", file=sys.stderr)
        for failure in report.failures:
            print(f"  {failure}", file=sys.stderr)
        print(
            "\nFix: import the named kernel primitive. If an engine genuinely needs a "
            "specialised version, add `# kernel-exempt: <reason>` above the def — it "
            "will be printed on every run instead of silently ignored.",
            file=sys.stderr,
        )
        return 1
    print("\nPASS: no promoted primitive is re-implemented outside kernel-2027.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())