"""Ingest must decide what a file is from its bytes, not its name.

The failure this prevents is measured, not hypothetical: `infer_artifact_kind`
labelled twelve real formats — parquet, hdf5, dxf, sqlite, docx, xlsx, pdf,
zip, png, mp4, an unknown, and a file with no extension — all as `text`. A PNG
then travelled through `workspace.read_file`, which decodes with
`errors="replace"`, and the model reasoned about forty bytes of U+FFFD as if
it were a badly written document.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from ai2027_protocol.contracts import ToolInvocation
from ai2027_protocol.ingest import Family, extension_disagrees, probe_bytes
from ai2027_protocol.tools import SandboxToolRuntime, ToolContext


class SignatureTests(unittest.TestCase):
    """Every family must be recognised from its bytes."""

    SIGNATURES = {
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 40: Family.IMAGE,
        b"\xff\xd8\xff\xe0" + b"\x00" * 40: Family.IMAGE,
        b"GIF89a" + b"\x00" * 40: Family.IMAGE,
        b"%PDF-1.7\n": Family.PDF,
        b"PK\x03\x04" + b"\x00" * 40: Family.ZIP,
        b"7z\xbc\xaf\x27\x1c" + b"\x00" * 40: Family.ARCHIVE,
        b"\x1f\x8b\x08" + b"\x00" * 40: Family.ARCHIVE,
        b"SQLite format 3\x00": Family.DATABASE,
        b"PAR1" + b"\x00" * 40: Family.COLUMNAR,
        b"\x89HDF\r\n\x1a\n": Family.COLUMNAR,
        b"CDF\x01" + b"\x00" * 40: Family.COLUMNAR,
        b"glTF" + b"\x00" * 40: Family.GEOMETRY,
        b"AC10" + b"\x00" * 40: Family.GEOMETRY,
        b"ISO-10303-21;": Family.GEOMETRY,
        b"\x00\x00\x00\x18ftypisom": Family.VIDEO,
        b"fLaC": Family.AUDIO,
    }

    def test_every_signature_is_recognised(self) -> None:
        for data, expected in self.SIGNATURES.items():
            with self.subTest(expected=expected, prefix=data[:8]):
                probe = probe_bytes(data)
                self.assertIs(probe.family, expected, probe.reason)

    def test_no_signature_is_reported_as_text(self) -> None:
        for data, expected in self.SIGNATURES.items():
            with self.subTest(expected=expected, prefix=data[:8]):
                self.assertFalse(probe_bytes(data).is_textual)


class TextTests(unittest.TestCase):
    """Text must be recognised without letting binary noise through."""

    def test_real_text_decodes(self) -> None:
        for data in (
            b"def f(): pass\n",
            "# عنوان\n\nنص عربي.".encode(),
            b"a,b\n1,2\n",
            b'{"a":1}',
            b"<!doctype html><html></html>",
        ):
            with self.subTest(prefix=data[:10]):
                self.assertTrue(probe_bytes(data).is_textual)

    def test_utf16_text_is_text(self) -> None:
        """A legitimate UTF-16 document starts with NULs and is still text."""
        self.assertTrue(probe_bytes("نص عربي".encode("utf-16")).is_textual)

    def test_random_bytes_are_not_text(self) -> None:
        """The regression this test exists for.

        Random bytes fail utf-8, utf-16 and utf-32 alike. They used to pass as
        UTF-16 because the codec was retried against a *trimmed* prefix, and an
        even-length byte string almost always decodes as UTF-16. Binary noise
        was therefore classified as text — the opposite of the goal.
        """
        self.assertFalse(probe_bytes(bytes(range(1, 200))).is_textual)

    def test_an_empty_file_is_text_not_binary(self) -> None:
        self.assertTrue(probe_bytes(b"").is_textual)


class DisagreementTests(unittest.TestCase):
    """A name that lies about its bytes must be reported."""

    def test_a_png_named_txt_is_reported(self) -> None:
        probe = probe_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
        message = extension_disagrees("photo.txt", probe)
        self.assertIsNotNone(message)
        self.assertIn("png", message)

    def test_a_markdown_file_named_png_is_reported(self) -> None:
        probe = probe_bytes(b"# real content\n")
        message = extension_disagrees("page.png", probe)
        self.assertIsNotNone(message)
        self.assertIn("text", message)

    def test_an_honest_name_is_silent(self) -> None:
        self.assertIsNone(extension_disagrees("a.md", probe_bytes(b"# hi\n")))
        self.assertIsNone(
            extension_disagrees("a.png", probe_bytes(b"\x89PNG\r\n\x1a\n"))
        )


class ReadFileRefusalTests(unittest.TestCase):
    """The tool that decodes text must refuse a binary file, with a reason."""

    def _context(self, root: Path) -> ToolContext:
        return ToolContext(
            session_id="t",
            workspace_root=root,
            sandbox_dir=root,
            ledger_path=root / "l.db",
            sessions_root=root,
            kernel_bridge=MagicMock(),
            memory_bridge=MagicMock(),
            connectors_bridge=MagicMock(),
            auditor=MagicMock(),
        )

    def test_a_png_is_refused_rather_than_decoded(self) -> None:
        runtime = SandboxToolRuntime()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "page.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 60)
            outcome = asyncio.run(
                runtime.execute(
                    ToolInvocation(
                        tool="workspace.read_file",
                        args={"path": "page.png", "scope": "workspace"},
                    ),
                    self._context(root),
                )
            )
        self.assertFalse(outcome.ok)
        self.assertIn("not text", outcome.error or "")
        self.assertIn("png", outcome.error or "")

    def test_arabic_text_is_still_readable(self) -> None:
        runtime = SandboxToolRuntime()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes.md").write_text("# عنوان\n\nنص عربي.\n", encoding="utf-8")
            outcome = asyncio.run(
                runtime.execute(
                    ToolInvocation(
                        tool="workspace.read_file",
                        args={"path": "notes.md", "scope": "workspace"},
                    ),
                    self._context(root),
                )
            )
        self.assertTrue(outcome.ok, outcome.error)
        self.assertIn("عنوان", outcome.summary + str(outcome.output or ""))


if __name__ == "__main__":
    unittest.main()
