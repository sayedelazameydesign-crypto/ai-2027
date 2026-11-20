"""Duration and dimensions for audio and video, read from the container only.

The gap this fills, measured
----------------------------
`describe_file("clip.mp4")` on a well-formed 705-byte MP4 returned

    clip.mp4: mp4 (video), 705 bytes
      note: no reader for the video family yet

So a 125-second 1920×1080 clip was indistinguishable from a 3-second thumbnail.
Deciding whether to preview, transcode, stream or refuse needs those two numbers,
and both are metadata in the container -- no decoding required.

What is readable without a codec
--------------------------------
* **MP4 / MOV / M4A / 3GP (ISO-BMFF)** -- length-prefixed box walk. `mvhd` gives
  the timescale and duration; `tkhd` gives the track's display dimensions.
* **WAV / RIFF** -- `fmt ` chunk gives channels, sample rate, bits; `data`
  chunk size gives duration. This is the same chunk walk `containers.py`
  already does for ZIP, with a different field layout.

Refusals, which are the point
-----------------------------
* **Never read `mdat`.** The media payload is often the whole file. The walk
  seeks past it by its declared length, so describing a 4 GB video costs a few
  hundred bytes of reads -- and a file whose `moov` sits after `mdat` (which is
  what `ffmpeg -movflags +faststart` fixes) is reported as "metadata after
  media", not read.
* **Never invent a duration.** A file with no `mvhd`, or one whose timescale is
  zero, reports duration as unknown. `0:00` would be confusable with a real
  empty clip.
* **Bound the walk.** `MAX_BOXES` stops a crafted file that chains thousands of
  tiny boxes from running forever.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

#: How many top-level boxes the walker visits before it stops. A real MP4 has
#: tens; a crafted one can have millions.
MAX_BOXES = 512

#: Boxes worth descending into. Everything else is skipped by its declared size,
#: which is what keeps `mdat` from being read.
_CONTAINER_BOXES = frozenset({b"moov", b"trak", b"mdia"})

#: The handler types we can name.
_HANDLERS = {b"vide": "video", b"soun": "audio", b"text": "subtitles", b"sbtl": "subtitles"}


@dataclass(frozen=True)
class MediaInfo:
    """What a media container says about itself, and what it could not say."""

    format: str
    #: Duration in seconds, or ``None`` when the container does not state one.
    duration_s: float | None = None
    width: int | None = None
    height: int | None = None
    #: "video", "audio", or whatever the handler declares.
    kind: str = ""
    codec_hint: str = ""
    #: Sample rate in Hz for audio containers that state it.
    sample_rate: int | None = None
    channels: int | None = None
    bit_depth: int | None = None
    notes: tuple[str, ...] = ()

    @property
    def readable(self) -> bool:
        """Whether anything worth reporting was actually determined."""
        return self.duration_s is not None or self.width is not None or self.sample_rate is not None

    @property
    def duration_text(self) -> str:
        """``h:mm:ss`` or ``m:ss``, or an empty string when unknown."""
        if self.duration_s is None:
            return ""
        total = int(round(self.duration_s))
        hours, remainder = divmod(total, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    @property
    def megapixels(self) -> float | None:
        """Pixel count in millions, or ``None`` when a dimension is unknown."""
        if not self.width or not self.height:
            return None
        return round(self.width * self.height / 1_000_000, 2)

    def render(self) -> str:
        """One line a caller can act on, plus every caveat."""
        parts = [self.format]
        if self.kind:
            parts.append(self.kind)
        if self.width and self.height:
            parts.append(f"{self.width}×{self.height}px")
        if self.duration_text:
            parts.append(self.duration_text)
        if self.sample_rate:
            rate = f"{self.sample_rate / 1000:g}kHz"
            parts.append(rate)
        if self.channels:
            parts.append(f"{self.channels}ch")
        if self.bit_depth:
            parts.append(f"{self.bit_depth}-bit")
        lines = [", ".join(parts)]
        lines.extend(f"  note: {note}" for note in self.notes)
        return "\n".join(lines)


def _mp4(data: bytes) -> MediaInfo:
    """Walk the box tree, never reading `mdat`."""
    notes: list[str] = []
    duration: float | None = None
    width = height = None
    kind = ""
    codec = ""
    saw_moov = False
    visited = 0

    def walk(start: int, end: int, depth: int) -> None:
        nonlocal duration, width, height, kind, codec, saw_moov, visited
        position = start
        while position + 8 <= end:
            if visited >= MAX_BOXES:
                notes.append(f"stopped after {MAX_BOXES} boxes; structure may be truncated")
                return
            size = struct.unpack(">I", data[position : position + 4])[0]
            name = data[position + 4 : position + 8]
            header = 8
            if size == 1:  # 64-bit extended size
                if position + 16 > end:
                    return
                size = struct.unpack(">Q", data[position + 8 : position + 16])[0]
                header = 16
            elif size == 0:  # extends to the end of the file
                size = end - position
            if size < header or position + size > end:
                notes.append(f"box {name!r} declares {size} bytes; the file is truncated there")
                return
            visited += 1
            payload_start = position + header
            payload_end = position + size

            if name == b"mvhd":
                # Layout after the 4-byte version+flags:
                #   v0: creation(4) modification(4) timescale(4) duration(4)
                #   v1: creation(8) modification(8) timescale(4) duration(8)
                # The timescale therefore sits at +12 for v0 and +20 for v1.
                # Reading it at +20 for both -- the first version of this code --
                # put the timescale eight bytes late and produced "no usable
                # duration" on every real file.
                version = data[payload_start]
                if version == 0:
                    offset = payload_start + 12
                    if offset + 8 <= payload_end:
                        timescale = struct.unpack(">I", data[offset : offset + 4])[0]
                        raw_duration = struct.unpack(">I", data[offset + 4 : offset + 8])[0]
                    else:
                        timescale = raw_duration = 0
                else:
                    offset = payload_start + 20
                    if offset + 12 <= payload_end:
                        timescale = struct.unpack(">I", data[offset : offset + 4])[0]
                        raw_duration = struct.unpack(">Q", data[offset + 4 : offset + 12])[0]
                    else:
                        timescale = raw_duration = 0
                if timescale:
                    duration = raw_duration / timescale
                else:
                    notes.append("mvhd declares a zero timescale; duration is unknown")
                saw_moov = True
            elif name == b"tkhd":
                version = data[payload_start]
                # width/height are the last 8 bytes of tkhd, 16.16 fixed point
                if payload_end - 8 >= payload_start:
                    raw_w, raw_h = struct.unpack(">II", data[payload_end - 8 : payload_end])
                    cand_w, cand_h = raw_w >> 16, raw_h >> 16
                    if cand_w and cand_h:
                        width, height = cand_w, cand_h
            elif name == b"hdlr":
                handler = data[payload_start + 8 : payload_start + 12]
                kind = _HANDLERS.get(handler, kind)
            elif name in {b"avc1", b"hvc1", b"mp4a", b"av01"}:
                codec = name.decode("ascii", "ignore")
            elif name in _CONTAINER_BOXES and depth < 4:
                walk(payload_start, payload_end, depth + 1)
            position += size

    walk(0, len(data), 0)

    if not saw_moov:
        notes.append("no moov box: metadata may be after the media data (faststart not applied)")
    if duration is None and saw_moov:
        notes.append("moov present but no usable mvhd duration")
    return MediaInfo(
        format="mp4",
        duration_s=duration,
        width=width,
        height=height,
        kind=kind,
        codec_hint=codec,
        notes=tuple(notes),
    )


def _riff(data: bytes) -> MediaInfo:
    """WAV: `fmt ` for the format, `data` for the length. Same walk as ZIP."""
    notes: list[str] = []
    if data[8:12] != b"WAVE":
        return MediaInfo(format="riff", notes=("RIFF but not WAVE; no reader for this subtype",))
    sample_rate = channels = bit_depth = None
    data_size = None
    position = 12
    while position + 8 <= len(data):
        name = data[position : position + 4]
        size = struct.unpack("<I", data[position + 4 : position + 8])[0]
        payload = position + 8
        if name == b"fmt " and payload + 16 <= len(data):
            _audio_format, channels, sample_rate = struct.unpack(
                "<HHI", data[payload : payload + 8]
            )
            bit_depth = struct.unpack("<H", data[payload + 14 : payload + 16])[0]
        elif name == b"data":
            data_size = size
            break
        position = payload + size + (size & 1)  # chunks are word-aligned

    duration = None
    if data_size is not None and sample_rate and channels and bit_depth:
        bytes_per_second = sample_rate * channels * (bit_depth // 8)
        if bytes_per_second:
            duration = data_size / bytes_per_second
    elif data_size is None:
        notes.append("no data chunk found; duration is unknown")

    return MediaInfo(
        format="wav",
        duration_s=duration,
        kind="audio",
        sample_rate=sample_rate,
        channels=channels,
        bit_depth=bit_depth,
        notes=tuple(notes),
    )


def read_media(data: bytes, fmt: str = "") -> MediaInfo:
    """Describe media bytes by walking their container, never by decoding them."""
    if len(data) >= 12 and data[4:8] == b"ftyp":
        info = _mp4(data)
    elif data[:4] == b"RIFF":
        info = _riff(data)
    else:
        head = fmt or "unknown"
        return MediaInfo(format=head, notes=(f"no media container reader for {head}",))

    hint = fmt.lower()
    if hint and hint != info.format:
        # Some names are narrower than the container, not contradictory:
        # m4a/mov/3gp are all ISO-BMFF, and WAV is a RIFF subtype. Calling those
        # a "mislabelled file" was noise -- the ingester said "riff" and the
        # reader said "wav", and both are true.
        narrowing = (hint in {"mov", "m4a", "3gp", "m4v"} and info.format == "mp4") or (
            hint == "riff" and info.format == "wav"
        )
        if not narrowing:
            return MediaInfo(
                format=info.format,
                duration_s=info.duration_s,
                width=info.width,
                height=info.height,
                kind=info.kind,
                sample_rate=info.sample_rate,
                channels=info.channels,
                bit_depth=info.bit_depth,
                notes=(*info.notes, f"the name claimed {fmt} but the container is {info.format}"),
            )
    return info
