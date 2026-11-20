"""PDF structure read from the file, with every refusal made explicit.

The interesting cases here are not "can it read a page count" but the three
refusals: a compressed-object PDF where the count is *not* readable, a file
whose `/Count` contradicts its own page objects, and a header that is not a PDF.
Each one must produce a sentence a caller can act on, never a guessed number.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai2027_protocol.adapters import Operation, default_registry
from ai2027_protocol.delivery import DeliveryForm, plan_delivery
from ai2027_protocol.ir import describe_file
from ai2027_protocol.pdf import MAX_SCAN_BYTES, PdfInfo, read_pdf
from ai2027_protocol.verify import verify_file


def _pdf(body: str, version: str = "1.7") -> bytes:
    """A minimal well-formed PDF wrapper around whatever objects a test needs."""
    return f"%PDF-{version}\n{body}\n%%EOF".encode()


class PageCountTests(unittest.TestCase):
    def test_count_and_page_objects_agree(self) -> None:
        data = _pdf(
            "2 0 obj <</Type/Pages/Count 2/Kids[3 0 R 4 0 R]>> endobj\n"
            "3 0 obj <</Type/Page/MediaBox[0 0 612 792]>> endobj\n"
            "4 0 obj <</Type/Page/MediaBox[0 0 612 792]>> endobj"
        )
        info = read_pdf(data)
        self.assertEqual(info.pages, 2)
        self.assertEqual(info.page_objects, 2)
        self.assertFalse(info.inconsistent_page_count)

    def test_count_found_when_declared_after_type(self) -> None:
        info = read_pdf(_pdf("1 0 obj <</Count 7/Type/Pages>> endobj"))
        self.assertEqual(info.pages, 7)

    def test_disagreement_is_reported_not_resolved(self) -> None:
        data = _pdf(
            "2 0 obj <</Type/Pages/Count 12/Kids[3 0 R]>> endobj\n"
            "3 0 obj <</Type/Page/MediaBox[0 0 595 842]>> endobj"
        )
        info = read_pdf(data)
        self.assertEqual(info.pages, 12)
        self.assertEqual(info.page_objects, 1)
        self.assertTrue(info.inconsistent_page_count)
        self.assertIn("disagrees with itself", info.render())

    def test_pages_object_is_not_counted_as_a_page(self) -> None:
        # `/Type /Pages` must not match the page-object pattern.
        info = read_pdf(_pdf("2 0 obj <</Type/Pages/Count 0>> endobj"))
        self.assertEqual(info.pages, 0)
        self.assertIsNone(info.page_objects)


class CompressionTests(unittest.TestCase):
    def test_object_streams_are_named_not_decompressed(self) -> None:
        data = _pdf(
            "1 0 obj <</Type/ObjStm/N 40/First 300>> stream\nendstream endobj",
        )
        info = read_pdf(data)
        self.assertTrue(info.compressed_objects)
        self.assertIsNone(info.pages)
        self.assertTrue(any("decompressor" in note for note in info.notes))

    def test_missing_count_is_reported_when_readable(self) -> None:
        info = read_pdf(_pdf("1 0 obj <</Type/Catalog>> endobj"))
        self.assertIsNone(info.pages)
        self.assertTrue(any("no /Count" in note for note in info.notes))


class PageSizeTests(unittest.TestCase):
    def test_a4_is_recognised(self) -> None:
        info = read_pdf(_pdf("1 0 obj <</MediaBox[0 0 595.276 841.89]>> endobj"))
        self.assertEqual(info.page_size, "A4")

    def test_us_letter_is_recognised(self) -> None:
        info = read_pdf(_pdf("1 0 obj <</MediaBox[0 0 612 792]>> endobj"))
        self.assertEqual(info.page_size, "US Letter")

    def test_other_sizes_report_dimensions(self) -> None:
        info = read_pdf(_pdf("1 0 obj <</MediaBox[0 0 200 400]>> endobj"))
        self.assertEqual(info.page_size, "200×400pt")

    def test_negative_origin_is_handled(self) -> None:
        info = read_pdf(_pdf("1 0 obj <</MediaBox[-10 -10 585 832]>> endobj"))
        self.assertEqual((info.page_width, info.page_height), (595.0, 842.0))


class MetadataTests(unittest.TestCase):
    def test_ascii_metadata(self) -> None:
        info = read_pdf(_pdf("5 0 obj <</Title(Quarterly report)/Author(A. Engineer)>> endobj"))
        self.assertIn(("title", "Quarterly report"), info.metadata)
        self.assertIn(("author", "A. Engineer"), info.metadata)

    def test_utf16be_arabic_title(self) -> None:
        payload = "تقرير الربع".encode("utf-16-be")
        data = b"%PDF-1.7\n5 0 obj <</Title(\xfe\xff" + payload + b")>> endobj\n%%EOF"
        info = read_pdf(data)
        self.assertIn(("title", "تقرير الربع"), info.metadata)

    def test_unreadable_string_is_named_not_replaced(self) -> None:
        # Bytes that are neither UTF-16 nor UTF-8 must not become U+FFFD soup.
        data = b"%PDF-1.7\n5 0 obj <</Title(\xfe\xff\x00)>> endobj\n%%EOF"
        info = read_pdf(data)
        self.assertTrue(any("unreadable" in value for _, value in info.metadata))

    def test_unknown_keys_are_not_dumped(self) -> None:
        info = read_pdf(_pdf("5 0 obj <</Keywords(noise)/Title(kept)>> endobj"))
        self.assertEqual([key for key, _ in info.metadata], ["title"])


class RefusalTests(unittest.TestCase):
    def test_not_a_pdf(self) -> None:
        info = read_pdf(b"hello world")
        self.assertFalse(info.readable)
        self.assertIn("no %PDF- header", info.render())

    def test_scan_budget_is_declared(self) -> None:
        big = _pdf("1 0 obj <</Type/Pages/Count 3>> endobj") + b" " * MAX_SCAN_BYTES
        info = read_pdf(big)
        self.assertTrue(info.truncated_scan)
        self.assertIn("scan stopped", info.render())

    def test_version_is_read(self) -> None:
        self.assertEqual(read_pdf(_pdf("", version="2.0")).version, "2.0")

    def test_empty_info_is_not_readable(self) -> None:
        self.assertFalse(PdfInfo().readable)


class IntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_pdf_adapter_claims_the_family(self) -> None:
        from ai2027_protocol.ingest import probe_bytes

        adapter = default_registry().for_probe(probe_bytes(b"%PDF-1.7\n"))
        self.assertIsNotNone(adapter)
        assert adapter is not None
        self.assertEqual(adapter.name, "pdf")
        self.assertTrue(adapter.supports(Operation.RENDER))

    def test_describe_file_reports_the_page_count(self) -> None:
        path = self.tmp / "doc.pdf"
        path.write_bytes(
            _pdf(
                "2 0 obj <</Type/Pages/Count 1>> endobj\n"
                "3 0 obj <</Type/Page/MediaBox[0 0 595 842]>> endobj"
            )
        )
        rendered = describe_file(str(path)).render()
        self.assertIn("1 page(s)", rendered)
        self.assertNotIn("no reader for the pdf family", rendered)

    def test_valid_pdf_delivers_native(self) -> None:
        path = self.tmp / "doc.pdf"
        path.write_bytes(
            _pdf(
                "2 0 obj <</Type/Pages/Count 1>> endobj\n"
                "3 0 obj <</Type/Page/MediaBox[0 0 595 842]>> endobj"
            )
        )
        described = describe_file(str(path))
        plan = plan_delivery(described, verification=verify_file(path, kind=described.kind))
        self.assertIs(plan.primary.form, DeliveryForm.NATIVE)
        self.assertEqual(plan.primary.widget, "pdf")

    def test_self_contradicting_pdf_fails_validation(self) -> None:
        path = self.tmp / "bad.pdf"
        path.write_bytes(
            _pdf(
                "2 0 obj <</Type/Pages/Count 12>> endobj\n"
                "3 0 obj <</Type/Page/MediaBox[0 0 595 842]>> endobj"
            )
        )
        described = describe_file(str(path))
        verdict = verify_file(path, display_path="bad.pdf", kind=described.kind)
        self.assertFalse(verdict.ok)
        self.assertTrue(any("page count" in check.name for check in verdict.failures))
        plan = plan_delivery(described, verification=verdict)
        self.assertIs(plan.primary.form, DeliveryForm.REPORT)

    def test_headerless_pdf_fails_validation(self) -> None:
        path = self.tmp / "fake.pdf"
        path.write_bytes(b"not a pdf at all")
        verdict = verify_file(path, display_path="fake.pdf", kind="pdf")
        self.assertFalse(verdict.ok)


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()


class RenderingTests(unittest.TestCase):
    """The rendered line is what a caller reads; it must carry every caveat."""

    def test_compression_is_named_in_the_render(self) -> None:
        info = read_pdf(_pdf("1 0 obj <</Type/ObjStm/N 5>> stream endstream endobj"))
        self.assertIn("compressed object streams", info.render())

    def test_metadata_is_rendered_key_by_key(self) -> None:
        info = read_pdf(_pdf("5 0 obj <</Title(Report)/Author(Eng)>> endobj"))
        rendered = info.render()
        self.assertIn("title: Report", rendered)
        self.assertIn("author: Eng", rendered)

    def test_latin1_fallback_is_readable_not_dropped(self) -> None:
        # Bytes that are not UTF-8 but every one maps to a character: a reader
        # can show this, so calling it "unreadable" would be needlessly coy.
        data = b"%PDF-1.7\n5 0 obj <</Title(Caf\xe9)>> endobj\n%%EOF"
        info = read_pdf(data)
        self.assertIn(("title", "Café"), info.metadata)

    def test_pages_of_zero_still_reads(self) -> None:
        info = read_pdf(_pdf("1 0 obj <</Type/Pages/Count 0>> endobj"))
        self.assertEqual(info.pages, 0)
        self.assertIn("0 page(s)", info.render())

    def test_duplicate_metadata_key_is_taken_once(self) -> None:
        data = _pdf("5 0 obj <</Title(First)>> endobj\n6 0 obj <</Title(Second)>> endobj")
        titles = [value for key, value in read_pdf(data).metadata if key == "title"]
        self.assertEqual(titles, ["First"])

    def test_truncated_scan_note_uses_the_constant(self) -> None:
        info = read_pdf(_pdf("x") + b" " * MAX_SCAN_BYTES)
        self.assertIn(f"{MAX_SCAN_BYTES:,}", info.render())
