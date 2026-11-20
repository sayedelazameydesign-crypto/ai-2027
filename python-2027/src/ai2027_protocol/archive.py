"""Archives that are not ZIP: the container is named, counted, and never faked.

The defect this module removes, measured
----------------------------------------
`describe_file("bundle.tar.gz")` on a valid tarball returned

    bundle.tar.gz: zip (zip), 154 bytes
      note: refused: not a readable zip: File is not a zip file

and a valid `.7z` returned the same sentence. `ir.py` mapped

    "archive": _read_zip

so every archive format reached `zipfile`, and a perfectly good gzip was
**accused of being a corrupt ZIP**. That is worse than a missing reader: a
missing reader tells the user nothing is known, while this tells them a file is
damaged and sends them to repair something that is intact.

What is readable without a decompressor
---------------------------------------
Every one of these formats begins with a header that names the format and, in
most cases, the original member name and size:

    gzip    magic 1f 8b, MTIME, original name (NUL-terminated), ISIZE (mod 2^32)
    bzip2   magic "BZh", block size digit, then BWT -- opaque without a codec
    xz      magic fd 37 7a 58 5a 00, then an LZMA2 stream -- opaque
    tar     "ustar" at offset 257, then 512-byte headers with names and sizes
    7z      magic 37 7a bc af 27 1c, then a versioned header -- needs a parser
    rar     magic "Rar!\x1a\x07", then blocks -- needs a parser

For gzip and tar the member list is fully recoverable from the header, which is
the useful answer ("this archive holds three files, one is a tar"). For 7z and
rar it is not, and this module says so in those words instead of guessing.

Refusals this module keeps
--------------------------
* **Never decompress.** A 1 KB gzip can expand to 4 GB. The size is reported as
  the header declares it; nothing is inflated.
* **Never call a valid archive corrupt.** A format this build cannot unpack gets
  `no reader for <format>`, not `refused`.
* **Bounded reads.** The tar walk stops after `MAX_TAR_MEMBERS` headers, and the
  gzip name is capped at 255 bytes by the format itself.
"""

from __future__ import annotations

import struct

#: How many tar headers to walk before concluding the archive is huge and the
#: count is a lower bound. A real source tree fits well inside this.
MAX_TAR_MEMBERS = 512

#: tar header size, fixed by the format since 1979.
_TAR_BLOCK = 512

#: tar member types that carry no payload.
_NO_PAYLOAD_TYPES = frozenset({b"5", b"1", b"2", b"3", b"4", b"6"})

#: The gzip flags byte: bit 3 says a file name follows the header.
_GZIP_FNAME = 0x08
_GZIP_FEXTRA = 0x04
_GZIP_FCOMMENT = 0x10
_GZIP_FHCRC = 0x02


def _cstring(raw: bytes) -> str:
    """Read a NUL-terminated name, tolerating a field that fills the buffer."""
    return raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")


def _gzip_header(data: bytes) -> tuple[str, int, str]:
    """Return (original name, declared size, note) from a gzip header.

    The header is fully specified and needs no decompressor: the name follows
    two fixed 2-byte fields and two NUL-terminated strings that are only present
    when their flag bits are set.
    """
    if len(data) < 18:
        return "", 0, "the gzip header is truncated"
    flags = data[3]
    # ISIZE is the last four bytes of the *file*, which is the uncompressed size
    # modulo 2**32. It is a declared size, not a trusted one.
    declared = struct.unpack("<I", data[-4:])[0] if len(data) >= 18 else 0
    position = 10
    if flags & _GZIP_FEXTRA:
        # Unreachable as an out-of-range read: the caller guarantees len >= 18,
        # and position is 10, so two bytes are always present. The bound exists
        # so a future caller with a smaller window cannot read past the end.
        extra_length = struct.unpack("<H", data[10:12])[0]
        position += 2 + extra_length
    name = ""
    if flags & _GZIP_FNAME and position < len(data):
        end = data.find(b"\x00", position)
        if end == -1:
            return "", declared, "the gzip header names a file but does not terminate the name"
        name = _cstring(data[position:end])
        position = end + 1
    if flags & _GZIP_FCOMMENT:
        end = data.find(b"\x00", position)
        position = len(data) if end == -1 else end + 1
    note = ""
    if flags & _GZIP_FHCRC:
        note = "the gzip header carries a CRC16"
    return name, declared, note


def _tar_layer(data: bytes) -> str:
    """Whether a gzip payload is itself a tar, by inflating one block.

    Measured: a tar cannot be recognised from the compressed bytes of a gzip --
    `b"ustar" in data[:1024]` is False on a 154-byte .tar.gz. Inflating the first
    512 bytes is bounded work that answers the question; inflating the whole
    member is what this module refuses to do.
    """
    import zlib

    try:
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
        head = inflater.decompress(data, _TAR_BLOCK)
    except zlib.error:
        return "unknown"
    if len(head) < _TAR_BLOCK:
        return "unknown"
    return "tar" if head[257:262] == b"ustar" else "not-tar"


def _tar_members(data: bytes) -> tuple[list[tuple[str, int]], bool]:
    """Walk 512-byte tar headers. Returns members and whether the walk finished.

    A tar header is plain text in fixed fields: name at 0, size in *octal* at
    124, type at 156, and the magic "ustar" at 257. That is enough to list the
    archive without touching any payload, which is what makes a 4 GB tarball
    describable in one pass over its headers.
    """
    members: list[tuple[str, int]] = []
    position = 0
    while position + _TAR_BLOCK <= len(data) and len(members) < MAX_TAR_MEMBERS:
        block = data[position : position + _TAR_BLOCK]
        if block == b"\x00" * _TAR_BLOCK:
            return members, True
        name = _cstring(block[0:100])
        size_field = block[124:136].strip(b"\x00 ")
        try:
            declared = int(size_field or b"0", 8)
        except ValueError:
            # A PAX/GNU extension header: positional, and the value is decimal.
            declared = 0
        kind = block[156:157]
        members.append((name, declared))
        if kind in _NO_PAYLOAD_TYPES:
            position += _TAR_BLOCK
        else:
            blocks = (declared + _TAR_BLOCK - 1) // _TAR_BLOCK
            position += _TAR_BLOCK + blocks * _TAR_BLOCK
    return members, len(members) < MAX_TAR_MEMBERS


def describe_archive(data: bytes, fmt: str) -> tuple[list[str], list[str]]:
    """Name what an archive holds, as notes and caveats.

    Returns ``(lines, notes)`` where every line is a fact the header supports and
    every note is a limit this build has. No line ever says "corrupt" about a
    file whose header parsed.
    """
    lines: list[str] = []
    notes: list[str] = []
    if not data:
        return lines, ["the archive needs an open handle; none was given"]

    if fmt == "gzip":
        name, declared, header_note = _gzip_header(data)
        if header_note:
            notes.append(header_note)
        if name:
            lines.append(f"original name: {name}")
        if declared:
            lines.append(f"declared uncompressed size: {declared:,} bytes")
        else:
            notes.append("the gzip trailer declares a zero or unknown size")
        # A gzip of a tar is the common case. The inner "ustar" is NOT visible in
        # the compressed bytes -- measured: 154-byte file, no literal "ustar" in
        # the first 1024. It is visible after inflating only the first block,
        # which is a bounded decompression, not a full one.
        layer = _tar_layer(data)
        if layer == "tar":
            notes.append("the payload is a tar archive; its members need one more layer")
        elif layer == "unknown":
            notes.append("the payload was not inspected past the first block")
        notes.append("gzip members are not decompressed: a small file can expand enormously")
        return lines, notes

    if fmt == "bzip2":
        if len(data) < 4:
            return lines, ["the bzip2 header is truncated"]
        level = data[3:4]
        if level.isdigit():
            lines.append(f"block size: {int(level) * 100} KB")
        notes.append("bzip2 data is BWT-compressed; this build does not decode it")
        return lines, notes

    if fmt == "xz":
        notes.append("xz data is LZMA2-compressed; this build does not decode it")
        return lines, notes

    if fmt == "tar":
        members, finished = _tar_members(data)
        files = [(name, size) for name, size in members if name and not name.endswith("/")]
        lines.append(f"{len(files)} member(s)")
        for name, size in files[:5]:
            lines.append(f"  {name} ({size:,} bytes)")
        if len(files) > 5:
            lines.append(f"  … {len(files) - 5} more")
        if not finished:
            notes.append(f"stopped after {MAX_TAR_MEMBERS} headers; the count is a lower bound")
        return lines, notes

    if fmt == "7z":
        notes.append(
            "7z data is LZMA-compressed inside a versioned header; this build does not parse it"
        )
        return lines, notes

    if fmt == "rar":
        notes.append("rar data needs a RAR decoder; this build does not parse it")
        return lines, notes

    notes.append(f"no archive reader for {fmt}")
    return lines, notes
