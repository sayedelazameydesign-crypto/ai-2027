"""Tests for collector/protocol_engine.py — Unified Add-on Collection Protocol v1 (offline)."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collector import protocol_engine as pe  # noqa: E402


def write_addon(root: Path, addon_id: str, manifest: object = None, *, files: dict | None = None,
                folder: str | None = None) -> Path:
    d = root / "addons" / (folder or addon_id)
    d.mkdir(parents=True, exist_ok=True)
    for rel, content in (files if files is not None else {"main.py": "x = 1\n"}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    if manifest is None:
        manifest = {"id": addon_id, "name": addon_id, "version": "1.0.0", "entrypoint": "main.py"}
    if manifest != "ABSENT":
        (d / "addon.json").write_text(
            manifest if isinstance(manifest, str) else json.dumps(manifest), encoding="utf-8"
        )
    return d


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.engine = pe.UnifiedAddonCollector(self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def by_id(self) -> dict:
        return {a["addon_id"]: a for a in self.engine.collect()["addons"]}


class ManifestTests(Base):
    def test_valid_addon_is_active_with_digest(self) -> None:
        write_addon(self.tmp, "good_one")
        rec = self.by_id()["good_one"]
        self.assertEqual(rec["status"], "active", rec["errors"])
        self.assertRegex(rec["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(rec["file_count"], 2)

    def test_one_broken_addon_does_not_stop_the_others(self) -> None:
        write_addon(self.tmp, "good_one")
        write_addon(self.tmp, "no_manifest", "ABSENT")
        write_addon(self.tmp, "bad_json", "{not json")
        recs = self.by_id()
        self.assertEqual(recs["good_one"]["status"], "active")
        self.assertEqual(recs["no_manifest"]["status"], "rejected")
        self.assertIn("[missing_manifest]", recs["no_manifest"]["errors"][0])
        self.assertIn("[bad_json]", recs["bad_json"]["errors"][0])

    def test_path_traversal_and_absolute_entrypoints_are_rejected(self) -> None:
        (self.tmp / "outside.py").write_text("x", encoding="utf-8")
        write_addon(self.tmp, "escape_up", {"id": "escape_up", "name": "e", "version": "1.0.0",
                                            "entrypoint": "../../outside.py"})
        write_addon(self.tmp, "escape_abs", {"id": "escape_abs", "name": "e", "version": "1.0.0",
                                             "entrypoint": str(self.tmp / "outside.py")})
        write_addon(self.tmp, "entry_is_dir", {"id": "entry_is_dir", "name": "e", "version": "1.0.0",
                                               "entrypoint": "assets"}, files={"assets/a.txt": "a"})
        recs = self.by_id()
        self.assertTrue(any("[path_traversal]" in e for e in recs["escape_up"]["errors"]))
        self.assertTrue(any("[bad_entrypoint]" in e for e in recs["escape_abs"]["errors"]))
        self.assertTrue(any("[missing_entrypoint]" in e for e in recs["entry_is_dir"]["errors"]))

    def test_types_semver_unknown_fields_and_id_folder_rule(self) -> None:
        write_addon(self.tmp, "str_deps", {"id": "str_deps", "name": "s", "version": "1.0.0",
                                           "entrypoint": "main.py", "dependencies": "abc"})
        write_addon(self.tmp, "bad_ver", {"id": "bad_ver", "name": "s", "version": "v1",
                                          "entrypoint": "main.py"})
        write_addon(self.tmp, "extra_key", {"id": "extra_key", "name": "s", "version": "1.0.0",
                                            "entrypoint": "main.py", "run_as_root": True})
        write_addon(self.tmp, "wrong_dir", {"id": "other_id", "name": "s", "version": "1.0.0",
                                            "entrypoint": "main.py"})
        recs = self.by_id()
        self.assertTrue(any("[bad_type]" in e for e in recs["str_deps"]["errors"]))
        self.assertEqual(recs["str_deps"]["dependencies"], [], "a string must never be split into letters")
        self.assertTrue(any("[bad_version]" in e for e in recs["bad_ver"]["errors"]))
        self.assertTrue(any("[unknown_fields]" in e for e in recs["extra_key"]["errors"]))
        self.assertEqual(recs["other_id"]["status"], "rejected")
        self.assertTrue(any("[id_folder_mismatch]" in e for e in recs["other_id"]["errors"]))

    def test_permissions_come_from_the_allowlist_only(self) -> None:
        write_addon(self.tmp, "ok_perms", {"id": "ok_perms", "name": "p", "version": "1.0.0",
                                           "entrypoint": "main.py", "permissions": ["network", "read_fs"]})
        write_addon(self.tmp, "bad_perms", {"id": "bad_perms", "name": "p", "version": "1.0.0",
                                            "entrypoint": "main.py", "permissions": ["write_anywhere"]})
        recs = self.by_id()
        self.assertEqual(recs["ok_perms"]["status"], "active")
        self.assertEqual(recs["bad_perms"]["status"], "rejected")
        self.assertTrue(any("[unknown_permission]" in e for e in recs["bad_perms"]["errors"]))

    @unittest.skipIf(sys.platform.startswith("win"), "symlinks need privileges on Windows")
    def test_symlinks_are_rejected_and_never_followed(self) -> None:
        secret = self.tmp / "secret.txt"
        secret.write_text("TOP-SECRET", encoding="utf-8")
        d = write_addon(self.tmp, "linker")
        os.symlink(secret, d / "leak.txt")
        outside_dir = self.tmp / "outside_dir"
        outside_dir.mkdir()
        (outside_dir / "x.txt").write_text("outside", encoding="utf-8")
        os.symlink(outside_dir, d / "linked_dir", target_is_directory=True)
        before = pe.compute_directory_sha256(d)
        secret.write_text("CHANGED", encoding="utf-8")
        self.assertEqual(pe.compute_directory_sha256(d), before, "hash must not read through a symlink")
        rec = self.by_id()["linker"]
        self.assertEqual(rec["status"], "rejected")
        self.assertIn("[symlink]", rec["errors"][0])
        self.assertIn("linked_dir", rec["errors"][0])
        self.assertIn("leak.txt", rec["errors"][0])


class HashTests(Base):
    def test_digest_is_stable_and_ignores_build_noise(self) -> None:
        d = write_addon(self.tmp, "stable_one")
        first = pe.compute_directory_sha256(d)
        (d / "__pycache__").mkdir()
        (d / "__pycache__" / "main.cpython-312.pyc").write_bytes(b"\x00junk")
        (d / ".DS_Store").write_bytes(b"x")
        self.assertEqual(pe.compute_directory_sha256(d), first)

    def test_digest_framing_is_unambiguous(self) -> None:
        a = write_addon(self.tmp, "frame_a", files={"ab": "c"})
        b = write_addon(self.tmp, "frame_b", files={"a": "bc"})
        (a / "addon.json").unlink()
        (b / "addon.json").unlink()
        self.assertNotEqual(pe.compute_directory_sha256(a), pe.compute_directory_sha256(b))


class DependencyTests(Base):
    def m(self, addon_id: str, deps: list) -> dict:
        return {"id": addon_id, "name": addon_id, "version": "1.0.0", "entrypoint": "main.py",
                "dependencies": deps}

    def test_degradation_is_transitive(self) -> None:
        # Dependants sort BEFORE their dependencies, so a single pass in scan order
        # cannot get this right; only a fixpoint can.
        write_addon(self.tmp, "aaa_top", self.m("aaa_top", ["mmm_mid"]))
        write_addon(self.tmp, "mmm_mid", self.m("mmm_mid", ["zzz_base"]))
        write_addon(self.tmp, "zzz_base", self.m("zzz_base", ["missing_dep"]))
        write_addon(self.tmp, "free_ddd", self.m("free_ddd", []))
        recs = self.by_id()
        for addon_id in ("aaa_top", "mmm_mid", "zzz_base"):
            self.assertEqual(recs[addon_id]["status"], "degraded", addon_id)
        self.assertEqual(recs["free_ddd"]["status"], "active")

    def test_cycles_are_detected(self) -> None:
        write_addon(self.tmp, "cyc_one", self.m("cyc_one", ["cyc_two"]))
        write_addon(self.tmp, "cyc_two", self.m("cyc_two", ["cyc_one"]))
        write_addon(self.tmp, "uses_cyc", self.m("uses_cyc", ["cyc_one"]))
        recs = self.by_id()
        for addon_id in ("cyc_one", "cyc_two"):
            self.assertTrue(any("[dependency_cycle]" in e for e in recs[addon_id]["errors"]), addon_id)
        self.assertEqual(recs["uses_cyc"]["status"], "degraded")

    def test_self_dependency_is_rejected(self) -> None:
        write_addon(self.tmp, "selfish", self.m("selfish", ["selfish"]))
        self.assertEqual(self.by_id()["selfish"]["status"], "rejected")

    def test_dependency_on_rejected_addon_degrades(self) -> None:
        write_addon(self.tmp, "broken_dep", "ABSENT")
        write_addon(self.tmp, "needs_it", self.m("needs_it", ["broken_dep"]))
        self.assertEqual(self.by_id()["needs_it"]["status"], "degraded")


class RegistryAndDriftTests(Base):
    def test_registry_is_deterministic_and_audit_only_on_change(self) -> None:
        write_addon(self.tmp, "det_one")
        self.engine.collect()
        first = self.engine.registry_file.read_bytes()
        self.engine.collect()
        self.assertEqual(self.engine.registry_file.read_bytes(), first)
        self.assertNotIn(b"generated_at", first)
        lines = self.engine.audit_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1, "an unchanged registry must not add audit noise")

    def test_check_detects_modification_addition_and_removal_after_indexing(self) -> None:
        d = write_addon(self.tmp, "watched")
        write_addon(self.tmp, "gone_soon")
        self.engine.collect()
        self.assertEqual(self.engine.check(), (True, []))

        (d / "main.py").write_text("x = 2  # tampered\n", encoding="utf-8")
        shutil.rmtree(self.tmp / "addons" / "gone_soon")
        write_addon(self.tmp, "newcomer")
        ok, problems = self.engine.check()
        self.assertFalse(ok)
        joined = "\n".join(problems)
        self.assertIn("watched: files changed after indexing", joined)
        self.assertIn("gone_soon: indexed but no longer on disk", joined)
        self.assertIn("newcomer: on disk but not indexed", joined)

    def test_check_is_read_only(self) -> None:
        write_addon(self.tmp, "ro_one")
        self.engine.collect()
        snapshot = {p: p.read_bytes() for p in self.engine.registry_dir.iterdir()}
        (self.tmp / "addons" / "ro_one" / "main.py").write_text("y = 3\n", encoding="utf-8")
        self.engine.check()
        self.assertEqual({p: p.read_bytes() for p in self.engine.registry_dir.iterdir()}, snapshot)

    def test_audit_chain_detects_edits(self) -> None:
        write_addon(self.tmp, "chain_one")
        self.engine.collect()
        write_addon(self.tmp, "chain_two")
        self.engine.collect()
        self.assertEqual(self.engine.verify_audit_chain(), [])
        lines = self.engine.audit_file.read_text(encoding="utf-8").splitlines()
        first = json.loads(lines[0])
        first["active_count"] = 999
        lines[0] = json.dumps(first, ensure_ascii=False, sort_keys=True)
        self.engine.audit_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        problems = self.engine.verify_audit_chain()
        self.assertTrue(any("self_hash mismatch" in p for p in problems), problems)

    def test_cli_check_exit_codes(self) -> None:
        write_addon(self.tmp, "cli_one")
        self.assertEqual(pe.main(["--root", str(self.tmp)]), 0)
        self.assertEqual(pe.main(["--root", str(self.tmp), "--check"]), 0)
        (self.tmp / "addons" / "cli_one" / "main.py").write_text("z = 9\n", encoding="utf-8")
        self.assertEqual(pe.main(["--root", str(self.tmp), "--check"]), 1)


class CommittedRegistryTests(unittest.TestCase):
    """The repository's own registry must match its addons/ tree (CI drift gate)."""

    def test_committed_registry_matches_disk(self) -> None:
        ok, problems = pe.UnifiedAddonCollector(ROOT).check()
        self.assertTrue(ok, "run `python3 collector/protocol_engine.py` and commit:\n" + "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
