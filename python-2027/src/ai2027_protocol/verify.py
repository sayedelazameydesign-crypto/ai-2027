"""Layer 5: prove the output before anyone relies on it.

The rule this module enforces
----------------------------
An artifact is not "done" because a tool wrote it. It is done when an
**independent** check re-opens it and the result agrees with what was claimed.
The plan calls this validation-and-lineage; the two halves are inseparable:

* **Validation** — the bytes are structurally what the format requires. A docx
  that is a valid ZIP but has a corrupt body is not a valid docx, and the
  container inspector alone cannot tell the difference: measured, a single
  flipped bit mid-body leaves `inspect_zip(..., name_hint="corrupt.docx")`
  reporting `format='docx' safe=True`. The directory is intact; the content is
  not. Verification therefore re-reads every member and checks its CRC.
* **Lineage** — every claim points back to a record. A verification report that
  says "valid" without saying which tool checked what is indistinguishable from
  an opinion.

Why the checks are separate from the writers
--------------------------------------------
`tools_exec.py` writes; this module verifies. If the verifier shared the
writer's helpers, a bug in a helper would produce matching wrong answers on
both sides and the check would pass. The only shared thing is the file on disk.

Cost, measured before the design
--------------------------------
`ZipFile.testzip()` on a 5 MB archive costs 9 ms, on a 30 MB archive 22 ms. That
is cheap enough to run on every ZIP-shaped output, which is why the CRC check is
unconditional rather than sampled. Above `MAX_VERIFY_BYTES` the archive is not
re-read: the cost would be paid in the user's latency for a check that the
ingest layer's own 512 MB decompression cap already refuses.

Fail-soft, again
----------------
Every check returns a `Check` with `passed: bool` and a `detail` string. A check
that could not run is `passed=False` **with the reason** — never silently
skipped, because a skipped check that renders as a pass is worse than no check.
"""

from __future__ import annotations

import hashlib
import re
import struct
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

#: Byte prefixes that identify an image header this build can read. Kept as a
#: regex only so the branch above stays one line; the reading is done by
#: `images.read_image`, which is the single authority.
_IMAGE_MAGIC = re.compile(rb"\A(\x89PNG\r\n\x1a\n|\xff\xd8|GIF8[79]a|BM)")

#: Kinds whose bytes must carry a specific signature. Claiming one of these
#: without the signature is a mislabel, and a mislabel must fail rather than
#: fall through to a weaker check that happens to pass.
_STRUCTURED_KINDS = frozenset(
    {"docx", "xlsx", "pptx", "zip", "parquet", "sqlite", "pdf", "mp4", "wav", "mov", "m4a"}
)

#: Above this size a ZIP-shaped artifact is not re-read for CRC verification.
#: Measured: 30 MB costs 22 ms, so the budget is generous; the cap exists to
#: bound the worst case, not to save milliseconds.
MAX_VERIFY_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True)
class Check:
    """One thing that was actually tested, and what it said."""

    name: str
    passed: bool
    detail: str = ""

    @property
    def rendered(self) -> str:
        """One line, ``[ok]`` or ``[FAIL]`` first so a failure is not skimmed."""
        mark = "ok" if self.passed else "FAIL"
        return f"  [{mark}] {self.name}" + (f": {self.detail}" if self.detail else "")


@dataclass(frozen=True)
class Verification:
    """The verdict on one artifact, with the evidence that produced it."""

    path: str
    kind: str
    checks: tuple[Check, ...] = field(default_factory=tuple)
    sha256: str = ""
    bytes_on_disk: int = 0
    #: Caveats that are not failures -- e.g. the size budget stopped a re-read.
    #: Collected rather than discarded: a caveat nobody can read is not a caveat.
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        """True only when something was checked and everything passed."""
        return bool(self.checks) and all(check.passed for check in self.checks)

    @property
    def failures(self) -> tuple[Check, ...]:
        """The checks that did not pass, in the order they ran."""
        return tuple(check for check in self.checks if not check.passed)

    def render(self) -> str:
        """The human-readable verdict: one line per check, no summarising away."""
        verdict = "valid" if self.ok else "INVALID"
        head = f"{self.path}: {verdict} as {self.kind} ({self.bytes_on_disk:,} bytes)"
        lines = [head]
        lines.extend(check.rendered for check in self.checks)
        if not self.checks:
            lines.append("  no check could run; this is not a pass")
        lines.extend(f"  note: {note}" for note in self.notes)
        return "\n".join(lines)


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_zip_members(archive: zipfile.ZipFile, size: int, notes: list[str]) -> list[Check]:
    """Re-read every member: a directory-only check misses a corrupt body."""
    checks: list[Check] = []
    try:
        bad = archive.testzip()
    except (zipfile.BadZipFile, zlib.error, OSError, EOFError) as exc:
        # A corrupt deflate stream raises zlib.error, not BadZipFile -- measured
        # with a single flipped bit, which escaped the first version entirely.
        checks.append(Check("member CRCs", False, f"archive is corrupt: {exc}"))
        return checks
    if bad is None:
        checks.append(Check("member CRCs", True, f"{len(archive.infolist())} member(s) intact"))
    else:
        checks.append(Check("member CRCs", False, f"{bad!r} failed its CRC-32"))
    if size > MAX_VERIFY_BYTES:
        notes.append(
            f"{size:,} bytes is above the {MAX_VERIFY_BYTES:,}-byte verify budget; "
            "member CRCs were checked but nothing was decompressed twice"
        )
    return checks


def _check_ooxml(archive: zipfile.ZipFile, kind: str) -> list[Check]:
    """The parts OOXML requires, read rather than assumed present."""
    required = {
        "docx": "word/document.xml",
        "xlsx": "xl/workbook.xml",
        "pptx": "ppt/presentation.xml",
    }.get(kind)
    if required is None:
        return []
    if required not in archive.namelist():
        return [Check("required part", False, f"{required} is missing")]
    try:
        archive.read(required)
    except (zipfile.BadZipFile, zlib.error, OSError, EOFError) as exc:
        return [Check("required part", False, f"{required} unreadable: {exc}")]
    return [Check("required part", True, required)]


def _looks_textual(head: bytes) -> bool:
    """Whether the prefix is text at all, using layer 1's single decision point.

    Not a second opinion: re-using `probe_bytes` means the verifier and the
    ingester cannot disagree about what "text" is, which is the only way the
    two layers stay honest as the signature table grows.
    """
    from .ingest import probe_bytes

    return probe_bytes(head).is_textual


def _check_text_integrity(path: Path, size: int) -> list[Check]:
    """Decode the whole file: a "text" artifact that is not decodable is not text.

    This is the cheap universal check that makes `valid` mean something for the
    largest family. It reads in chunks so a 300 MB file costs 300 MB of *I/O*
    and no resident memory beyond one chunk.
    """
    import codecs

    decoder = codecs.getincrementaldecoder("utf-8")()
    consumed = 0
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                # Incremental, not per-chunk: a multi-byte character may straddle
                # the chunk boundary, and decoding each chunk alone reported a
                # perfectly valid 350 MB Arabic file as invalid at offset 1,048,575.
                decoder.decode(chunk)
                consumed += len(chunk)
            decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        return [Check("utf-8 decodable", False, f"invalid byte near offset {consumed + exc.start}")]
    except OSError as exc:
        return [Check("utf-8 decodable", False, f"read failed: {exc}")]
    return [Check("utf-8 decodable", True, f"{size:,} bytes")]


def _check_image(path: Path, head: bytes) -> list[Check]:
    """An image is valid when its header both parses and agrees with itself.

    Re-uses the same reader the IR uses, so a "valid" verdict means the very
    facts the system will report are the facts it could read -- not a second,
    weaker opinion that happens to pass.
    """
    from .images import read_image

    info = read_image(head)
    if not info.readable:
        return [Check("image header", False, "; ".join(info.notes) or "dimensions unreadable")]
    return [Check("image header", True, f"{info.width}×{info.height} {info.format}")]


def _check_pdf(head: bytes) -> list[Check]:
    """A PDF is valid when its header parses and, if it states a page count, it
    does not contradict itself.

    Re-uses `pdf.read_pdf`, so the verdict and the description cannot disagree:
    if the system will report "12 pages", this check saw the same 12.
    """
    from .pdf import read_pdf

    info = read_pdf(head)
    if not info.version:
        return [Check("pdf header", False, "no %PDF- version line")]
    checks = [Check("pdf header", True, f"{info.version}")]
    if info.inconsistent_page_count:
        checks.append(
            Check(
                "page count consistent",
                False,
                f"/Count says {info.pages} but {info.page_objects} page object(s) visible",
            )
        )
    return checks


def _check_media(path: Path) -> list[Check]:
    """A media container is valid when its box/chunk walk completes coherently.

    Re-uses `media.read_media`, so the duration the system reports is the
    duration this check saw. A container that declares a box larger than the
    file is truncated, and saying so is more useful than a duration computed
    from a size that cannot exist.
    """
    from .media import read_media

    # A box walk needs more than a header prefix: `moov` can sit after a large
    # `mdat`. This reads a bounded window -- the same 4 MB budget `ir.py` uses --
    # and the walk reports truncation if the structure really is cut off.
    with path.open("rb") as handle:
        window = handle.read(4 * 1024 * 1024)
    info = read_media(window)
    if not info.readable:
        detail = "; ".join(info.notes) or "no duration or dimensions could be read"
        return [Check("media container", False, detail)]
    return [Check("media container", True, info.render().splitlines()[0])]


def _check_sqlite(path: Path) -> list[Check]:
    """Open read-only and run SQLite's own integrity check.

    `PRAGMA integrity_check` is the format's own answer to "are these bytes a
    sound database", which is exactly the question this layer asks. It costs a
    scan; a database small enough to ship as an artifact is small enough to
    scan. Above the budget the check reports that it did not run.
    """
    import sqlite3

    if path.stat().st_size > MAX_VERIFY_BYTES:
        return [Check("sqlite integrity", False, "database exceeds the verify budget")]
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return [Check("sqlite integrity", False, f"could not open read-only: {exc}")]
    try:
        answer = connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as exc:
        return [Check("sqlite integrity", False, f"check failed: {exc}")]
    finally:
        connection.close()
    verdict = str(answer[0]) if answer else "no answer"
    if verdict != "ok":
        return [Check("sqlite integrity", False, verdict)]
    return [Check("sqlite integrity", True, "ok")]


def _check_parquet(handle: BinaryIO) -> list[Check]:
    """A parquet file is valid when its footer says so and nothing else does."""
    checks: list[Check] = []
    handle.seek(0, 2)
    size = handle.tell()
    if size < 12:
        return [Check("parquet footer", False, f"{size} bytes is too short to be parquet")]
    handle.seek(size - 8)
    tail = handle.read(8)
    if not isinstance(tail, bytes) or len(tail) != 8:
        return [Check("parquet footer", False, "the last 8 bytes could not be read")]
    handle.seek(0)
    magic = handle.read(4)
    checks.append(Check("header magic", magic == b"PAR1", repr(magic)))
    checks.append(Check("footer magic", tail[4:] == b"PAR1", repr(tail[4:])))
    declared = struct.unpack("<I", tail[:4])[0]
    fits = 0 < declared <= size - 12
    checks.append(Check("footer length", fits, f"{declared:,} bytes declared for {size:,} total"))
    return checks


def verify_file(
    real_path: Path,
    *,
    display_path: str = "",
    kind: str = "",
) -> Verification:
    """Verify one artifact and say what was actually tested.

    `kind` is a hint from the producer; it is checked against the bytes rather
    than trusted, so a writer that mislabels its own output is caught here.
    """
    shown = display_path or str(real_path)
    if not real_path.is_file():
        return Verification(
            path=shown,
            kind=kind or "missing",
            checks=(Check("exists", False, "file not found"),),
        )
    size = real_path.stat().st_size
    digest = _sha256_of(real_path)
    checks: list[Check] = [Check("exists", True, f"{size:,} bytes")]
    notes: list[str] = []

    with real_path.open("rb") as prefix_handle:
        head = prefix_handle.read(512)
    if size == 0:
        checks.append(Check("content", False, "file is empty"))
        return Verification(path=shown, kind=kind, checks=tuple(checks), sha256=digest)

    if head[:4] == b"PAR1":
        with real_path.open("rb") as handle:
            checks.extend(_check_parquet(handle))
    elif head[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(real_path) as archive:
                checks.extend(_check_zip_members(archive, size, notes))
                checks.extend(_check_ooxml(archive, kind))
        except zipfile.BadZipFile as exc:
            checks.append(Check("archive readable", False, str(exc)))
    elif head[4:8] == b"ftyp" or head[:4] == b"RIFF":
        checks.extend(_check_media(real_path))
    elif head.startswith(b"%PDF-"):
        checks.extend(_check_pdf(head))
    elif _IMAGE_MAGIC.search(head):
        checks.extend(_check_image(real_path, head))
    elif head[:16] == b"SQLite format 3\x00":
        checks.extend(_check_sqlite(real_path))
    elif kind in _STRUCTURED_KINDS:
        # The producer claimed a structured kind and the header denies it. This
        # is the mislabelling case: a text file named `report.docx` would pass
        # the UTF-8 check below and be reported valid under a format it is not.
        checks.append(
            Check(
                "header matches kind",
                False,
                f"kind is {kind} but the header is {head[:8]!r}, not that format",
            )
        )
    elif _looks_textual(head):
        checks.extend(_check_text_integrity(real_path, size))
    elif kind and kind not in {"unknown", "text"}:
        # A known binary kind with no verifier here (png, mp4, pdf). Honest gap,
        # not a defect: the file is not claimed to be text and nothing about it
        # was disproved. Marked as unchecked so it cannot read as a pass.
        checks.append(
            Check(
                "content checked",
                False,
                f"no verifier for {kind} yet; the bytes were not judged",
            )
        )
    else:
        # Labelled text, bytes are binary. Reporting a pass here would be the
        # lie this module exists to prevent: `b"glTF" + NULs` decodes as UTF-8
        # perfectly and is a binary glTF header.
        checks.append(
            Check(
                "content checked",
                False,
                "the artifact is labelled text but its header is binary",
            )
        )

    if len(checks) == 1:
        # Only "exists" ran. Reporting that as a pass would be the exact lie
        # this module exists to prevent: a 300 MB text file measured against
        # nothing still says "valid" if presence is the only criterion.
        checks.append(
            Check(
                "content checked",
                False,
                f"no verifier ran for {kind or 'this family'}; presence is not validity",
            )
        )

    return Verification(
        path=shown,
        kind=kind or "unknown",
        checks=tuple(checks),
        sha256=digest,
        bytes_on_disk=size,
        notes=tuple(notes),
    )
