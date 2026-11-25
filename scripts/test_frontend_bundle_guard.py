#!/usr/bin/env python3
"""Tests for scripts/frontend_bundle_guard.py — hermetic, stdlib-only.

Run from repository root:
    python3 -m unittest scripts.test_frontend_bundle_guard -v
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import frontend_bundle_guard as fbg  # noqa: E402

# Constructed dynamically at import time so static scanners see test shapes, not literals.
SYNTHETIC_GOOGLE = "AIza" + "Sy" + "B" * 33
SYNTHETIC_OPENAI = "sk-" + "R" * 36
SYNTHETIC_GH = "ghp_" + "m" * 38
SYNTHETIC_PEM = "-----BEGIN " + "RSA PRIVATE KEY-----"


def _init_frontend_fixture(base: Path, name: str = "app-ui", *, lockfile: bool = True) -> Path:
    fe = base / name
    fe.mkdir(parents=True)
    (fe / "package.json").write_text(
        json.dumps({"name": name, "private": True, "scripts": {"build": "vite build"}}),
        encoding="utf-8",
    )
    if lockfile:
        (fe / "package-lock.json").write_text(
            json.dumps({"name": name, "lockfileVersion": 3}),
            encoding="utf-8",
        )
    dist = fe / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><html><body><div id='root'></div>"
        "<script type='module' src='/assets/app.js'></script></body></html>\n",
        encoding="utf-8",
    )
    (dist / "assets" / "app.js").write_text(
        "console.log('AI-2027 static bundle OK');\n",
        encoding="utf-8",
    )
    (dist / "assets" / "style.css").write_text(
        "body { margin: 0; background: #06090f; }\n",
        encoding="utf-8",
    )
    return fe


class TargetValidationTests(unittest.TestCase):
    def test_01_real_workspace_ui_target_validates_cleanly(self) -> None:
        target = fbg.validate_target(ROOT, "workspace-ui", "dist", require_lockfile=True)
        self.assertEqual(target.frontend_rel, "workspace-ui")
        self.assertEqual(target.build_output_rel, "dist")
        self.assertTrue(target.has_lockfile)
        self.assertEqual(target.package_name, "ai-2027-workspace-ui")

    def test_02_frozen_projects_archive_is_refused(self) -> None:
        with self.assertRaisesRegex(fbg.BundleGuardError, "protected or archival layer"):
            fbg.validate_target(
                ROOT, "projects/Software-Agent-Architect", "dist", require_lockfile=False
            )

    def test_03_traversal_separators_and_option_injection_are_refused(self) -> None:
        bad_inputs = (
            "",
            "   ",
            " workspace-ui",
            "-rf",
            "--help",
            "/etc/passwd",
            "~/app",
            "../escape",
            "workspace-ui/../escape",
            "workspace-ui\\dist",
            "C:/workspace-ui",
            "workspace-ui/%2e%2e/escape",
            "workspace-ui/%2fescape",
            "workspace-ui/%5cescape",
            "workspace-ui\x00evil",
            ".github",
            ".git",
            "kernel-2027",
            "python-2027",
        )
        for bad in bad_inputs:
            with self.subTest(bad=bad):
                with self.assertRaises(fbg.BundleGuardError):
                    fbg.validate_target(ROOT, bad, "dist", require_lockfile=False)

    def test_04_build_output_traversal_and_hidden_segments_are_refused(self) -> None:
        for bad_out in ("", "..", "../escape", "dist/../../escape", ".git", "dist\\sub", "-out"):
            with self.subTest(bad_out=bad_out):
                with self.assertRaises(fbg.BundleGuardError):
                    fbg.validate_target(ROOT, "workspace-ui", bad_out, require_lockfile=True)

    def test_05_missing_lockfile_is_refused_when_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_frontend_fixture(repo, "app-ui", lockfile=False)
            with self.assertRaisesRegex(fbg.BundleGuardError, "package-lock.json is required"):
                fbg.validate_target(repo, "app-ui", "dist", require_lockfile=True)

    def test_06_symlinked_frontend_or_package_json_is_refused(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            outside = Path(tmp) / "outside"
            repo.mkdir()
            _init_frontend_fixture(outside, "real-ui")
            os.symlink(outside / "real-ui", repo / "linked-ui")
            with self.assertRaisesRegex(fbg.BundleGuardError, "symbolic link"):
                fbg.validate_target(repo, "linked-ui", "dist", require_lockfile=True)


class BundlePackagingAndSecurityTests(unittest.TestCase):
    def test_10_deterministic_tar_reproduces_identical_sha256_across_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            fe = _init_frontend_fixture(repo)
            tar_a = repo / "a.tar"
            tar_b = repo / "b.tar"
            report_a = fbg.package_bundle(repo, "app-ui", "dist", str(tar_a))
            # Mutate filesystem mtimes on source files — deterministic tar must ignore mtimes.
            os.utime(fe / "dist" / "index.html", (1_700_000_000, 1_700_000_000))
            os.utime(fe / "dist" / "assets" / "app.js", (1_800_000_000, 1_800_000_000))
            report_b = fbg.package_bundle(repo, "app-ui", "dist", str(tar_b))

            self.assertEqual(report_a["bundle_sha256"], report_b["bundle_sha256"])
            self.assertEqual(report_a["artifact_sha256"], report_b["artifact_sha256"])
            self.assertEqual(tar_a.read_bytes(), tar_b.read_bytes())
            self.assertEqual(report_a["file_count"], 3)

    def test_11_symlink_in_build_output_is_refused_before_packaging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            fe = _init_frontend_fixture(repo)
            secret_target = repo / "outside.txt"
            secret_target.write_text("sensitive-host-file\n", encoding="utf-8")
            os.symlink(secret_target, fe / "dist" / "leak.txt")
            with self.assertRaisesRegex(fbg.BundleGuardError, "symbolic link refused"):
                fbg.package_bundle(repo, "app-ui", "dist", str(repo / "out.tar"))
            self.assertFalse((repo / "out.tar").exists())

    def test_12_hardlink_in_build_output_is_refused_on_posix(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX st_nlink hardlink check")
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            fe = _init_frontend_fixture(repo)
            outside = repo / "host-config.txt"
            outside.write_text("host-only-data\n", encoding="utf-8")
            os.link(outside, fe / "dist" / "hardlinked.txt")
            with self.assertRaisesRegex(fbg.BundleGuardError, "hard-linked file refused"):
                fbg.package_bundle(repo, "app-ui", "dist", str(repo / "out.tar"))
            self.assertFalse((repo / "out.tar").exists())

    def test_13_baked_credentials_in_js_or_binary_are_refused_without_echoing_value(self) -> None:
        cases = (
            ("assets/app.js", f"const k = '{SYNTHETIC_GOOGLE}';\n", SYNTHETIC_GOOGLE),
            ("assets/vendor.js", f"export const x = '{SYNTHETIC_OPENAI}';\n", SYNTHETIC_OPENAI),
            ("assets/ci.js", f"const gh = '{SYNTHETIC_GH}';\n", SYNTHETIC_GH),
            ("assets/ material.txt", f"{SYNTHETIC_PEM}\nabc\n", SYNTHETIC_PEM),
            ("assets/font.woff2", b"\x00\x01\x02" + SYNTHETIC_GOOGLE.encode("ascii") + b"\xff", SYNTHETIC_GOOGLE),
        )
        for rel_file, payload, sensitive_text in cases:
            with self.subTest(rel_file=rel_file):
                with tempfile.TemporaryDirectory() as tmp:
                    repo = Path(tmp)
                    fe = _init_frontend_fixture(repo)
                    dest = fe / "dist" / rel_file.strip()
                    if isinstance(payload, bytes):
                        dest.write_bytes(payload)
                    else:
                        dest.write_text(payload, encoding="utf-8")
                    with self.assertRaises(fbg.BundleGuardError) as ctx:
                        fbg.package_bundle(repo, "app-ui", "dist", str(repo / "out.tar"))
                    # The error message must name the location/rule, NEVER the value itself.
                    self.assertNotIn(sensitive_text, str(ctx.exception))
                    self.assertFalse((repo / "out.tar").exists())

    def test_14_forbidden_files_and_missing_index_html_are_refused(self) -> None:
        for bad_name in (".env", ".env.production", "private.key", "cert.pem", ".npmrc"):
            with self.subTest(bad_name=bad_name):
                with tempfile.TemporaryDirectory() as tmp:
                    repo = Path(tmp)
                    fe = _init_frontend_fixture(repo)
                    (fe / "dist" / bad_name).write_text("DATA=1\n", encoding="utf-8")
                    with self.assertRaises(fbg.BundleGuardError):
                        fbg.package_bundle(repo, "app-ui", "dist", str(repo / "out.tar"))

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            fe = _init_frontend_fixture(repo)
            (fe / "dist" / "index.html").unlink()
            with self.assertRaisesRegex(fbg.BundleGuardError, "index.html"):
                fbg.package_bundle(repo, "app-ui", "dist", str(repo / "out.tar"))

    def test_15_independent_tar_verifier_rejects_crafted_symlink_and_traversal_archives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad_link_tar = Path(tmp) / "symlink.tar"
            with tarfile.open(bad_link_tar, "w", format=tarfile.GNU_FORMAT) as tf:
                idx = tarfile.TarInfo("./index.html")
                idx.size = 12
                tf.addfile(idx, io.BytesIO(b"<h1>ok</h1>\n"))
                sym = tarfile.TarInfo("./leak")
                sym.type = tarfile.SYMTYPE
                sym.linkname = "/etc/passwd"
                tf.addfile(sym)
            with self.assertRaisesRegex(fbg.BundleGuardError, "symbolic or hard link"):
                fbg.verify_tar_archive(str(bad_link_tar))

            bad_trav_tar = Path(tmp) / "traversal.tar"
            with tarfile.open(bad_trav_tar, "w", format=tarfile.GNU_FORMAT) as tf:
                evil = tarfile.TarInfo("../escape.html")
                evil.size = 4
                tf.addfile(evil, io.BytesIO(b"bad\n"))
            with self.assertRaises(fbg.BundleGuardError):
                fbg.verify_tar_archive(str(bad_trav_tar))

    def test_16_cli_manifest_and_step_summary_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_frontend_fixture(repo)
            out_tar = repo / "artifact.tar"
            manifest = repo / "manifest.json"
            summary = repo / "summary.md"

            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "frontend_bundle_guard.py"),
                    "--repo",
                    str(repo),
                    "package",
                    "--frontend-path",
                    "app-ui",
                    "--build-output",
                    "dist",
                    "--require-lockfile",
                    "--out",
                    str(out_tar),
                    "--manifest",
                    str(manifest),
                    "--step-summary",
                    str(summary),
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            parsed = json.loads(proc.stdout)
            saved = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(parsed["artifact_sha256"], saved["artifact_sha256"])
            self.assertIn(parsed["artifact_sha256"], summary.read_text(encoding="utf-8"))

            verify_proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "frontend_bundle_guard.py"),
                    "--repo",
                    str(repo),
                    "verify-tar",
                    "--tar",
                    str(out_tar),
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(verify_proc.returncode, 0, verify_proc.stderr)
            reverified = json.loads(verify_proc.stdout)
            self.assertEqual(reverified["artifact_sha256"], parsed["artifact_sha256"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
