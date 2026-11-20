"""What is *inside* a container, decided without trusting what it claims.

Why this is a separate layer
----------------------------
A signature cannot tell a DOCX from an XLSX from an EPUB. All four are ZIP
files starting with the same four bytes, and `ingest.probe_bytes` answers
`zip` for every one of them — measured, not assumed. The type lives in the
container's *directory*: a DOCX has `word/document.xml`, an XLSX has
`xl/workbook.xml`.

Reading that directory is where the danger is. Nothing in this repository
checked decompression limits, and a 10 KB ZIP can declare 100 GB of output
(measured ratio: 1016x on a run of zero bytes). Extracting it without a gate
is a denial of service the attacker gets for free.

Guarantees
----------
* **Read the directory first, never the data.** `listing` touches names and
  declared sizes only, so the bomb is disarmed before a byte is inflated.
* **Refuse absolute and climbing paths.** A ZIP entry may be named
  `../../etc/passwd`; that is a write primitive, not a document.
* **A budget, not a setting.** Every limit here has a number and a reason,
  and exceeding one is a refusal with the measured value, not a warning.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field

#: Zip bombs are defined by ratio as much as by size: highly repetitive data
#: compresses thousands of times. Measured on this repository's own test data,
#: ordinary documents stay under 20x.
MAX_COMPRESSION_RATIO = 200.0
#: The largest total uncompressed size a probe will accept. Bigger is a
#: deliberate refusal, not a crash.
MAX_TOTAL_UNCOMPRESSED = 512 * 1024 * 1024
#: How many entries are inspected. A container with 100,000 files is not a
#: document; reading every name would itself be the attack.
MAX_ENTRIES = 20_000

#: The marker files that identify an OOXML/ODF container, most specific first.
#: (marker, format, human name)
_OOXML_MARKERS: tuple[tuple[str, str], ...] = (
    ("word/document.xml", "docx"),
    ("xl/workbook.xml", "xlsx"),
    ("ppt/presentation.xml", "pptx"),
    ("visio/document.xml", "vsdx"),
)
_ODF_MARKERS: tuple[tuple[str, str], ...] = (("content.xml", "odt-or-ods"),)


@dataclass(frozen=True)
class ContainerReport:
    """What a ZIP-shaped file contains, and whether it may be opened."""

    #: A concrete format when the directory identifies one (``docx``), else
    #: ``zip``. Never decided from the extension.
    format: str
    #: Entry names, in archive order. Empty when the container was refused.
    entries: tuple[str, ...] = ()
    #: Sum of the *declared* uncompressed sizes. This is what an attacker
    #: controls, which is exactly why it is checked before reading.
    declared_uncompressed: int = 0
    #: Why the container was refused, or "" when it is safe to open.
    refusal: str = ""
    #: Non-fatal observations worth telling the user (macros, links, nesting).
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_safe_to_open(self) -> bool:
        """Whether extraction may proceed."""
        return not self.refusal


def _is_unsafe_name(name: str) -> str:
    """Why an entry name is unsafe to extract, or "" if it is fine."""
    if not name or name in {".", ".."}:
        return "empty or dot name"
    if name.startswith("/") or name.startswith("\\"):
        return "absolute path"
    if ":" in name.split("/")[0]:
        return "drive-qualified path"
    parts = name.replace("\\", "/").split("/")
    if any(part == ".." for part in parts):
        return "parent-directory traversal"
    return ""


def _detect_format(names: frozenset[str], name_hint: str) -> str:
    """Name the container from its directory, falling back to the extension.

    The directory is evidence; the extension is a claim. When the directory
    is silent — a plain ZIP of holiday photos — the extension is the only
    signal left, and the result says so by staying generic.
    """
    for marker, fmt in _OOXML_MARKERS:
        if marker in names:
            return fmt
    # EPUB is checked before ODF: an EPUB also carries `mimetype`, so the
    # generic ODF branch was claiming it first — measured, and wrong.
    if "META-INF/container.xml" in names:
        return "epub"
    for marker, fmt in _ODF_MARKERS:
        if marker in names:
            return fmt
    if "mimetype" in names:
        return "odf"
    lowered = name_hint.lower()
    for suffix, fmt in (
        (".docx", "docx"),
        (".xlsx", "xlsx"),
        (".pptx", "pptx"),
        (".epub", "epub"),
        (".jar", "jar"),
        (".apk", "apk"),
    ):
        if lowered.endswith(suffix):
            return fmt
    return "zip"


def inspect_zip(data: bytes, *, name_hint: str = "") -> ContainerReport:
    """Read a ZIP's directory and decide whether it is safe to open.

    Nothing is inflated: `infolist()` reads the central directory, so a bomb
    is refused before it can consume memory.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        return ContainerReport(format="zip", refusal=f"not a readable zip: {exc}")
    except Exception as exc:  # noqa: BLE001 - a malformed container must not crash a run
        return ContainerReport(format="zip", refusal=f"unreadable container: {exc}")

    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            return ContainerReport(
                format="zip",
                refusal=(
                    f"{len(infos)} entries exceeds the {MAX_ENTRIES} limit; "
                    "a directory this large is not a document"
                ),
            )

        names: list[str] = []
        total_declared = 0
        for info in infos:
            unsafe = _is_unsafe_name(info.filename)
            if unsafe:
                return ContainerReport(
                    format="zip",
                    refusal=f"entry {info.filename!r} has {unsafe}",
                )
            names.append(info.filename)
            total_declared += max(0, info.file_size)

        if total_declared > MAX_TOTAL_UNCOMPRESSED:
            return ContainerReport(
                format="zip",
                entries=tuple(names),
                declared_uncompressed=total_declared,
                refusal=(
                    f"declares {total_declared:,} bytes uncompressed, over the "
                    f"{MAX_TOTAL_UNCOMPRESSED:,} limit"
                ),
            )

        compressed = sum(max(1, info.compress_size) for info in infos)
        ratio = total_declared / compressed if compressed else 0.0
        if ratio > MAX_COMPRESSION_RATIO:
            return ContainerReport(
                format="zip",
                entries=tuple(names),
                declared_uncompressed=total_declared,
                refusal=(
                    f"compression ratio {ratio:.0f}x exceeds the "
                    f"{MAX_COMPRESSION_RATIO:.0f}x limit ({total_declared:,} bytes "
                    f"from {compressed:,}); this is the shape of a zip bomb"
                ),
            )

        name_set = frozenset(names)
        warnings: list[str] = []
        # Macros are executable content inside a document container. Opening
        # the file is not running them, but the user must be told they exist.
        if "xl/vbaProject.bin" in name_set or "word/vbaProject.bin" in name_set:
            warnings.append("contains VBA macros")
        if any(name.lower().endswith((".exe", ".dll", ".scr", ".js", ".vbs")) for name in names):
            warnings.append("contains an executable file inside the container")
        nested = [
            name
            for name in names
            if name.lower().endswith((".zip", ".docx", ".xlsx", ".doc", ".xls", ".7z", ".rar"))
        ]
        if nested:
            warnings.append(f"{len(nested)} nested archive(s); depth not followed")

        return ContainerReport(
            format=_detect_format(name_set, name_hint),
            entries=tuple(names),
            declared_uncompressed=total_declared,
            warnings=tuple(warnings),
        )


def read_member(data: bytes, member: str, *, limit: int = 8 * 1024 * 1024) -> bytes:
    """Read one member, refusing anything larger than `limit`.

    The declared size is checked *and* the actual read is capped, because a
    container can lie about its size in both directions.
    """
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        info = archive.getinfo(member)
        if info.file_size > limit:
            raise ValueError(
                f"{member} declares {info.file_size:,} bytes, over the {limit:,} limit"
            )
        with archive.open(info) as handle:
            chunk = handle.read(limit + 1)
    if len(chunk) > limit:
        raise ValueError(f"{member} produced more than {limit:,} bytes despite its declaration")
    return chunk
