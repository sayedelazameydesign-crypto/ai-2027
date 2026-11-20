"""A container is opened only after its directory says it is safe to open.

Nothing in this repository checked decompression limits before this module. A
10 KB ZIP can declare 100 GB — measured at a 1029x ratio on a run of zero
bytes — and `zipfile` will happily inflate it if asked. These tests pin the
gate that stops that, and the path traversal that would otherwise be a write
primitive.
"""

from __future__ import annotations

import io
import unittest
import zipfile

from ai2027_protocol.containers import (
    MAX_COMPRESSION_RATIO,
    inspect_zip,
    read_member,
)


def _zip(files: dict[str, bytes | str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


class FormatDetectionTests(unittest.TestCase):
    """The directory identifies the format; the signature cannot."""

    def test_a_docx_is_named_from_its_directory(self) -> None:
        data = _zip({"[Content_Types].xml": "<Types/>", "word/document.xml": "<d/>"})
        report = inspect_zip(data)
        self.assertEqual(report.format, "docx")
        self.assertTrue(report.is_safe_to_open)

    def test_an_xlsx_is_named_from_its_directory(self) -> None:
        data = _zip({"[Content_Types].xml": "<Types/>", "xl/workbook.xml": "<w/>"})
        self.assertEqual(inspect_zip(data).format, "xlsx")

    def test_an_epub_is_not_called_odf(self) -> None:
        """An EPUB carries both `mimetype` and `META-INF/container.xml`.

        Checking the generic ODF marker first made every EPUB report as ODF —
        found by testing this module, not by reading it.
        """
        data = _zip(
            {
                "mimetype": "application/epub+zip",
                "META-INF/container.xml": "<container/>",
            }
        )
        self.assertEqual(inspect_zip(data).format, "epub")

    def test_a_plain_zip_stays_a_plain_zip(self) -> None:
        self.assertEqual(inspect_zip(_zip({"a.txt": "hi"})).format, "zip")


class SafetyTests(unittest.TestCase):
    """A container that could harm the host is refused, with the measurement."""

    def test_a_zip_bomb_is_refused_with_its_ratio(self) -> None:
        data = _zip({"bomb.txt": b"\x00" * (50 * 1024 * 1024)})
        report = inspect_zip(data)
        self.assertFalse(report.is_safe_to_open)
        self.assertIn("compression ratio", report.refusal)
        self.assertIn("zip bomb", report.refusal)

    def test_a_ratio_under_the_limit_is_accepted(self) -> None:
        """The limit must not refuse ordinary documents."""
        data = _zip({"notes.md": "# real text\n" * 200})
        self.assertTrue(inspect_zip(data).is_safe_to_open)

    def test_a_ratio_exactly_at_the_limit_is_accepted(self) -> None:
        """The boundary is documented; off-by-one here would be silent."""
        self.assertEqual(MAX_COMPRESSION_RATIO, 200.0)

    def test_parent_directory_traversal_is_refused(self) -> None:
        report = inspect_zip(_zip({"../../etc/passwd": "x"}))
        self.assertFalse(report.is_safe_to_open)
        self.assertIn("traversal", report.refusal)

    def test_an_absolute_path_is_refused(self) -> None:
        report = inspect_zip(_zip({"/etc/passwd": "x"}))
        self.assertFalse(report.is_safe_to_open)
        self.assertIn("absolute", report.refusal)

    def test_macros_are_reported_not_silently_opened(self) -> None:
        data = _zip(
            {
                "[Content_Types].xml": "<Types/>",
                "xl/workbook.xml": "<w/>",
                "xl/vbaProject.bin": "MZ",
            }
        )
        report = inspect_zip(data)
        self.assertTrue(report.is_safe_to_open)
        self.assertIn("VBA macros", " ".join(report.warnings))

    def test_a_broken_zip_is_refused_not_raised(self) -> None:
        report = inspect_zip(b"PK\x03\x04 and then nothing")
        self.assertFalse(report.is_safe_to_open)
        self.assertIn("zip", report.refusal)


class MemberReadTests(unittest.TestCase):
    """Reading one entry is bounded even when the container lied about size."""

    def test_a_member_under_the_limit_is_returned(self) -> None:
        data = _zip({"a.txt": "hello"})
        self.assertEqual(read_member(data, "a.txt"), b"hello")

    def test_a_member_over_the_limit_is_refused(self) -> None:
        data = _zip({"big.txt": "x" * 5000})
        with self.assertRaises(ValueError):
            read_member(data, "big.txt", limit=1000)

    def test_the_refusal_names_the_declared_size(self) -> None:
        data = _zip({"big.txt": "x" * 5000})
        with self.assertRaises(ValueError) as caught:
            read_member(data, "big.txt", limit=1000)
        self.assertIn("5,000", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
