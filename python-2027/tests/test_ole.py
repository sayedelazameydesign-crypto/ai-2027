"""OLE2 compound files: the directory names the document.

Every fixture is a real 512-byte CFB header plus real 128-byte directory
entries, because the whole point of the module is that those bytes *are* the
structure.

Three defects this file pins, all found by measuring:
  1. an `.xls` requires `Workbook` (BIFF8) **or** `Book` (BIFF5), and a
     whole-set test made every workbook report "no format known";
  2. notes printed twice because the IR re-rendered OleInfo's own;
  3. one directory sector = four entries, so a real document looked truncated.
"""

from __future__ import annotations

import io
import struct
import tempfile
import unittest
from pathlib import Path

from _ole_test_support import (
    ROOT,
    STORAGE,
    STREAM,
    _cfb,
    _cfb_difat_chain,
    _cfb_difat_loop,
    _cfb_long_directory,
    _cfb_noncontiguous_fat,
    _cfb_two_sectors,
    _cfb_v4,
    _ShortReadAt,
    _Unseekable,
)
from ai2027_protocol.adapters import Operation, default_registry
from ai2027_protocol.delivery import DeliveryForm, plan_delivery
from ai2027_protocol.ir import describe_file
from ai2027_protocol.ole import MAGIC, OleInfo, OleStream, read_ole, read_ole_handle
from ai2027_protocol.verify import verify_file


class IdentificationTests(unittest.TestCase):
    def test_word_document(self) -> None:
        info = read_ole(_cfb([ROOT, ("WordDocument", STREAM), ("1Table", STREAM)]))
        self.assertEqual(info.document_kind, "doc")

    def test_excel_biff8_workbook(self) -> None:
        self.assertEqual(read_ole(_cfb([ROOT, ("Workbook", STREAM)])).document_kind, "xls")

    def test_excel_biff5_book(self) -> None:
        # The regression: BIFF5 files carry `Book`, not `Workbook`. A whole-set
        # test (`required <= names`) demanded both and matched neither.
        self.assertEqual(read_ole(_cfb([ROOT, ("Book", STREAM)])).document_kind, "xls")

    def test_powerpoint(self) -> None:
        self.assertEqual(
            read_ole(_cfb([ROOT, ("PowerPoint Document", STREAM)])).document_kind, "ppt"
        )

    def test_outlook_message(self) -> None:
        self.assertEqual(
            read_ole(_cfb([ROOT, ("__properties_version1.0", STREAM)])).document_kind, "msg"
        )

    def test_visio_document(self) -> None:
        self.assertEqual(read_ole(_cfb([ROOT, ("VisioDocument", STREAM)])).document_kind, "vsd")

    def test_encrypted_office_file(self) -> None:
        info = read_ole(_cfb([ROOT, ("EncryptedPackage", STREAM), ("EncryptionInfo", STREAM)]))
        self.assertEqual(info.document_kind, "office-encrypted")
        self.assertTrue(info.encrypted)

    def test_unknown_streams_are_named_as_unknown(self) -> None:
        info = read_ole(_cfb([ROOT, ("SomethingElse", STREAM)]))
        self.assertEqual(info.document_kind, "")
        self.assertTrue(any("no document format" in note for note in info.notes))

    def test_metadata_streams_do_not_identify_a_format(self) -> None:
        info = read_ole(_cfb([ROOT, ("CompObj", STREAM), ("\x05SummaryInformation", STREAM)]))
        self.assertEqual(info.document_kind, "")
        self.assertEqual(info.content_streams, ())


class DirectoryTests(unittest.TestCase):
    def test_streams_are_listed_with_sizes(self) -> None:
        info = read_ole(_cfb([ROOT, ("WordDocument", STREAM)], on_disk=1234))
        content = info.content_streams
        self.assertEqual(len(content), 1)
        self.assertEqual(content[0].size, 1234)

    def test_root_is_not_a_content_stream(self) -> None:
        info = read_ole(_cfb([ROOT, ("Workbook", STREAM)]))
        self.assertNotIn("Root Entry", [stream.name for stream in info.content_streams])

    def test_total_stream_bytes(self) -> None:
        self.assertEqual(
            read_ole(_cfb([ROOT, ("Workbook", STREAM)], on_disk=500)).total_stream_bytes, 500
        )

    def test_storage_names_are_reported_when_nothing_matches(self) -> None:
        info = read_ole(_cfb([ROOT, ("SomeStorage", STORAGE)]))
        self.assertTrue(any("storages present" in note for note in info.notes))

    def test_the_directory_chain_spans_two_sectors(self) -> None:
        # Measured before the FAT walk: four entries, one sector. A real .doc
        # with nine streams looked like a four-stream document.
        info = read_ole(_cfb_two_sectors())
        self.assertEqual(len(info.streams), 7)
        self.assertIn("Stream4", [stream.name for stream in info.streams])
        self.assertEqual(info.document_kind, "doc")
        self.assertTrue(info.directory_complete)

    def test_noncontiguous_fat_sectors_are_followed_from_difat(self) -> None:
        info = read_ole(_cfb_noncontiguous_fat())
        self.assertTrue(info.directory_complete, info.notes)
        self.assertEqual(info.document_kind, "xls")

    def test_difat_chain_names_the_fat_sector_beyond_inline_entries(self) -> None:
        info = read_ole(_cfb_difat_chain())
        self.assertTrue(info.directory_complete, info.notes)
        self.assertEqual(info.document_kind, "doc")

    def test_difat_loop_is_reported_and_not_validated(self) -> None:
        info = read_ole(_cfb_difat_loop())
        self.assertFalse(info.directory_complete)
        self.assertTrue(any("DIFAT chain loops" in note for note in info.notes))

    def test_unknown_document_kind_is_still_a_complete_container(self) -> None:
        info = read_ole(_cfb([ROOT, ("SomethingElse", STREAM)]))
        self.assertTrue(info.directory_complete)
        self.assertTrue(info.readable)
        self.assertEqual(info.document_kind, "")


class RefusalTests(unittest.TestCase):
    def test_not_an_ole_file(self) -> None:
        info = read_ole(b"PK\x03\x04" + b"\x00" * 100)
        self.assertFalse(info.readable)
        self.assertIn("no OLE2 signature", info.render())

    def test_header_too_short(self) -> None:
        info = read_ole(MAGIC + b"\x00" * 100)
        self.assertFalse(info.readable)
        self.assertTrue(any("needs 512" in note for note in info.notes))

    def test_directory_sector_past_end_is_reported(self) -> None:
        broken = bytearray(_cfb([ROOT, ("Workbook", STREAM)]))
        broken[48:52] = struct.pack("<I", 2)  # FAT entry exists; sector data does not
        info = read_ole(bytes(broken))
        self.assertFalse(info.streams)
        self.assertTrue(any("past the end" in note for note in info.notes))

    def test_empty_directory(self) -> None:
        info = read_ole(_cfb([]))
        self.assertFalse(info.readable)
        self.assertTrue(any("empty or unreadable" in note for note in info.notes))

    def test_no_fat_sectors_is_not_a_valid_directory(self) -> None:
        data = bytearray(_cfb([ROOT, ("Workbook", STREAM)]))
        data[44:48] = struct.pack("<I", 0)
        info = read_ole(bytes(data))
        self.assertFalse(info.directory_complete)
        self.assertTrue(any("declares no FAT sectors" in note for note in info.notes))

    def test_invalid_byte_order_is_reported(self) -> None:
        data = bytearray(_cfb([ROOT, ("Workbook", STREAM)]))
        data[28:30] = struct.pack("<H", 0x1234)
        info = read_ole(bytes(data))
        self.assertFalse(info.directory_complete)
        self.assertTrue(any("unexpected byte order" in note for note in info.notes))

    def test_version_and_sector_shift_must_agree(self) -> None:
        data = bytearray(_cfb([ROOT, ("Workbook", STREAM)]))
        data[30:32] = struct.pack("<H", 12)
        info = read_ole(bytes(data))
        self.assertFalse(info.directory_complete)
        self.assertTrue(any("expects sector shift" in note for note in info.notes))

    def test_unknown_version_is_reported(self) -> None:
        data = bytearray(_cfb([ROOT, ("Workbook", STREAM)]))
        data[26:28] = struct.pack("<H", 9)
        self.assertTrue(
            any("unexpected major version" in note for note in read_ole(bytes(data)).notes)
        )

    def test_odd_sector_size_falls_back_to_512(self) -> None:
        data = bytearray(_cfb([ROOT, ("Workbook", STREAM)]))
        data[30:32] = struct.pack("<H", 7)
        info = read_ole(bytes(data))
        self.assertEqual(info.sector_size, 512)
        self.assertTrue(any("unexpected sector shift" in note for note in info.notes))

    def test_version_4_4096_byte_sectors(self) -> None:
        info = read_ole(_cfb_v4([ROOT, ("Workbook", STREAM)]))
        self.assertEqual(info.version, "v4")
        self.assertEqual(info.sector_size, 4096)
        self.assertEqual(info.document_kind, "xls")

    def test_a_self_referencing_chain_stops(self) -> None:
        # The FAT already sits at offset 1024 (sector 1). Overwriting its first
        # entry points the directory chain back at sector 0. Appending the bytes
        # instead -- the first version of this test -- left the original FAT in
        # place, so the chain ended normally and nothing looped.
        data = bytearray(_cfb([ROOT, ("SomethingElse", STREAM)]))
        data[1024:1028] = struct.pack("<I", 0)  # FAT[0] = 0: sector 0 points at itself
        info = read_ole(bytes(data))
        self.assertTrue(any("loops" in note or "itself" in note for note in info.notes), info.notes)

    def test_empty_info_render_is_honest(self) -> None:
        self.assertFalse(OleInfo().readable)
        self.assertEqual(OleStream(name="x", kind="stream").size, 0)


class MalformedDirectoryTests(unittest.TestCase):
    def test_invalid_entry_type_is_refused(self) -> None:
        data = bytearray(_cfb([ROOT, ("Workbook", STREAM)]))
        data[512 + 66] = 3
        info = read_ole(bytes(data))
        self.assertFalse(info.directory_complete)
        self.assertTrue(any("invalid type" in note for note in info.notes))

    def test_name_length_over_64_is_refused(self) -> None:
        data = bytearray(_cfb([ROOT, ("Workbook", STREAM)]))
        data[512 + 64 : 512 + 66] = struct.pack("<H", 66)
        info = read_ole(bytes(data))
        self.assertFalse(info.directory_complete)
        self.assertTrue(any("invalid name length" in note for note in info.notes))

    def test_odd_name_length_is_refused(self) -> None:
        data = bytearray(_cfb([ROOT, ("Workbook", STREAM)]))
        data[512 + 64 : 512 + 66] = struct.pack("<H", 3)
        info = read_ole(bytes(data))
        self.assertFalse(info.directory_complete)
        self.assertTrue(any("invalid name length" in note for note in info.notes))

    def test_missing_name_terminator_is_refused(self) -> None:
        data = bytearray(_cfb([ROOT, ("Workbook", STREAM)]))
        data[512 + 20 : 512 + 22] = b"\x01\x00"
        info = read_ole(bytes(data))
        self.assertFalse(info.directory_complete)
        self.assertTrue(any("NUL terminator" in note for note in info.notes))

    def test_invalid_utf16_is_refused(self) -> None:
        data = bytearray(_cfb([ROOT, ("Workbook", STREAM)]))
        data[512:514] = b"\x00\xd8"  # isolated high surrogate U+D800
        info = read_ole(bytes(data))
        self.assertFalse(info.directory_complete)
        self.assertTrue(any("invalid UTF-16LE" in note for note in info.notes))

    def test_empty_entry_name_is_refused(self) -> None:
        data = bytearray(_cfb([ROOT, ("Workbook", STREAM)]))
        data[512:514] = b"\x00\x00"
        data[512 + 64 : 512 + 66] = struct.pack("<H", 2)
        info = read_ole(bytes(data))
        self.assertFalse(info.directory_complete)
        self.assertTrue(any("empty name" in note for note in info.notes))

    def test_v3_stream_size_high_word_is_refused(self) -> None:
        data = bytearray(_cfb([ROOT, ("Workbook", STREAM)]))
        data[512 + 120 : 512 + 128] = struct.pack("<Q", (1 << 32) + 7)
        info = read_ole(bytes(data))
        self.assertFalse(info.directory_complete)
        self.assertTrue(any("v3 size high word" in note for note in info.notes))

    def test_directory_requires_exactly_one_root(self) -> None:
        info = read_ole(_cfb([("Workbook", STREAM)]))
        self.assertFalse(info.directory_complete)
        self.assertTrue(any("0 root entries" in note for note in info.notes))

    def test_duplicate_root_entries_are_refused(self) -> None:
        info = read_ole(_cfb([ROOT, ROOT]))
        self.assertFalse(info.directory_complete)
        self.assertTrue(any("2 root entries" in note for note in info.notes))


class BoundedWalkTests(unittest.TestCase):
    """The two bounds a crafted file would otherwise run past."""

    def test_a_fat_sector_past_the_end_is_not_read(self) -> None:
        # Four FAT sectors claimed, but only one DIFAT id is present and points
        # beyond the file. The reader refuses the truncated map; it does not
        # unpack non-existent bytes or pretend the directory chain ended.
        data = bytearray(_cfb([ROOT, ("Workbook", STREAM)]))
        data[44:48] = struct.pack("<I", 4)
        data[76:80] = struct.pack("<I", 999)
        info = read_ole(bytes(data))
        self.assertFalse(info.directory_complete)
        self.assertTrue(any("past the end" in note for note in info.notes))
        self.assertTrue(any("of 4 declared FAT sectors" in note for note in info.notes))

    def test_fat_sector_cap_is_reported(self) -> None:
        data = bytearray(_cfb([ROOT, ("Workbook", STREAM)]))
        data[44:48] = struct.pack("<I", 129)
        info = read_ole(bytes(data))
        self.assertFalse(info.directory_complete)
        self.assertTrue(any("capped at 128" in note for note in info.notes))

    def test_difat_sector_past_the_supplied_prefix_is_reported(self) -> None:
        data = bytearray(_cfb_difat_chain())
        data[68:72] = struct.pack("<I", 9999)
        info = read_ole(bytes(data))
        self.assertFalse(info.directory_complete)
        self.assertTrue(any("DIFAT sector 9999 is past" in note for note in info.notes))

    def test_duplicate_fat_sector_ids_are_reported(self) -> None:
        data = bytearray(_cfb([ROOT, ("Workbook", STREAM)]))
        data[44:48] = struct.pack("<I", 2)
        data[80:84] = struct.pack("<I", 1)
        info = read_ole(bytes(data))
        self.assertFalse(info.directory_complete)
        self.assertTrue(any("more than once" in note for note in info.notes))

    def test_a_chain_that_repeats_a_visited_sector_stops(self) -> None:
        # Sector 0 -> sector 1 -> sector 0; the FAT doubles as directory sector
        # 1 in this intentionally malformed chain.
        data = bytearray(_cfb([ROOT, ("SomethingElse", STREAM)]))
        fat_start = 512 + 512  # one directory sector, then the FAT
        data[fat_start : fat_start + 8] = struct.pack("<I", 1) + struct.pack("<I", 0)
        data += b"\x00" * 512
        info = read_ole(bytes(data))
        self.assertTrue(any("loops" in note or "itself" in note for note in info.notes), info.notes)

    def test_directory_sector_limit_is_enforced(self) -> None:
        info = read_ole(_cfb_long_directory())
        self.assertFalse(info.directory_complete)
        self.assertTrue(any("exceeds 1024 sectors" in note for note in info.notes))

    def test_free_sector_marker_stops_directory_chain(self) -> None:
        data = bytearray(_cfb([ROOT, ("Workbook", STREAM)]))
        data[1024:1028] = struct.pack("<I", 0xFFFFFFFF)
        info = read_ole(bytes(data))
        self.assertFalse(info.directory_complete)
        self.assertTrue(any("points at a free sector" in note for note in info.notes))

    def test_special_sector_marker_stops_directory_chain(self) -> None:
        data = bytearray(_cfb([ROOT, ("Workbook", STREAM)]))
        data[1024:1028] = struct.pack("<I", 0xFFFFFFFD)
        info = read_ole(bytes(data))
        self.assertFalse(info.directory_complete)
        self.assertTrue(any("special sector marker" in note for note in info.notes))

    def test_missing_fat_entry_is_reported(self) -> None:
        data = bytearray(_cfb([ROOT, ("Workbook", STREAM)]))
        data[48:52] = struct.pack("<I", 9999)
        info = read_ole(bytes(data))
        self.assertFalse(info.directory_complete)
        self.assertTrue(any("FAT has no entry" in note for note in info.notes))

    def test_initial_free_sector_marker_is_refused(self) -> None:
        data = bytearray(_cfb([ROOT, ("Workbook", STREAM)]))
        data[48:52] = struct.pack("<I", 0xFFFFFFFF)
        info = read_ole(bytes(data))
        self.assertFalse(info.directory_complete)
        self.assertTrue(any("special sector marker" in note for note in info.notes))

    def test_initial_end_of_chain_without_a_root_is_not_a_directory(self) -> None:
        data = bytearray(_cfb([ROOT, ("Workbook", STREAM)]))
        data[48:52] = struct.pack("<I", 0xFFFFFFFE)
        info = read_ole(bytes(data))
        self.assertFalse(info.directory_complete)
        self.assertTrue(any("0 root entries" in note for note in info.notes))


class RenderingTests(unittest.TestCase):
    def test_render_names_the_kind_and_the_streams(self) -> None:
        rendered = read_ole(_cfb([ROOT, ("WordDocument", STREAM), ("1Table", STREAM)])).render()
        self.assertIn("doc", rendered)
        self.assertIn("WordDocument", rendered)
        self.assertIn("2 stream(s)", rendered)

    def test_more_than_eight_streams_are_counted(self) -> None:
        streams = tuple(OleStream(name=f"S{i}", kind="stream", size=10) for i in range(11))
        rendered = OleInfo(version="v3", streams=streams, document_kind="doc").render()
        self.assertIn("… 3 more stream(s)", rendered)
        self.assertNotIn("S9", rendered)

    def test_the_encrypted_marker_is_rendered(self) -> None:
        info = read_ole(_cfb([ROOT, ("EncryptedPackage", STREAM)]))
        self.assertIn("encrypted", info.render().splitlines()[0])


class SeekOnlyTests(unittest.TestCase):
    def test_handle_reader_seeks_metadata_and_never_reads_payload(self) -> None:
        """The file handle sees only the header, FAT and directory sectors."""

        class ReadSpy(io.BytesIO):
            def __init__(self, data: bytes) -> None:
                super().__init__(data)
                self.ranges: list[tuple[int, int]] = []

            def read(self, size: int | None = None) -> bytes:
                start = self.tell()
                result = super().read(size)
                self.ranges.append((start, start + len(result)))
                return result

        data = bytearray(_cfb([ROOT, ("WordDocument", STREAM)], on_disk=4096))
        word_entry = 512 + 128
        data[word_entry + 116 : word_entry + 120] = struct.pack("<I", 2)
        data[word_entry + 120 : word_entry + 128] = struct.pack("<Q", 4096)
        payload_start = len(data)  # sector 2 begins immediately after the FAT
        data.extend(b"P" * 4096)

        handle = ReadSpy(bytes(data))
        handle.seek(23)
        info = read_ole_handle(handle)

        self.assertTrue(info.directory_complete, info.notes)
        self.assertEqual(handle.tell(), 23)  # caller's cursor is preserved
        self.assertTrue(handle.ranges)
        self.assertTrue(all(end <= payload_start for _start, end in handle.ranges), handle.ranges)

    def test_short_seekable_handle_reports_a_short_header(self) -> None:
        handle = io.BytesIO(MAGIC + b"\x00" * 100)
        info = read_ole_handle(handle)
        self.assertTrue(any("needs 512" in note for note in info.notes))

    def test_short_fat_read_fails_soft(self) -> None:
        handle = _ShortReadAt(_cfb([ROOT, ("Workbook", STREAM)]), 1024)
        info = read_ole_handle(handle)
        self.assertFalse(info.directory_complete)
        self.assertTrue(
            any("FAT sector 1 could not be read completely" in note for note in info.notes)
        )

    def test_short_directory_read_fails_soft(self) -> None:
        handle = _ShortReadAt(_cfb([ROOT, ("Workbook", STREAM)]), 512)
        info = read_ole_handle(handle)
        self.assertFalse(info.directory_complete)
        self.assertTrue(
            any("directory sector 0 could not be read completely" in note for note in info.notes)
        )

    def test_short_difat_read_fails_soft(self) -> None:
        offset = (110 + 1) * 512
        handle = _ShortReadAt(_cfb_difat_chain(), offset)
        info = read_ole_handle(handle)
        self.assertFalse(info.directory_complete)
        self.assertTrue(
            any("DIFAT sector 110 could not be read completely" in note for note in info.notes)
        )

    def test_unseekable_handle_is_an_explicit_refusal(self) -> None:
        info = read_ole_handle(_Unseekable(b"anything"))
        self.assertTrue(any("could not be read or sought" in note for note in info.notes))


class IntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_ole_adapter_claims_the_family(self) -> None:
        from ai2027_protocol.ingest import probe_bytes

        adapter = default_registry().for_probe(probe_bytes(MAGIC + b"\x00" * 20))
        assert adapter is not None
        self.assertEqual(adapter.name, "ole")
        self.assertTrue(adapter.supports(Operation.INSPECT))
        self.assertFalse(adapter.supports(Operation.SLICE))

    def test_describe_names_the_document(self) -> None:
        path = self.tmp / "old.doc"
        path.write_bytes(_cfb([ROOT, ("WordDocument", STREAM)]))
        rendered = describe_file(str(path)).render()
        self.assertIn("doc", rendered)
        self.assertNotIn("no reader for the ole family", rendered)

    def test_internal_streams_win_over_the_extension(self) -> None:
        path = self.tmp / "misleading.doc"
        path.write_bytes(_cfb([ROOT, ("Workbook", STREAM)]))
        self.assertEqual(describe_file(path).kind, "xls")

    def test_a_looping_directory_fails_validation(self) -> None:
        path = self.tmp / "loop.doc"
        data = bytearray(_cfb([ROOT, ("WordDocument", STREAM)]))
        data[1024:1028] = struct.pack("<I", 0)
        path.write_bytes(data)
        verdict = verify_file(path, kind="doc")
        self.assertFalse(verdict.ok)
        self.assertTrue(any("loops" in check.detail for check in verdict.failures))

    def test_verifier_rejects_a_claimed_kind_that_disagrees_with_directory(self) -> None:
        path = self.tmp / "workbook.doc"
        path.write_bytes(_cfb([ROOT, ("Workbook", STREAM)]))
        verdict = verify_file(path, kind="doc")
        self.assertFalse(verdict.ok)
        self.assertTrue(any("identifies xls" in check.detail for check in verdict.failures))

    def test_a_valid_doc_delivers_native(self) -> None:
        path = self.tmp / "old.doc"
        path.write_bytes(_cfb([ROOT, ("WordDocument", STREAM)]))
        described = describe_file(str(path))
        verification = verify_file(path, kind=described.kind)
        self.assertTrue(verification.ok, verification.render())
        self.assertTrue(any("payloads were not parsed" in note for note in verification.notes))
        plan = plan_delivery(described, verification=verification)
        self.assertIs(plan.primary.form, DeliveryForm.NATIVE)
        self.assertEqual(plan.primary.widget, "ole")

    def test_no_note_is_repeated(self) -> None:
        # The IR used to re-render OleInfo's notes, so an unknown-kind caveat
        # printed twice. A real unknown stream exercises the path.
        path = self.tmp / "unknown.ole"
        path.write_bytes(_cfb([ROOT, ("SomethingElse", STREAM)]))
        rendered = describe_file(str(path)).render()
        note = "the stream names match no document format this reader knows"
        self.assertEqual(rendered.count(note), 1, rendered)

    def test_an_empty_directory_fails_validation(self) -> None:
        path = self.tmp / "empty.doc"
        path.write_bytes(_cfb([]))
        verdict = verify_file(path, display_path="empty.doc", kind="ole")
        self.assertFalse(verdict.ok)
        self.assertTrue(any("ole directory" in check.name for check in verdict.failures))


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
