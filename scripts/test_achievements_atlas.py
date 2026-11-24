#!/usr/bin/env python3
"""Tests for the Achievement Atlas generator (stdlib only, no side effects)."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("achievements_atlas", HERE / "achievements_atlas.py")
assert SPEC and SPEC.loader
atlas = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(atlas)


class AtlasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = atlas.build_data()

    def test_every_card_is_well_formed_and_unique(self) -> None:
        cards = self.data["cards"]
        self.assertGreater(len(cards), 200)
        ids = [card["id"] for card in cards]
        self.assertEqual(len(ids), len(set(ids)), "duplicate achievement ids")
        for card in cards:
            self.assertIn(card["category"], atlas.CATEGORIES)
            self.assertTrue(card["title"])
            self.assertTrue(card["source"])
            self.assertTrue(card["url"])
            self.assertTrue(card["status"])
            self.assertIsInstance(card["bullets"], list)
            self.assertIn(card["evidence"]["level"], atlas.EVIDENCE_LABELS)
            self.assertTrue(card["evidence"]["basis"])

    def test_dot_github_paths_are_not_mangled(self) -> None:
        self.assertEqual(atlas.repo_path(".github/workflows/ci.yml"), ".github/workflows/ci.yml")
        self.assertEqual(atlas.source_url(".github/workflows/ci.yml"), "../.github/workflows/ci.yml")
        self.assertIn("/blob/HEAD/.github/workflows/ci.yml", atlas.github_url(".github/workflows/ci.yml"))

    def test_counts_match_cards(self) -> None:
        cards = self.data["cards"]
        self.assertEqual(sum(self.data["categoryCounts"].values()), len(cards))
        self.assertEqual(sum(self.data["statusCounts"].values()), len(cards))

    def test_html_is_self_contained(self) -> None:
        page = atlas.render_html(self.data)
        self.assertIn("const ATLAS = ", page)
        self.assertIn("data:font/woff2;base64,", page)
        self.assertIn("أطلس إنجازات AI-2027", page)
        self.assertNotIn("__ATLAS_DATA__", page)
        self.assertNotIn("__AR_FONT__", page)
        self.assertNotIn("__LAT_FONT__", page)

    def test_cli_check_detects_missing_file(self) -> None:
        import contextlib
        import io
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            with contextlib.redirect_stderr(io.StringIO()):
                code = atlas.main(["--check", "--output", str(Path(folder) / "missing.html")])
            self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
