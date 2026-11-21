#!/usr/bin/env python3
"""Offline, verified snapshots for a quiesced Live Agent runtime.

This tool does not configure a host, encrypt archives, or promise cross-instance
persistence. Stop writes (preferably stop the service) before creating a snapshot.
Archives contain run records, artifacts, ledgers, and memory; protect them as
sensitive user data. Restore only into a new, isolated directory.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote

MANIFEST_NAME = "AI2027_BACKUP_MANIFEST.json"
_ALLOWED_ROOTS = {"runs", "sandboxes", "state"}
_SQLITE_SUFFIXES = ("", "-wal", "-shm", "-journal")
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_MAX_MEMBERS = 100_000
_MAX_TOTAL_BYTES = 8 * 1024 * 1024 * 1024
_MAX_MANIFEST_BYTES = 16 * 1024 * 1024
_CHUNK_SIZE = 1024 * 1024


class SnapshotError(ValueError):
    """A snapshot failed a path, integrity, or storage-layout check."""


def _absolute_no_links(value: str | Path) -> Path:
    raw = Path(value).expanduser()
    if ".." in raw.parts:
        raise SnapshotError("paths must not contain parent traversal")
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    absolute = Path(os.path.abspath(os.fspath(raw)))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            if current.is_symlink():
                raise SnapshotError("paths must not traverse symbolic links")
        except OSError as exc:
            raise SnapshotError("path could not be inspected") from exc
    return absolute


def _regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except FileNotFoundError:
        return False


def _validate_sqlite_bundle(database: Path) -> bool:
    """Validate a database and its optional WAL/SHM sidecars without following links."""
    present: dict[str, bool] = {}
    for suffix in _SQLITE_SUFFIXES:
        path = Path(str(database) + suffix)
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            present[path.name] = False
            continue
        except OSError as exc:
            raise SnapshotError("SQLite runtime files could not be inspected") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise SnapshotError("SQLite runtime files must not be symbolic links")
        if not stat.S_ISREG(metadata.st_mode):
            raise SnapshotError("SQLite runtime files must be regular files")
        present[path.name] = True
    has_database = present[database.name]
    if not has_database and any(
        present[database.name + suffix] for suffix in _SQLITE_SUFFIXES[1:]
    ):
        raise SnapshotError("SQLite sidecar exists without its database")
    return has_database


def _safe_copy_file(source: Path, target: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise SnapshotError("source file could not be opened safely") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise SnapshotError("snapshot accepts regular files only")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with os.fdopen(descriptor, "rb", closefd=False) as src, target.open("xb") as dst:
            shutil.copyfileobj(src, dst, _CHUNK_SIZE)
            dst.flush()
            os.fsync(dst.fileno())
        os.chmod(target, 0o600)
    finally:
        os.close(descriptor)


def _copy_tree(source: Path, target: Path, *, skip_memory_files: bool = False) -> None:
    try:
        mode = source.lstat().st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(mode):
        raise SnapshotError("runtime storage roots must be real directories")
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    for entry in sorted(source.iterdir(), key=lambda item: item.name):
        if skip_memory_files and entry.name in {
            "memory.db", "memory.db-wal", "memory.db-shm", "memory.db-journal"
        }:
            continue
        try:
            entry_mode = entry.lstat().st_mode
        except OSError as exc:
            raise SnapshotError("runtime entry could not be inspected") from exc
        if stat.S_ISLNK(entry_mode):
            raise SnapshotError("runtime snapshot refuses symbolic links")
        destination = target / entry.name
        if stat.S_ISDIR(entry_mode):
            _copy_tree(entry, destination, skip_memory_files=False)
        elif stat.S_ISREG(entry_mode):
            _safe_copy_file(entry, destination)
        else:
            raise SnapshotError("runtime snapshot accepts regular files and directories only")


def _sqlite_read_uri(path: Path) -> str:
    return "file:" + quote(path.as_posix(), safe="/") + "?mode=ro"


def _sqlite_backup(source: Path, target: Path) -> None:
    if not _validate_sqlite_bundle(source):
        raise SnapshotError("memory database is missing")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    source_db = target_db = None
    try:
        source_db = sqlite3.connect(_sqlite_read_uri(source), uri=True)
        source_db.execute("PRAGMA query_only=ON")
        target_db = sqlite3.connect(target)
        source_db.backup(target_db)
        result = target_db.execute("PRAGMA quick_check").fetchone()
        if not result or result[0] != "ok":
            raise SnapshotError("memory database integrity check failed")
        target_db.close()
        target_db = None
        os.chmod(target, 0o600)
    except sqlite3.Error as exc:
        raise SnapshotError("memory database backup failed") from exc
    finally:
        if target_db is not None:
            target_db.close()
        if source_db is not None:
            source_db.close()


def _sha256(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_CHUNK_SIZE)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _archive_add_file(archive: tarfile.TarFile, path: Path, arcname: str) -> None:
    info = archive.gettarinfo(str(path), arcname=arcname)
    if not info.isfile():
        raise SnapshotError("snapshot archive accepts regular files only")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise SnapshotError("snapshot entry changed type")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            archive.addfile(info, handle)
    finally:
        os.close(descriptor)


def backup_runtime(runtime_dir: str | Path, archive_path: str | Path) -> dict[str, int]:
    """Create an atomic, checksummed tar.gz snapshot without following links."""
    source = _absolute_no_links(runtime_dir)
    if not source.is_dir():
        raise SnapshotError("runtime directory does not exist")
    destination = _absolute_no_links(archive_path)
    if destination.exists() or destination.is_symlink():
        raise SnapshotError("backup destination already exists")
    if not destination.parent.is_dir():
        raise SnapshotError("backup destination parent must already exist")
    if source == destination.parent or source in destination.parents:
        raise SnapshotError("backup archive must be outside the runtime directory")

    with tempfile.TemporaryDirectory(prefix="ai2027-snapshot-") as temporary:
        snapshot = Path(temporary) / "runtime"
        snapshot.mkdir(mode=0o700)
        for name in sorted(_ALLOWED_ROOTS):
            _copy_tree(source / name, snapshot / name,
                       skip_memory_files=(name == "state"))

        # Older Live Agent installations kept memory.db beside runs/ and
        # sandboxes/. Normalize that supported layout into the canonical
        # state/memory.db path so a restored runtime can boot unchanged.
        current_database = source / "state" / "memory.db"
        legacy_database = source / "memory.db"
        has_current = _validate_sqlite_bundle(current_database)
        has_legacy = _validate_sqlite_bundle(legacy_database)
        if has_current and has_legacy:
            raise SnapshotError("multiple runtime memory databases are ambiguous")
        database = current_database if has_current else legacy_database if has_legacy else None
        if database is not None:
            _sqlite_backup(database, snapshot / "state" / "memory.db")

        file_entries = []
        for path in sorted(snapshot.rglob("*")):
            if path.is_symlink():
                raise SnapshotError("snapshot tree contains a symbolic link")
            relative = path.relative_to(snapshot).as_posix()
            _safe_member_name(relative)
            if path.is_file():
                size, digest = _sha256(path)
                file_entries.append({"path": relative, "size": size, "sha256": digest})
        total_bytes = sum(entry["size"] for entry in file_entries)
        if len(file_entries) > _MAX_MEMBERS or total_bytes > _MAX_TOTAL_BYTES:
            raise SnapshotError("runtime exceeds the snapshot safety limit")
        manifest = {
            "format": "ai2027-runtime-snapshot",
            "version": 1,
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "files": file_entries,
        }
        manifest_bytes = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()

        temporary_archive = destination.parent / f".{destination.name}.{os.urandom(8).hex()}.tmp"
        archive_fd: int | None = None
        try:
            # The archive contains user data: create it private before writing any bytes.
            archive_fd = os.open(
                temporary_archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(archive_fd, "wb") as raw:
                archive_fd = None  # fdopen owns it from here
                with tarfile.open(fileobj=raw, mode="w:gz") as archive:
                    for path in sorted(snapshot.rglob("*")):
                        relative = path.relative_to(snapshot).as_posix()
                        if path.is_dir():
                            archive.add(path, arcname=relative, recursive=False)
                        else:
                            _archive_add_file(archive, path, relative)
                    manifest_info = tarfile.TarInfo(MANIFEST_NAME)
                    manifest_info.size = len(manifest_bytes)
                    manifest_info.mode = 0o600
                    archive.addfile(manifest_info, io.BytesIO(manifest_bytes))
                raw.flush()
                os.fsync(raw.fileno())
            # Hard-link publication is atomic and refuses to replace an existing archive.
            os.link(temporary_archive, destination)
        except FileExistsError as exc:
            raise SnapshotError("backup destination already exists") from exc
        except OSError as exc:
            raise SnapshotError("backup archive could not be published safely") from exc
        finally:
            if archive_fd is not None:
                os.close(archive_fd)
            try:
                temporary_archive.unlink()
            except FileNotFoundError:
                pass
    return {"files": len(file_entries), "bytes": total_bytes}


def _safe_member_name(name: str) -> str:
    if not name or name.startswith("/") or "\\" in name or "\x00" in name:
        raise SnapshotError("archive contains an unsafe member name")
    trimmed = name[:-1] if name.endswith("/") else name
    parts = trimmed.split("/")
    if not trimmed or any(part in {"", ".", ".."} for part in parts):
        raise SnapshotError("archive contains an unsafe member path")
    reserved = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", "CLOCK$"}
    invalid_windows_chars = set('<>:"|?*')
    for part in parts:
        utf16_units = sum(2 if ord(char) > 0xFFFF else 1 for char in part)
        if (
            any(
                ord(char) < 0x20
                or 0xD800 <= ord(char) <= 0xDFFF
                or char in invalid_windows_chars
                for char in part
            )
            or part.endswith((".", " "))
            or utf16_units > 255
        ):
            raise SnapshotError("archive contains a non-portable path component")
        device_name = part.split(".", 1)[0].rstrip(" .").upper()
        if (
            device_name in reserved
            or re.fullmatch(r"(?:COM|LPT)(?:[1-9¹²³])", device_name)
        ):
            raise SnapshotError("archive contains a reserved Windows device name")
    path = PurePosixPath(trimmed)
    if path.is_absolute() or parts[0] not in _ALLOWED_ROOTS | {MANIFEST_NAME}:
        raise SnapshotError("archive contains a path outside the runtime layout")
    if parts[0] == MANIFEST_NAME and len(parts) != 1:
        raise SnapshotError("archive manifest path is invalid")
    return trimmed


def _validated_members(archive: tarfile.TarFile) -> tuple[list[tarfile.TarInfo], dict, int]:
    members = archive.getmembers()
    if len(members) > _MAX_MEMBERS + 10:
        raise SnapshotError("archive contains too many entries")
    names: dict[str, tarfile.TarInfo] = {}
    windows_names: set[str] = set()
    manifest_member = None
    total_bytes = 0
    regular_files: dict[str, tarfile.TarInfo] = {}
    directories: set[str] = set()
    for member in members:
        name = _safe_member_name(member.name)
        if name in names:
            raise SnapshotError("archive contains duplicate entries")
        if name.casefold() in windows_names:
            raise SnapshotError("archive contains a Windows case-insensitive path collision")
        names[name] = member
        windows_names.add(name.casefold())
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise SnapshotError("archive contains a link or special file")
        if name == MANIFEST_NAME:
            if not member.isfile() or member.size > _MAX_MANIFEST_BYTES:
                raise SnapshotError("archive manifest is invalid")
            manifest_member = member
        elif member.isdir():
            directories.add(name)
        elif member.isfile():
            if member.size < 0:
                raise SnapshotError("archive member size is invalid")
            total_bytes += member.size
            if total_bytes > _MAX_TOTAL_BYTES:
                raise SnapshotError("archive exceeds the restore safety limit")
            regular_files[name] = member
        else:
            raise SnapshotError("archive contains an unsupported entry type")
    if manifest_member is None:
        raise SnapshotError("archive manifest is missing")
    if set(regular_files) & directories:
        raise SnapshotError("archive has a file/directory path conflict")
    for filename in regular_files:
        parent = PurePosixPath(filename).parent
        while parent.as_posix() != ".":
            if parent.as_posix() in regular_files:
                raise SnapshotError("archive has a file/directory path conflict")
            parent = parent.parent
    raw_manifest = archive.extractfile(manifest_member)
    if raw_manifest is None:
        raise SnapshotError("archive manifest could not be read")
    try:
        manifest_bytes = raw_manifest.read(_MAX_MANIFEST_BYTES + 1)
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise SnapshotError("archive manifest is malformed") from exc
    if len(manifest_bytes) > _MAX_MANIFEST_BYTES:
        raise SnapshotError("archive manifest is too large")
    if not isinstance(manifest, dict) or manifest.get("format") != "ai2027-runtime-snapshot" or manifest.get("version") != 1:
        raise SnapshotError("archive format is not supported")
    entries = manifest.get("files")
    if not isinstance(entries, list) or len(entries) > _MAX_MEMBERS:
        raise SnapshotError("archive manifest file list is invalid")
    declared: dict[str, tuple[int, str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise SnapshotError("archive manifest entry is invalid")
        path = entry.get("path")
        size = entry.get("size")
        digest = entry.get("sha256")
        if not isinstance(path, str) or path == MANIFEST_NAME:
            raise SnapshotError("archive manifest path is invalid")
        path = _safe_member_name(path)
        if (
            not isinstance(size, int) or isinstance(size, bool) or size < 0
            or not isinstance(digest, str) or not _HASH_RE.fullmatch(digest)
            or path in declared
        ):
            raise SnapshotError("archive manifest integrity entry is invalid")
        declared[path] = (size, digest)
    if set(declared) != set(regular_files):
        raise SnapshotError("archive contents do not match the manifest")
    for name, member in regular_files.items():
        if declared[name][0] != member.size:
            raise SnapshotError("archive member size does not match the manifest")
    return members, {"files": declared, "directories": directories}, total_bytes


def _verify_sqlite(path: Path) -> None:
    if not path.exists():
        return
    if not _regular_file(path):
        raise SnapshotError("restored memory database is not a regular file")
    connection = None
    try:
        connection = sqlite3.connect(_sqlite_read_uri(path), uri=True)
        result = connection.execute("PRAGMA quick_check").fetchone()
        if not result or result[0] != "ok":
            raise SnapshotError("restored memory database failed integrity check")
    except sqlite3.Error as exc:
        raise SnapshotError("restored memory database could not be verified") from exc
    finally:
        if connection is not None:
            connection.close()


def restore_runtime(archive_path: str | Path, destination: str | Path) -> dict[str, int]:
    """Validate and restore an archive into a new directory without overwriting."""
    archive_file = _absolute_no_links(archive_path)
    if not _regular_file(archive_file):
        raise SnapshotError("backup archive is not a regular file")
    target = _absolute_no_links(destination)
    if target.exists() or target.is_symlink():
        raise SnapshotError("restore destination must not already exist")
    parent = target.parent
    if not parent.is_dir():
        raise SnapshotError("restore parent directory must already exist")
    lock_path = parent / f".{target.name}.ai2027-restore.lock"
    try:
        lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise SnapshotError("another restore is using this destination") from exc
    stage: Path | None = None
    try:
        if target.exists() or target.is_symlink():
            raise SnapshotError("restore destination must not already exist")
        stage = Path(tempfile.mkdtemp(prefix=f".{target.name}.ai2027-restore-", dir=parent))
        with tarfile.open(archive_file, mode="r:gz") as archive:
            members, manifest, total_bytes = _validated_members(archive)
            directories = sorted(manifest["directories"], key=lambda item: (item.count("/"), item))
            for name in directories:
                directory = stage.joinpath(*PurePosixPath(name).parts)
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            verified_bytes = 0
            for member in members:
                if member.name == MANIFEST_NAME or member.isdir():
                    continue
                name = _safe_member_name(member.name)
                expected_size, expected_hash = manifest["files"][name]
                target_file = stage.joinpath(*PurePosixPath(name).parts)
                target_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                digest = hashlib.sha256()
                written = 0
                source_file = archive.extractfile(member)
                if source_file is None:
                    raise SnapshotError("archive file could not be read")
                with source_file, target_file.open("xb") as output:
                    while True:
                        chunk = source_file.read(_CHUNK_SIZE)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > expected_size or written > _MAX_TOTAL_BYTES:
                            raise SnapshotError("archive file exceeded its declared size")
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                os.chmod(target_file, 0o600)
                if written != expected_size or digest.hexdigest() != expected_hash:
                    raise SnapshotError("archive file failed its SHA-256 check")
                verified_bytes += written
            if verified_bytes != total_bytes:
                raise SnapshotError("restored byte count does not match archive metadata")
        _verify_sqlite(stage / "state" / "memory.db")
        if target.exists() or target.is_symlink():
            raise SnapshotError("restore destination appeared during restore")
        os.rename(stage, target)
        stage = None
        return {"files": len(manifest["files"]), "bytes": total_bytes}
    except (tarfile.TarError, OSError, ValueError, sqlite3.Error) as exc:
        if isinstance(exc, SnapshotError):
            raise
        raise SnapshotError("backup archive could not be safely restored") from exc
    finally:
        os.close(lock_descriptor)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
        if stage is not None and stage.exists():
            shutil.rmtree(stage)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verified offline Live Agent runtime snapshot")
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup_parser = subparsers.add_parser("backup", help="snapshot a stopped/quiesced runtime")
    backup_parser.add_argument("runtime_dir")
    backup_parser.add_argument("archive")
    restore_parser = subparsers.add_parser("restore", help="restore into a new isolated directory")
    restore_parser.add_argument("archive")
    restore_parser.add_argument("destination")
    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            result = backup_runtime(args.runtime_dir, args.archive)
            print(f"backup verified: files={result['files']} bytes={result['bytes']}")
        else:
            result = restore_runtime(args.archive, args.destination)
            print(f"restore verified: files={result['files']} bytes={result['bytes']}")
    except (OSError, SnapshotError, sqlite3.Error, tarfile.TarError):
        sys.stderr.write("ERROR: snapshot operation failed; details withheld\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
