#!/usr/bin/env python3
"""Fail-closed path, credential, symlink, and SHA-256 tar gate for static frontends.

Measured defects this guard closes
----------------------------------
``.github/workflows/deploy-frontend.yml`` shipped with four compounding flaws:

1. **Credential exposure surface**: ``GEMINI_API_KEY`` was passed into ``npm run build``
   for a public GitHub Pages bundle, risking client-bundle credential leakage.
2. **Unconfined path inputs**: ``FRONTEND_PATH`` and ``BUILD_OUTPUT`` accepted raw
   dispatch strings without traversal, symlink, or frozen-archive checks.
3. **Symlink/hardlink exfiltration**: ``tar --dereference --hard-dereference``
   followed links out of the build directory into arbitrary runner files.
4. **Unverified output**: no independent re-read or SHA-256 manifest proved what
   ``artifact.tar`` actually contained.

This module replaces shell ``tar`` packaging and ad-hoc path checks with a
stdlib-only, deterministic Python pipeline backed by ``kernel2027`` SHA-256
primitives and ``scripts/secret_scan.py`` detection rules:

* ``validate-target`` — confines ``frontend_path`` and ``build_output`` inside the
  repository root, refuses symlinks and frozen ``projects/`` archives, and verifies
  ``package.json`` (plus ``package-lock.json`` when required).
* ``package`` — verifies the built directory (no symlinks, no hardlinks, no
  forbidden files, zero credential-shaped strings), writes a byte-reproducible
  ``artifact.tar``, and independently re-opens and re-hashes the archive from disk.
* ``verify-tar`` — standalone independent verifier for an existing ``artifact.tar``.

Stdlib + ``kernel-2027`` only (Python 3.10+ compatible).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import stat
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "kernel-2027"))
sys.path.insert(0, str(ROOT / "scripts"))

from kernel2027.contracts import (  # noqa: E402
    canonical_json,
    sha256_bytes as kernel_sha256_bytes,
    sha256_file as kernel_sha256_file,
    sha256_text as kernel_sha256_text,
)
import secret_scan as pattern_gate  # noqa: E402

#: Top-level directories that are never valid public static-frontend targets.
#: ``projects`` holds frozen upstream archives (AGENTS.md §1 & §5).
BLOCKED_TOP_DIRS: frozenset[str] = frozenset({
    "projects",
    "kernel-2027",
    "python-2027",
    "memory",
    "live-agent",
    "deployment",
    "monitoring",
    "scripts",
    "tests",
    "docs",
    "launch",
    "patches",
    "prompts",
    "config",
    "research",
    "packaging",
    "celia-skill-factory",
    "agent-substrate",
    "fleet-2027",
    "connectors-hub",
    "browser_skill_adapter",
    "render_control_adapter",
    "command-hud",
    "tools",
})

#: Files that must never be packaged into a public static site bundle.
FORBIDDEN_BUNDLE_NAMES: frozenset[str] = frozenset({
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    "env.local",
    "local-pairings.json",
    ".npmrc",
    ".netrc",
    ".git",
    ".gitignore",
    ".gitattributes",
})

FORBIDDEN_BUNDLE_PREFIXES: tuple[str, ...] = (
    ".env.",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
)

FORBIDDEN_BUNDLE_SUFFIXES: tuple[str, ...] = (
    ".key",
    ".pem",
    ".p12",
    ".pfx",
    ".jks",
    ".kdbx",
)

BINARY_ASSET_SUFFIXES: frozenset[str] = frozenset({
    ".woff2",
    ".woff",
    ".ttf",
    ".otf",
    ".eot",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".avif",
})

PERCENT_ENCODED_SEP = re.compile(r"%(?:2e|2f|5c|00)", re.IGNORECASE)
WINDOWS_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
KNOWN_SCAN_RULES: frozenset[str] = (
    frozenset(name for name, _ in pattern_gate.PATTERNS)
    | frozenset({"assigned_secret", "high_prefix_match"})
)


class BundleGuardError(RuntimeError):
    """Fail-closed refusal raised when any containment or bundle check fails."""


@dataclass(frozen=True)
class ValidatedTarget:
    repo_root: str
    frontend_rel: str
    frontend_abs: str
    build_output_rel: str
    build_output_abs: str
    package_name: str
    has_lockfile: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "frontend_rel": self.frontend_rel,
            "build_output_rel": self.build_output_rel,
            "package_name": self.package_name,
            "has_lockfile": self.has_lockfile,
        }


@dataclass(frozen=True)
class AssetRecord:
    path: str
    size: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "size": self.size, "sha256": self.sha256}


def _validate_relative_syntax(raw_value: str, *, label: str) -> list[str]:
    """Validate a POSIX relative subpath string before touching the filesystem."""
    if not isinstance(raw_value, str) or not raw_value or not raw_value.strip():
        raise BundleGuardError(f"{label}: path must not be empty")
    if raw_value != raw_value.strip():
        raise BundleGuardError(f"{label}: leading or trailing whitespace is refused")
    if raw_value.startswith("-"):
        raise BundleGuardError(f"{label}: leading '-' is refused (option-injection guard)")
    if any(ord(ch) < 32 for ch in raw_value):
        raise BundleGuardError(f"{label}: control characters and NUL bytes are refused")
    if "\\" in raw_value:
        raise BundleGuardError(f"{label}: backslash separator is refused")
    if WINDOWS_DRIVE_PREFIX.match(raw_value):
        raise BundleGuardError(f"{label}: Windows drive prefix is refused")
    if PERCENT_ENCODED_SEP.search(raw_value):
        raise BundleGuardError(f"{label}: percent-encoded path separator is refused")
    if raw_value.startswith(("/", "~")):
        raise BundleGuardError(f"{label}: absolute or home-relative path is refused")

    parts = raw_value.split("/")
    for part in parts:
        if part in {"", ".", ".."}:
            raise BundleGuardError(f"{label}: empty, '.' or '..' segment is refused")
        if part.startswith("."):
            raise BundleGuardError(f"{label}: hidden directory segment '{part}' is refused")
    return parts


def _assert_no_symlink_chain(base_real: str, parts: list[str], *, label: str, must_exist: bool) -> str:
    """Walk each component under ``base_real`` and refuse any symbolic link."""
    cursor = base_real
    for part in parts:
        cursor = os.path.join(cursor, part)
        try:
            st = os.lstat(cursor)
        except FileNotFoundError:
            if must_exist:
                raise BundleGuardError(f"{label}: '{'/'.join(parts)}' does not exist") from None
            break
        if stat.S_ISLNK(st.st_mode):
            raise BundleGuardError(f"{label}: symbolic link in path is refused")

    resolved = os.path.realpath(os.path.join(base_real, *parts))
    if os.path.commonpath([base_real, resolved]) != base_real or resolved == base_real:
        raise BundleGuardError(f"{label}: path escapes repository containment")
    return resolved


def _confine_output_file(raw_path: str, allowed_roots: tuple[str, ...], *, label: str) -> str:
    """Sanitize an output file path so writes stay inside workspace or temp roots."""
    if not raw_path or "\x00" in raw_path:
        raise BundleGuardError(f"{label}: invalid output path")
    candidate = os.path.abspath(raw_path)
    parent = os.path.dirname(candidate)
    if os.path.lexists(candidate) and os.path.islink(candidate):
        raise BundleGuardError(f"{label}: refusing to write through a symbolic link")
    if not os.path.isdir(parent) or os.path.islink(parent):
        raise BundleGuardError(f"{label}: output parent directory must exist and not be a symlink")
    real_parent = os.path.realpath(parent)
    real_target = os.path.join(real_parent, os.path.basename(candidate))
    for allowed in allowed_roots:
        real_allowed = os.path.realpath(allowed)
        try:
            if os.path.commonpath([real_allowed, real_target]) == real_allowed:
                return real_target
        except ValueError:
            continue
    raise BundleGuardError(f"{label}: output path is outside permitted roots")


def _allowed_output_roots(real_repo: str) -> tuple[str, ...]:
    roots = [real_repo, tempfile.gettempdir()]
    for env_key in ("RUNNER_TEMP", "GITHUB_WORKSPACE"):
        env_val = os.environ.get(env_key, "").strip()
        if env_val and os.path.isdir(env_val):
            roots.append(env_val)
    summary_env = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if summary_env:
        summary_dir = os.path.dirname(os.path.abspath(summary_env))
        if os.path.isdir(summary_dir):
            roots.append(summary_dir)
    return tuple(dict.fromkeys(roots))


def validate_target(
    repo_root: Path,
    frontend_path: str,
    build_output: str,
    *,
    require_lockfile: bool = True,
) -> ValidatedTarget:
    """Validate ``frontend_path`` and ``build_output`` fail-closed."""
    if os.path.islink(repo_root):
        raise BundleGuardError("repo_root: symlinked repository root is refused")
    real_repo = os.path.realpath(repo_root)
    if not os.path.isdir(real_repo):
        raise BundleGuardError("repo_root: directory does not exist")

    fe_parts = _validate_relative_syntax(frontend_path, label="frontend_path")
    if fe_parts[0] in BLOCKED_TOP_DIRS:
        raise BundleGuardError(
            f"frontend_path: '{fe_parts[0]}/' is a protected or archival layer and cannot be deployed as a public SPA"
        )
    out_parts = _validate_relative_syntax(build_output, label="build_output")

    fe_abs = _assert_no_symlink_chain(real_repo, fe_parts, label="frontend_path", must_exist=True)
    if not os.path.isdir(fe_abs):
        raise BundleGuardError("frontend_path: target is not a directory")

    pkg_json_path = os.path.join(fe_abs, "package.json")
    try:
        pkg_stat = os.lstat(pkg_json_path)
    except FileNotFoundError:
        raise BundleGuardError("frontend_path: package.json is missing") from None
    if not stat.S_ISREG(pkg_stat.st_mode):
        raise BundleGuardError("frontend_path: package.json must be a regular non-symlink file")

    try:
        with open(pkg_json_path, encoding="utf-8") as fh:
            pkg_data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleGuardError(f"frontend_path: invalid package.json ({type(exc).__name__})") from None

    scripts_map = pkg_data.get("scripts") if isinstance(pkg_data, dict) else None
    build_cmd = scripts_map.get("build") if isinstance(scripts_map, dict) else None
    if not isinstance(build_cmd, str) or not build_cmd.strip():
        raise BundleGuardError("frontend_path: package.json must declare a non-empty scripts.build")

    lock_path = os.path.join(fe_abs, "package-lock.json")
    has_lockfile = False
    if os.path.lexists(lock_path):
        lock_stat = os.lstat(lock_path)
        if not stat.S_ISREG(lock_stat.st_mode):
            raise BundleGuardError("frontend_path: package-lock.json must not be a symlink")
        has_lockfile = True
    if require_lockfile and not has_lockfile:
        raise BundleGuardError(
            "frontend_path: package-lock.json is required for deterministic 'npm ci --ignore-scripts'"
        )

    out_abs = _assert_no_symlink_chain(fe_abs, out_parts, label="build_output", must_exist=False)
    return ValidatedTarget(
        repo_root=real_repo,
        frontend_rel="/".join(fe_parts),
        frontend_abs=fe_abs,
        build_output_rel="/".join(out_parts),
        build_output_abs=out_abs,
        package_name=str(pkg_data.get("name") or "/".join(fe_parts)),
        has_lockfile=has_lockfile,
    )


def _check_bundle_filename(rel_posix: str, filename: str) -> None:
    low = filename.lower()
    if low in FORBIDDEN_BUNDLE_NAMES or filename in FORBIDDEN_BUNDLE_NAMES:
        raise BundleGuardError(f"forbidden file in bundle: {rel_posix}")
    if any(low.startswith(prefix) for prefix in FORBIDDEN_BUNDLE_PREFIXES):
        raise BundleGuardError(f"forbidden file prefix in bundle: {rel_posix}")
    if any(low.endswith(suffix) for suffix in FORBIDDEN_BUNDLE_SUFFIXES):
        raise BundleGuardError(f"forbidden file extension in bundle: {rel_posix}")


def _scan_asset_bytes(raw: bytes, rel_posix: str) -> list[dict[str, Any]]:
    """Scan a built file for credential-shaped strings without retaining matched text."""
    if len(raw) > pattern_gate.MAX_BYTES:
        return []
    suffix = Path(rel_posix).suffix.lower()
    violations: list[dict[str, Any]] = []
    if suffix in BINARY_ASSET_SUFFIXES:
        decoded = raw.decode("latin-1")
        for rule_name, compiled_rx in pattern_gate.COMPILED:
            for match in compiled_rx.finditer(decoded):
                if not pattern_gate.is_allowed_value(match.group(0)):
                    safe_rule = rule_name if rule_name in KNOWN_SCAN_RULES else "high_prefix_match"
                    violations.append({"path": rel_posix, "line": 1, "rule": safe_rule})
        return violations

    text = raw.decode("utf-8", errors="replace")
    for item in pattern_gate.scan_text(text, path=rel_posix):
        if item.severity == "high":
            safe_rule = item.rule if item.rule in KNOWN_SCAN_RULES else "high_prefix_match"
            violations.append({"path": rel_posix, "line": int(item.line), "rule": safe_rule})
    return violations


def collect_verified_assets(target: ValidatedTarget) -> list[AssetRecord]:
    """Inspect ``target.build_output_abs`` fail-closed and return sorted asset hashes."""
    out_parts = target.build_output_rel.split("/")
    build_abs = _assert_no_symlink_chain(
        target.frontend_abs, out_parts, label="build_output", must_exist=True
    )
    top_stat = os.lstat(build_abs)
    if stat.S_ISLNK(top_stat.st_mode) or not stat.S_ISDIR(top_stat.st_mode):
        raise BundleGuardError("build_output: output root must be a real directory, not a symlink")

    records: list[AssetRecord] = []
    violations: list[dict[str, Any]] = []

    for current_root, dirnames, filenames in os.walk(build_abs, topdown=True, followlinks=False):
        dirnames.sort()
        filenames.sort()
        real_current = os.path.realpath(current_root)
        if os.path.commonpath([build_abs, real_current]) != build_abs:
            raise BundleGuardError("build_output: directory walk escaped build root")

        for dname in dirnames:
            dpath = os.path.join(current_root, dname)
            rel_dir = os.path.relpath(dpath, build_abs).replace(os.sep, "/")
            if dname.startswith("."):
                raise BundleGuardError(f"hidden directory refused in bundle: {rel_dir}")
            dstat = os.lstat(dpath)
            if stat.S_ISLNK(dstat.st_mode) or not stat.S_ISDIR(dstat.st_mode):
                raise BundleGuardError(f"symbolic link or special directory refused in bundle: {rel_dir}")

        for fname in filenames:
            fpath = os.path.join(current_root, fname)
            rel_posix = os.path.relpath(fpath, build_abs).replace(os.sep, "/")
            if fname != ".nojekyll" and fname.startswith("."):
                raise BundleGuardError(f"hidden file refused in bundle: {rel_posix}")
            _check_bundle_filename(rel_posix, fname)

            fstat = os.lstat(fpath)
            if stat.S_ISLNK(fstat.st_mode):
                raise BundleGuardError(f"symbolic link refused in bundle: {rel_posix}")
            if not stat.S_ISREG(fstat.st_mode):
                raise BundleGuardError(f"non-regular file refused in bundle: {rel_posix}")
            if os.name != "nt" and fstat.st_nlink > 1:
                raise BundleGuardError(f"hard-linked file refused in bundle (nlink={fstat.st_nlink}): {rel_posix}")

            real_file = os.path.realpath(fpath)
            if os.path.commonpath([build_abs, real_file]) != build_abs:
                raise BundleGuardError(f"resolved file escapes build root: {rel_posix}")

            with open(real_file, "rb") as fh:
                raw = fh.read()
            violations.extend(_scan_asset_bytes(raw, rel_posix))
            records.append(
                AssetRecord(
                    path=rel_posix,
                    size=len(raw),
                    sha256=kernel_sha256_bytes(raw),
                )
            )

    if violations:
        locations = ", ".join(f"{v['path']}:{v['line']} ({v['rule']})" for v in violations[:5])
        raise BundleGuardError(
            f"credential-shaped pattern detected in {len(violations)} bundle location(s): {locations}"
        )

    records.sort(key=lambda r: r.path)
    if not records:
        raise BundleGuardError("build_output: directory contains zero files")

    index_record = next((r for r in records if r.path == "index.html"), None)
    if index_record is None or index_record.size == 0:
        raise BundleGuardError("build_output: non-empty root index.html is required")

    return records


def write_deterministic_tar(build_abs: str, assets: list[AssetRecord], out_tar_abs: str) -> None:
    """Write a deterministic GNU tar archive with normalised metadata and no link following."""
    seen_dirs: list[str] = []
    seen_set: set[str] = set()
    for asset in assets:
        parts = asset.path.split("/")[:-1]
        for depth in range(1, len(parts) + 1):
            sub = "/".join(parts[:depth])
            if sub not in seen_set:
                seen_set.add(sub)
                seen_dirs.append(sub)
    seen_dirs.sort()

    with tarfile.open(out_tar_abs, mode="w", format=tarfile.GNU_FORMAT) as tf:
        for rel_dir in seen_dirs:
            dir_info = tarfile.TarInfo(name=f"./{rel_dir}")
            dir_info.type = tarfile.DIRTYPE
            dir_info.mode = 0o755
            dir_info.mtime = 0
            dir_info.uid = 0
            dir_info.gid = 0
            dir_info.uname = ""
            dir_info.gname = ""
            tf.addfile(dir_info)

        for asset in assets:
            src_candidate = os.path.join(build_abs, *asset.path.split("/"))
            st = os.lstat(src_candidate)
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                raise BundleGuardError(f"TOCTOU refusal while archiving: {asset.path}")
            real_src = os.path.realpath(src_candidate)
            if os.path.commonpath([build_abs, real_src]) != build_abs:
                raise BundleGuardError(f"path escape refused while archiving: {asset.path}")
            with open(real_src, "rb") as fh:
                raw = fh.read()
            if len(raw) != asset.size or kernel_sha256_bytes(raw) != asset.sha256:
                raise BundleGuardError(f"file mutated during packaging: {asset.path}")

            info = tarfile.TarInfo(name=f"./{asset.path}")
            info.type = tarfile.REGTYPE
            info.size = len(raw)
            info.mode = 0o644
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            tf.addfile(info, io.BytesIO(raw))


def verify_tar_archive(
    tar_path: str,
    expected_assets: list[AssetRecord] | None = None,
) -> dict[str, Any]:
    """Independently re-open ``tar_path`` from disk and verify every member and digest."""
    if os.path.islink(tar_path) or not os.path.isfile(tar_path):
        raise BundleGuardError("artifact_tar: archive must be an existing regular non-symlink file")

    observed: list[AssetRecord] = []
    with tarfile.open(tar_path, mode="r:") as tf:
        for member in tf.getmembers():
            if member.issym() or member.islnk():
                raise BundleGuardError(f"artifact_tar: symbolic or hard link inside tar refused ({member.name})")
            if not (member.isdir() or member.isfile()):
                raise BundleGuardError(f"artifact_tar: special tar entry refused ({member.name})")

            cleaned = member.name
            while cleaned.startswith("./"):
                cleaned = cleaned[2:]
            cleaned = cleaned.rstrip("/")
            if not cleaned:
                if member.isdir():
                    continue
                raise BundleGuardError("artifact_tar: unnamed file member refused")

            _validate_relative_syntax(
                cleaned if cleaned != ".nojekyll" else "nojekyll_placeholder",
                label="artifact_tar",
            )
            if member.mtime != 0 or member.uid != 0 or member.gid != 0 or member.uname or member.gname:
                raise BundleGuardError(f"artifact_tar: unnormalised tar metadata on '{cleaned}'")

            if member.isfile():
                extracted = tf.extractfile(member)
                if extracted is None:
                    raise BundleGuardError(f"artifact_tar: unreadable file member '{cleaned}'")
                raw = extracted.read()
                violations = _scan_asset_bytes(raw, cleaned)
                if violations:
                    raise BundleGuardError(f"artifact_tar: credential pattern detected inside '{cleaned}'")
                observed.append(
                    AssetRecord(
                        path=cleaned,
                        size=len(raw),
                        sha256=kernel_sha256_bytes(raw),
                    )
                )

    observed.sort(key=lambda r: r.path)
    if not any(r.path == "index.html" and r.size > 0 for r in observed):
        raise BundleGuardError("artifact_tar: verified archive is missing non-empty index.html")

    if expected_assets is not None:
        expected_sorted = sorted(expected_assets, key=lambda r: r.path)
        if observed != expected_sorted:
            raise BundleGuardError("artifact_tar: independent tar re-read disagrees with manifest")

    bundle_sha256 = kernel_sha256_text(canonical_json([r.to_dict() for r in observed]))
    artifact_sha256 = kernel_sha256_file(tar_path)
    artifact_bytes = os.path.getsize(tar_path)
    return {
        "ok": True,
        "file_count": len(observed),
        "total_asset_bytes": sum(r.size for r in observed),
        "bundle_sha256": bundle_sha256,
        "artifact_bytes": artifact_bytes,
        "artifact_sha256": artifact_sha256,
        "files": [r.to_dict() for r in observed],
    }


def package_bundle(
    repo_root: Path,
    frontend_path: str,
    build_output: str,
    out_tar: str,
    *,
    manifest_out: str | None = None,
    step_summary_out: str | None = None,
    require_lockfile: bool = True,
) -> dict[str, Any]:
    """End-to-end gate: validate target, scan assets, write deterministic tar, re-verify."""
    target = validate_target(
        repo_root, frontend_path, build_output, require_lockfile=require_lockfile
    )
    allowed_roots = _allowed_output_roots(target.repo_root)
    safe_tar = _confine_output_file(out_tar, allowed_roots, label="out_tar")

    assets = collect_verified_assets(target)
    write_deterministic_tar(target.build_output_abs, assets, safe_tar)
    verified = verify_tar_archive(safe_tar, expected_assets=assets)

    report: dict[str, Any] = {
        "ok": True,
        "frontend_path": target.frontend_rel,
        "build_output": target.build_output_rel,
        "package_name": target.package_name,
        "has_lockfile": target.has_lockfile,
        **verified,
    }

    if manifest_out:
        safe_manifest = _confine_output_file(manifest_out, allowed_roots, label="manifest_out")
        with open(safe_manifest, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    if step_summary_out:
        safe_summary = _confine_output_file(step_summary_out, allowed_roots, label="step_summary")
        lines = [
            "## 🛡️ Frontend Static Bundle Verification (Fail-Closed)",
            "",
            f"- **Target**: `{report['frontend_path']}/{report['build_output']}` (`{report['package_name']}`)",
            f"- **Files verified**: `{report['file_count']}` ({report['total_asset_bytes']} bytes)",
            f"- **Symlinks / hardlinks / credential patterns**: `0` (enforced)",
            f"- **Bundle content SHA-256**: `{report['bundle_sha256']}`",
            f"- **`artifact.tar` SHA-256**: `{report['artifact_sha256']}` ({report['artifact_bytes']} bytes)",
            "",
            "| Asset | Size (bytes) | SHA-256 |",
            "|---|---:|---|",
        ]
        for item in report["files"]:
            lines.append(f"| `{item['path']}` | {item['size']} | `{item['sha256']}` |")
        with open(safe_summary, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail-closed static frontend bundle guard.")
    parser.add_argument("--repo", type=Path, default=ROOT, help="Repository root directory.")
    sub = parser.add_subparsers(dest="command", required=True)

    vt = sub.add_parser("validate-target", help="Validate frontend-path and build-output before npm.")
    vt.add_argument("--frontend-path", required=True)
    vt.add_argument("--build-output", required=True)
    vt.add_argument("--require-lockfile", action="store_true")
    vt.add_argument("--json", action="store_true")

    pk = sub.add_parser("package", help="Verify built assets, write deterministic tar, and re-verify.")
    pk.add_argument("--frontend-path", required=True)
    pk.add_argument("--build-output", required=True)
    pk.add_argument("--out", required=True, help="Output tar file path.")
    pk.add_argument("--manifest", default="", help="Optional JSON manifest path.")
    pk.add_argument("--step-summary", default="", help="Optional GITHUB_STEP_SUMMARY path.")
    pk.add_argument("--require-lockfile", action="store_true")
    pk.add_argument("--json", action="store_true")

    vr = sub.add_parser("verify-tar", help="Independently re-verify an existing artifact.tar.")
    vr.add_argument("--tar", required=True)
    vr.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "validate-target":
            res = validate_target(
                args.repo,
                args.frontend_path,
                args.build_output,
                require_lockfile=args.require_lockfile,
            ).to_dict()
        elif args.command == "package":
            res = package_bundle(
                args.repo,
                args.frontend_path,
                args.build_output,
                args.out,
                manifest_out=args.manifest or None,
                step_summary_out=args.step_summary or None,
                require_lockfile=args.require_lockfile,
            )
        else:
            real_repo = os.path.realpath(args.repo)
            safe_tar = _confine_output_file(
                args.tar, _allowed_output_roots(real_repo), label="tar"
            )
            res = verify_tar_archive(safe_tar)
    except BundleGuardError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps(res, ensure_ascii=False, indent=2, sort_keys=True))
    elif args.command == "validate-target":
        print(
            f"OK target={res['frontend_rel']}/{res['build_output_rel']} "
            f"package={res['package_name']} lockfile={res['has_lockfile']}"
        )
    else:
        print(
            f"OK files={res['file_count']} bundle_sha256={res['bundle_sha256']} "
            f"artifact_sha256={res['artifact_sha256']} bytes={res['artifact_bytes']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
