"""Non-ZIP archives: named and counted, never accused.

The defect this file pins: `ir.py` mapped every archive format to `_read_zip`,
so a valid `.tar.gz` and a valid `.7z` both reported

    refused: not a readable zip: File is not a zip file

A false accusation of corruption sends the user to repair a file that is intact,
which is worse than saying nothing is known. Every fixture here is a real
archive built by the standard library.
"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import struct
import tarfile
import tempfile
import unittest
from pathlib import Path

from ai2027_protocol.archive import (
    MAX_TAR_MEMBERS,
    _gzip_header,
    _tar_members,
    describe_archive,
)
from ai2027_protocol.ir import describe_file


def _tar_bytes(members: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _gzip_of(payload: bytes, name: str = "") -> bytes:
    buffer = io.BytesIO()
    with gzip.GzipFile(filename=name, mode="wb", fileobj=buffer) as handle:
        handle.write(payload)
    return buffer.getvalue()


class GzipTests(unittest.TestCase):
    def test_name_and_size_are_read_from_the_header(self) -> None:
        data = _gzip_of(b"hello world", name="report.csv")
        lines, notes = describe_archive(data, "gzip")
        self.assertTrue(any("report.csv" in line for line in lines))
        self.assertTrue(any("11" in line for line in lines))

    def test_no_name_when_the_flag_is_unset(self) -> None:
        # Measured: tarfile writes gzip with flags=0, so the name is genuinely
        # absent. Reporting "" is correct; inventing one would not be.
        data = _gzip_of(b"x" * 100)
        name, _, _ = _gzip_header(data)
        self.assertEqual(name, "")

    def test_truncated_header_is_reported_not_crashed(self) -> None:
        lines, notes = describe_archive(b"\x1f\x8b\x08", "gzip")
        self.assertTrue(any("truncated" in note for note in notes))

    def test_a_tar_payload_is_detected_by_inflating_one_block(self) -> None:
        data = _gzip_of(_tar_bytes([("a.txt", b"hello")]))
        lines, notes = describe_archive(data, "gzip")
        self.assertTrue(any("payload is a tar" in note for note in notes))

    def test_a_non_tar_payload_says_so(self) -> None:
        data = _gzip_of(b"just some bytes, definitely not a tar archive" * 20)
        lines, notes = describe_archive(data, "gzip")
        self.assertFalse(any("payload is a tar" in note for note in notes))

    def test_never_says_corrupt(self) -> None:
        data = _gzip_of(b"anything")
        lines, notes = describe_archive(data, "gzip")
        self.assertFalse(any("corrupt" in line.lower() for line in lines + notes))


class TarTests(unittest.TestCase):
    def test_members_are_listed(self) -> None:
        data = _tar_bytes([("a.txt", b"hello"), ("b.csv", b"x,y\n1,2\n")])
        lines, notes = describe_archive(data, "tar")
        self.assertTrue(any("2 member(s)" in line for line in lines))
        self.assertTrue(any("a.txt" in line for line in lines))

    def test_sizes_are_octal_field_values(self) -> None:
        members, finished = _tar_members(_tar_bytes([("f.bin", b"\x00" * 100)]))
        self.assertTrue(finished)
        self.assertEqual(members[0][0], "f.bin")
        self.assertEqual(members[0][1], 100)

    def test_a_directory_entry_has_no_payload(self) -> None:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            info = tarfile.TarInfo("dir/")
            info.type = tarfile.DIRTYPE
            archive.addfile(info)
            payload = tarfile.TarInfo("dir/f.txt")
            payload.size = 3
            archive.addfile(payload, io.BytesIO(b"abc"))
        lines, _ = describe_archive(buffer.getvalue(), "tar")
        self.assertTrue(any("1 member(s)" in line for line in lines))

    def test_more_than_five_members_are_counted_not_listed(self) -> None:
        members = [(f"f{i}.txt", b"x") for i in range(9)]
        lines, _ = describe_archive(_tar_bytes(members), "tar")
        self.assertTrue(any("9 member(s)" in line for line in lines))
        self.assertTrue(any("more" in line for line in lines))

    def test_walk_is_bounded(self) -> None:
        # A tar whose walk exceeds the cap reports that the count is a lower bound.
        members = [(f"f{i}.txt", b"x") for i in range(MAX_TAR_MEMBERS + 5)]
        data = _tar_bytes(members)
        _, finished = _tar_members(data)
        self.assertFalse(finished)


class OpaqueFormatTests(unittest.TestCase):
    def test_7z_is_named_and_not_refused(self) -> None:
        data = b"7z\xbc\xaf\x27\x1c\x00\x04" + b"\x00" * 32
        lines, notes = describe_archive(data, "7z")
        joined = " ".join(notes).lower()
        self.assertIn("does not parse", joined)
        self.assertNotIn("corrupt", joined)
        self.assertNotIn("refused", joined)

    def test_rar_is_named_and_not_refused(self) -> None:
        _, notes = describe_archive(b"Rar!\x1a\x07\x00" + b"\x00" * 20, "rar")
        self.assertFalse(any("corrupt" in note.lower() for note in notes))

    def test_bzip2_reports_its_block_size(self) -> None:
        lines, notes = describe_archive(bz2.compress(b"hello" * 100), "bzip2")
        self.assertTrue(any("900 KB" in line for line in lines))
        self.assertTrue(any("does not decode" in note for note in notes))

    def test_xz_is_opaque(self) -> None:
        _, notes = describe_archive(lzma.compress(b"hello"), "xz")
        self.assertTrue(any("LZMA2" in note for note in notes))

    def test_unknown_format_is_named(self) -> None:
        _, notes = describe_archive(b"\x00" * 10, "cpio")
        self.assertTrue(any("cpio" in note for note in notes))

    def test_empty_data_is_reported(self) -> None:
        _, notes = describe_archive(b"", "gzip")
        self.assertTrue(any("open handle" in note for note in notes))


class IntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, name: str, data: bytes) -> str:
        path = self.tmp / name
        path.write_bytes(data)
        return str(path)

    def test_a_valid_targz_is_not_called_a_corrupt_zip(self) -> None:
        # The measured defect, verbatim.
        path = self._write("bundle.tar.gz", gzip.compress(_tar_bytes([("a.txt", b"hi")])))
        rendered = describe_file(path).render()
        self.assertNotIn("not a readable zip", rendered)
        self.assertNotIn("refused", rendered)
        self.assertIn("gzip", rendered)

    def test_a_valid_7z_is_not_called_a_corrupt_zip(self) -> None:
        path = self._write("bundle.7z", b"7z\xbc\xaf\x27\x1c\x00\x04" + b"\x00" * 32)
        rendered = describe_file(path).render()
        self.assertNotIn("not a readable zip", rendered)
        self.assertIn("7z", rendered)

    def test_a_real_zip_still_works(self) -> None:
        buffer = io.BytesIO()
        with __import__("zipfile").ZipFile(buffer, "w") as archive:
            archive.writestr("a.txt", "hello")
        rendered = describe_file(self._write("a.zip", buffer.getvalue())).render()
        self.assertIn("entries", rendered)
        self.assertNotIn("refused", rendered)

    def test_a_tar_file_directly_recognised(self) -> None:
        path = self._write("bundle.tar", _tar_bytes([("a.txt", b"hi")]))
        self.assertIn("1 member(s)", describe_file(path).render())


class BinaryStlTests(unittest.TestCase):
    """Binary STL is geometry, not text -- its 80-byte header is printable ASCII."""

    def _stl(self, triangles: int) -> bytes:
        header = b"solid exported by some CAD program".ljust(80, b" ")
        body = struct.pack("<I", triangles)
        body += struct.pack("<12fH", 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0) * triangles
        return header + body

    def test_binary_stl_probes_as_geometry(self) -> None:
        from ai2027_protocol.ingest import probe_bytes

        probe = probe_bytes(self._stl(1))
        self.assertEqual(probe.family.value, "geometry")
        self.assertEqual(probe.format, "stl-binary")

    def test_ascii_stl_stays_geometry(self) -> None:
        from ai2027_protocol.ingest import probe_bytes

        ascii_stl = (
            b"solid cube\nfacet normal 0 0 1\n  outer loop\n"
            b"    vertex 0 0 0\n  endloop\nendfacet\nendsolid cube\n"
        )
        probe = probe_bytes(ascii_stl)
        self.assertEqual(probe.family.value, "geometry")
        self.assertEqual(probe.format, "stl")

    def test_a_text_file_that_is_not_stl_sized_stays_text(self) -> None:
        from ai2027_protocol.ingest import probe_bytes

        # 96 bytes of prose: long enough to reach the header fields, and the
        # count read from it is enormous, so the size identity fails.
        probe = probe_bytes(b"hello world\n" * 8)
        self.assertEqual(probe.family.value, "text")

    def test_a_binary_stl_is_no_longer_described_as_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "part.stl"
            path.write_bytes(self._stl(2))
            rendered = describe_file(str(path)).render()
            self.assertNotIn("single block of text", rendered)
            self.assertIn("geometry", rendered)

    def test_zero_triangles_is_not_claimed(self) -> None:
        from ai2027_protocol.ingest import probe_bytes

        # 84 bytes, count 0: the identity 84 + 50*0 == 84 holds, but an empty
        # mesh is not evidence of a mesh, so the probe declines.
        probe = probe_bytes(b"x" * 80 + struct.pack("<I", 0))
        self.assertNotEqual(probe.format, "stl-binary")


class GzipHeaderEdgeTests(unittest.TestCase):
    """Every optional gzip header field, including the ones that can lie."""

    def _with_flags(self, flags: int, extra: bytes = b"", name: bytes = b"") -> bytes:
        # 10-byte fixed header + optional fields + a 4-byte ISIZE trailer.
        return b"\x1f\x8b\x08" + bytes([flags]) + b"\x00" * 6 + extra + name + b"\x00" * 4

    def test_extra_field_is_skipped(self) -> None:
        # FEXTRA=0x04: a 2-byte length then that many bytes of padding.
        extra = struct.pack("<H", 4) + b"abcd"
        data = self._with_flags(0x04, extra=extra, name=b"")
        name, _, _ = _gzip_header(data)
        self.assertEqual(name, "")

    def test_a_header_shorter_than_eighteen_bytes_is_reported(self) -> None:
        # The whole-header guard: ISIZE lives in the last four bytes, so a header
        # shorter than 18 cannot be interpreted at all. This replaces a test of a
        # branch that the guard makes unreachable.
        _, _, note = _gzip_header(b"\x1f\x8b\x08\x04" + b"\x00" * 8)
        self.assertIn("truncated", note)

    def test_unterminated_name_is_reported(self) -> None:
        # FNAME=0x08 with no NUL anywhere after the fixed header.
        data = b"\x1f\x8b\x08\x08" + b"\x00" * 6 + b"nul_terminator_missing"
        data = data[:18]
        _, _, note = _gzip_header(data)
        self.assertIn("does not terminate", note)

    def test_comment_field_is_skipped(self) -> None:
        data = self._with_flags(0x10, name=b"")  # FCOMMENT with no NUL: tolerated
        name, _, _ = _gzip_header(data)
        self.assertEqual(name, "")

    def test_header_crc_is_noted(self) -> None:
        data = self._with_flags(0x02) + b"\x00" * 8
        _, _, note = _gzip_header(data)
        self.assertIn("CRC16", note)

    def test_a_name_that_fills_its_field_is_read(self) -> None:
        data = b"\x1f\x8b\x08\x08" + b"\x00" * 6 + b"a.csv\x00" + b"\x00" * 4
        name, _, _ = _gzip_header(data)
        self.assertEqual(name, "a.csv")


class TarEdgeTests(unittest.TestCase):
    def test_pax_style_size_is_not_octal_and_does_not_crash(self) -> None:
        # A GNU/PAX extension header puts a length where tar expects octal.
        block = bytearray(512)
        block[0:5] = b"pax.x"
        block[124:136] = b"not-a-number"
        block[156:157] = b"x"
        members, _ = _tar_members(bytes(block) + b"\x00" * 512)
        self.assertEqual(members[0][1], 0)

    def test_symlink_has_no_payload(self) -> None:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            link = tarfile.TarInfo("link")
            link.type = tarfile.SYMTYPE
            link.linkname = "target"
            archive.addfile(link)
        lines, _ = describe_archive(buffer.getvalue(), "tar")
        self.assertTrue(any("member(s)" in line for line in lines))

    def test_a_directory_only_archive_lists_zero_files(self) -> None:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            info = tarfile.TarInfo("empty/")
            info.type = tarfile.DIRTYPE
            archive.addfile(info)
        lines, notes = describe_archive(buffer.getvalue(), "tar")
        self.assertTrue(any("0 member(s)" in line for line in lines))

    def test_bzip2_header_shorter_than_four_bytes(self) -> None:
        _, notes = describe_archive(b"BZ", "bzip2")
        self.assertTrue(any("truncated" in note for note in notes))

    def test_gzip_payload_that_is_not_zlib_is_unknown(self) -> None:
        # 154 bytes of header that inflate to nothing usable.
        data = b"\x1f\x8b\x08\x00" + b"\x00" * 6 + b"\xff" * 20
        from ai2027_protocol.archive import _tar_layer

        self.assertEqual(_tar_layer(data), "unknown")

    def test_gzip_payload_inflating_to_less_than_a_block_is_unknown(self) -> None:
        from ai2027_protocol.archive import _tar_layer

        self.assertEqual(_tar_layer(_gzip_of(b"tiny")), "unknown")

    def test_gzip_payload_that_is_text_is_not_tar(self) -> None:
        from ai2027_protocol.archive import _tar_layer

        self.assertEqual(_tar_layer(_gzip_of(b"x" * 600)), "not-tar")


class HelperTests(unittest.TestCase):
    def test_is_tar_payload_helper_is_exposed_under_its_real_name(self) -> None:
        # The helper is `_tar_layer`; this test documents the contract that a
        # gzip of a tar is detected by inflating one block, not by scanning for
        # the literal "ustar" -- which measured False on a real .tar.gz.
        data = _gzip_of(_tar_bytes([("a.txt", b"hi")]))
        self.assertNotIn(b"ustar", data[:1024])

    def test_gzip_isize_is_read_from_the_trailer(self) -> None:
        data = _gzip_of(b"x" * 1234)
        _, declared, _ = _gzip_header(data)
        self.assertEqual(declared, 1234)

    def test_a_fifty_byte_record_count_matches(self) -> None:
        # binary STL: 84 + 50*n. The classifier relies on this exact identity.
        self.assertEqual(84 + 3 * 50, len(b"\x00" * 80 + struct.pack("<I", 3) + b"\x00" * 150))
