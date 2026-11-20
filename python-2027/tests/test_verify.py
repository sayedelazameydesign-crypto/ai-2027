"""Layer 5: an artifact is done when an independent check says so.

The tests build corrupt artifacts on purpose, because the failure this layer
exists to catch is a file that *looks* fine: a docx is a ZIP, so a valid
directory made the container inspector report `safe=True` on a body whose bytes
were wrong. Every corruption here is produced by flipping a real byte inside a
real member, not by truncating (truncation is the easy case).
"""

from __future__ import annotations

import io
import random
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from ai2027_protocol.tools import ToolOutcome
from ai2027_protocol.verify import MAX_VERIFY_BYTES, Check, Verification, verify_file


def _docx_body(seed: int = 5, size: int = 4000) -> bytes:
    """A docx whose body is incompressible enough to have a real deflate stream."""
    # Not cryptographic: this only needs to be incompressible enough that the
    # member has a real deflate stream to corrupt. noqa is the honest label.
    generator = random.Random(seed)  # noqa: S311
    text = "".join(generator.choice("abcdefg ") for _ in range(size))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", text)
    return buffer.getvalue()


def _corrupt_member(data: bytes, member: str = "word/document.xml") -> bytes:
    """Flip one byte inside the member's compressed payload."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        info = next(item for item in archive.infolist() if item.filename == member)
    offset = info.header_offset + 30 + len(info.filename) + info.compress_size // 2
    corrupted = bytearray(data)
    corrupted[offset] ^= 0xFF
    return bytes(corrupted)


def _parquet(metadata: int = 126) -> bytes:
    meta = b"SCHEMA" + b"\x00" * (metadata - 6)
    return b"PAR1" + b"\x00" * 300 + meta + struct.pack("<I", len(meta)) + b"PAR1"


class ZipValidityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_valid_docx_passes(self) -> None:
        path = self.tmp / "good.docx"
        path.write_bytes(_docx_body())
        verdict = verify_file(path, display_path="good.docx", kind="docx")
        self.assertTrue(verdict.ok, verdict.render())
        self.assertTrue(verdict.sha256)
        self.assertTrue(any(check.name == "member CRCs" for check in verdict.checks))

    def test_one_flipped_byte_fails(self) -> None:
        # The whole point: the directory is still perfect and the body is not.
        path = self.tmp / "bad.docx"
        path.write_bytes(_corrupt_member(_docx_body()))
        verdict = verify_file(path, display_path="bad.docx", kind="docx")
        self.assertFalse(verdict.ok)
        self.assertTrue(verdict.failures)

    def test_missing_required_part_fails(self) -> None:
        path = self.tmp / "partial.docx"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
        path.write_bytes(buffer.getvalue())
        verdict = verify_file(path, display_path="partial.docx", kind="docx")
        self.assertFalse(verdict.ok)
        self.assertTrue(any("word/document.xml is missing" in c.detail for c in verdict.failures))

    def test_xlsx_checks_its_own_part(self) -> None:
        path = self.tmp / "book.xlsx"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("xl/workbook.xml", "<workbook/>")
        path.write_bytes(buffer.getvalue())
        verdict = verify_file(path, display_path="book.xlsx", kind="xlsx")
        self.assertTrue(verdict.ok, verdict.render())

    def test_plain_zip_needs_no_ooxml_part(self) -> None:
        path = self.tmp / "plain.zip"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("a.txt", "hello")
        path.write_bytes(buffer.getvalue())
        verdict = verify_file(path, display_path="plain.zip", kind="zip")
        self.assertTrue(verdict.ok, verdict.render())

    def test_truncated_archive_fails_cleanly(self) -> None:
        path = self.tmp / "cut.zip"
        path.write_bytes(_docx_body()[:40])
        verdict = verify_file(path, display_path="cut.zip", kind="zip")
        self.assertFalse(verdict.ok)


class ParquetValidityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_footer_magic_and_length(self) -> None:
        path = self.tmp / "ok.parquet"
        path.write_bytes(_parquet())
        verdict = verify_file(path, display_path="ok.parquet", kind="parquet")
        self.assertTrue(verdict.ok, verdict.render())
        names = {check.name for check in verdict.checks}
        self.assertEqual(names, {"exists", "header magic", "footer magic", "footer length"})

    def test_lying_footer_length_fails(self) -> None:
        path = self.tmp / "lie.parquet"
        body = bytearray(_parquet())
        body[-8:-4] = struct.pack("<I", 999_999)
        path.write_bytes(bytes(body))
        verdict = verify_file(path, display_path="lie.parquet", kind="parquet")
        self.assertFalse(verdict.ok)

    def test_too_short_to_be_parquet(self) -> None:
        path = self.tmp / "tiny.parquet"
        path.write_bytes(b"PAR1")
        verdict = verify_file(path, display_path="tiny.parquet", kind="parquet")
        self.assertFalse(verdict.ok)


class TextValidityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_utf8_text_passes(self) -> None:
        path = self.tmp / "a.md"
        path.write_text("تقرير\n" * 100, encoding="utf-8")
        verdict = verify_file(path, display_path="a.md", kind="text")
        self.assertTrue(verdict.ok, verdict.render())

    def test_binary_labelled_text_fails(self) -> None:
        path = self.tmp / "liar.txt"
        path.write_bytes(bytes(range(256)) * 4)
        verdict = verify_file(path, display_path="liar.txt", kind="text")
        self.assertFalse(verdict.ok)

    def test_multibyte_character_across_a_chunk_boundary(self) -> None:
        # The regression: decoding each 1 MB chunk alone reported a valid
        # Arabic file as invalid at offset 1,048,575.
        path = self.tmp / "edge.md"
        path.write_text("a" * (1024 * 1024 - 1) + "م" + "b" * 100, encoding="utf-8")
        verdict = verify_file(path, display_path="edge.md", kind="text")
        self.assertTrue(verdict.ok, verdict.render())

    def test_empty_file_is_not_valid(self) -> None:
        path = self.tmp / "zero.md"
        path.write_bytes(b"")
        verdict = verify_file(path, display_path="zero.md", kind="text")
        self.assertFalse(verdict.ok)

    def test_missing_file_is_not_valid_and_names_why(self) -> None:
        verdict = verify_file(self.tmp / "absent.md", display_path="absent.md", kind="text")
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.failures[0].name, "exists")


class HonestyTests(unittest.TestCase):
    """Presence must never render as validity."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_unchecked_family_is_not_a_pass(self) -> None:
        path = self.tmp / "frame.glb"
        path.write_bytes(b"glTF" + b"\x00" * 60)
        verdict = verify_file(path, display_path="frame.glb", kind="glb")
        self.assertFalse(verdict.ok)
        self.assertTrue(any("not textual" in c.detail for c in verdict.failures))

    def test_no_checks_render_says_so(self) -> None:
        empty = Verification(path="x", kind="unknown")
        rendered = empty.render()
        self.assertIn("not a pass", rendered)
        self.assertFalse(empty.ok)

    def test_verify_budget_is_declared(self) -> None:
        # The constant must exist and be a real number, not a promise in prose.
        self.assertGreater(MAX_VERIFY_BYTES, 0)

    def test_check_renders_both_marks(self) -> None:
        self.assertIn("[ok]", Check("a", True).rendered)
        self.assertIn("[FAIL]", Check("a", False).rendered)


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()


class ValidateToolTests(unittest.TestCase):
    """The tool must reach the same verdict as the function it wraps."""

    def setUp(self) -> None:
        import asyncio
        from unittest.mock import MagicMock

        from ai2027_protocol.tools import SandboxToolRuntime, ToolContext

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.asyncio = asyncio
        self.runtime = SandboxToolRuntime()
        self.ctx = ToolContext(
            session_id="s",
            workspace_root=self.tmp,
            sandbox_dir=self.tmp,
            ledger_path=self.tmp / "l.jsonl",
            sessions_root=self.tmp / "s",
            kernel_bridge=MagicMock(),
            memory_bridge=MagicMock(),
            connectors_bridge=MagicMock(),
            auditor=MagicMock(),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, **args: str) -> ToolOutcome:
        from ai2027_protocol.contracts import ToolInvocation

        invocation = ToolInvocation(tool="sandbox.validate_artifact", args=args)
        return self.asyncio.run(self.runtime.execute(invocation, self.ctx))

    def test_valid_docx_is_ok_with_a_hash(self) -> None:
        (self.tmp / "g.docx").write_bytes(_docx_body())
        outcome = self._run(path="g.docx", kind="docx")
        self.assertTrue(outcome.ok, outcome.error)
        self.assertIsNotNone(outcome.verified_sha256)
        self.assertEqual(outcome.artifact_kind, "docx")

    def test_corrupt_docx_is_not_ok_and_names_the_check(self) -> None:
        (self.tmp / "b.docx").write_bytes(_corrupt_member(_docx_body()))
        outcome = self._run(path="b.docx", kind="docx")
        self.assertFalse(outcome.ok)
        self.assertIn("member CRCs", outcome.summary)
        self.assertIn("document.xml", outcome.error or "")

    def test_missing_path_reports_exists(self) -> None:
        outcome = self._run(path="ghost.docx", kind="docx")
        self.assertFalse(outcome.ok)
        self.assertIn("exists", outcome.summary)

    def test_empty_path_is_refused(self) -> None:
        outcome = self._run(path="   ")
        self.assertFalse(outcome.ok)
        self.assertIn("non-empty path", outcome.error or "")

    def test_kind_is_checked_against_the_bytes(self) -> None:
        # A writer that claims "docx" for a text file must be caught: the
        # header is not a ZIP, so the OOXML checks cannot run and must not
        # silently pass.
        (self.tmp / "fake.docx").write_text("هذا نص وليس مستندًا\n", encoding="utf-8")
        outcome = self._run(path="fake.docx", kind="docx")
        self.assertFalse(outcome.ok)


class DeepCorruptionTests(unittest.TestCase):
    """The paths a single flipped byte does not reach.

    A flipped bit usually breaks the deflate stream before the CRC is compared,
    so the `testzip()` mismatch branch and the size-budget note never ran. They
    do run on real archives, so they get tests that hit them directly.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_stored_member_with_wrong_crc_is_named(self) -> None:
        # ZIP_STORED puts the bytes verbatim, so a flipped byte survives to the
        # CRC comparison instead of failing decompression first.
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr("data.txt", "abcdefghij" * 100)
        good = buffer.getvalue()
        with zipfile.ZipFile(io.BytesIO(good)) as archive:
            info = archive.infolist()[0]
        offset = info.header_offset + 30 + len(info.filename) + 10
        corrupted = bytearray(good)
        corrupted[offset] ^= 0x01
        path = self.tmp / "stored.zip"
        path.write_bytes(bytes(corrupted))

        verdict = verify_file(path, display_path="stored.zip", kind="zip")
        self.assertFalse(verdict.ok)
        self.assertTrue(
            any("CRC-32" in check.detail for check in verdict.failures), verdict.render()
        )

    def test_budget_note_is_written_when_the_archive_is_large(self) -> None:
        from unittest.mock import patch

        path = self.tmp / "big.zip"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("a.txt", "hello")
        path.write_bytes(buffer.getvalue())

        # Force the budget branch without writing a 256 MB fixture.
        with patch("ai2027_protocol.verify.MAX_VERIFY_BYTES", 1):
            verdict = verify_file(path, display_path="big.zip", kind="zip")
        self.assertTrue(verdict.ok)
        # The note is a caveat, not a failure: the checks still all passed.
        self.assertTrue(any("verify budget" in item for item in verdict.notes), verdict.notes)
