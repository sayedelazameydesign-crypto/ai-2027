"""Geometry files: counted from headers, never tessellated.

The gap this file pins: a valid 748-byte GLB and a valid STEP both reported

    note: no reader for the geometry family yet

Every fixture is a real file built byte by byte, because the module's whole claim
is that the bytes declare the answer.
"""

from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path
from typing import Any

from ai2027_protocol.adapters import Operation, default_registry
from ai2027_protocol.delivery import DeliveryForm, plan_delivery
from ai2027_protocol.geometry import GeometryInfo, read_geometry
from ai2027_protocol.ir import describe_file
from ai2027_protocol.verify import verify_file


def _glb(scene: dict[str, Any], bin_bytes: bytes = b"\x00" * 288, version: int = 2) -> bytes:
    """A real GLB: header, JSON chunk, BIN chunk, with correct lengths."""
    payload = json.dumps(scene).encode("utf-8")
    payload += b" " * ((4 - len(payload) % 4) % 4)
    total = 12 + 8 + len(payload) + 8 + len(bin_bytes)
    out = b"glTF" + struct.pack("<II", version, total)
    out += struct.pack("<I", len(payload)) + b"JSON" + payload
    out += struct.pack("<I", len(bin_bytes)) + b"BIN\x00" + bin_bytes
    return out


SCENE = {
    "asset": {"version": "2.0", "generator": "test-cad 1.0"},
    "scenes": [{"nodes": [0]}],
    "nodes": [{"mesh": 0, "name": "Cube"}],
    "meshes": [{"name": "Cube", "primitives": [{"attributes": {"POSITION": 0}, "mode": 4}]}],
    "accessors": [{"componentType": 5126, "count": 24, "type": "VEC3"}],
    "bufferViews": [{"buffer": 0, "byteLength": 288}],
    "buffers": [{"byteLength": 288}],
}

STEP = b"""ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('test'),'2;1');
FILE_NAME('part.step','2026-01-01T00:00:00',('a'),('b'),'pre','FreeCAD 1.0','');
FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));
ENDSEC;
DATA;
#1=CARTESIAN_POINT('',(0.,0.,0.));
#2=CARTESIAN_POINT('',(1.,0.,0.));
#3=DIRECTION('',(0.,0.,1.));
ENDSEC;
END-ISO-10303-21;
"""


def _stl(triangles: list[tuple[float, ...]], header: bytes = b"binary stl") -> bytes:
    out = header.ljust(80, b" ")[:80] + struct.pack("<I", len(triangles))
    for normal_and_vertices in triangles:
        out += struct.pack("<12fH", *normal_and_vertices, 0)
    return out


ONE_TRIANGLE = (0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0)


class GlbTests(unittest.TestCase):
    def test_scene_counts_are_read_from_the_json_chunk(self) -> None:
        info = read_geometry(_glb(SCENE), "glb")
        self.assertEqual(info.format, "glb")
        self.assertEqual(info.version, "2")
        self.assertEqual(info.objects, 1)
        self.assertEqual(info.object_label, "mesh")
        self.assertEqual(info.vertices, 24)
        self.assertEqual(info.triangles, 8)

    def test_the_generator_is_named(self) -> None:
        self.assertEqual(read_geometry(_glb(SCENE), "glb").generator, "test-cad 1.0")

    def test_a_mislabelled_length_is_reported(self) -> None:
        data = bytearray(_glb(SCENE))
        data[8:12] = struct.pack("<I", 9999)  # declares a length the file lacks
        info = read_geometry(bytes(data), "glb")
        self.assertTrue(any("declares" in note for note in info.notes))

    def test_a_truncated_file_is_reported(self) -> None:
        info = read_geometry(_glb(SCENE)[:-100], "glb")
        self.assertTrue(info.notes)

    def test_no_magic_is_refused(self) -> None:
        self.assertFalse(read_geometry(b"not a glb at all", "glb").readable)

    def test_a_json_chunk_that_does_not_parse_is_reported(self) -> None:
        bad = b"glTF" + struct.pack("<II", 2, 24) + struct.pack("<I", 4) + b"JSON" + b"{bad"
        info = read_geometry(bad, "glb")
        self.assertTrue(any("does not parse" in note for note in info.notes))

    def test_no_json_chunk_is_reported(self) -> None:
        only_bin = b"glTF" + struct.pack("<II", 2, 20) + struct.pack("<I", 0) + b"BIN\x00"
        info = read_geometry(only_bin, "glb")
        self.assertTrue(any("no JSON chunk" in note for note in info.notes))

    def test_an_oversized_json_chunk_is_refused_not_allocated(self) -> None:
        # The chunk must actually fit in the file, or the bounds check fires
        # first and the allocation guard is never reached. 9 MB of padding is
        # the cheapest way to reach it.
        from ai2027_protocol.geometry import MAX_JSON_CHUNK

        big = MAX_JSON_CHUNK + 1024
        evil = b"glTF" + struct.pack("<II", 2, 12 + 8 + big)
        evil += struct.pack("<I", big) + b"JSON" + b" " * big
        info = read_geometry(evil, "glb")
        self.assertTrue(any("refused rather than allocated" in note for note in info.notes))

    def test_a_chunk_that_runs_past_the_file_is_reported(self) -> None:
        evil = b"glTF" + struct.pack("<II", 2, 20) + struct.pack("<I", 100_000_000) + b"JSON"
        info = read_geometry(evil, "glb")
        self.assertTrue(any("remain" in note for note in info.notes))

    def test_a_version_other_than_two_is_noted(self) -> None:
        info = read_geometry(_glb(SCENE, version=1), "glb")
        self.assertTrue(any("version 1" in note for note in info.notes))

    def test_external_buffers_are_noted_when_there_is_no_bin(self) -> None:
        # The BIN chunk must be *absent*, not zero-length: a zero-length BIN
        # header is still a BIN chunk, and my first fixture wrote one.
        payload = json.dumps(SCENE).encode("utf-8")
        payload += b" " * ((4 - len(payload) % 4) % 4)
        json_only = b"glTF" + struct.pack("<II", 2, 12 + 8 + len(payload))
        json_only += struct.pack("<I", len(payload)) + b"JSON" + payload
        info = read_geometry(json_only, "glb")
        self.assertTrue(any("external buffers" in note for note in info.notes))

    def test_a_scene_with_no_meshes_counts_scenes(self) -> None:
        scene = {"asset": {"version": "2.0"}, "scenes": [{"nodes": []}]}
        info = read_geometry(_glb(scene, bin_bytes=b""), "glb")
        self.assertEqual(info.objects, 1)
        self.assertEqual(info.object_label, "scene")

    def test_a_short_header_is_reported(self) -> None:
        info = read_geometry(b"glTF", "glb")
        self.assertTrue(any("needs 12" in note for note in info.notes))


class StepTests(unittest.TestCase):
    def test_schema_and_entity_count(self) -> None:
        info = read_geometry(STEP, "step")
        self.assertEqual(info.format, "step")
        self.assertEqual(info.schema, "AP214")
        self.assertEqual(info.objects, 3)
        self.assertEqual(info.object_label, "entity")

    def test_the_originating_system_is_read(self) -> None:
        self.assertEqual(read_geometry(STEP, "step").generator, "FreeCAD 1.0")

    def test_a_missing_end_marker_is_reported(self) -> None:
        info = read_geometry(STEP.replace(b"END-ISO-10303-21;", b""), "step")
        self.assertTrue(any("truncated" in note for note in info.notes))

    def test_a_missing_schema_is_reported(self) -> None:
        # Removing the *line* is the test; replacing the name leaves
        # FILE_SCHEMA(('X')), which still parses and is a different case.
        no_schema = STEP.replace(b"FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));", b"")
        info = read_geometry(no_schema, "step")
        self.assertTrue(any("application protocol is unknown" in note for note in info.notes))

    def test_an_empty_data_section_is_reported(self) -> None:
        empty = STEP.replace(b"#1=CARTESIAN_POINT('',(0.,0.,0.));", b"")
        empty = empty.replace(b"#2=CARTESIAN_POINT('',(1.,0.,0.));", b"")
        empty = empty.replace(b"#3=DIRECTION('',(0.,0.,1.));", b"")
        info = read_geometry(empty, "step")
        self.assertTrue(any("DATA section is empty" in note for note in info.notes))

    def test_ap203_is_labelled(self) -> None:
        ap203 = STEP.replace(b"AUTOMOTIVE_DESIGN", b"CONFIG_CONTROL_DESIGN")
        self.assertEqual(read_geometry(ap203, "step").schema, "AP203")

    def test_an_unknown_schema_is_passed_through(self) -> None:
        odd = STEP.replace(b"AUTOMOTIVE_DESIGN", b"SOMETHING_NEW")
        self.assertEqual(read_geometry(odd, "step").schema, "SOMETHING_NEW")


class StlTests(unittest.TestCase):
    def test_binary_triangle_count_and_bounds(self) -> None:
        info = read_geometry(_stl([ONE_TRIANGLE]), "stl-binary")
        self.assertEqual(info.format, "stl")
        self.assertEqual(info.version, "binary")
        self.assertEqual(info.triangles, 1)
        self.assertEqual(info.vertices, 3)
        self.assertGreater(info.extent, 0)

    def test_a_size_that_does_not_match_the_count_is_reported(self) -> None:
        data = _stl([ONE_TRIANGLE]) + b"\x00" * 10
        info = read_geometry(data, "stl-binary")
        self.assertTrue(any("declares" in note for note in info.notes))

    def test_ascii_stl_is_counted(self) -> None:
        ascii_stl = (
            b"solid cube\n"
            b"facet normal 0 0 1\n outer loop\n  vertex 0 0 0\n  vertex 1 0 0\n"
            b"  vertex 0 1 0\n endloop\nendfacet\nendsolid cube\n"
        )
        info = read_geometry(ascii_stl, "stl")
        self.assertEqual(info.version, "ascii")
        self.assertEqual(info.triangles, 1)
        self.assertEqual(info.vertices, 3)

    def test_a_solid_header_with_no_facets_is_reported(self) -> None:
        info = read_geometry(b"solid empty\nno facets here\n", "stl")
        self.assertTrue(any("no facets" in note for note in info.notes))

    def test_a_file_shorter_than_the_header_is_reported(self) -> None:
        info = read_geometry(b"\x00" * 40, "stl-binary")
        self.assertTrue(any("shorter than the 84-byte header" in note for note in info.notes))

    def test_the_walk_is_bounded(self) -> None:
        # A claimed count past the budget must say the bounds are a lower bound
        # rather than scan a giant file.
        from ai2027_protocol.geometry import _MAX_STL_TRIANGLES

        data = bytearray(84 + 50)
        data[80:84] = struct.pack("<I", _MAX_STL_TRIANGLES + 10)
        info = read_geometry(bytes(data), "stl-binary")
        self.assertTrue(any("lower bound" in note for note in info.notes))


class NeverLoadsTheMeshTests(unittest.TestCase):
    """The BIN chunk is skipped by declared length, so size cannot matter."""

    def test_a_four_megabyte_bin_chunk_costs_nothing(self) -> None:
        import time

        payload = json.dumps(SCENE).encode("utf-8")
        payload += b" " * ((4 - len(payload) % 4) % 4)
        bin_chunk = b"\x00" * (4 * 1024 * 1024)
        total = 12 + 8 + len(payload) + 8 + len(bin_chunk)
        glb = b"glTF" + struct.pack("<II", 2, total)
        glb += struct.pack("<I", len(payload)) + b"JSON" + payload
        glb += struct.pack("<I", len(bin_chunk)) + b"BIN\x00" + bin_chunk

        started = time.perf_counter()
        info = read_geometry(glb, "glb")
        elapsed_ms = (time.perf_counter() - started) * 1000
        # Measured: 0.06 ms for 4 MB. The budget of 50 ms leaves room for a
        # loaded machine without hiding a regression that reads the payload.
        self.assertLess(elapsed_ms, 50, f"{elapsed_ms:.1f} ms -- the BIN chunk was read")
        self.assertEqual(info.vertices, 24)


class SelfInflictedDefectTests(unittest.TestCase):
    """Defects found by reading geometry.py's own uncovered lines."""

    def test_a_six_field_file_name_does_not_invent_a_tool(self) -> None:
        # FILE_NAME index 4 is the preprocessor; index 5 is the originating
        # system. My first version reported fields[4] for a short record, which
        # is a wrong answer rather than a missing one.
        six = STEP.replace(b"('a'),('b'),'pre','FreeCAD 1.0',''", b"('a'),('b'),'pre',''")
        self.assertEqual(read_geometry(six, "step").generator, "")

    def test_the_generator_field_is_the_sixth(self) -> None:
        self.assertEqual(read_geometry(STEP, "step").generator, "FreeCAD 1.0")

    def test_no_units_are_ever_claimed(self) -> None:
        # `units` existed, was rendered, and no reader set it -- a field that
        # could only ever be empty. STEP does not state its unit in the header,
        # so claiming millimetres would be a guess. The field is gone.
        self.assertFalse(hasattr(GeometryInfo(), "units"))

    def test_a_primitive_with_no_position_accessor_counts_zero(self) -> None:
        scene = dict(SCENE)
        scene["meshes"] = [{"primitives": [{"attributes": {}}]}]
        self.assertEqual(read_geometry(_glb(scene), "glb").vertices, 0)

    def test_a_non_triangle_mode_adds_no_triangles(self) -> None:
        scene = dict(SCENE)
        scene["meshes"] = [{"primitives": [{"attributes": {"POSITION": 0}, "mode": 1}]}]
        info = read_geometry(_glb(scene), "glb")
        self.assertEqual(info.triangles, 0)
        self.assertEqual(info.vertices, 24)

    def test_a_json_array_is_not_a_scene(self) -> None:
        payload = b"[1,2,3]"
        data = b"glTF" + struct.pack("<II", 2, 12 + 8 + len(payload))
        data += struct.pack("<I", len(payload)) + b"JSON" + payload
        info = read_geometry(data, "glb")
        self.assertTrue(any("not an object" in note for note in info.notes))

    def test_a_step_with_no_iso_header_is_noted(self) -> None:
        info = read_geometry(b"DATA;\n#1=A();\nENDSEC;\n", "step")
        self.assertTrue(any("does not begin" in note for note in info.notes))

    def test_empty_input_is_reported(self) -> None:
        self.assertFalse(read_geometry(b"", "glb").readable)

    def test_an_unknown_format_is_named_not_guessed(self) -> None:
        # Measured: the first version fell through to `data[80:84] and
        # len(data) >= 84`, so 200 bytes of prose came back as binary STL with
        # zero triangles. A reader must not re-guess a format the prober
        # already decided.
        for payload in (bytes(200), b"hello world " * 17, bytes(range(200))):
            info = read_geometry(payload, "obj")
            self.assertEqual(info.format, "obj")
            self.assertFalse(info.readable)
            self.assertTrue(any("no geometry reader for obj" in note for note in info.notes))

    def test_text_is_never_read_as_a_mesh(self) -> None:
        prose = ("This is ordinary prose about nothing geometric at all. " * 5).encode()
        self.assertFalse(read_geometry(prose, "obj").readable)


class RenderingTests(unittest.TestCase):
    def test_pluralisation_is_english(self) -> None:
        # Measured: `entity` + "s" printed "entitys", and one triangle printed
        # "1 triangles". A reader notices that before the number.
        self.assertEqual(GeometryInfo(objects=1, object_label="entity").object_text, "1 entity")
        self.assertEqual(GeometryInfo(objects=3, object_label="entity").object_text, "3 entities")
        self.assertEqual(GeometryInfo(triangles=1).triangle_text, "1 triangle")
        self.assertEqual(GeometryInfo(triangles=900).triangle_text, "900 triangles")

    def test_empty_info_is_not_readable(self) -> None:
        self.assertFalse(GeometryInfo().readable)

    def test_render_carries_the_counts(self) -> None:
        rendered = read_geometry(_glb(SCENE), "glb").render()
        self.assertIn("24 vertices", rendered)
        self.assertIn("8 triangles", rendered)


class IntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, name: str, data: bytes) -> Path:
        path = self.tmp / name
        path.write_bytes(data)
        return path

    def test_the_adapter_claims_the_family(self) -> None:
        from ai2027_protocol.ingest import probe_bytes

        adapter = default_registry().for_probe(probe_bytes(_glb(SCENE)))
        assert adapter is not None
        self.assertEqual(adapter.name, "geometry")
        self.assertTrue(adapter.supports(Operation.SLICE))

    def test_describe_names_the_mesh(self) -> None:
        path = self._write("cube.glb", _glb(SCENE))
        rendered = describe_file(str(path)).render()
        self.assertIn("1 mesh", rendered)
        self.assertNotIn("no reader for the geometry family", rendered)

    def test_a_valid_step_delivers_native(self) -> None:
        path = self._write("part.step", STEP)
        described = describe_file(str(path))
        plan = plan_delivery(described, verification=verify_file(path, kind=described.kind))
        self.assertIs(plan.primary.form, DeliveryForm.NATIVE)

    def test_a_valid_stl_delivers_native(self) -> None:
        # Binary STL has no magic, so this exercises the kind-based verify route.
        path = self._write("part.stl", _stl([ONE_TRIANGLE]))
        described = describe_file(str(path))
        self.assertEqual(described.kind, "stl-binary")
        plan = plan_delivery(described, verification=verify_file(path, kind=described.kind))
        self.assertIs(plan.primary.form, DeliveryForm.NATIVE)

    def test_a_truncated_glb_is_invalid(self) -> None:
        path = self._write("trunc.glb", _glb(SCENE)[:-100])
        verdict = verify_file(path, display_path="trunc.glb", kind="glb")
        self.assertFalse(verdict.ok)

    def test_a_valid_glb_passes_verification(self) -> None:
        path = self._write("ok.glb", _glb(SCENE))
        self.assertTrue(verify_file(path, kind="glb").ok)

    def test_a_step_with_no_end_marker_is_invalid(self) -> None:
        path = self._write("bad.step", STEP.replace(b"END-ISO-10303-21;", b""))
        self.assertFalse(verify_file(path, kind="step").ok)


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
