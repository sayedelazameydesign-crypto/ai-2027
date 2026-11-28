#!/usr/bin/env python3
"""Tests for scripts/frontend_bundle_guard.py — hermetic, stdlib-only.

Run from repository root:
    python3 -m unittest scripts.test_frontend_bundle_guard -v
"""
from __future__ import annotations

import ast
import io
import json
import os
import re
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

    def test_07_package_json_with_lifecycle_hooks_is_refused(self) -> None:
        for hook in ("prebuild", "postbuild", "preinstall", "install", "postinstall", "prepare"):
            with self.subTest(hook=hook):
                with tempfile.TemporaryDirectory() as tmp:
                    repo = Path(tmp)
                    fe = _init_frontend_fixture(repo, "app-ui", lockfile=True)
                    (fe / "package.json").write_text(
                        json.dumps(
                            {
                                "name": "app-ui",
                                "private": True,
                                "scripts": {"build": "vite build", hook: "echo hook"},
                            }
                        ),
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(fbg.BundleGuardError, "forbidden lifecycle hook"):
                        fbg.validate_target(repo, "app-ui", "dist", require_lockfile=True)


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

    def test_17_large_asset_beyond_2mib_with_trailing_secret_is_refused(self) -> None:
        """Regression for P1: files > secret_scan.MAX_BYTES (2 MiB) must NEVER skip scanning."""
        beyond_limit = fbg.pattern_gate.MAX_BYTES + (512 * 1024)  # 2.5 MiB prefix before secret
        self.assertGreater(beyond_limit, 2 * 1024 * 1024)
        for ext, binary_mode in ((".js", False), (".wasm", True)):
            with self.subTest(ext=ext):
                with tempfile.TemporaryDirectory() as tmp:
                    repo = Path(tmp)
                    fe = _init_frontend_fixture(repo)
                    target_asset = fe / "dist" / "assets" / f"huge{ext}"
                    if binary_mode:
                        payload = (b"\x00\x61\x73\x6d" * (beyond_limit // 4)) + SYNTHETIC_GOOGLE.encode("ascii") + b"\x00"
                    else:
                        filler = (b"// benign bundle padding\n" * ((beyond_limit // 25) + 1))[:beyond_limit]
                        payload = filler + f"const leaked = '{SYNTHETIC_GOOGLE}';\n".encode("utf-8")
                    target_asset.write_bytes(payload)

                    with self.assertRaises(fbg.BundleGuardError) as ctx:
                        fbg.package_bundle(repo, "app-ui", "dist", str(repo / "out.tar"))
                    self.assertIn("credential-shaped pattern", str(ctx.exception))
                    self.assertNotIn(SYNTHETIC_GOOGLE, str(ctx.exception))
                    self.assertFalse((repo / "out.tar").exists())

    def test_18_secret_split_across_sliding_window_boundaries_is_refused(self) -> None:
        """Credentials straddling 1 MiB stride or 1 MiB + 16 KiB overlap boundaries must be caught."""
        boundary_offsets = (
            fbg.SCAN_WINDOW_BYTES - 12,
            fbg.SCAN_WINDOW_BYTES + fbg.SCAN_OVERLAP_BYTES - 12,
            (2 * fbg.SCAN_WINDOW_BYTES) - 7,
        )
        total_len = (2 * fbg.SCAN_WINDOW_BYTES) + 131_072  # ~2.125 MiB
        token_bytes = SYNTHETIC_OPENAI.encode("ascii")
        for offset in boundary_offsets:
            with self.subTest(offset=offset):
                buf = bytearray(b"a" * total_len)
                buf[offset - 1 : offset] = b"'"
                buf[offset : offset + len(token_bytes)] = token_bytes
                buf[offset + len(token_bytes) : offset + len(token_bytes) + 1] = b"'"
                hits = fbg._scan_asset_bytes(bytes(buf), "assets/chunk.js")
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]["rule"], "openai_key")

    def test_19_inline_allowlist_pragma_does_not_bypass_public_bundle_scan(self) -> None:
        """Minified single-line JS containing pragma comments must still block baked secrets."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            fe = _init_frontend_fixture(repo)
            minified = (
                "/* allowlist-secret */ /* pragma: allowlist secret */ "
                f"window.__CFG={{key:'{SYNTHETIC_GH}'}};\n"
            )
            (fe / "dist" / "assets" / "app.js").write_text(minified, encoding="utf-8")
            with self.assertRaisesRegex(fbg.BundleGuardError, "credential-shaped pattern"):
                fbg.package_bundle(repo, "app-ui", "dist", str(repo / "out.tar"))

    def test_20_oversized_asset_total_bundle_and_duplicate_tar_entries_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            fe = _init_frontend_fixture(repo)
            (fe / "dist" / "assets" / "big.bin").write_bytes(b"x" * 2048)

            with self.assertRaisesRegex(fbg.BundleGuardError, "exceeds maximum allowed size"):
                fbg.package_bundle(
                    repo, "app-ui", "dist", str(repo / "out.tar"), max_asset_bytes=1024
                )
            with self.assertRaisesRegex(fbg.BundleGuardError, "exceeds maximum allowed total size"):
                fbg.package_bundle(
                    repo,
                    "app-ui",
                    "dist",
                    str(repo / "out.tar"),
                    max_asset_bytes=4096,
                    max_bundle_bytes=1500,
                )

            dup_tar = repo / "dup.tar"
            with tarfile.open(dup_tar, "w", format=tarfile.GNU_FORMAT) as tf:
                for body in (b"<h1>first</h1>\n", b"<h1>second</h1>\n"):
                    ti = tarfile.TarInfo("./index.html")
                    ti.size = len(body)
                    ti.mtime = 0
                    tf.addfile(ti, io.BytesIO(body))
            with self.assertRaisesRegex(fbg.BundleGuardError, "duplicate member path"):
                fbg.verify_tar_archive(str(dup_tar))

    def test_21_benign_large_asset_beyond_2mib_packages_and_verifies_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            fe = _init_frontend_fixture(repo)
            benign_size = fbg.pattern_gate.MAX_BYTES + (256 * 1024)  # 2.25 MiB clean asset
            (fe / "dist" / "assets" / "vendor.js").write_bytes(b"var ok = 1;\n" * (benign_size // 12))
            out_tar = repo / "clean-large.tar"
            report = fbg.package_bundle(repo, "app-ui", "dist", str(out_tar))
            self.assertTrue(report["ok"])
            self.assertGreater(report["total_asset_bytes"], fbg.pattern_gate.MAX_BYTES)


def _extract_pr_trigger_paths(workflow_text: str) -> list[str]:
    lines = workflow_text.splitlines()
    in_pr = False
    in_paths = False
    pr_indent = 0
    paths_indent = 0
    collected: list[str] = []

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if not in_pr:
            if stripped == "pull_request:":
                in_pr = True
                pr_indent = indent
            continue
        if indent <= pr_indent:
            break
        if not in_paths:
            if stripped == "paths:":
                in_paths = True
                paths_indent = indent
            continue
        if indent <= paths_indent:
            break
        if stripped.startswith("- "):
            val = stripped[2:].strip().strip('"').strip("'")
            collected.append(val)
    return collected


def _matches_gha_path_filter(repo_rel_posix: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if pat.endswith("/**"):
            prefix = pat[:-3].rstrip("/")
            if repo_rel_posix == prefix or repo_rel_posix.startswith(prefix + "/"):
                return True
        else:
            rx = "^" + re.escape(pat).replace(r"\*\*", ".*").replace(r"\*", "[^/]*") + "$"
            if re.match(rx, repo_rel_posix):
                return True
    return False


class WorkflowTriggerAndLifecycleTests(unittest.TestCase):
    def test_30_workflow_disables_lifecycle_scripts_for_both_ci_and_build(self) -> None:
        wf_text = (ROOT / ".github" / "workflows" / "deploy-frontend.yml").read_text(encoding="utf-8")
        active = "\n".join(
            line for line in wf_text.splitlines() if not line.lstrip().startswith("#")
        )
        self.assertIn('npm --prefix "$FRONTEND_PATH" ci --ignore-scripts', active)
        self.assertIn('npm --prefix "$FRONTEND_PATH" --ignore-scripts run build', active)
        self.assertNotIn('npm --prefix "$FRONTEND_PATH" run build', active)

    def test_31_pull_request_paths_cover_all_ast_discovered_guard_dependencies(self) -> None:
        wf_text = (ROOT / ".github" / "workflows" / "deploy-frontend.yml").read_text(encoding="utf-8")
        patterns = _extract_pr_trigger_paths(wf_text)
        self.assertTrue(patterns, "deploy-frontend.yml must declare on.pull_request.paths")

        guard_src = (ROOT / "scripts" / "frontend_bundle_guard.py").read_text(encoding="utf-8")
        tree = ast.parse(guard_src, filename="scripts/frontend_bundle_guard.py")
        stdlib_names = set(sys.stdlib_module_names) | {"__future__"}
        local_modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top not in stdlib_names:
                        local_modules.add(top)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                if top not in stdlib_names:
                    local_modules.add(top)

        module_to_repo_paths: dict[str, tuple[str, ...]] = {
            "kernel2027": ("kernel-2027/kernel2027/__init__.py", "kernel-2027/kernel2027/contracts.py"),
            "secret_scan": ("scripts/secret_scan.py", "scripts/test_secret_scan.py"),
        }
        for mod in sorted(local_modules):
            self.assertIn(
                mod,
                module_to_repo_paths,
                f"unmapped non-stdlib import '{mod}' in frontend_bundle_guard.py; add to pull_request.paths and test map",
            )
            for dep_path in module_to_repo_paths[mod]:
                self.assertTrue((ROOT / dep_path).is_file(), f"missing dependency file {dep_path}")
                self.assertTrue(
                    _matches_gha_path_filter(dep_path, patterns),
                    f"dependency '{dep_path}' is NOT covered by deploy-frontend.yml pull_request.paths: {patterns}",
                )

        required_paths = (
            "workspace-ui/package.json",
            "workspace-ui/src/App.tsx",
            ".github/workflows/deploy-frontend.yml",
            "scripts/frontend_bundle_guard.py",
            "scripts/test_frontend_bundle_guard.py",
            "scripts/workflow_secret_audit.py",
            "scripts/test_workflow_secret_audit.py",
        )
        for req in required_paths:
            self.assertTrue(
                _matches_gha_path_filter(req, patterns),
                f"required path '{req}' is NOT covered by deploy-frontend.yml pull_request.paths",
            )
        self.assertFalse(_matches_gha_path_filter("README.md", patterns))


if __name__ == "__main__":
    unittest.main(verbosity=2)
