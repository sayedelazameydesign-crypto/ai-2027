#!/usr/bin/env python3
"""Offline tests for scripts/install_browserskill.sh — fail-closed by proof.

Exercises the real shell script against a local file:// release mirror so CI
proves (without network access) that:
  1. a valid pinned archive with matching SHA-256 installs cleanly;
  2. a tampered archive is REFUSED and leaves no binary behind;
  3. a manifest without a SHA-256 is REFUSED (closing upstream's warn-and-skip);
  4. a manifest for the wrong version is REFUSED;
  5. a tar traversal entry (`../pwn`) is REFUSED;
  6. `--check` distinguishes installed (0), missing (2), and drifted (1);
  7. the script never invokes `sudo` and never writes to shell rc files.

Stdlib only. Run: python3 scripts/test_install_browserskill.py -v
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "install_browserskill.sh"
PS_SCRIPT = ROOT / "scripts" / "install_browserskill.ps1"


def _make_tar(path: Path, entries: list[tuple[str, bytes, int]]) -> str:
    with tarfile.open(path, "w:gz") as tf:
        for name, data, mode in entries:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mode = mode
            tf.addfile(info, io.BytesIO(data))
    return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipIf(os.name == "nt", "POSIX shell installer test")
class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        if not shutil.which("curl"):
            self.skipTest("curl not available")
        self.tmp = Path(tempfile.mkdtemp(prefix="ai2027-bsk-inst-"))
        self.mirror = self.tmp / "mirror"
        self.dest = self.tmp / "bin"
        self.home = self.tmp / "home"
        self.mirror.mkdir()
        self.home.mkdir()
        # Dry-run first to discover this runner's exact platform_key + archive name.
        dry = self._run("--dry-run")
        self.assertEqual(dry.returncode, 0, dry.stderr)
        plan = json.loads(dry.stdout)
        self.platform_key = plan["platform"]
        self.archive_name = Path(plan["archive_url"]).name
        self.version = plan["pinned_version"]

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *args: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["BSK_INSTALL_DIR"] = str(self.dest)
        env["BSK_RELEASE_BASE_URL"] = self.mirror.as_uri()
        env["PATH"] = "/usr/local/bin:/usr/bin:/bin"
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["sh", str(SCRIPT), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

    def _write_release(self, ver: str = "0.5.0", sha_override: str | None = None,
                       tar_entries: list[tuple[str, bytes, int]] | None = None) -> str:
        fake_bsk = f"#!/bin/sh\necho 'bsk {ver}'\n".encode("utf-8")
        entries = tar_entries or [("bsk", fake_bsk, 0o755)]
        sha = _make_tar(self.mirror / self.archive_name, entries)
        manifest = {
            "version": ver,
            "assets": {
                self.platform_key: {
                    "archive": self.archive_name,
                    "sha256": sha if sha_override is None else sha_override,
                }
            },
        }
        (self.mirror / "version.json").write_text(json.dumps(manifest), encoding="utf-8")
        return sha

    def test_01_dry_run_pins_0_5_0_and_declares_fail_closed(self):
        r = self._run("--dry-run")
        self.assertEqual(r.returncode, 0)
        d = json.loads(r.stdout)
        self.assertEqual(d["pinned_version"], "0.5.0")
        self.assertEqual(d["tag"], "cli-v0.5.0")
        self.assertFalse(d["uses_sudo"])
        self.assertFalse(d["mutates_shell_rc"])
        self.assertIn("fail-closed", d["checksum_policy"])

    def test_02_valid_archive_installs_and_check_passes(self):
        rc_rcfile = self.home / ".bashrc"
        rc_rcfile.write_text("# untouched\n", encoding="utf-8")
        self._write_release("0.5.0")

        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        installed = self.dest / "bsk"
        self.assertTrue(installed.is_file())
        self.assertTrue(installed.stat().st_mode & stat.S_IXUSR)
        # Never modifies shell rc files.
        self.assertEqual(rc_rcfile.read_text(encoding="utf-8"), "# untouched\n")

        chk = self._run("--check")
        self.assertEqual(chk.returncode, 0, chk.stderr)
        self.assertIn("0.5.0", chk.stdout)

    def test_03_tampered_archive_is_refused(self):
        self._write_release("0.5.0", sha_override="0" * 64)
        r = self._run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("MISMATCH", r.stderr)
        self.assertFalse((self.dest / "bsk").exists())

    def test_04_missing_checksum_is_refused_fail_closed(self):
        self._write_release("0.5.0", sha_override="")
        r = self._run()
        self.assertEqual(r.returncode, 1)
        self.assertFalse((self.dest / "bsk").exists())

    def test_05_manifest_version_mismatch_is_refused(self):
        self._write_release("0.4.2")
        r = self._run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("version mismatch", r.stderr)
        self.assertFalse((self.dest / "bsk").exists())

    def test_06_tar_traversal_entry_is_refused(self):
        self._write_release("0.5.0", tar_entries=[("../bsk", b"#!/bin/sh\n", 0o755)])
        r = self._run()
        self.assertEqual(r.returncode, 1)
        self.assertFalse((self.dest / "bsk").exists())

    def test_07_check_reports_2_when_missing_and_1_on_version_drift(self):
        missing = self._run("--check")
        self.assertEqual(missing.returncode, 2)
        self.assertIn("SKIPPED", missing.stdout)

        self.dest.mkdir(parents=True, exist_ok=True)
        drifted = self.dest / "bsk"
        drifted.write_text("#!/bin/sh\necho 'bsk 0.4.1'\n", encoding="utf-8")
        drifted.chmod(0o755)
        drift = self._run("--check")
        self.assertEqual(drift.returncode, 1)
        self.assertIn("DRIFT", drift.stderr)

    def test_08_refuses_to_install_inside_projects_archive(self):
        self._write_release("0.5.0")
        r = self._run(extra_env={"BSK_INSTALL_DIR": str(ROOT / "projects" / "bsk")})
        self.assertEqual(r.returncode, 1)
        self.assertIn("projects/", r.stderr)
        self.assertFalse((ROOT / "projects" / "bsk").exists())

    def test_09_unreachable_mirror_exits_3_blocked(self):
        r = self._run(extra_env={"BSK_RELEASE_BASE_URL": (self.tmp / "absent").as_uri()})
        self.assertEqual(r.returncode, 3)
        self.assertIn("BLOCKED", r.stderr)

    def test_10_static_safety_no_sudo_and_windows_companion_present(self):
        sh_body = SCRIPT.read_text(encoding="utf-8")
        for line in sh_body.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            self.assertNotIn("sudo ", stripped)
        self.assertTrue(PS_SCRIPT.is_file())
        ps_body = PS_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("0.5.0", ps_body)
        self.assertIn("Get-FileHash -Algorithm SHA256", ps_body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
