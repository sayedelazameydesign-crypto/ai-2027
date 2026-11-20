"""Image metadata read from the header, without a decoder or an image library.

Every fixture here is built from the format's own byte layout, so a failure
means the reader is wrong rather than that a sample drifted. The corrupt cases
matter most: an image whose header cannot be read must say so, because "0×0"
and "1920×1080" must never be confusable.
"""

from __future__ import annotations

import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from ai2027_protocol.adapters import Operation, default_registry
from ai2027_protocol.delivery import DeliveryForm, plan_delivery
from ai2027_protocol.images import ImageInfo, read_image
from ai2027_protocol.ir import describe_file
from ai2027_protocol.verify import verify_file


def _png(width: int, height: int, *, colour_type: int = 6, bit_depth: int = 8) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, bit_depth, colour_type, 0, 0, 0)
    chunk = b"IHDR" + ihdr
    crc = struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + chunk + crc + b"\x00" * 20


def _jpeg(width: int, height: int, *, marker: int = 0xC0, depth: int = 8) -> bytes:
    return (
        b"\xff\xd8"
        + bytes([0xFF, marker])
        + struct.pack(">H", 17)
        + bytes([depth])
        + struct.pack(">HH", height, width)
        + b"\x03"
        + b"\x00" * 9
    )


class PngTests(unittest.TestCase):
    def test_dimensions_and_colour(self) -> None:
        info = read_image(_png(1920, 1080))
        self.assertEqual((info.width, info.height), (1920, 1080))
        self.assertEqual(info.colour, "RGBA")
        self.assertEqual(info.bit_depth, 8)

    def test_derived_measures(self) -> None:
        info = read_image(_png(1920, 1080))
        self.assertAlmostEqual(info.aspect or 0, 1.778, places=3)
        self.assertAlmostEqual(info.megapixels or 0, 2.07, places=2)
        self.assertTrue(info.readable)

    def test_palette_and_low_bit_depth(self) -> None:
        info = read_image(_png(16, 16, colour_type=3, bit_depth=4))
        self.assertEqual(info.colour, "palette")
        self.assertEqual(info.bit_depth, 4)

    def test_greyscale_catalogue(self) -> None:
        self.assertEqual(read_image(_png(8, 8, colour_type=0)).colour, "greyscale")
        self.assertEqual(
            read_image(_png(8, 8, colour_type=4)).colour,
            "greyscale+alpha",
        )

    def test_missing_ihdr_is_reported_not_guessed(self) -> None:
        info = read_image(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
        self.assertFalse(info.readable)
        self.assertIn("no IHDR", info.render())

    def test_zero_dimension_is_flagged(self) -> None:
        info = read_image(_png(0, 0))
        self.assertTrue(any("zero dimension" in note for note in info.notes))
        self.assertFalse(info.readable)


class JpegTests(unittest.TestCase):
    def test_baseline_dimensions(self) -> None:
        info = read_image(_jpeg(4032, 3024))
        self.assertEqual((info.width, info.height), (4032, 3024))
        self.assertEqual(info.colour, "baseline")

    def test_progressive_marker_is_found(self) -> None:
        info = read_image(_jpeg(8000, 6000, marker=0xC2, depth=12))
        self.assertEqual((info.width, info.height), (8000, 6000))
        self.assertEqual(info.bit_depth, 12)

    def test_skips_segments_before_the_sof(self) -> None:
        # An APP1 block precedes the frame header, which is the normal EXIF case.
        app1 = b"\xff\xe1" + struct.pack(">H", 30) + b"Exif\x00" + b"\x00" * 24
        info = read_image(b"\xff\xd8" + app1 + _jpeg(600, 800)[2:])
        self.assertEqual((info.width, info.height), (600, 800))

    def test_no_sof_is_reported(self) -> None:
        info = read_image(b"\xff\xd8" + b"\xff\xda" + b"\x00" * 20)
        self.assertFalse(info.readable)
        self.assertTrue(any("no SOF" in note for note in info.notes))


class GifBmpTests(unittest.TestCase):
    def test_gif_screen_descriptor(self) -> None:
        info = read_image(b"GIF89a" + struct.pack("<HH", 320, 240) + b"\x00" * 12)
        self.assertEqual((info.width, info.height), (320, 240))

    def test_gif_frame_count_is_not_guessed(self) -> None:
        info = read_image(b"GIF89a" + struct.pack("<HH", 10, 10) + b"\x00" * 12)
        self.assertIsNone(info.frames)
        self.assertTrue(any("not counted" in note for note in info.notes))

    def test_bmp_top_down_height(self) -> None:
        data = b"BM" + b"\x00" * 16 + struct.pack("<ii", 1024, -768) + b"\x00" * 10
        info = read_image(data)
        self.assertEqual((info.width, info.height), (1024, 768))
        self.assertTrue(any("top-down" in note for note in info.notes))

    def test_bmp_too_short(self) -> None:
        info = read_image(b"BM" + b"\x00" * 4)
        self.assertFalse(info.readable)


class HonestyTests(unittest.TestCase):
    def test_mislabelled_name_is_exposed(self) -> None:
        info = read_image(_jpeg(100, 100), "png")
        self.assertEqual(info.format, "jpeg")
        self.assertTrue(any("claimed png" in note for note in info.notes))

    def test_unknown_format_says_so(self) -> None:
        info = read_image(b"XXXX" + b"\x00" * 40, "webp")
        self.assertFalse(info.readable)
        self.assertTrue(any("no image header reader" in note for note in info.notes))

    def test_render_is_one_line_plus_notes(self) -> None:
        lines = read_image(_png(100, 50)).render().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("100×50", lines[0])

    def test_aspect_is_none_without_dimensions(self) -> None:
        self.assertIsNone(ImageInfo(format="png").aspect)
        self.assertIsNone(ImageInfo(format="png").megapixels)


class IntegrationTests(unittest.TestCase):
    """A real PNG must reach the user as an image, not as text."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_image_adapter_claims_the_family(self) -> None:
        from ai2027_protocol.ingest import probe_bytes

        adapter = default_registry().for_probe(probe_bytes(_png(10, 10)))
        self.assertIsNotNone(adapter)
        assert adapter is not None
        self.assertEqual(adapter.name, "image")
        self.assertTrue(adapter.supports(Operation.RENDER))

    def test_describe_file_reports_dimensions(self) -> None:
        path = self.tmp / "photo.png"
        path.write_bytes(_png(1920, 1080))
        rendered = describe_file(str(path)).render()
        self.assertIn("1920×1080", rendered)
        self.assertNotIn("no reader for the image family", rendered)

    def test_valid_image_delivers_native(self) -> None:
        path = self.tmp / "photo.png"
        path.write_bytes(_png(1920, 1080))
        described = describe_file(str(path))
        plan = plan_delivery(described, verification=verify_file(path, kind=described.kind))
        self.assertIs(plan.primary.form, DeliveryForm.NATIVE)
        self.assertEqual(plan.primary.widget, "image")

    def test_corrupt_image_degrades_to_report(self) -> None:
        path = self.tmp / "broken.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
        described = describe_file(str(path))
        verdict = verify_file(path, display_path="broken.png", kind=described.kind)
        self.assertFalse(verdict.ok)
        plan = plan_delivery(described, verification=verdict)
        self.assertIs(plan.primary.form, DeliveryForm.REPORT)

    def test_is_inspectable_counts_a_readable_image(self) -> None:
        path = self.tmp / "photo.png"
        path.write_bytes(_png(640, 480))
        self.assertTrue(describe_file(str(path)).is_inspectable)


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()


class MarkerWalkTests(unittest.TestCase):
    """The JPEG walk must survive the marker shapes real files contain."""

    def test_standalone_markers_are_skipped(self) -> None:
        # RST/TEM markers carry no length field; a walk that reads one as a
        # length runs off into the image data and never finds the frame.
        filler = b"\xff\xd0" * 3 + b"\xff\xd8"
        info = read_image(b"\xff\xd8" + filler + _jpeg(320, 240)[2:])
        self.assertEqual((info.width, info.height), (320, 240))

    def test_zero_length_segment_stops_instead_of_looping(self) -> None:
        # A malformed length of 0 would make `position += 2 + 0` a no-op forever.
        info = read_image(b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 0) + b"\x00" * 20)
        self.assertFalse(info.readable)

    def test_scan_marker_before_sof_is_reported(self) -> None:
        info = read_image(b"\xff\xd8" + b"\xff\xda" + b"\x00" * 20)
        self.assertTrue(any("no SOF" in note for note in info.notes))


class GifEdgeTests(unittest.TestCase):
    def test_short_gif_is_reported(self) -> None:
        info = read_image(b"GIF89a" + b"\x00")
        self.assertFalse(info.readable)
        self.assertTrue(any("too short" in note for note in info.notes))

    def test_animated_frames_are_rendered_when_declared(self) -> None:
        # The reader never guesses a frame count, but if one is supplied it
        # must appear in the rendering rather than being dropped.
        info = ImageInfo(format="gif", width=100, height=100, frames=24)
        self.assertIn("24 frames", info.render())

    def test_single_frame_is_not_noise(self) -> None:
        info = ImageInfo(format="gif", width=100, height=100, frames=1)
        self.assertNotIn("frames", info.render())
