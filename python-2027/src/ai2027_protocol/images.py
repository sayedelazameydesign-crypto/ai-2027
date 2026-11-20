"""Image metadata without an image library.

The gap this fills, measured
----------------------------
`describe_file("a.png")` on a real 1920×1080 PNG returned

    a.png: png (image), 53 bytes
      note: no reader for the image family yet

So the system knew it was a PNG and nothing else: not the dimensions, not
whether it was a photo or a 16×16 icon, not whether it was RGB or a palette.
That is the difference between "here is a picture" and "here is a 1920×1080
RGB picture, 1.778 aspect, 8-bit — safe to preview inline".

Why not Pillow
--------------
The sandbox runs stdlib-only with a sanitised environment. Adding an image
library for four integers at a fixed header offset is a supply-chain surface
bought for arithmetic — the same reasoning recorded in `ir.py` for the parquet
footer and in `containers.py` for the ZIP directory.

What is readable without a decoder
----------------------------------
* **PNG** — IHDR is a fixed 13-byte chunk at offset 16: width, height, bit
  depth, colour type. Exact, and the file cannot lie about them without being
  an invalid PNG.
* **JPEG** — dimensions live in a SOF marker found by walking the segment
  chain; normally within the first few hundred bytes.
* **GIF** — logical screen descriptor at offset 6, little-endian.
* **BMP** — DIB header at offset 18, little-endian, negative height for
  top-down bitmaps.

Anything undeterminable is reported as unknown rather than guessed: "0×0" would
be indistinguishable from a real empty canvas, which is the failure mode this
whole layer exists to refuse.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

#: PNG colour types, per the specification's table.
_PNG_COLOUR = {
    0: "greyscale",
    2: "RGB",
    3: "palette",
    4: "greyscale+alpha",
    6: "RGBA",
}

#: JPEG frame markers that carry dimensions. Any of them means "this is a SOF".
_JPEG_SOF_MARKERS = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)

#: How far into a JPEG the SOF marker is searched for. A frame header beyond
#: this is not a real file; stopping means a crafted input cannot make the
#: reader walk 4 GB.
_JPEG_SCAN_LIMIT = 1_000_000


@dataclass(frozen=True)
class ImageInfo:
    """What an image's own header says about it, and what it could not say."""

    format: str
    width: int | None = None
    height: int | None = None
    #: Bits per channel, when the format states it.
    bit_depth: int | None = None
    #: A human name for the colour model when the format states it.
    colour: str = ""
    #: How many pages/frames the file declares, when it declares one.
    frames: int | None = None
    #: Everything the reader could not determine, named explicitly.
    notes: tuple[str, ...] = ()

    @property
    def aspect(self) -> float | None:
        """Width ÷ height, or ``None`` when a dimension is unknown."""
        if not self.width or not self.height:
            return None
        return round(self.width / self.height, 3)

    @property
    def megapixels(self) -> float | None:
        """Pixel count in millions, or ``None`` when a dimension is unknown."""
        if not self.width or not self.height:
            return None
        return round(self.width * self.height / 1_000_000, 2)

    @property
    def readable(self) -> bool:
        """Whether a preview can be sized without decoding the pixels."""
        return bool(self.width and self.height)

    def render(self) -> str:
        """One line a caller can act on, plus every caveat."""
        if self.readable:
            # `readable` guarantees both dimensions, so `megapixels` and `aspect`
            # cannot be None here. Checking anyway would be a branch no input can
            # reach, and a "defensive" branch that can never run is dead weight
            # dressed as caution.
            parts = [f"{self.format} {self.width}×{self.height}px"]
            parts.append(f"{self.megapixels}MP")
            parts.append(f"{self.aspect} aspect")
            if self.bit_depth:
                parts.append(f"{self.bit_depth}-bit")
            if self.colour:
                parts.append(self.colour)
            if self.frames is not None and self.frames > 1:
                parts.append(f"{self.frames} frames")
            lines = [", ".join(parts)]
        else:
            lines = [f"{self.format}: dimensions unreadable"]
        lines.extend(f"  note: {note}" for note in self.notes)
        return "\n".join(lines)


def _png(data: bytes) -> ImageInfo:
    """IHDR is a fixed 13-byte chunk at offset 16; dimensions are big-endian."""
    if len(data) < 33 or data[12:16] != b"IHDR":
        return ImageInfo(format="png", notes=("no IHDR chunk at the expected offset",))
    width, height, bit_depth, colour_type = struct.unpack(">IIBB", data[16:26])
    notes: list[str] = []
    if width == 0 or height == 0:
        notes.append("the header declares a zero dimension; the file is invalid")
    return ImageInfo(
        format="png",
        width=width,
        height=height,
        bit_depth=bit_depth,
        colour=_PNG_COLOUR.get(colour_type, f"colour type {colour_type}"),
        notes=tuple(notes),
    )


def _jpeg(data: bytes) -> ImageInfo:
    """Walk the segment chain to the first SOF marker and read its dimensions."""
    position = 2  # past the SOI marker
    limit = min(len(data), _JPEG_SCAN_LIMIT)
    while position + 4 <= limit:
        if data[position] != 0xFF:
            position += 1
            continue
        marker = data[position + 1]
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            position += 2
            continue
        if marker == 0xDA:  # start of scan: no SOF came before the image data
            break
        length = struct.unpack(">H", data[position + 2 : position + 4])[0]
        if length < 2:
            break
        if marker in _JPEG_SOF_MARKERS and position + 9 <= len(data):
            height, width = struct.unpack(">HH", data[position + 5 : position + 9])
            return ImageInfo(
                format="jpeg",
                width=width,
                height=height,
                bit_depth=data[position + 4],
                colour="YCbCr" if marker != 0xC0 else "baseline",
            )
        position += 2 + length
    return ImageInfo(format="jpeg", notes=("no SOF marker found before the image data",))


def _gif(data: bytes) -> ImageInfo:
    """The logical screen descriptor at offset 6 is little-endian."""
    if len(data) < 10:
        return ImageInfo(format="gif", notes=("file is too short for a screen descriptor",))
    width, height = struct.unpack("<HH", data[6:10])
    # Counting frames needs a block walk. Reporting "1 frame" for an animation
    # would be a guess, so the note says it was not counted.
    return ImageInfo(
        format="gif",
        width=width,
        height=height,
        notes=("frame count needs a block walk; not counted",),
    )


def _bmp(data: bytes) -> ImageInfo:
    """The DIB header at offset 18 is little-endian; height may be negative."""
    if len(data) < 26:
        return ImageInfo(format="bmp", notes=("file is too short for a DIB header",))
    width, height = struct.unpack("<ii", data[18:26])
    notes: list[str] = []
    if height < 0:
        notes.append("top-down bitmap (negative height)")
        height = -height
    return ImageInfo(format="bmp", width=width, height=height, notes=tuple(notes))


def read_image(data: bytes, fmt: str = "") -> ImageInfo:
    """Describe image bytes by their own header, without decoding pixels.

    `fmt` is a hint from the ingester; the header decides. A hint the bytes deny
    is reported, because a `.png` that is really a JPEG is exactly the kind of
    mislabel this module should expose rather than reproduce.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        info = _png(data)
    elif data.startswith(b"\xff\xd8"):
        info = _jpeg(data)
    elif data[:6] in {b"GIF87a", b"GIF89a"}:
        info = _gif(data)
    elif data.startswith(b"BM"):
        info = _bmp(data)
    else:
        head = fmt or "unknown"
        return ImageInfo(format=head, notes=(f"no image header reader for {head}",))

    hint = fmt.lower()
    if hint and hint not in info.format and not info.format.startswith(hint):
        return ImageInfo(
            format=info.format,
            width=info.width,
            height=info.height,
            bit_depth=info.bit_depth,
            colour=info.colour,
            frames=info.frames,
            notes=(*info.notes, f"the name claimed {fmt} but the header is {info.format}"),
        )
    return info
