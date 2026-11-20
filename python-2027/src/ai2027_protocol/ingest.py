"""What a file *is*, decided from its bytes rather than its name.

Why this exists
---------------
The system classified every artifact by its extension. Measured on twelve
real formats — parquet, hdf5, dxf, sqlite, docx, xlsx, pdf, zip, png, mp4 —
all twelve were labelled ``text``, because the map knew eleven extensions and
fell through to a default. A PNG was then read as text: the reader decoded
binary into replacement characters, the scorer saw gibberish, and the report
said the model wrote a bad page.

An extension is a claim. A signature is evidence. This module reads the first
bytes and answers with the container family, so a reader can refuse to decode
what is not text instead of producing mojibake.

Design constraints
------------------
* **No dependencies.** A signature table is a few hundred bytes of constants;
  a package would be a supply-chain surface for the same information.
* **No false authority.** An unknown file is reported as ``unknown`` with the
  reason, never guessed from the name. The caller decides what to do.
* **Bounded work.** Only a fixed prefix is read. A 4 GB file costs the same
  as a 4-byte one, so probe costs cannot be used to exhaust memory.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

#: How many bytes a probe needs. Every signature below fits in this prefix, so
#: one read answers every question and a huge file costs nothing extra.
PROBE_BYTES = 512


class Family(StrEnum):
    """The container family of a file, which decides how it may be read."""

    TEXT = "text"
    #: A ZIP container. DOCX, XLSX, PPTX, EPUB, JAR and .odt are all ZIPs, so
    #: the extension is required to say which one -- the bytes cannot.
    ZIP = "zip"
    OLE = "ole"
    PDF = "pdf"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    ARCHIVE = "archive"
    #: Columnar / scientific binaries: parquet, hdf5, arrow, netcdf.
    COLUMNAR = "columnar"
    DATABASE = "database"
    #: Engineering and geometry: dxf, dwg, step, stl, glb.
    GEOMETRY = "geometry"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Probe:
    """The result of reading a file's signature."""

    family: Family
    #: The concrete format when the bytes identify one (``png``, ``parquet``),
    #: otherwise the family name. Never guessed from the extension.
    format: str
    #: Bytes actually inspected, so a caller can tell a whole file from a prefix.
    bytes_read: int
    #: Why this answer was chosen. A probe that cannot explain itself is a guess.
    reason: str

    @property
    def is_textual(self) -> bool:
        """Whether the bytes may be decoded as text without producing mojibake.

        A container that *contains* text (zip, ole) is not textual: decoding it
        yields binary noise even when its payload is a document.
        """
        return self.family is Family.TEXT


#: Magic prefixes, longest first within a family so the specific beats the general.
#: (signature, offset, family, format name)
_SIGNATURES: tuple[tuple[bytes, int, Family, str], ...] = (
    # Documents and archives -------------------------------------------------
    (b"%PDF-", 0, Family.PDF, "pdf"),
    (b"PK\x03\x04", 0, Family.ZIP, "zip"),
    (b"PK\x05\x06", 0, Family.ZIP, "zip-empty"),
    (b"PK\x07\x08", 0, Family.ZIP, "zip-spanned"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", 0, Family.OLE, "ole"),
    (b"7z\xbc\xaf\x27\x1c", 0, Family.ARCHIVE, "7z"),
    (b"Rar!\x1a\x07", 0, Family.ARCHIVE, "rar"),
    (b"\x1f\x8b", 0, Family.ARCHIVE, "gzip"),
    (b"BZh", 0, Family.ARCHIVE, "bzip2"),
    (b"\xfd7zXZ\x00", 0, Family.ARCHIVE, "xz"),
    (b"ustar", 257, Family.ARCHIVE, "tar"),
    # Images -----------------------------------------------------------------
    (b"\x89PNG\r\n\x1a\n", 0, Family.IMAGE, "png"),
    (b"\xff\xd8\xff", 0, Family.IMAGE, "jpeg"),
    (b"GIF87a", 0, Family.IMAGE, "gif"),
    (b"GIF89a", 0, Family.IMAGE, "gif"),
    (b"BM", 0, Family.IMAGE, "bmp"),
    (b"II*\x00", 0, Family.IMAGE, "tiff"),
    (b"MM\x00*", 0, Family.IMAGE, "tiff"),
    # Audio and video --------------------------------------------------------
    (b"ID3", 0, Family.AUDIO, "mp3"),
    (b"\xff\xfb", 0, Family.AUDIO, "mp3"),
    (b"OggS", 0, Family.AUDIO, "ogg"),
    (b"fLaC", 0, Family.AUDIO, "flac"),
    (b"RIFF", 0, Family.AUDIO, "riff"),
    (b"\x1aE\xdf\xa3", 0, Family.VIDEO, "matroska"),
    # Columnar and scientific ------------------------------------------------
    (b"PAR1", 0, Family.COLUMNAR, "parquet"),
    (b"\x89HDF\r\n\x1a\n", 0, Family.COLUMNAR, "hdf5"),
    (b"ARROW1", 0, Family.COLUMNAR, "arrow"),
    (b"CDF\x01", 0, Family.COLUMNAR, "netcdf"),
    (b"CDF\x02", 0, Family.COLUMNAR, "netcdf"),
    (b"Obj\x01", 0, Family.COLUMNAR, "avro"),
    # Databases --------------------------------------------------------------
    (b"SQLite format 3\x00", 0, Family.DATABASE, "sqlite"),
    # Geometry ---------------------------------------------------------------
    (b"glTF", 0, Family.GEOMETRY, "glb"),
    (b"AC10", 0, Family.GEOMETRY, "dwg"),
    (b"ISO-10303-21", 0, Family.GEOMETRY, "step"),
    (b"solid ", 0, Family.GEOMETRY, "stl"),
)

#: ISO base media (mp4, mov, heic) is "....ftyp" -- the box size varies, so the
#: marker is checked at a fixed offset instead of as a prefix.
_ISO_BMFF_FTYP = (4, b"ftyp")


def _looks_textual(prefix: bytes) -> tuple[bool, str]:
    """Whether a byte prefix decodes as text, and why.

    UTF-16 and UTF-32 text starts with NUL bytes, and a naive "no NULs" test
    would call a legitimate UTF-16 document binary. The test is therefore on
    the *decoded* result: if it decodes under one of the UTF codecs without
    replacement characters, it is text. Everything else is not.
    """
    if not prefix:
        return True, "empty file"
    for encoding in ("utf-8", "utf-16", "utf-32"):
        try:
            decoded = prefix.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
        if "\ufffd" not in decoded:
            return True, f"decodes as {encoding}"
    # A prefix can cut a multi-byte character in half. Retrying the trimmed
    # prefix with EVERY codec is how binary noise got called text: random
    # bytes fail all three codecs, but random bytes minus their last three
    # succeed as UTF-16, because even-length byte strings almost always do.
    # The retry is therefore narrow: utf-8 only (where a cut is common and the
    # codec still rejects noise), and at most three bytes off the end.
    for cut in (1, 2, 3):
        trimmed = prefix[:-cut]
        if not trimmed:
            break
        try:
            decoded = trimmed.decode("utf-8")
        except (UnicodeDecodeError, UnicodeError):
            continue
        if "\ufffd" not in decoded:
            return True, "decodes as utf-8 (prefix cut mid-character)"
    return False, "does not decode as utf-8/16/32"


def _is_binary_stl(data: bytes) -> bool:
    """Whether these bytes are a binary STL, by its arithmetic signature.

    Binary STL has no magic: an 80-byte header of arbitrary text (often a CAD
    program's name), a 32-bit triangle count, then exactly 50 bytes per triangle.
    The header's text is why the generic textual test called a valid mesh "text"
    -- measured, `part.stl` (134 bytes, one triangle) was described as

        text (text) ... no headings; this is a single block of text

    The signature is arithmetic, and it is exact: 84 + 50*n must equal the file
    size. A text file that happens to pass this is possible but not plausible,
    and the size is known, so the test is decisive rather than heuristic.
    """
    if len(data) < 84:
        return False
    count = int.from_bytes(data[80:84], "little")
    if count == 0:
        return False
    return 84 + count * 50 == len(data)


def probe_bytes(data: bytes) -> Probe:
    """Classify a byte prefix. The single decision point for "what is this?"."""
    prefix = data[:PROBE_BYTES]

    # STL runs before the signature table, deliberately. Binary STL has no magic
    # and its 80-byte header is free text -- many CAD programs write "solid ..."
    # into it, which matches the ASCII-STL signature at offset 0. The prefix is
    # therefore genuinely ambiguous, and the only decisive discriminator is the
    # size identity 84 + 50*n == len(data). Checking it first is what makes a
    # binary mesh geometry instead of "a single block of text".
    if len(data) >= 84 and _is_binary_stl(data):
        return Probe(
            family=Family.GEOMETRY,
            format="stl-binary",
            bytes_read=len(prefix),
            reason="80-byte header + 50 bytes per triangle matches the file size",
        )

    for signature, offset, family, name in _SIGNATURES:
        end = offset + len(signature)
        if len(prefix) >= end and prefix[offset:end] == signature:
            return Probe(
                family=family,
                format=name,
                bytes_read=len(prefix),
                reason=f"signature at offset {offset}",
            )

    ftyp_offset, ftyp = _ISO_BMFF_FTYP
    if (
        len(prefix) >= ftyp_offset + len(ftyp)
        and prefix[ftyp_offset : ftyp_offset + len(ftyp)] == ftyp
    ):
        return Probe(
            family=Family.VIDEO,
            format="mp4",
            bytes_read=len(prefix),
            reason="iso base media ftyp box",
        )

    textual, why = _looks_textual(prefix)
    if textual:
        return Probe(Family.TEXT, "text", len(prefix), why)
    return Probe(
        Family.UNKNOWN,
        "unknown",
        len(prefix),
        f"{why}; no known signature",
    )


def probe_file(path: str | Path, limit: int = PROBE_BYTES) -> Probe:
    """Read at most `limit` bytes from a file and classify them.

    The prefix read is the whole point: probing a large file must not load it.
    """
    with open(path, "rb") as handle:  # noqa: PTH123 - explicit binary read
        return probe_bytes(handle.read(limit))


def extension_disagrees(rel_path: str, probe: Probe) -> str | None:
    """Whether the name claims something the bytes deny, and what was claimed.

    Returns a short description when they disagree, otherwise ``None``. This
    is the check that would have caught a PNG labelled ``.txt`` — the failure
    that made a binary file get read as a document.
    """
    lowered = rel_path.lower().rsplit(".", 1)
    suffix = f".{lowered[1]}" if len(lowered) == 2 else ""
    claims_text = suffix in {
        ".txt",
        ".md",
        ".markdown",
        ".csv",
        ".json",
        ".html",
        ".htm",
        ".css",
        ".js",
        ".mjs",
        ".py",
        ".svg",
        ".xml",
        ".yaml",
        ".yml",
    }
    if claims_text and not probe.is_textual:
        return f"name says text ({suffix or 'no extension'}) but bytes are {probe.format}"
    if suffix and not claims_text and probe.is_textual and probe.family is Family.TEXT:
        # A binary extension on text content is the reverse mismatch: a .png
        # that is really a markdown file. Not dangerous, but worth naming.
        if suffix.lstrip(".") in {"png", "jpg", "jpeg", "gif", "pdf", "zip"}:
            return f"name says {suffix} but bytes are text"
    return None
