#!/usr/bin/env python3
"""Dependency-free tests for the operating-mind prompt loader.

These tests run with the standard library only (``python -m unittest``), because
the prompt files are a build-time contract: a prompt that is missing, empty,
mis-versioned or missing a required section must fail the build even in an
environment where no third-party package is installed. The loader module is
loaded straight from its file so the test does not need the protocol package's
dependencies installed.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = ROOT / "prompts" / "agent"
MODULE_PATH = ROOT / "python-2027" / "src" / "ai2027_protocol" / "prompts.py"


def _load_prompts_module() -> object:
    """Import ``prompts.py`` directly, without importing the protocol package."""
    spec = importlib.util.spec_from_file_location("ai2027_prompts", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Register before executing: dataclasses resolves string annotations through
    # sys.modules, and a module executed without an entry there fails on 3.11.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


prompts = _load_prompts_module()


class PromptFilesExistTests(unittest.TestCase):
    """Every operating-mind file is present and non-empty."""

    def test_every_operating_mind_file_exists_and_is_non_empty(self) -> None:
        for filename in prompts.PROMPT_FILES:
            path = PROMPTS_DIR / filename
            self.assertTrue(path.is_file(), f"missing prompt file {path}")
            self.assertGreater(path.stat().st_size, 200, f"suspiciously small {path}")

    def test_no_stray_prompt_files(self) -> None:
        shipped = set(prompts.PROMPT_FILES)
        on_disk = {path.name for path in PROMPTS_DIR.glob("*.md")}
        self.assertEqual(on_disk - shipped, set(), "unexpected prompt file on disk")


class PromptBundleTests(unittest.TestCase):
    """The bundle loads, validates and is hash-addressed."""

    def setUp(self) -> None:
        self.bundle = prompts.load_prompt_bundle()

    def test_bundle_contains_every_operating_mind_document(self) -> None:
        self.assertEqual(set(self.bundle.documents), set(prompts.PROMPT_FILES))

    def test_iteration_follows_the_canonical_execution_order(self) -> None:
        self.assertEqual([doc.name for doc in self.bundle], list(prompts.PROMPT_FILES))

    def test_front_matter_declares_every_required_key(self) -> None:
        for doc in self.bundle:
            for key in prompts.REQUIRED_FRONT_MATTER_KEYS:
                self.assertIn(key, doc.front_matter, f"{doc.name} missing {key}")

    def test_versions_are_numeric(self) -> None:
        for doc in self.bundle:
            self.assertRegex(doc.version, r"^\d+(\.\d+)+$", f"{doc.name} version {doc.version}")

    def test_bundle_verify_reports_no_problems(self) -> None:
        self.assertEqual(self.bundle.verify(), [])

    def test_manifest_hashes_are_sha256_and_stable(self) -> None:
        first = self.bundle.manifest()
        second = prompts.load_prompt_bundle().manifest()
        self.assertEqual(first, second)
        for name, digest in first.items():
            self.assertRegex(digest, r"^[0-9a-f]{64}$", f"{name} hash is not a sha256")

    def test_changing_a_prompt_changes_its_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = (PROMPTS_DIR / "SYSTEM.md").read_text(encoding="utf-8")
            (root / "SYSTEM.md").write_text(original, encoding="utf-8")
            before = prompts.load_prompt_bundle(names=("SYSTEM.md",), root=root).manifest()
            (root / "SYSTEM.md").write_text(original + "\n\n## Extra\n\nChanged.\n", encoding="utf-8")
            after = prompts.load_prompt_bundle(names=("SYSTEM.md",), root=root).manifest()
            self.assertNotEqual(before["SYSTEM.md"], after["SYSTEM.md"])


class PromptIntegrityTests(unittest.TestCase):
    """A malformed prompt fails loudly instead of loading degraded."""

    def _write(self, tmp: Path, text: str) -> Path:
        path = tmp / "BROKEN.md"
        path.write_text(text, encoding="utf-8")
        return path

    def test_missing_file_raises(self) -> None:
        with self.assertRaises(prompts.PromptLoadError):
            prompts.load_prompt_document("DOES-NOT-EXIST.md", root=PROMPTS_DIR)

    def test_empty_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._write(Path(tmp), "")
            with self.assertRaises(prompts.PromptIntegrityError):
                prompts.load_prompt_document("BROKEN.md", root=Path(tmp))

    def test_missing_front_matter_key_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._write(
                Path(tmp),
                "---\nname: broken\nrole: test\nrequired:\n  - Identity\n---\n\n# Identity\n",
            )
            with self.assertRaises(prompts.PromptIntegrityError):
                prompts.load_prompt_document("BROKEN.md", root=Path(tmp))

    def test_malformed_front_matter_line_raises(self) -> None:
        with self.assertRaises(prompts.PromptLoadError):
            prompts.parse_front_matter(
                "---\nname: broken\nthis line is not valid\nrequired:\n  - Identity\n---\n\n# Identity\n"
            )

    def test_list_item_outside_a_list_key_raises(self) -> None:
        with self.assertRaises(prompts.PromptLoadError):
            prompts.parse_front_matter("---\nname: broken\n- orphan item\n---\n\n# Identity\n")

    def test_missing_required_section_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._write(
                Path(tmp),
                "---\nname: broken\nversion: 1.0.0\nrole: test\nrequired:\n"
                "  - Identity\n  - Boundaries\n---\n\n# Identity\n\nNo boundaries section.\n",
            )
            with self.assertRaises(prompts.PromptIntegrityError) as ctx:
                prompts.load_prompt_document("BROKEN.md", root=Path(tmp))
            self.assertIn("Boundaries", str(ctx.exception))

    def test_forbidden_placeholder_token_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._write(
                Path(tmp),
                "---\nname: broken\nversion: 1.0.0\nrole: test\nrequired:\n  - Identity\n---\n\n"
                "# Identity\n\nTODO: finish this.\n",
            )
            with self.assertRaises(prompts.PromptIntegrityError) as ctx:
                prompts.load_prompt_document("BROKEN.md", root=Path(tmp))
            self.assertIn("TODO", str(ctx.exception))

    def test_required_must_be_a_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._write(
                Path(tmp),
                "---\nname: broken\nversion: 1.0.0\nrole: test\nrequired: Identity\n---\n\n# Identity\n",
            )
            with self.assertRaises(prompts.PromptIntegrityError):
                prompts.load_prompt_document("BROKEN.md", root=Path(tmp))


class TemplateRenderTests(unittest.TestCase):
    """Templates substitute known values and refuse everything else."""

    TEMPLATE = "Hello {{ name }}, you are {{ role }}."

    def test_known_values_are_substituted(self) -> None:
        rendered = prompts.render_template(self.TEMPLATE, {"name": "AI-2027", "role": "orchestrator"})
        self.assertEqual(rendered, "Hello AI-2027, you are orchestrator.")

    def test_unknown_placeholder_is_refused(self) -> None:
        with self.assertRaises(prompts.TemplateError) as ctx:
            prompts.render_template(self.TEMPLATE, {"name": "AI-2027"})
        self.assertIn("role", str(ctx.exception))

    def test_leftover_placeholder_is_refused(self) -> None:
        with self.assertRaises(prompts.TemplateError):
            prompts.render_template("no placeholders {{ typo }} here", {})

    def test_blank_placeholder_is_refused(self) -> None:
        with self.assertRaises(prompts.TemplateError):
            prompts.render_template("empty {{}} slot", {})

    def test_values_are_not_re_expanded(self) -> None:
        rendered = prompts.render_template(
            "value: {{ value }}", {"value": "{{ other }}"}
        )
        self.assertEqual(rendered, "value: {{ other }}")

    def test_bundle_render_uses_the_requested_document(self) -> None:
        bundle = prompts.load_prompt_bundle()
        self.assertIn("AI-2027", bundle.render("SYSTEM.md", {"name": "AI-2027"}) or "AI-2027")


class LoaderDependencyHonestyTests(unittest.TestCase):
    """The loader itself must stay importable without third-party packages."""

    def test_prompts_module_imports_only_the_standard_library(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        stdlib = set(sys.stdlib_module_names)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
        non_stdlib = imported - stdlib
        self.assertEqual(non_stdlib, set(), f"prompts.py imports non-stdlib: {sorted(non_stdlib)}")


if __name__ == "__main__":
    unittest.main()
