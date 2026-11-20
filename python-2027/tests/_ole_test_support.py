"""Small, realistic CFB byte fixtures shared by OLE reader tests."""

from __future__ import annotations

import io
import struct

from ai2027_protocol.ole import MAGIC

ROOT = ("Root Entry", 5)
STREAM = 2
STORAGE = 1


class _ShortReadAt(io.BytesIO):
    """A file handle that deliberately returns one byte short at one sector."""

    def __init__(self, data: bytes, short_offset: int) -> None:
        super().__init__(data)
        self.short_offset = short_offset

    def read(self, size: int | None = None) -> bytes:
        if self.tell() == self.short_offset and size is not None and size > 0:
            size -= 1
        return super().read(size)


class _Unseekable(io.BytesIO):
    def tell(self) -> int:
        raise OSError("not seekable")


def _entry(name: str, kind: int, size: int = 1024) -> bytes:
    entry = bytearray(128)
    encoded = name.encode("utf-16-le") + b"\x00\x00"
    entry[0 : len(encoded)] = encoded
    entry[64:66] = struct.pack("<H", len(encoded))
    entry[66] = kind
    entry[120:128] = struct.pack("<Q", size)
    return bytes(entry)


def _cfb(entries: list[tuple[str, int]], *, on_disk: int | None = None) -> bytes:
    """A CFB with one 512-byte directory sector."""
    header = bytearray(512)
    header[0:8] = MAGIC
    header[26:28] = struct.pack("<H", 3)
    header[28:30] = struct.pack("<H", 0xFFFE)  # little-endian marker
    header[30:32] = struct.pack("<H", 9)
    header[44:48] = struct.pack("<I", 1)  # one FAT sector
    header[48:52] = struct.pack("<I", 0)  # directory at sector 0
    header[76:512] = struct.pack("<I", 0xFFFFFFFF) * 109  # unused DIFAT slots
    header[76:80] = struct.pack("<I", 1)  # FAT at sector 1
    directory = b"".join(
        _entry(name, kind, on_disk if on_disk is not None else 1024) for name, kind in entries
    )
    directory += b"\x00" * (512 - len(directory))
    fat = struct.pack("<I", 0xFFFFFFFE) + struct.pack("<I", 0xFFFFFFFF) * 127
    return bytes(header) + directory + fat


def _cfb_two_sectors() -> bytes:
    """A CFB whose directory spans two sectors, linked by a real FAT."""
    header = bytearray(512)
    header[0:8] = MAGIC
    header[26:28] = struct.pack("<H", 3)
    header[28:30] = struct.pack("<H", 0xFFFE)
    header[30:32] = struct.pack("<H", 9)
    header[44:48] = struct.pack("<I", 1)
    header[48:52] = struct.pack("<I", 0)
    header[76:512] = struct.pack("<I", 0xFFFFFFFF) * 109
    header[76:80] = struct.pack("<I", 2)
    dir0 = (
        _entry("Root Entry", 5)
        + _entry("WordDocument", STREAM)
        + _entry("1Table", STREAM)
        + _entry("CompObj", STREAM)
    )
    dir1 = _entry("Data", STREAM) + _entry("Stream4", STREAM) + _entry("Macros", STREAM)
    dir1 += b"\x00" * 128
    fat = struct.pack("<I", 1) + struct.pack("<I", 0xFFFFFFFE) + struct.pack("<I", 0xFFFFFFFF) * 126
    return bytes(header) + dir0 + dir1 + fat


def _cfb_v4(entries: list[tuple[str, int]]) -> bytes:
    """A v4 compound file: 4096-byte sectors, so 32 entries per sector."""
    header = bytearray(512)
    header[0:8] = MAGIC
    header[26:28] = struct.pack("<H", 4)
    header[28:30] = struct.pack("<H", 0xFFFE)
    header[30:32] = struct.pack("<H", 12)
    header[44:48] = struct.pack("<I", 1)
    header[48:52] = struct.pack("<I", 0)
    header[76:512] = struct.pack("<I", 0xFFFFFFFF) * 109
    header[76:80] = struct.pack("<I", 1)
    directory = b"".join(_entry(name, kind) for name, kind in entries)
    directory += b"\x00" * (4096 - len(directory))
    fat = struct.pack("<I", 0xFFFFFFFE) + struct.pack("<I", 0xFFFFFFFF) * 1023
    # In v4, sector zero starts after the full 4096-byte header area.
    return bytes(header) + b"\x00" * (4096 - len(header)) + directory + fat


def _base_header(
    *, major: int = 3, sector_shift: int | None = None, fat_sectors: int = 1, first_dir: int = 0
) -> bytearray:
    """A valid CFB header with all unused DIFAT entries marked FREESECT."""
    header = bytearray(512)
    header[0:8] = MAGIC
    header[26:28] = struct.pack("<H", major)
    header[28:30] = struct.pack("<H", 0xFFFE)
    header[30:32] = struct.pack(
        "<H", sector_shift if sector_shift is not None else (9 if major == 3 else 12)
    )
    header[44:48] = struct.pack("<I", fat_sectors)
    header[48:52] = struct.pack("<I", first_dir)
    header[76:512] = struct.pack("<I", 0xFFFFFFFF) * 109
    return header


def _cfb_noncontiguous_fat() -> bytes:
    """Two FAT sectors at ids 1 and 3; directory sector 128 uses FAT #2."""
    header = _base_header(fat_sectors=2, first_dir=128)
    header[76:80] = struct.pack("<I", 1)
    header[80:84] = struct.pack("<I", 3)
    sectors = bytearray(129 * 512)
    # FAT sector 1 maps regular sectors 0..127, none of which is the directory.
    sectors[1 * 512 : 2 * 512] = b"\xff" * 512
    # FAT sector 3 maps regular sectors 128..255; sector 128 ends here.
    fat2 = 3 * 512
    sectors[fat2 : fat2 + 4] = struct.pack("<I", 0xFFFFFFFE)
    directory = _entry("Root Entry", 5) + _entry("Workbook", STREAM)
    directory += b"\x00" * (512 - len(directory))
    sectors[128 * 512 : 129 * 512] = directory
    return bytes(header) + bytes(sectors)


def _cfb_difat_chain() -> bytes:
    """110 FAT sectors: 109 ids inline plus the 110th in DIFAT sector 110."""
    header = _base_header(fat_sectors=110)
    header[76:512] = b"".join(struct.pack("<I", sector) for sector in range(1, 110))
    header[68:72] = struct.pack("<I", 110)
    header[72:76] = struct.pack("<I", 1)
    sectors = bytearray(b"\xff" * (112 * 512))  # sector ids 0 through 111
    directory = _entry("Root Entry", 5) + _entry("WordDocument", STREAM)
    directory += b"\x00" * (512 - len(directory))
    sectors[0:512] = directory
    # FAT[0] (directory sector 0) = ENDOFCHAIN.
    sectors[512:516] = struct.pack("<I", 0xFFFFFFFE)
    # DIFAT sector 110 names FAT sector 111; the last slot ends the DIFAT chain.
    difat_offset = 110 * 512
    sectors[difat_offset : difat_offset + 4] = struct.pack("<I", 111)
    sectors[difat_offset + 508 : difat_offset + 512] = struct.pack("<I", 0xFFFFFFFE)
    return bytes(header) + bytes(sectors)


def _cfb_difat_loop() -> bytes:
    """109 FAT ids inline; a malformed DIFAT sector points back to itself."""
    header = _base_header(fat_sectors=110)
    header[76:512] = b"".join(struct.pack("<I", sector) for sector in range(1, 110))
    header[68:72] = struct.pack("<I", 110)
    header[72:76] = struct.pack("<I", 2)
    sectors = bytearray(b"\xff" * (111 * 512))  # sector ids 0 through 110
    directory = _entry("Root Entry", 5) + _entry("WordDocument", STREAM)
    directory += b"\x00" * (512 - len(directory))
    sectors[0:512] = directory
    sectors[512:516] = struct.pack("<I", 0xFFFFFFFE)
    sectors[110 * 512 + 508 : 110 * 512 + 512] = struct.pack("<I", 110)
    return bytes(header) + bytes(sectors)


def _cfb_long_directory() -> bytes:
    """A directory chain that needs 1,025 sectors, one past the reader's cap."""
    directory_count = 1025
    fat_count = 9  # 9 * 128 FAT entries cover the 1,025 directory sectors.
    first_fat = directory_count
    header = _base_header(fat_sectors=fat_count, first_dir=0)
    header[76 : 76 + fat_count * 4] = b"".join(
        struct.pack("<I", first_fat + index) for index in range(fat_count)
    )
    sectors = bytearray((directory_count + fat_count) * 512)
    sectors[0:512] = _entry("Root Entry", 5) + b"\x00" * (512 - 128)
    fat_values = (
        [index + 1 for index in range(directory_count - 1)]
        + [0xFFFFFFFE]
        + [0xFFFFFFFF] * (fat_count * 128 - directory_count)
    )
    for index in range(fat_count):
        start = index * 128
        chunk = b"".join(struct.pack("<I", value) for value in fat_values[start : start + 128])
        sector_id = first_fat + index
        sectors[sector_id * 512 : (sector_id + 1) * 512] = chunk
    return bytes(header) + bytes(sectors)
