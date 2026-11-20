"""Editing that cannot corrupt: the original is proven identical.

The claim this file tests is not "the edit works". It is "the edit worked and
the original is bit-for-bit what it was", proven by comparing SHA-256 before and
after, plus "a refused edit left nothing behind".
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from ai2027_protocol.edit import (
    MAX_EDIT_BYTES,
    EditError,
    EditResult,
    _churn_ratio,
    _line_delta,
    append_line,
    replace_text,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ReplaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.source = self.dir / "note.txt"
        self.source.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        self.original_sha = _sha(self.source)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_edit_lands_in_a_sibling_file(self) -> None:
        result = replace_text(self.source, "beta", "BETA")
        self.assertEqual(Path(result.target).name, "note.txt.new")
        self.assertEqual(self.source.read_text(), "alpha\nbeta\ngamma\n")
        self.assertEqual(Path(result.target).read_text(), "alpha\nBETA\ngamma\n")

    def test_the_original_is_untouched(self) -> None:
        result = replace_text(self.source, "beta", "BETA")
        self.assertTrue(result.source_untouched)
        self.assertEqual(_sha(self.source), self.original_sha)

    def test_the_target_differs_from_the_original(self) -> None:
        result = replace_text(self.source, "beta", "BETA")
        self.assertNotEqual(_sha(Path(result.target)), self.original_sha)

    def test_the_summary_reports_the_churn(self) -> None:
        result = replace_text(self.source, "beta", "BETA")
        self.assertIn("+1/-1 lines", result.summary)
        self.assertIn("original intact", result.summary)

    def test_only_one_occurrence_is_replaced_by_default(self) -> None:
        self.source.write_text("x\nx\nx\n", encoding="utf-8")
        result = replace_text(self.source, "x", "y")
        self.assertEqual(Path(result.target).read_text(), "y\nx\nx\n")

    def test_count_replaces_more(self) -> None:
        self.source.write_text("x\nx\nx\n", encoding="utf-8")
        result = replace_text(self.source, "x", "y", count=3)
        self.assertEqual(Path(result.target).read_text(), "y\ny\ny\n")

    def test_a_same_length_replacement_still_reports_churn(self) -> None:
        # Measured defect: the first version computed |len(new)-len(old)|/len(old),
        # so beta -> BETA reported "0.0% changed" beside "+1/-1 lines". Both
        # numbers were true and they contradicted each other.
        result = replace_text(self.source, "beta", "BETA")
        self.assertGreater(result.changed_ratio, 0.3)


class RefusalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _text(self, name: str, body: str) -> Path:
        path = self.dir / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_missing_search_text_is_refused(self) -> None:
        path = self._text("a.txt", "hello\n")
        with self.assertRaises(EditError):
            replace_text(path, "absent", "x")

    def test_empty_search_text_is_refused(self) -> None:
        path = self._text("a.txt", "hello\n")
        with self.assertRaises(EditError):
            replace_text(path, "", "x")

    def test_a_no_op_edit_is_refused(self) -> None:
        path = self._text("a.txt", "hello\n")
        with self.assertRaises(EditError):
            replace_text(path, "hello", "hello")

    def test_a_missing_file_is_refused(self) -> None:
        with self.assertRaises(EditError):
            replace_text(self.dir / "nope.txt", "a", "b")

    def test_binary_content_is_refused(self) -> None:
        path = self.dir / "b.dat"
        path.write_bytes(b"\x00\x01\x02")
        with self.assertRaises(EditError):
            replace_text(path, "a", "b")

    def test_invalid_utf8_is_refused(self) -> None:
        path = self.dir / "l.txt"
        path.write_bytes(b"caf\xe9")
        with self.assertRaises(EditError):
            replace_text(path, "a", "b")

    def test_zero_count_is_refused(self) -> None:
        path = self._text("a.txt", "hello\n")
        with self.assertRaises(EditError):
            replace_text(path, "hello", "x", count=0)

    def test_nothing_is_left_behind_after_a_refusal(self) -> None:
        path = self._text("a.txt", "hello\n")
        for bad in (("absent", "x"), ("", "x"), ("hello", "hello")):
            with self.assertRaises(EditError):
                replace_text(path, *bad)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["a.txt"])

    def test_an_oversized_file_is_refused(self) -> None:
        from unittest import mock

        path = self._text("big.txt", "hello\n")
        with mock.patch("ai2027_protocol.edit.MAX_EDIT_BYTES", 2):
            with self.assertRaises(EditError):
                replace_text(path, "hello", "x")
        self.assertEqual(MAX_EDIT_BYTES, 16 * 1024 * 1024)


class AppendTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_line_is_appended_to_the_copy(self) -> None:
        path = self.dir / "log.txt"
        path.write_text("first\n", encoding="utf-8")
        result = append_line(path, "second")
        self.assertEqual(Path(result.target).read_text(), "first\nsecond\n")
        self.assertEqual(path.read_text(), "first\n")

    def test_a_file_without_a_trailing_newline_is_terminated(self) -> None:
        path = self.dir / "log.txt"
        path.write_text("first", encoding="utf-8")
        result = append_line(path, "second")
        self.assertEqual(Path(result.target).read_text(), "first\nsecond\n")

    def test_the_original_stays_intact(self) -> None:
        path = self.dir / "log.txt"
        path.write_text("first\n", encoding="utf-8")
        before = _sha(path)
        result = append_line(path, "second")
        self.assertTrue(result.source_untouched)
        self.assertEqual(_sha(path), before)

    def test_a_missing_file_is_refused(self) -> None:
        with self.assertRaises(EditError):
            append_line(self.dir / "nope.txt", "x")


class HelperTests(unittest.TestCase):
    def test_line_delta_counts_a_pure_addition(self) -> None:
        added, removed = _line_delta("a\nb\n", "a\nb\nc\n")
        self.assertEqual((added, removed), (1, 0))

    def test_line_delta_counts_a_pure_removal(self) -> None:
        added, removed = _line_delta("a\nb\nc\n", "a\nb\n")
        self.assertEqual((added, removed), (0, 1))

    def test_line_delta_counts_a_replacement(self) -> None:
        added, removed = _line_delta("a\nb\n", "a\nc\n")
        self.assertEqual((added, removed), (1, 1))

    def test_duplicate_lines_are_counted_as_multisets(self) -> None:
        added, removed = _line_delta("x\nx\n", "x\nx\nx\n")
        self.assertEqual((added, removed), (1, 0))

    def test_churn_of_an_empty_original(self) -> None:
        self.assertEqual(_churn_ratio("", "x\n", 1, 0), 1.0)
        self.assertEqual(_churn_ratio("", "", 0, 0), 0.0)

    def test_churn_is_capped_at_one(self) -> None:
        self.assertEqual(_churn_ratio("a\n", "b\nc\nd\n", 3, 1), 1.0)

    def test_a_result_defaults_to_untouched(self) -> None:
        result = EditResult("a", "b", "same", "same", 0, 0, 0.0)
        self.assertTrue(result.source_untouched)

    def test_a_result_reports_a_modified_original(self) -> None:
        result = EditResult("a", "b", "one", "two", 0, 0, 0.0)
        self.assertFalse(result.source_untouched)
        self.assertIn("MODIFIED", result.summary)


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
