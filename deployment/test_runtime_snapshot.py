#!/usr/bin/env python3
"""Offline backup/restore and path-safety tests for Live Agent runtime snapshots."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import stat
import sqlite3
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_local_module(name: str, source: Path):
    spec = importlib.util.spec_from_file_location(name, source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load test module {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runtime_snapshot = _load_local_module(
    "ai2027_runtime_snapshot_test", ROOT / "deployment" / "runtime_snapshot.py"
)
live_server = _load_local_module(
    "ai2027_live_agent_server_snapshot_test", ROOT / "live-agent" / "server.py"
)


class RuntimeSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ai2027-snapshot-test-")
        self.root = Path(self.temp.name)
        self.source = self.root / "runtime"
        self.archive = self.root / "runtime-backup.tar.gz"
        self.restored = self.root / "restored-runtime"
        self.saved_paths = (live_server.RUNS_DIR, live_server.SANDBOXES_DIR,
                            live_server.MEMORY_DB)
        self.saved_memory = live_server._memory
        self.saved_index = dict(live_server._run_index)
        live_server._configure_runtime(self.source)
        live_server._prepare_state(fresh=True)
        self.record = live_server.run_goal("create_file", {
            "path": "backup-proof/proof.txt",
            "content": "synthetic-backup-restore-proof",
        })

    def tearDown(self):
        live_server.RUNS_DIR, live_server.SANDBOXES_DIR, live_server.MEMORY_DB = self.saved_paths
        live_server._memory = self.saved_memory
        live_server._run_index.clear()
        live_server._run_index.update(self.saved_index)
        self.temp.cleanup()

    def test_verified_backup_restore_recovers_record_artifact_ledger_and_memory(self):
        result = runtime_snapshot.backup_runtime(self.source, self.archive)
        self.assertGreaterEqual(result["files"], 4)
        self.assertGreater(result["bytes"], 0)
        self.assertTrue(self.archive.is_file())
        if sys.platform != "win32":
            self.assertEqual(stat.S_IMODE(self.archive.stat().st_mode), 0o600)

        restored_result = runtime_snapshot.restore_runtime(self.archive, self.restored)
        self.assertEqual(restored_result, result)
        live_server._configure_runtime(self.restored)
        warnings = live_server._prepare_state()
        self.assertEqual(warnings, [])

        run_id = self.record["run_id"]
        loaded = live_server.read_run(run_id)
        self.assertEqual(loaded["status"], "VERIFIED")
        self.assertEqual(live_server._run_index[run_id]["status"], "VERIFIED")
        artifact, filename = live_server.sandbox_file(run_id, "backup-proof/proof.txt")
        self.assertEqual(filename, "proof.txt")
        self.assertEqual(artifact, b"synthetic-backup-restore-proof")
        ledger = self.restored / "runs" / "ledgers" / f"{run_id}.jsonl"
        self.assertTrue(ledger.is_file())
        self.assertGreaterEqual(live_server.memory().stats()["memories"], 1)

    def test_backup_normalizes_legacy_root_level_memory_database(self):
        legacy_runtime = self.root / "legacy-runtime"
        legacy_runtime.mkdir()
        database = legacy_runtime / "memory.db"
        connection = sqlite3.connect(database)
        try:
            connection.execute("CREATE TABLE legacy_memory (value TEXT NOT NULL)")
            connection.execute("INSERT INTO legacy_memory VALUES (?)", ("preserved-legacy-memory",))
            connection.commit()
        finally:
            connection.close()

        legacy_archive = self.root / "legacy-backup.tar.gz"
        legacy_restore = self.root / "legacy-restored"
        result = runtime_snapshot.backup_runtime(legacy_runtime, legacy_archive)
        self.assertEqual(result["files"], 1)
        with tarfile.open(legacy_archive, mode="r:gz") as archive:
            names = archive.getnames()
        self.assertIn("state/memory.db", names)
        self.assertNotIn("memory.db", names)

        runtime_snapshot.restore_runtime(legacy_archive, legacy_restore)
        with sqlite3.connect(legacy_restore / "state" / "memory.db") as connection:
            row = connection.execute("SELECT value FROM legacy_memory").fetchone()
        self.assertEqual(row, ("preserved-legacy-memory",))

    def test_backup_rejects_orphaned_sqlite_sidecars_in_both_runtime_layouts(self):
        for database_parent in (Path("."), Path("state")):
            for suffix in ("-wal", "-shm", "-journal"):
                with self.subTest(parent=database_parent.as_posix(), suffix=suffix):
                    runtime = self.root / f"orphan-{database_parent.name or 'root'}-{suffix[1:]}"
                    runtime.mkdir()
                    parent = runtime / database_parent
                    parent.mkdir(parents=True, exist_ok=True)
                    (parent / f"memory.db{suffix}").write_bytes(b"orphaned-sidecar")
                    archive = self.root / f"{runtime.name}.tar.gz"
                    with self.assertRaisesRegex(
                        runtime_snapshot.SnapshotError,
                        "SQLite sidecar exists without its database",
                    ):
                        runtime_snapshot.backup_runtime(runtime, archive)
                    self.assertFalse(archive.exists())

    def test_backup_explicitly_rejects_database_and_wal_symlinks(self):
        outside = self.root / "external-wal"
        outside.write_bytes(b"must-not-be-read")
        for relative_db in (Path("memory.db"), Path("state") / "memory.db"):
            with self.subTest(database=relative_db.as_posix()):
                runtime = self.root / ("linked-" + relative_db.parent.name)
                runtime.mkdir()
                database = runtime / relative_db
                database.parent.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(database)
                try:
                    connection.execute("CREATE TABLE protected (value TEXT)")
                    connection.commit()
                finally:
                    connection.close()
                sidecar = Path(str(database) + "-wal")
                try:
                    sidecar.symlink_to(outside)
                except (OSError, NotImplementedError) as exc:
                    self.skipTest(f"file symlinks unavailable: {exc}")
                archive = self.root / (runtime.name + ".tar.gz")
                with self.assertRaisesRegex(
                    runtime_snapshot.SnapshotError, "SQLite runtime files must not be symbolic links"
                ):
                    runtime_snapshot.backup_runtime(runtime, archive)
                self.assertFalse(archive.exists())
                sidecar.unlink()

                database.unlink()
                database.symlink_to(outside)
                with self.assertRaisesRegex(
                    runtime_snapshot.SnapshotError, "SQLite runtime files must not be symbolic links"
                ):
                    runtime_snapshot.backup_runtime(runtime, archive)
                self.assertFalse(archive.exists())
                database.unlink()
        self.assertEqual(outside.read_bytes(), b"must-not-be-read")

    def test_backup_refuses_symlinked_runtime_root_and_keeps_external_data(self):
        outside = self.root / "outside"
        outside.mkdir()
        marker = outside / "not-for-deletion.txt"
        marker.write_text("synthetic external data", encoding="utf-8")
        linked_runtime = self.root / "linked-runtime"
        linked_runtime.mkdir()
        try:
            (linked_runtime / "runs").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"directory symlinks unavailable: {exc}")
        with self.assertRaises(runtime_snapshot.SnapshotError):
            runtime_snapshot.backup_runtime(linked_runtime, self.archive)
        self.assertEqual(marker.read_text(encoding="utf-8"), "synthetic external data")
        self.assertFalse(self.archive.exists())

    def test_restore_rejects_traversal_and_does_not_publish_partial_tree(self):
        malicious = self.root / "traversal.tar.gz"
        with tarfile.open(malicious, mode="w:gz") as archive:
            data = b"must-not-escape"
            info = tarfile.TarInfo("../outside.txt")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        with self.assertRaises(runtime_snapshot.SnapshotError):
            runtime_snapshot.restore_runtime(malicious, self.restored)
        self.assertFalse(self.restored.exists())
        self.assertFalse((self.root / "outside.txt").exists())
        self.assertFalse(list(self.root.glob(".restored.ai2027-restore-*")))

    def test_restore_rejects_windows_incompatible_and_ambiguous_components(self):
        unsafe_paths = (
            "runs/C:/outside.txt",
            "runs/NUL/hidden.txt",
            "runs/COM1.txt",
            "runs/COM1 .txt",
            "runs/trailing./file.txt",
            "runs/trailing /file.txt",
            "runs/invalid?.txt",
            "runs/control\x01.txt",
            "runs/" + "😀" * 128 + ".txt",
            "runs/" + "a" * 256 + ".txt",
        )
        for index, unsafe_path in enumerate(unsafe_paths):
            with self.subTest(path=unsafe_path):
                malicious = self.root / f"unsafe-{index}.tar.gz"
                destination = self.root / f"restored-unsafe-{index}"
                file_bytes = b"synthetic-portability-proof"
                manifest = {
                    "format": "ai2027-runtime-snapshot",
                    "version": 1,
                    "created_at": "synthetic",
                    "files": [{
                        "path": unsafe_path,
                        "size": len(file_bytes),
                        "sha256": hashlib.sha256(file_bytes).hexdigest(),
                    }],
                }
                with tarfile.open(malicious, mode="w:gz") as archive:
                    data = json.dumps(manifest).encode()
                    info = tarfile.TarInfo(runtime_snapshot.MANIFEST_NAME)
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
                    info = tarfile.TarInfo(unsafe_path)
                    info.size = len(file_bytes)
                    archive.addfile(info, io.BytesIO(file_bytes))
                with self.assertRaises(runtime_snapshot.SnapshotError):
                    runtime_snapshot.restore_runtime(malicious, destination)
                self.assertFalse(destination.exists())
                self.assertFalse(list(self.root.glob(f".{destination.name}.ai2027-restore-*")))

    def test_restore_rejects_windows_case_insensitive_name_collisions(self):
        malicious = self.root / "case-collision.tar.gz"
        files = {
            "runs/Case.txt": b"first",
            "runs/case.txt": b"second",
        }
        manifest = {
            "format": "ai2027-runtime-snapshot",
            "version": 1,
            "created_at": "synthetic",
            "files": [
                {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                for name, data in files.items()
            ],
        }
        with tarfile.open(malicious, mode="w:gz") as archive:
            data = json.dumps(manifest).encode()
            info = tarfile.TarInfo(runtime_snapshot.MANIFEST_NAME)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
            for name, contents in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(contents)
                archive.addfile(info, io.BytesIO(contents))

        with self.assertRaisesRegex(runtime_snapshot.SnapshotError, "case-insensitive"):
            runtime_snapshot.restore_runtime(malicious, self.restored)
        self.assertFalse(self.restored.exists())
        self.assertFalse(list(self.root.glob(".restored.ai2027-restore-*")))

    def test_restore_detects_hash_mismatch_without_overwriting(self):
        malicious = self.root / "mismatched.tar.gz"
        file_bytes = b"tampered"
        manifest = {
            "format": "ai2027-runtime-snapshot",
            "version": 1,
            "created_at": "synthetic",
            "files": [{
                "path": "runs/synthetic.json",
                "size": len(file_bytes),
                "sha256": hashlib.sha256(b"expected").hexdigest(),
            }],
        }
        with tarfile.open(malicious, mode="w:gz") as archive:
            data = json.dumps(manifest).encode()
            info = tarfile.TarInfo(runtime_snapshot.MANIFEST_NAME)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
            info = tarfile.TarInfo("runs/synthetic.json")
            info.size = len(file_bytes)
            archive.addfile(info, io.BytesIO(file_bytes))

        with self.assertRaises(runtime_snapshot.SnapshotError):
            runtime_snapshot.restore_runtime(malicious, self.restored)
        self.assertFalse(self.restored.exists())
        self.assertFalse(list(self.root.glob(".restored.ai2027-restore-*")))

        runtime_snapshot.backup_runtime(self.source, self.archive)
        existing = self.root / "existing"
        existing.mkdir()
        proof = existing / "keep.txt"
        proof.write_text("keep", encoding="utf-8")
        with self.assertRaises(runtime_snapshot.SnapshotError):
            runtime_snapshot.restore_runtime(self.archive, existing)
        self.assertEqual(proof.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main(verbosity=2)
