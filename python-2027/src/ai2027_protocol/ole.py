"""Inspect OLE2 / Compound File Binary directories without opening payloads.

The measured gap this fills
---------------------------
`describe_file("old.doc")` on an OLE2 compound file used to return

    old.doc: ole (ole), 1,024 bytes
      note: no reader for the ole family yet

But OLE2 *is* a directory: a FAT-based container holding streams with UTF-16
names. `WordDocument` identifies a Word document; `Workbook` or `Book` an Excel
workbook; `EncryptedPackage` an encrypted Office document. Directory names
answer what the document is without loading a word processor or reading its
streams.

What this reader actually reads
-------------------------------
* the 512-byte header and its DIFAT, including the version and sector size;
* only the FAT sectors needed to follow the directory chain;
* directory sectors, whose 128-byte entries declare stream names and sizes.

It does **not** parse the mini-stream, stream payloads, macros, or document text.
Normal callers pass a seekable handle; only metadata sectors are read, not a
sequential file prefix. The FAT/DIFAT and directory walks have loop guards and
fixed byte/sector caps; a short or malformed chain is reported as partial,
never silently described as complete. `read_ole(bytes)` is also available for
callers that already hold a bounded byte string.

No extension guessing, no format-parser dependency, no writes.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import BinaryIO, Protocol

#: The OLE2 signature, unchanged since the Compound File format's introduction.
MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

#: Special sector markers from [MS-CFB].
_FREESECT = 0xFFFFFFFF
_ENDOFCHAIN = 0xFFFFFFFE
_DIFSECT = 0xFFFFFFFC
_FATSECT = 0xFFFFFFFD
_SPECIAL_SECTORS = frozenset({_FREESECT, _ENDOFCHAIN, _DIFSECT, _FATSECT})

#: The header contains 109 DIFAT entries. Additional FAT-sector identifiers are
#: stored in a linked DIFAT; both walks have a strict bound.
_HEADER_DIFAT_ENTRIES = 109
_MAX_FAT_SECTORS = 128
_MAX_DIFAT_SECTORS = 128
_MAX_DIRECTORY_SECTORS = 1024
_MAX_DIRECTORY_BYTES = 512 * 1024

#: Directory entry kinds from [MS-CFB].
_EMPTY = 0
_STORAGE = 1
_STREAM = 2
_ROOT = 5
_ENTRY_KIND = {_STORAGE: "storage", _STREAM: "stream", _ROOT: "root"}


class _RandomAccessSource(Protocol):
    """A file-sized source from which only named ranges are read."""

    @property
    def size(self) -> int: ...

    def read_at(self, offset: int, length: int) -> bytes: ...


@dataclass(frozen=True)
class _BytesSource:
    """Random access over bytes already supplied by a caller or test."""

    data: bytes

    @property
    def size(self) -> int:
        return len(self.data)

    def read_at(self, offset: int, length: int) -> bytes:
        return self.data[offset : offset + length]


@dataclass
class _HandleSource:
    """Seek only to metadata sectors; never turn a compound file into one blob."""

    handle: BinaryIO
    size: int

    def read_at(self, offset: int, length: int) -> bytes:
        if offset < 0 or length < 0 or offset >= self.size:
            return b""
        bounded_length = min(length, self.size - offset)
        self.handle.seek(offset)
        return self.handle.read(bounded_length)


#: These entries describe the OLE wrapper or encryption, not document content.
_METADATA_STREAMS = frozenset(
    {
        "CompObj",
        "ObjInfo",
        "\x05SummaryInformation",
        "\x05DocumentSummaryInformation",
        "\x01Ole",
        "Ole",
        "EncryptionInfo",
    }
)

#: A stream name is sufficient evidence only when it belongs to a known family.
#: For Excel, `Workbook` is BIFF8 and `Book` is BIFF5: either one identifies XLS.
_STREAM_ANY: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({"WordDocument"}), "doc"),
    (frozenset({"Workbook", "Book"}), "xls"),
    (frozenset({"PowerPoint Document"}), "ppt"),
    (frozenset({"__properties_version1.0"}), "msg"),
    (frozenset({"VisioDocument"}), "vsd"),
)


def _sector_offset(sector_id: int, sector_size: int) -> int:
    """Byte offset for a regular sector; v4 sector zero starts at byte 4096."""
    return (sector_id + 1) * sector_size


@dataclass(frozen=True)
class OleStream:
    """One directory entry, named and sized as the file declares."""

    name: str
    kind: str
    size: int = 0

    @property
    def is_metadata(self) -> bool:
        """Whether this entry is bookkeeping rather than document content."""
        return self.name in _METADATA_STREAMS


@dataclass(frozen=True)
class OleInfo:
    """The directory facts visible without reading a single stream payload."""

    version: str = ""
    sector_size: int = 0
    #: Directory entries in file order, including the root and storage entries.
    streams: tuple[OleStream, ...] = ()
    #: `doc`, `xls`, `ppt`, `msg`, `vsd`, `office-encrypted`, or empty if unknown.
    document_kind: str = ""
    encrypted: bool = False
    #: True only when the directory chain reached ENDOFCHAIN and its structure
    #: was internally coherent. Partial entries may still be safely rendered.
    directory_complete: bool = False
    notes: tuple[str, ...] = ()

    @property
    def readable(self) -> bool:
        """Whether at least one directory entry was safely extracted."""
        return bool(self.streams)

    @property
    def content_streams(self) -> tuple[OleStream, ...]:
        """Streams that may hold document content; metadata is excluded."""
        return tuple(
            stream for stream in self.streams if stream.kind == "stream" and not stream.is_metadata
        )

    @property
    def total_stream_bytes(self) -> int:
        """Sum declared content-stream sizes; payload bytes are never read."""
        return sum(stream.size for stream in self.content_streams)

    def render(self) -> str:
        """A compact description, explicitly marking a partially read directory."""
        head = [f"ole2 {self.version}".strip()]
        if self.document_kind:
            head.append(self.document_kind)
        if self.encrypted:
            head.append("encrypted")
        content = self.content_streams
        if content:
            head.append(f"{len(content)} stream(s)")
        if self.streams and not self.directory_complete:
            head.append("partial directory")
        lines = [", ".join(part for part in head if part)]
        for stream in content[:8]:
            lines.append(f"  {stream.name} ({stream.size:,} bytes)")
        if len(content) > 8:
            lines.append(f"  … {len(content) - 8} more stream(s)")
        lines.extend(f"  note: {note}" for note in self.notes)
        return "\n".join(lines)


def _decode_entry_name(raw: bytes) -> str:
    """Decode a validated, NUL-terminated UTF-16LE directory name."""
    return raw.decode("utf-16-le").rstrip("\x00")


def _read_fat(
    source: _RandomAccessSource, header: bytes, sector_size: int, fat_sector_count: int
) -> tuple[dict[int, int], tuple[str, ...]]:
    """Read the FAT sectors named by the header DIFAT; never assume contiguity.

    The first version treated `first DIFAT entry + i` as FAT sector `i`. That
    happened to work for one FAT sector, but CFB stores an explicit sector id
    for every FAT sector. A non-contiguous FAT is valid and must be followed by
    those ids instead of inventing adjacent ones.
    """
    notes: list[str] = []
    fat: dict[int, int] = {}
    if fat_sector_count == 0:
        return fat, ("the header declares no FAT sectors",)

    wanted = min(fat_sector_count, _MAX_FAT_SECTORS)
    if fat_sector_count > _MAX_FAT_SECTORS:
        notes.append(
            f"the header declares {fat_sector_count} FAT sectors; capped at {_MAX_FAT_SECTORS}"
        )

    # The header's 109-entry DIFAT occupies bytes 76..511. Unused entries are
    # FREESECT. A zero in an unused slot is not accepted as a made-up sector.
    fat_sector_ids: list[int] = []
    for offset in range(76, 76 + _HEADER_DIFAT_ENTRIES * 4, 4):
        sector_id = struct.unpack_from("<I", header, offset)[0]
        if sector_id not in _SPECIAL_SECTORS:
            fat_sector_ids.append(sector_id)
            if len(fat_sector_ids) >= wanted:
                break

    # If the inline DIFAT cannot name every declared FAT sector, follow its own
    # linked list. Each DIFAT sector has (sector_size/4)-1 FAT ids and a final
    # 32-bit pointer to the next DIFAT sector.
    first_difat = struct.unpack_from("<I", header, 68)[0]
    difat_count = struct.unpack_from("<I", header, 72)[0]
    if len(fat_sector_ids) < wanted and difat_count:
        difat_sector = first_difat
        visited_difat: set[int] = set()
        max_difat = min(difat_count, _MAX_DIFAT_SECTORS)
        if difat_count > _MAX_DIFAT_SECTORS:
            notes.append(
                f"the DIFAT chain declares {difat_count} sectors; capped at {_MAX_DIFAT_SECTORS}"
            )
        slots = sector_size // 4
        for _ in range(max_difat):
            if difat_sector in _SPECIAL_SECTORS:
                notes.append("the DIFAT chain ends before all FAT sectors are named")
                break
            if difat_sector in visited_difat:
                notes.append("the DIFAT chain loops; stopping at the repeat")
                break
            visited_difat.add(difat_sector)
            offset = _sector_offset(difat_sector, sector_size)
            if offset + sector_size > source.size:
                notes.append(f"DIFAT sector {difat_sector} is past the end of the supplied data")
                break
            block = source.read_at(offset, sector_size)
            if len(block) != sector_size:
                notes.append(f"DIFAT sector {difat_sector} could not be read completely")
                break
            for slot in range(slots - 1):
                sector_id = struct.unpack_from("<I", block, slot * 4)[0]
                if sector_id not in _SPECIAL_SECTORS:
                    fat_sector_ids.append(sector_id)
                    if len(fat_sector_ids) >= wanted:
                        break
            if len(fat_sector_ids) >= wanted:
                break
            difat_sector = struct.unpack_from("<I", block, (slots - 1) * 4)[0]
        if len(visited_difat) < difat_count and len(fat_sector_ids) < wanted:
            notes.append("the DIFAT chain is incomplete")

    if len(fat_sector_ids) < fat_sector_count:
        notes.append(
            f"the DIFAT names {len(fat_sector_ids)} of {fat_sector_count} declared FAT sectors"
        )

    # Duplicate sector ids make the mapping ambiguous; keep the first and mark
    # the directory unverifiable rather than quietly overwriting table entries.
    if len(set(fat_sector_ids)) != len(fat_sector_ids):
        notes.append("the DIFAT names a FAT sector more than once")

    entries_per_sector = sector_size // 4
    for index, sector_id in enumerate(fat_sector_ids[:wanted]):
        offset = _sector_offset(sector_id, sector_size)
        if offset + sector_size > source.size:
            notes.append(f"FAT sector {sector_id} is past the end of the supplied data")
            break
        block = source.read_at(offset, sector_size)
        if len(block) != sector_size:
            notes.append(f"FAT sector {sector_id} could not be read completely")
            break
        for slot in range(entries_per_sector):
            fat[index * entries_per_sector + slot] = struct.unpack_from("<I", block, slot * 4)[0]

    return fat, tuple(notes)


def _read_directory(
    source: _RandomAccessSource,
    sector_size: int,
    first_dir_sector: int,
    fat: dict[int, int],
    major_version: int,
) -> tuple[list[OleStream], bool, tuple[str, ...]]:
    """Follow the directory FAT chain, retaining safe entries on any failure."""
    streams: list[OleStream] = []
    notes: list[str] = []
    visited: set[int] = set()
    sector = first_dir_sector
    entries_per_sector = sector_size // 128
    max_directory_sectors = min(_MAX_DIRECTORY_SECTORS, _MAX_DIRECTORY_BYTES // sector_size)
    chain_ended = False
    entries_valid = True
    root_count = 0

    while True:
        if sector == _ENDOFCHAIN:
            chain_ended = True
            break
        if sector in {_FREESECT, _FATSECT, _DIFSECT}:
            notes.append(f"the directory chain stops at special sector marker 0x{sector:08x}")
            break
        if sector in visited:
            notes.append("the directory chain loops; stopping at the repeat")
            break
        if len(visited) >= max_directory_sectors:
            notes.append(f"the directory chain exceeds {max_directory_sectors} sectors; stopping")
            break
        if sector not in fat:
            notes.append(f"FAT has no entry for directory sector {sector}")
            break

        visited.add(sector)
        offset = _sector_offset(sector, sector_size)
        if offset + sector_size > source.size:
            notes.append(f"directory sector {sector} is past the end of the supplied data")
            break
        block = source.read_at(offset, sector_size)
        if len(block) != sector_size:
            notes.append(f"directory sector {sector} could not be read completely")
            break

        for index in range(entries_per_sector):
            entry_offset = index * 128
            entry = block[entry_offset : entry_offset + 128]
            kind_byte = entry[66]
            if kind_byte == _EMPTY:
                continue
            if kind_byte not in _ENTRY_KIND:
                notes.append(
                    f"directory entry {index} in sector {sector} has invalid type {kind_byte}"
                )
                entries_valid = False
                continue

            name_length = struct.unpack_from("<H", entry, 64)[0]
            if name_length < 2 or name_length > 64 or name_length % 2:
                notes.append(
                    f"directory entry {index} in sector {sector} has invalid name length "
                    f"{name_length}"
                )
                entries_valid = False
                continue
            raw_name = entry[:name_length]
            if raw_name[-2:] != b"\x00\x00":
                notes.append(
                    f"directory entry {index} in sector {sector} has no UTF-16 NUL terminator"
                )
                entries_valid = False
                continue
            try:
                name = _decode_entry_name(raw_name)
            except UnicodeDecodeError:
                notes.append(f"directory entry {index} in sector {sector} has invalid UTF-16LE")
                entries_valid = False
                continue
            if not name:
                notes.append(f"directory entry {index} in sector {sector} has an empty name")
                entries_valid = False
                continue

            size = struct.unpack_from("<Q", entry, 120)[0]
            if major_version == 3 and size >> 32:
                notes.append(f"directory entry {name!r} has a non-zero v3 size high word")
                entries_valid = False
            kind = _ENTRY_KIND[kind_byte]
            if kind_byte == _ROOT:
                root_count += 1
            streams.append(OleStream(name=name, kind=kind, size=size))

        next_sector = fat[sector]
        if next_sector == _ENDOFCHAIN:
            chain_ended = True
            break
        if next_sector == _FREESECT:
            notes.append("the directory chain points at a free sector")
            break
        if next_sector in {_FATSECT, _DIFSECT}:
            notes.append(f"the directory chain points at special sector marker 0x{next_sector:08x}")
            break
        sector = next_sector

    if root_count != 1:
        notes.append(f"the directory contains {root_count} root entries; exactly one is required")
        entries_valid = False
    if not streams:
        notes.append("the directory is empty or unreadable")

    complete = chain_ended and entries_valid
    return streams, complete, tuple(notes)


def _identify(streams: list[OleStream]) -> tuple[str, bool, tuple[str, ...]]:
    """Identify known document families from directory stream names."""
    names = {stream.name for stream in streams if stream.kind == "stream"}
    if "EncryptedPackage" in names:
        return "office-encrypted", True, ()
    for candidates, kind in _STREAM_ANY:
        if candidates & names:
            return kind, False, ()
    notes = ["the stream names match no document format this reader knows"]
    storage_names = {stream.name for stream in streams if stream.kind == "storage"}
    if storage_names:
        notes.insert(0, f"storages present: {', '.join(sorted(storage_names)[:6])}")
    return "", False, tuple(notes)


def _read_ole_source(source: _RandomAccessSource) -> OleInfo:
    """Shared parser for bytes and seek-based handles."""
    header = source.read_at(0, 512)
    if not header.startswith(MAGIC):
        return OleInfo(notes=("no OLE2 signature",))
    if len(header) < 512:
        return OleInfo(notes=(f"header is {len(header)} bytes; a compound file needs 512",))

    major = struct.unpack_from("<H", header, 26)[0]
    byte_order = struct.unpack_from("<H", header, 28)[0]
    sector_shift = struct.unpack_from("<H", header, 30)[0]
    first_dir_sector = struct.unpack_from("<I", header, 48)[0]
    fat_sector_count = struct.unpack_from("<I", header, 44)[0]

    notes: list[str] = []
    header_valid = True
    if byte_order != 0xFFFE:
        notes.append(f"unexpected byte order 0x{byte_order:04x}; expected 0xfffe")
        header_valid = False
    if major not in {3, 4}:
        notes.append(f"unexpected major version {major}")
        header_valid = False

    expected_shift = {3: 9, 4: 12}.get(major, sector_shift)
    if sector_shift != expected_shift:
        notes.append(
            f"major version {major} expects sector shift {expected_shift}, got {sector_shift}"
        )
        header_valid = False
    sector_size = 1 << sector_shift if sector_shift in {9, 12} else 512
    if sector_shift not in {9, 12}:
        notes.append(f"unexpected sector shift {sector_shift}; assuming 512")
        header_valid = False

    fat, fat_notes = _read_fat(source, header, sector_size, fat_sector_count)
    notes.extend(fat_notes)
    streams, directory_complete, directory_notes = _read_directory(
        source, sector_size, first_dir_sector, fat, major
    )
    notes.extend(directory_notes)
    document_kind, encrypted, identity_notes = _identify(streams)
    if streams and not document_kind and identity_notes:
        notes.extend(identity_notes)

    return OleInfo(
        version=f"v{major}",
        sector_size=sector_size,
        streams=tuple(streams),
        document_kind=document_kind,
        encrypted=encrypted,
        directory_complete=header_valid and not fat_notes and directory_complete,
        notes=tuple(notes),
    )


def read_ole(data: bytes) -> OleInfo:
    """Describe an OLE2 byte string, reading only its header/FAT/directory ranges."""
    return _read_ole_source(_BytesSource(data))


def read_ole_handle(handle: BinaryIO) -> OleInfo:
    """Describe a seekable compound file without sequentially reading its payload.

    Normal inspection routes through this function: it reads the header, FAT
    sectors and directory sectors by sector id. A 4 GB stream is not copied into
    memory or interpreted by this reader; a separate lineage hash may still scan
    the file in fixed-size chunks.
    """
    original_position: int | None = None
    try:
        original_position = handle.tell()
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(0)
        result = _read_ole_source(_HandleSource(handle, size))
    except (OSError, ValueError) as exc:
        result = OleInfo(notes=(f"compound file handle could not be read or sought: {exc}",))
    finally:
        if original_position is not None:
            try:
                handle.seek(original_position)
            except (OSError, ValueError):  # pragma: no cover - broken caller handle
                pass
    return result
