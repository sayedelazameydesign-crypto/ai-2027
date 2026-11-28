#!/usr/bin/env python3
"""Unified Add-on Collection Protocol v1 — scan, validate, hash, index, verify.

    python3 collector/protocol_engine.py            # collect: rebuild registry/registry.json
    python3 collector/protocol_engine.py --check    # verify: exit 1 if disk != registry (CI, read-only)
    python3 collector/protocol_engine.py --json     # machine-readable summary

Layout (relative to --root, default: the repository root):

    addons/<addon_id>/addon.json   manifest (id MUST equal the folder name)
    addons/<addon_id>/...          entrypoint + assets
    registry/registry.json         generated, deterministic (no timestamps) -> diffable in Git
    registry/audit_log.jsonl       append-only, hash-chained (prev_hash -> self_hash)

Guarantees (each one is pinned by tests/test_collector.py):

* one broken add-on never stops the run: it is recorded as ``rejected`` with reasons;
* symlinks are refused (they could make the hash read files outside the add-on);
* dependency degradation is transitive and cycles are detected;
* permissions must come from PERMISSIONS; unknown ones reject the add-on;
* ``--check`` recomputes every digest and reports drift against the committed
  registry — this is what actually detects "a file changed after indexing";
* the collector NEVER imports or executes add-on code. ``permissions`` are a
  declaration that a runtime (e.g. the kernel's policy gate) must enforce; this
  file cannot sandbox anything and does not claim to.

Stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

PROTOCOL_VERSION = "1.0.0"
ID_PATTERN = re.compile(r"^[a-z0-9_]{3,64}$")
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
REQUIRED_FIELDS = {"id", "name", "version", "entrypoint"}
OPTIONAL_FIELDS = {"description", "dependencies", "requirements", "permissions"}
# Governance: widening this set is a reviewed code change, never a manifest decision.
PERMISSIONS = frozenset({"read_fs", "network", "ui_hook"})
MAX_MANIFEST_BYTES = 64 * 1024
# Build/OS noise that must not change an add-on's identity.
IGNORED_DIRS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache"})
IGNORED_FILES = frozenset({".DS_Store", "Thumbs.db"})
IGNORED_SUFFIXES = (".pyc", ".pyo")


@dataclass
class AddonRecord:
    addon_id: str
    name: str
    version: str
    entrypoint: str
    path: str
    sha256: str
    dependencies: List[str]
    permissions: List[str]
    status: str  # active | degraded | rejected
    errors: List[str] = field(default_factory=list)
    requirements: List[str] = field(default_factory=list)
    file_count: int = 0


# ── hashing ──────────────────────────────────────────────────────────────────
def _ignored(rel_parts: Tuple[str, ...]) -> bool:
    if any(part in IGNORED_DIRS for part in rel_parts[:-1]):
        return True
    name = rel_parts[-1]
    return name in IGNORED_FILES or name.endswith(IGNORED_SUFFIXES) or rel_parts[0] in IGNORED_DIRS


def list_addon_files(target_dir: Path) -> Tuple[List[Path], List[str]]:
    """Return (files, symlinks) under target_dir in a stable order; never follows links."""
    files: List[Path] = []
    links: List[str] = []
    for current, dirnames, filenames in os.walk(target_dir, followlinks=False):
        base = Path(current)
        for d in list(dirnames):
            p = base / d
            if p.is_symlink():
                links.append(p.relative_to(target_dir).as_posix())
                dirnames.remove(d)
            elif d in IGNORED_DIRS:
                dirnames.remove(d)
        for f in filenames:
            p = base / f
            rel = p.relative_to(target_dir)
            if p.is_symlink():
                links.append(rel.as_posix())
            elif not _ignored(rel.parts):
                files.append(p)
    files.sort(key=lambda p: p.relative_to(target_dir).as_posix())
    return files, sorted(links)


def compute_directory_sha256(target_dir: Path) -> str:
    """Stable SHA-256 over (relative path, NUL, length, NUL, bytes) of every regular file.

    The length prefix makes the framing unambiguous, so moving bytes between a file's
    name and content, or between two adjacent files, always changes the digest.
    """
    hasher = hashlib.sha256()
    files, _links = list_addon_files(target_dir)
    for file_path in files:
        hasher.update(file_path.relative_to(target_dir).as_posix().encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(str(file_path.stat().st_size).encode("ascii"))
        hasher.update(b"\x00")
        with file_path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                hasher.update(chunk)
    return hasher.hexdigest()


# ── manifest validation ──────────────────────────────────────────────────────
def _str_list(data: Dict[str, Any], key: str, errors: List[str]) -> List[str]:
    value = data.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        errors.append(f"[bad_type] الحقل {key} يجب أن يكون قائمة نصوص غير فارغة.")
        return []
    if len(set(value)) != len(value):
        errors.append(f"[duplicate_entry] الحقل {key} يحتوي عناصر مكررة.")
    return list(value)


def validate_manifest(addon_dir: Path, data: Any) -> Tuple[bool, List[str]]:
    """Validate addon.json structure, types, permissions and the entrypoint location."""
    errors: List[str] = []
    if not isinstance(data, dict):
        return False, ["[bad_manifest] ملف addon.json يجب أن يحتوي على كائن JSON صالح."]

    missing = REQUIRED_FIELDS - set(data)
    if missing:
        errors.append(f"[missing_fields] حقول إلزامية مفقودة: {sorted(missing)}")
    unknown = set(data) - REQUIRED_FIELDS - OPTIONAL_FIELDS
    if unknown:
        errors.append(f"[unknown_fields] حقول غير معروفة في البروتوكول: {sorted(unknown)}")

    addon_id = data.get("id", "")
    if not isinstance(addon_id, str) or not ID_PATTERN.match(addon_id):
        errors.append("[bad_id] معرّف الإضافة id غير صالح (أحرف صغيرة وأرقام و _ بطول 3-64).")
    elif addon_id != addon_dir.name:
        errors.append(f"[id_folder_mismatch] id ({addon_id}) يجب أن يطابق اسم المجلد ({addon_dir.name}).")

    name = data.get("name", "")
    if "name" in data and (not isinstance(name, str) or not name.strip()):
        errors.append("[bad_name] الحقل name يجب أن يكون نصاً غير فارغ.")

    version = data.get("version", "")
    if "version" in data and (not isinstance(version, str) or not SEMVER_PATTERN.match(version)):
        errors.append("[bad_version] الحقل version يجب أن يكون إصداراً دلالياً مثل 1.0.0.")

    deps = _str_list(data, "dependencies", errors)
    for dep in deps:
        if not ID_PATTERN.match(dep):
            errors.append(f"[bad_dependency] اعتمادية غير صالحة (يجب أن تكون id إضافة): {dep}")
    if isinstance(addon_id, str) and addon_id in deps:
        errors.append("[self_dependency] الإضافة لا يجوز أن تعتمد على نفسها.")
    _str_list(data, "requirements", errors)

    perms = _str_list(data, "permissions", errors)
    bad_perms = sorted(set(perms) - PERMISSIONS)
    if bad_perms:
        errors.append(f"[unknown_permission] صلاحيات غير مصرح بها: {bad_perms} (المسموح: {sorted(PERMISSIONS)})")

    entrypoint = data.get("entrypoint", "")
    if "entrypoint" in data:
        if not isinstance(entrypoint, str) or not entrypoint.strip():
            errors.append("[bad_entrypoint] الحقل entrypoint يجب أن يكون مساراً نسبياً غير فارغ.")
        elif Path(entrypoint).is_absolute() or "\\" in entrypoint:
            errors.append("[bad_entrypoint] entrypoint يجب أن يكون مساراً نسبياً بفواصل '/'.")
        else:
            root = addon_dir.resolve()
            entry = (addon_dir / entrypoint).resolve()
            if root not in entry.parents:
                errors.append("[path_traversal] مسار entrypoint يحاول الخروج خارج مجلد الإضافة.")
            elif not entry.is_file():
                errors.append(f"[missing_entrypoint] ملف نقطة الدخول غير موجود: {entrypoint}")

    return not errors, errors


# ── collector ────────────────────────────────────────────────────────────────
class UnifiedAddonCollector:
    """Scan addons/, validate each add-on in isolation, and build one registry."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.addons_dir = self.repo_root / "addons"
        self.registry_dir = self.repo_root / "registry"
        self.registry_file = self.registry_dir / "registry.json"
        self.audit_file = self.registry_dir / "audit_log.jsonl"

    # -- audit log (hash-chained) ------------------------------------------
    def _last_audit_hash(self) -> str:
        if not self.audit_file.exists():
            return "0" * 64
        last = ""
        with self.audit_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    last = line
        if not last:
            return "0" * 64
        return str(json.loads(last).get("self_hash", "0" * 64))

    def _append_audit(self, event: Dict[str, Any]) -> None:
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        entry = dict(event)
        entry["timestamp_utc"] = datetime.now(timezone.utc).isoformat()
        entry["prev_hash"] = self._last_audit_hash()
        canonical = json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        entry["self_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self.audit_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")

    def verify_audit_chain(self) -> List[str]:
        """Return problems in registry/audit_log.jsonl (empty list = intact)."""
        problems: List[str] = []
        if not self.audit_file.exists():
            return problems
        prev = "0" * 64
        for n, line in enumerate(self.audit_file.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                problems.append(f"line {n}: not JSON")
                break
            claimed = entry.pop("self_hash", "")
            if entry.get("prev_hash") != prev:
                problems.append(f"line {n}: prev_hash does not link to line {n - 1}")
            canonical = json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != claimed:
                problems.append(f"line {n}: self_hash mismatch (entry edited)")
            prev = claimed
        return problems

    # -- scanning ----------------------------------------------------------
    def _reject(self, item: Path, digest: str, count: int, *errors: str) -> AddonRecord:
        return AddonRecord(
            addon_id=item.name, name=item.name, version="0.0.0", entrypoint="",
            path=item.relative_to(self.repo_root).as_posix(), sha256=digest,
            dependencies=[], permissions=[], status="rejected", errors=list(errors),
            file_count=count,
        )

    def _scan_one(self, item: Path) -> AddonRecord:
        files, links = list_addon_files(item)
        digest = compute_directory_sha256(item)
        if links:
            return self._reject(item, digest, len(files), f"[symlink] روابط رمزية غير مسموحة: {links}")
        manifest_path = item / "addon.json"
        if not manifest_path.is_file():
            return self._reject(item, digest, len(files), "[missing_manifest] ملف addon.json مفقود.")
        if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            return self._reject(item, digest, len(files), "[manifest_too_large] addon.json أكبر من 64KB.")
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return self._reject(item, digest, len(files), f"[bad_json] تعذر قراءة JSON: {type(exc).__name__}")

        ok, errors = validate_manifest(item, raw)
        data = raw if isinstance(raw, dict) else {}

        def as_list(key: str) -> List[str]:
            value = data.get(key)
            return list(value) if isinstance(value, list) and all(isinstance(v, str) for v in value) else []

        return AddonRecord(
            addon_id=str(data.get("id", item.name)) if isinstance(data.get("id"), str) else item.name,
            name=str(data.get("name", item.name)),
            version=str(data.get("version", "0.0.0")),
            entrypoint=str(data.get("entrypoint", "")),
            path=item.relative_to(self.repo_root).as_posix(),
            sha256=digest,
            dependencies=as_list("dependencies"),
            permissions=sorted(set(as_list("permissions"))),
            status="active" if ok else "rejected",
            errors=errors,
            requirements=as_list("requirements"),
            file_count=len(files),
        )

    def scan(self) -> List[AddonRecord]:
        records: List[AddonRecord] = []
        if not self.addons_dir.is_dir():
            return records
        seen: Dict[str, str] = {}
        for item in sorted(self.addons_dir.iterdir(), key=lambda p: p.name):
            if item.is_symlink():
                records.append(self._reject(item, "", 0, "[symlink] مجلد الإضافة رابط رمزي."))
                continue
            if not item.is_dir() or item.name.startswith("."):
                continue
            rec = self._scan_one(item)
            # Defence in depth: id == folder name already makes duplicates impossible on a
            # case-sensitive filesystem; this also catches case-folding collisions.
            key = rec.addon_id.casefold()
            if rec.status != "rejected":
                if key in seen:
                    rec.status = "rejected"
                    rec.errors.append(f"[duplicate_id] معرّف مكرر مع {seen[key]}")
                else:
                    seen[key] = rec.path
            records.append(rec)
        self._resolve_dependencies(records)
        return records

    @staticmethod
    def _resolve_dependencies(records: List[AddonRecord]) -> None:
        """Degrade every add-on whose dependency is missing, not active, or in a cycle."""
        by_id = {r.addon_id: r for r in records if r.status != "rejected"}
        # Cycle detection (iterative DFS, colours: 0 new, 1 on stack, 2 done).
        colour: Dict[str, int] = {}
        in_cycle: Set[str] = set()
        for start in sorted(by_id):
            if colour.get(start):
                continue
            stack: List[Tuple[str, int]] = [(start, 0)]
            path: List[str] = []
            while stack:
                node, idx = stack.pop()
                if idx == 0:
                    colour[node] = 1
                    path.append(node)
                deps = [d for d in by_id[node].dependencies if d in by_id]
                if idx < len(deps):
                    stack.append((node, idx + 1))
                    nxt = deps[idx]
                    if colour.get(nxt) == 1:
                        in_cycle.update(path[path.index(nxt):])
                    elif not colour.get(nxt):
                        stack.append((nxt, 0))
                else:
                    colour[node] = 2
                    path.pop()
        for addon_id in sorted(in_cycle):
            rec = by_id[addon_id]
            rec.status = "degraded"
            rec.errors.append("[dependency_cycle] الإضافة جزء من حلقة اعتماديات.")
        # Fixpoint: degradation propagates to every dependant.
        changed = True
        while changed:
            changed = False
            for rec in by_id.values():
                if rec.status != "active":
                    continue
                bad = [d for d in rec.dependencies if d not in by_id or by_id[d].status != "active"]
                if bad:
                    rec.status = "degraded"
                    rec.errors.append(f"[dependency_unavailable] اعتماديات غير مفعلة أو مفقودة: {sorted(bad)}")
                    changed = True

    # -- registry ----------------------------------------------------------
    @staticmethod
    def build_payload(records: Sequence[AddonRecord]) -> Dict[str, Any]:
        """Deterministic: identical inputs always give byte-identical JSON (no timestamps)."""
        counts = {s: sum(1 for r in records if r.status == s) for s in ("active", "degraded", "rejected")}
        return {
            "protocol_version": PROTOCOL_VERSION,
            "permissions_allowed": sorted(PERMISSIONS),
            "total_scanned": len(records),
            "active_count": counts["active"],
            "degraded_count": counts["degraded"],
            "rejected_count": counts["rejected"],
            "addons": [asdict(r) for r in records],
        }

    @staticmethod
    def render(payload: Dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def collect(self) -> Dict[str, Any]:
        """Rebuild registry.json atomically; audit only when the registry actually changes."""
        payload = self.build_payload(self.scan())
        text = self.render(payload)
        previous = self.registry_file.read_text(encoding="utf-8") if self.registry_file.exists() else None
        if text != previous:
            self.registry_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.registry_dir, prefix=".registry.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(text)
                os.replace(tmp, self.registry_file)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
            self._append_audit({
                "action": "collect_addons",
                "registry_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "total_scanned": payload["total_scanned"],
                "active_count": payload["active_count"],
                "degraded_count": payload["degraded_count"],
                "rejected_count": payload["rejected_count"],
            })
        return payload

    def check(self) -> Tuple[bool, List[str]]:
        """Read-only drift check: recompute everything and compare with registry.json."""
        problems: List[str] = []
        if not self.registry_file.exists():
            return False, ["registry/registry.json is missing — run the collector"]
        try:
            committed = json.loads(self.registry_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return False, ["registry/registry.json is not valid JSON"]
        fresh = self.build_payload(self.scan())
        old = {a["addon_id"]: a for a in committed.get("addons", [])}
        new = {a["addon_id"]: a for a in fresh["addons"]}
        for addon_id in sorted(set(old) | set(new)):
            if addon_id not in new:
                problems.append(f"{addon_id}: indexed but no longer on disk")
            elif addon_id not in old:
                problems.append(f"{addon_id}: on disk but not indexed")
            else:
                if old[addon_id].get("sha256") != new[addon_id]["sha256"]:
                    problems.append(f"{addon_id}: files changed after indexing (sha256 drift)")
                if old[addon_id].get("status") != new[addon_id]["status"]:
                    problems.append(
                        f"{addon_id}: status {old[addon_id].get('status')} -> {new[addon_id]['status']}"
                    )
        if not problems and self.render(committed) != self.render(fresh):
            problems.append("registry.json differs from a fresh build (protocol/metadata drift)")
        problems.extend(f"audit_log: {p}" for p in self.verify_audit_chain())
        return not problems, problems


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Unified Add-on Collection Protocol v1")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check", action="store_true", help="read-only drift check (exit 1 on drift)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    engine = UnifiedAddonCollector(args.root)

    if args.check:
        ok, problems = engine.check()
        if args.json:
            print(json.dumps({"ok": ok, "problems": problems}, ensure_ascii=False))
        else:
            print("OK: registry matches disk; audit chain intact" if ok else "DRIFT:")
            for p in problems:
                print(f"  - {p}")
        return 0 if ok else 1

    result = engine.collect()
    if args.json:
        print(json.dumps({k: v for k, v in result.items() if k != "addons"}, ensure_ascii=False))
    else:
        print(
            f"تم الفحص: {result['total_scanned']} | النشط: {result['active_count']} | "
            f"المتدهور: {result['degraded_count']} | المرفوض: {result['rejected_count']}"
        )
        for rec in result["addons"]:
            if rec["errors"]:
                print(f"  {rec['addon_id']} [{rec['status']}]: " + " ; ".join(rec["errors"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
