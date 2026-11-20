"""PDF structure read without a rendering engine.

The gap this fills, measured
----------------------------
`describe_file("plain.pdf")` on a 12-page PDF returned

    plain.pdf: pdf (pdf), 403 bytes
      note: no reader for the pdf family yet

A PDF is not a mystery box: page count, page size and document metadata are
plain text in the file when its objects are not compressed. That is the common
case for office exports and print-ready files, and it is enough to answer "how
long is this, what size is a page, who made it" without a PDF library.

What this reader refuses to do
------------------------------
* **Never decompress.** PDF 1.5+ may pack objects into compressed streams. This
  reader does not inflate them, so on such a file it reports that the count was
  not found rather than a number it did not read. A guessed page count is worse
  than an absent one: the absent one makes the caller ask.
* **Never trust `/Count` alone.** In a damaged file `/Count` can disagree with
  the number of `/Type /Page` objects. Both are reported when both are visible,
  and an disagreement is stated rather than resolved silently.
* **Never scan the whole file.** A 500 MB PDF is not read into memory: the scan
  is bounded by `MAX_SCAN_BYTES`, and the reader says when it stopped early.

Why the standard library is enough here
---------------------------------------
The facts live in a small subset of PDF syntax: dictionaries (`<< ... >>`),
names (`/Key`), numbers, and arrays. A regex over that subset finds them; a
parser that understood every PDF feature would not fit in this module, and would
still need a decompressor to be correct on modern files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: How much of the file is scanned for structure. Beyond this the reader stops
#: and says so -- a PDF whose page tree is past 8 MB is not one this reader can
#: honestly describe from a prefix.
MAX_SCAN_BYTES = 8 * 1024 * 1024

#: PDF metadata keys worth surfacing. Others are left alone rather than dumped
#: into the IR, where an unknown key would cost tokens and answer nothing.
_INFO_KEYS = {
    "Title": "title",
    "Author": "author",
    "Producer": "producer",
    "Creator": "creator",
    "CreationDate": "created",
    "ModDate": "modified",
}

_COUNT_PATTERN = re.compile(rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)")
_COUNT_PATTERN_ALT = re.compile(rb"/Count\s+(\d+)[^>]*?/Type\s*/Pages\b")
_PAGE_OBJECT_PATTERN = re.compile(rb"/Type\s*/Page\b(?!s)")
_MEDIABOX_PATTERN = re.compile(
    rb"/MediaBox\s*\[\s*([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s*\]"
)
_INFO_PATTERN = re.compile(
    rb"/(" + b"|".join(k.encode() for k in _INFO_KEYS) + rb")\s*\((.*?)\)", re.DOTALL
)
#: A literal `stream` keyword means at least one object is stream data; combined
#: with `/ObjStm` or `/FlateDecode` it means objects may be compressed.
_OBJSTM = re.compile(rb"/Type\s*/ObjStm|/ObjStm\b")


def _pdf_string(raw: bytes) -> str:
    """Decode a PDF literal string enough to be readable, or say it was binary.

    PDF strings may be UTF-16BE with a BOM, PDFDocEncoding, or raw bytes. This
    tries UTF-16BE and UTF-8 and otherwise reports the bytes as unreadable
    rather than emitting replacement characters that look like content.
    """
    if raw.startswith(b"\xfe\xff"):
        try:
            return raw[2:].decode("utf-16-be")
        except UnicodeDecodeError:
            return "(unreadable UTF-16)"
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        # latin-1 maps every byte to a code point, so this cannot raise. It is a
        # fallback, not a guess: PDFDocEncoding is close enough to latin-1 that
        # the text is readable, and saying "unreadable" for a byte string that a
        # reader *can* show would be needlessly coy.
        return raw.decode("latin-1")


@dataclass(frozen=True)
class PdfInfo:
    """What a PDF says about itself, and what this reader could not reach."""

    version: str = ""
    #: The page count the file states, or ``None`` when it could not be read.
    pages: int | None = None
    #: How many `/Type /Page` objects were visible -- a cross-check, not a count.
    page_objects: int | None = None
    #: Page size in points from the first `/MediaBox`, when visible.
    page_width: float | None = None
    page_height: float | None = None
    #: Whichever metadata keys were readable, lower-cased.
    metadata: tuple[tuple[str, str], ...] = ()
    #: True when the file declares compressed object streams.
    compressed_objects: bool = False
    #: True when the scan hit its byte budget before the end of the file.
    truncated_scan: bool = False
    notes: tuple[str, ...] = ()

    @property
    def readable(self) -> bool:
        """Whether anything structural was actually determined."""
        return self.pages is not None or bool(self.metadata) or self.page_width is not None

    @property
    def page_size(self) -> str:
        """A4/Letter/US-letter names when the size matches one, else dimensions."""
        if not self.page_width or not self.page_height:
            return ""
        width, height = self.page_width, self.page_height
        if 590 < width < 600 and 835 < height < 850:
            return "A4"
        if 605 < width < 620 and 790 < height < 800:
            return "US Letter"
        return f"{width:.0f}×{height:.0f}pt"

    @property
    def inconsistent_page_count(self) -> bool:
        """Whether the declared count disagrees with the page objects visible."""
        if self.pages is None or self.page_objects is None:
            return False
        return self.pages != self.page_objects

    def render(self) -> str:
        """A short description a caller can act on, plus every caveat."""
        parts = [f"pdf{(' ' + self.version) if self.version else ''}"]
        if self.pages is not None:
            parts.append(f"{self.pages} page(s)")
        if self.page_size:
            parts.append(self.page_size)
        if self.compressed_objects:
            parts.append("compressed object streams")
        lines = [", ".join(parts)]
        for key, value in self.metadata:
            lines.append(f"  {key}: {value}")
        if self.inconsistent_page_count:
            lines.append(
                f"  note: /Count says {self.pages} but {self.page_objects} "
                "page object(s) are visible; the file disagrees with itself"
            )
        if self.truncated_scan:
            lines.append(f"  note: structure scan stopped at {MAX_SCAN_BYTES:,} bytes")
        lines.extend(f"  note: {note}" for note in self.notes)
        return "\n".join(lines)


def read_pdf(data: bytes) -> PdfInfo:
    """Describe PDF bytes by reading their structure, never by rendering them."""
    notes: list[str] = []
    if not data.startswith(b"%PDF-"):
        return PdfInfo(notes=("no %PDF- header",))

    version = ""
    header = data[:16].split(b"-", 1)
    if len(header) == 2:
        version = header[1].split(b"\n", 1)[0].split(b"\r", 1)[0].decode("ascii", "ignore")

    scanned = data[:MAX_SCAN_BYTES]
    truncated = len(data) > MAX_SCAN_BYTES
    if truncated:
        notes.append("the page tree may be past the scan budget")

    compressed = bool(_OBJSTM.search(scanned))
    if compressed:
        notes.append(
            "objects are compressed; the page count needs a decompressor this build does not have"
        )

    pages: int | None = None
    for pattern in (_COUNT_PATTERN, _COUNT_PATTERN_ALT):
        found = pattern.search(scanned)
        if found:
            pages = int(found.group(1))
            break
    if pages is None and not compressed:
        notes.append("no /Count found in the readable part of the file")

    page_objects = len(_PAGE_OBJECT_PATTERN.findall(scanned)) or None

    width = height = None
    box = _MEDIABOX_PATTERN.search(scanned)
    if box:
        try:
            x0, y0, x1, y1 = (float(value) for value in box.groups())
            width, height = x1 - x0, y1 - y0
        except ValueError:  # pragma: no cover - the pattern only admits digits
            notes.append("a /MediaBox was found but could not be parsed")

    metadata: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in _INFO_PATTERN.finditer(scanned):
        key = _INFO_KEYS.get(match.group(1).decode("ascii", "ignore"))
        if key is None or key in seen:
            continue
        seen.add(key)
        metadata.append((key, _pdf_string(match.group(2)).strip()))

    return PdfInfo(
        version=version,
        pages=pages,
        page_objects=page_objects,
        page_width=width,
        page_height=height,
        metadata=tuple(metadata),
        compressed_objects=compressed,
        truncated_scan=truncated,
        notes=tuple(notes),
    )
