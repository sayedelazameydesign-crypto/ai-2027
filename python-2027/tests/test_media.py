"""Duration and dimensions from the container, without decoding a frame.

The two failures worth guarding are both about *not* reading:
`mdat` is often the whole file and must be skipped by its declared length, and
a container that does not state a duration must say so rather than render
"0:00", which is indistinguishable from a real empty clip.
"""

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from ai2027_protocol.adapters import Operation, default_registry
from ai2027_protocol.delivery import DeliveryForm, plan_delivery
from ai2027_protocol.ir import describe_file
from ai2027_protocol.media import MAX_BOXES, read_media
from ai2027_protocol.verify import verify_file


def _box(name: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + name + payload


def _mvhd(timescale: int = 1000, duration: int = 125_000) -> bytes:
    """mvhd version 0: timescale at +12, duration at +16 (after version+flags)."""
    return _box(
        b"mvhd",
        b"\x00" * 12 + struct.pack(">I", timescale) + struct.pack(">I", duration),
    )


def _mvhd_v1(timescale: int = 600, duration: int = 90_000) -> bytes:
    """mvhd version 1: timescale at +20, duration 8 bytes at +24."""
    return _box(
        b"mvhd",
        b"\x01"
        + b"\x00" * 3
        + struct.pack(">Q", 0)
        + struct.pack(">Q", 0)
        + struct.pack(">I", timescale)
        + struct.pack(">Q", duration),
    )


def _tkhd(width: int = 1920, height: int = 1080) -> bytes:
    """tkhd stores width/height as 16.16 fixed point in the last 8 bytes."""
    return _box(b"tkhd", b"\x00" * 76 + struct.pack(">II", width << 16, height << 16))


def _mp4(*, with_moov: bool = True, mdat_first: bool = True) -> bytes:
    ftyp = _box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2avc1mp41")
    moov = _box(
        b"moov",
        _mvhd() + _box(b"trak", _tkhd() + _box(b"hdlr", b"\x00" * 8 + b"vide" + b"\x00" * 13)),
    )
    mdat = _box(b"mdat", b"\x00" * 500)
    if not with_moov:
        return ftyp + mdat
    return ftyp + mdat + moov if mdat_first else ftyp + moov + mdat


def _wav(seconds: int = 90, rate: int = 44_100, channels: int = 2, bits: int = 16) -> bytes:
    block = rate * channels * (bits // 8)
    fmt = (
        b"WAVE"
        + b"fmt "
        + struct.pack("<I", 16)
        + struct.pack(
            "<HHIIHH",
            1,
            channels,
            rate,
            rate * channels * (bits // 8),
            channels * (bits // 8),
            bits,
        )
    )
    return (
        b"RIFF"
        + struct.pack("<I", 4 + len(fmt) + 8)
        + fmt
        + b"data"
        + struct.pack("<I", block * seconds)
    )


class Mp4Tests(unittest.TestCase):
    def test_duration_and_dimensions(self) -> None:
        info = read_media(_mp4())
        self.assertEqual(info.duration_s, 125.0)
        self.assertEqual((info.width, info.height), (1920, 1080))
        self.assertEqual(info.kind, "video")

    def test_version_1_mvhd_offsets(self) -> None:
        # The first implementation read the timescale at a fixed +20, which is
        # correct only for version 1; version 0 files reported no duration.
        info = read_media(_box(b"ftyp", b"isom" + b"\x00" * 8) + _box(b"moov", _mvhd_v1()))
        self.assertEqual(info.duration_s, 150.0)

    def test_moov_before_mdat_works_too(self) -> None:
        info = read_media(_mp4(mdat_first=False))
        self.assertEqual(info.duration_s, 125.0)

    def test_missing_moov_is_reported(self) -> None:
        info = read_media(_mp4(with_moov=False))
        self.assertIsNone(info.duration_s)
        self.assertTrue(any("no moov" in note for note in info.notes))

    def test_mdat_is_skipped_not_read(self) -> None:
        # A 4 MB mdat must cost nothing: the walk jumps its declared length.
        big = (
            _box(b"ftyp", b"isom" + b"\x00" * 8)
            + _box(b"mdat", b"\x00" * 4_000_000)
            + _box(b"moov", _mvhd())
        )
        self.assertEqual(read_media(big).duration_s, 125.0)

    def test_zero_timescale_is_not_a_division_error(self) -> None:
        info = read_media(_box(b"ftyp", b"isom" + b"\x00" * 8) + _box(b"moov", _mvhd(timescale=0)))
        self.assertIsNone(info.duration_s)
        self.assertTrue(any("zero timescale" in note for note in info.notes))

    def test_box_larger_than_the_file_is_truncation(self) -> None:
        broken = (
            _box(b"ftyp", b"isom" + b"\x00" * 8)
            + struct.pack(">I", 999_999)
            + b"mdat"
            + b"\x00" * 8
        )
        info = read_media(broken)
        self.assertTrue(any("truncated" in note for note in info.notes))

    def test_box_walk_is_bounded(self) -> None:
        # A crafted file of tiny boxes must not run forever.
        filler = b"".join(_box(b"free", b"") for _ in range(MAX_BOXES + 10))
        info = read_media(_box(b"ftyp", b"isom" + b"\x00" * 8) + filler)
        self.assertTrue(any("stopped after" in note for note in info.notes))


class WavTests(unittest.TestCase):
    def test_duration_rate_channels_depth(self) -> None:
        info = read_media(_wav(90))
        self.assertEqual(info.duration_s, 90.0)
        self.assertEqual(info.sample_rate, 44_100)
        self.assertEqual(info.channels, 2)
        self.assertEqual(info.bit_depth, 16)

    def test_mono_8bit_is_measured_not_assumed(self) -> None:
        info = read_media(_wav(10, rate=8_000, channels=1, bits=8))
        self.assertEqual(info.duration_s, 10.0)
        self.assertEqual(info.channels, 1)

    def test_riff_that_is_not_wave_is_named(self) -> None:
        info = read_media(b"RIFF" + struct.pack("<I", 4) + b"AVI " + b"\x00" * 8)
        self.assertFalse(info.readable)
        self.assertTrue(any("not WAVE" in note for note in info.notes))

    def test_no_data_chunk_is_reported(self) -> None:
        info = read_media(
            b"RIFF"
            + struct.pack("<I", 4)
            + b"WAVE"
            + b"fmt "
            + struct.pack("<I", 16)
            + b"\x00" * 16
        )
        self.assertIsNone(info.duration_s)
        self.assertTrue(any("no data chunk" in note for note in info.notes))

    def test_riff_is_a_narrower_name_not_a_mislabel(self) -> None:
        # The ingester names RIFF files "riff"; the reader says "wav". Both true.
        info = read_media(_wav(5), "riff")
        self.assertEqual(info.format, "wav")
        self.assertFalse(any("claimed" in note for note in info.notes))


class RenderingTests(unittest.TestCase):
    def test_duration_text_formats(self) -> None:
        from ai2027_protocol.media import MediaInfo

        self.assertEqual(MediaInfo(format="mp4", duration_s=125).duration_text, "2:05")
        self.assertEqual(MediaInfo(format="mp4", duration_s=3725).duration_text, "1:02:05")
        self.assertEqual(MediaInfo(format="mp4", duration_s=0).duration_text, "0:00")
        self.assertEqual(MediaInfo(format="mp4").duration_text, "")

    def test_megapixels(self) -> None:
        from ai2027_protocol.media import MediaInfo

        self.assertAlmostEqual(
            MediaInfo(format="mp4", width=1920, height=1080).megapixels or 0, 2.07, places=2
        )
        self.assertIsNone(MediaInfo(format="mp4").megapixels)

    def test_unknown_container_says_so(self) -> None:
        info = read_media(b"OggS" + b"\x00" * 40, "ogg")
        self.assertFalse(info.readable)
        self.assertIn("no media container reader", info.render())


class IntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_media_adapter_claims_both_families(self) -> None:
        from ai2027_protocol.ingest import probe_bytes

        for head in (b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 20, b"RIFF" + b"\x00" * 20):
            adapter = default_registry().for_probe(probe_bytes(head))
            self.assertIsNotNone(adapter, head[:8])
            assert adapter is not None
            self.assertEqual(adapter.name, "media")
            self.assertTrue(adapter.supports(Operation.RENDER))

    def test_describe_file_reports_the_duration(self) -> None:
        path = self.tmp / "clip.mp4"
        path.write_bytes(_mp4())
        rendered = describe_file(str(path)).render()
        self.assertIn("2:05", rendered)
        self.assertNotIn("no reader for the video family", rendered)

    def test_valid_video_delivers_native(self) -> None:
        path = self.tmp / "clip.mp4"
        path.write_bytes(_mp4())
        described = describe_file(str(path))
        plan = plan_delivery(described, verification=verify_file(path, kind=described.kind))
        self.assertIs(plan.primary.form, DeliveryForm.NATIVE)
        self.assertEqual(plan.primary.widget, "video")

    def test_valid_audio_delivers_native(self) -> None:
        path = self.tmp / "voice.wav"
        path.write_bytes(_wav(30))
        described = describe_file(str(path))
        plan = plan_delivery(described, verification=verify_file(path, kind=described.kind))
        self.assertIs(plan.primary.form, DeliveryForm.NATIVE)
        self.assertEqual(plan.primary.widget, "audio")

    def test_video_without_moov_fails_validation(self) -> None:
        path = self.tmp / "headless.mp4"
        path.write_bytes(_mp4(with_moov=False))
        verdict = verify_file(path, display_path="headless.mp4", kind="mp4")
        self.assertFalse(verdict.ok)


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()


class BoxLayoutTests(unittest.TestCase):
    """The layouts real files use, which the happy-path fixtures skip."""

    def test_64bit_box_size(self) -> None:
        # size==1 means a 64-bit length follows the name (used for >4GB mdat).
        payload = _mvhd()
        box = struct.pack(">I", 1) + b"moov" + struct.pack(">Q", 16 + len(payload)) + payload
        info = read_media(_box(b"ftyp", b"isom" + b"\x00" * 8) + box)
        self.assertEqual(info.duration_s, 125.0)

    def test_64bit_box_that_does_not_fit_is_ignored(self) -> None:
        broken = _box(b"ftyp", b"isom" + b"\x00" * 8) + struct.pack(">I", 1) + b"moov"
        info = read_media(broken)
        self.assertIsNone(info.duration_s)

    def test_zero_size_means_to_end_of_file(self) -> None:
        # size==0: the box extends to EOF. Common for the last mdat.
        head = _box(b"ftyp", b"isom" + b"\x00" * 8) + _box(b"moov", _mvhd())
        tail = struct.pack(">I", 0) + b"mdat" + b"\x00" * 100
        info = read_media(head + tail)
        self.assertEqual(info.duration_s, 125.0)

    def test_codec_box_is_named(self) -> None:
        payload = _box(b"avc1", b"\x00" * 8)
        info = read_media(_box(b"ftyp", b"isom" + b"\x00" * 8) + _box(b"moov", _mvhd() + payload))
        self.assertEqual(info.codec_hint, "avc1")

    def test_audio_handler_is_named(self) -> None:
        payload = _box(b"hdlr", b"\x00" * 8 + b"soun" + b"\x00" * 13)
        info = read_media(_box(b"ftyp", b"isom" + b"\x00" * 8) + _box(b"moov", _mvhd() + payload))
        self.assertEqual(info.kind, "audio")

    def test_truncated_mvhd_reports_zero_timescale(self) -> None:
        # A mvhd shorter than its own fields must not read past its end.
        info = read_media(
            _box(b"ftyp", b"isom" + b"\x00" * 8) + _box(b"moov", _box(b"mvhd", b"\x00" * 8))
        )
        self.assertIsNone(info.duration_s)

    def test_truncated_mvhd_v1_reports_zero_timescale(self) -> None:
        info = read_media(
            _box(b"ftyp", b"isom" + b"\x00" * 8)
            + _box(b"moov", _box(b"mvhd", b"\x01" + b"\x00" * 7))
        )
        self.assertIsNone(info.duration_s)

    def test_tkhd_that_is_too_short_yields_no_dimensions(self) -> None:
        info = read_media(
            _box(b"ftyp", b"isom" + b"\x00" * 8) + _box(b"moov", _box(b"tkhd", b"\x00" * 4))
        )
        self.assertIsNone(info.width)

    def test_tkhd_with_zero_dimensions_is_not_reported(self) -> None:
        # A zero W×H track (common for audio inside an MP4) must not render 0×0.
        info = read_media(_box(b"ftyp", b"isom" + b"\x00" * 8) + _box(b"moov", _tkhd(0, 0)))
        self.assertIsNone(info.width)


class RiffEdgeTests(unittest.TestCase):
    def test_fmt_without_data_and_no_second_chunk(self) -> None:
        info = read_media(b"RIFF" + struct.pack("<I", 4) + b"WAVE")
        self.assertIsNone(info.duration_s)
        self.assertTrue(any("no data chunk" in note for note in info.notes))

    def test_data_chunk_without_fmt_gives_unknown_duration(self) -> None:
        # data present but no fmt: there is no byte rate to divide by.
        info = read_media(
            b"RIFF"
            + struct.pack("<I", 4)
            + b"WAVE"
            + b"data"
            + struct.pack("<I", 8000)
            + b"\x00" * 8
        )
        self.assertIsNone(info.duration_s)

    def test_odd_sized_chunk_is_word_aligned(self) -> None:
        # RIFF pads odd chunks to an even boundary; a walk that ignores the pad
        # reads the next chunk name one byte off and finds no data chunk.
        odd = b"junk" + struct.pack("<I", 3) + b"abc" + b"\x00"
        fmt = b"fmt " + struct.pack("<I", 16) + struct.pack("<HHIIHH", 1, 2, 44100, 176400, 4, 16)
        body = b"WAVE" + odd + fmt + b"data" + struct.pack("<I", 352800)
        info = read_media(b"RIFF" + struct.pack("<I", len(body)) + body)
        # 352,800 bytes at 176,400 bytes/second = 2 seconds. The point of the
        # test is that the walk found `data` at all, not the arithmetic.
        self.assertEqual(info.duration_s, 2.0)

    def test_mov_named_file_keeps_the_mp4_container(self) -> None:
        info = read_media(_mp4(), "mov")
        self.assertEqual(info.format, "mp4")
        self.assertFalse(any("claimed" in note for note in info.notes))
