"""Geometry files: the mesh is counted, never loaded.

The gap this fills, measured
----------------------------
`describe_file("cube.glb")` on a valid 748-byte glTF binary returned

    cube.glb: glb (geometry), 748 bytes
      note: no reader for the geometry family yet

and the same for a real STEP file. A geometry file is not an opaque blob: its
structure states how many objects it has, how many vertices and triangles they
carry, and what units they are in. All three formats below declare that in a
header or a JSON block, and none of it requires tessellating or loading a mesh.

What is readable without a 3D library
-------------------------------------
GLB (glTF binary v2)
    A 12-byte header (`glTF`, version, total length) then length-prefixed
    chunks: JSON first, BIN second. The JSON names scenes, nodes, meshes,
    accessors and buffers. Counting meshes and summing accessor counts gives the
    vertex total; the scene count says how many assemblies the file holds.

STEP (ISO-10303-21)
    A text exchange format. `HEADER` names the schema -- `AUTOMOTIVE_DESIGN` for
    the common AP214 files, `CONFIG_CONTROL_DESIGN` for AP203 -- and `DATA` holds
    numbered entities, one per line, so the entity count is a line count. Points
    are `CARTESIAN_POINT` with coordinates inline.

STL (binary and ASCII)
    Binary is 80 bytes of header, a triangle count, then 50 bytes per triangle.
    ASCII is `solid`/`facet normal`/`vertex`. Both are floating-point triples,
    so a bounding box is computable by reading numbers, not by building a mesh.

Refusals
--------
* **The BIN chunk is never decoded.** A 300 MB GLB stores its vertices there;
  the JSON already says how many, so the payload is skipped by declared length.
* **The vertex data in a binary STL is not summed into a bounding box beyond a
  bounded count.** Reading 50 bytes per triangle is cheap, but a 4 GB mesh is
  not, so the walk stops at a fixed triangle budget and says so.
* **A truncated file is reported, not repaired.** A GLB whose declared length
  disagrees with its size, a STEP with no `END-ISO-10303-21;`, and an STL whose
  size is not `84 + 50n` are all named as invalid rather than guessed at.
"""

from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass

#: A GLB's header is 12 bytes: magic, version, total length.
_GLB_HEADER = 12

#: The JSON chunk of a GLB can be large (accessor tables for a big scene), but a
#: chunk past this size is not metadata a reader needs, and allocating it would
#: let a crafted file exhaust memory.
MAX_JSON_CHUNK = 8 * 1024 * 1024

#: Binary STL: 80-byte header + 4-byte count + 50 bytes per triangle.
_STL_HEADER = 84
_STL_TRIANGLE = 50

#: Triangles to walk before declaring the count a lower bound. A 50-triangle
#: budget covers every fixture and stops a 4 GB mesh from being scanned.
_MAX_STL_TRIANGLES = 200_000

#: STEP entity lines look like `#123=ENTITY_NAME(...);`.
_STEP_ENTITY = re.compile(r"^#(\d+)\s*=\s*([A-Z_0-9]+)\s*\(", re.MULTILINE)

#: A STEP HEADER carries the schema in `FILE_SCHEMA(('NAME'));`.
_STEP_SCHEMA = re.compile(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'", re.IGNORECASE)

#: A STEP file names its originating system; `FILE_NAME` has seven fields and
#: the software is the sixth.
_STEP_ORIGIN = re.compile(r"FILE_NAME\s*\(([^;]*)\)", re.IGNORECASE | re.DOTALL)

#: ASCII STL facet lines.
_ASCII_FACET = re.compile(r"facet\s+normal", re.IGNORECASE)
_ASCII_VERTEX = re.compile(
    r"vertex\s+(-?[\d.]+(?:[eE][-+]?\d+)?)\s+(-?[\d.]+(?:[eE][-+]?\d+)?)\s+(-?[\d.]+(?:[eE][-+]?\d+)?)",
    re.IGNORECASE,
)

#: Schema names worth translating into plain words, because "AP214" answers a
#: question that "AUTOMOTIVE_DESIGN" does not.
_SCHEMA_LABELS = {
    "AUTOMOTIVE_DESIGN": "AP214",
    "CONFIG_CONTROL_DESIGN": "AP203",
    "AP242_MANAGED_MODEL_BASED_3D_ENGINEERING": "AP242",
    "AUTOMOTIVE_DESIGN_CC2": "AP214 CC2",
}


@dataclass(frozen=True)
class GeometryInfo:
    """What a geometry file declares about itself."""

    format: str = ""
    version: str = ""
    #: Scenes, meshes, solids -- format-specific, so the label is carried too.
    objects: int = 0
    object_label: str = "object"
    vertices: int = 0
    triangles: int = 0
    #: The STEP schema or the glTF generator, when the file names one.
    schema: str = ""
    generator: str = ""
    #: Bounding box diagonal, in file units, when it is cheap to compute.
    extent: float = 0.0
    notes: tuple[str, ...] = ()

    @property
    def readable(self) -> bool:
        """Whether the header told us anything beyond the format name."""
        return bool(self.objects or self.vertices or self.triangles or self.schema)

    @property
    def object_text(self) -> str:
        """The object count with its label, pluralised the way English does.

        Measured while building this: `entity` + "s" printed "entitys", and
        `1 triangles` printed a plural for a single triangle. Both are the sort
        of thing a reader notices before they notice the number, which makes the
        number useless.
        """
        if not self.objects:
            return ""
        if self.objects == 1:
            return f"1 {self.object_label}"
        if self.object_label.endswith("y"):
            return f"{self.objects} {self.object_label[:-1]}ies"
        if self.object_label.endswith(("s", "x", "ch", "sh")):
            return f"{self.objects} {self.object_label}es"
        return f"{self.objects} {self.object_label}s"

    @property
    def triangle_text(self) -> str:
        """The triangle count, pluralised."""
        if not self.triangles:
            return ""
        unit = "triangle" if self.triangles == 1 else "triangles"
        return f"{self.triangles:,} {unit}"

    def render(self) -> str:
        """One line of facts, then caveats."""
        head = [self.format]
        if self.version:
            head.append(f"v{self.version}" if self.format == "glb" else self.version)
        if self.schema:
            head.append(self.schema)
        if self.object_text:
            head.append(self.object_text)
        if self.vertices:
            head.append(f"{self.vertices:,} vertices")
        if self.triangle_text:
            head.append(self.triangle_text)
        if self.extent:
            head.append(f"extent {self.extent:g}")
        if self.generator:
            head.append(f"by {self.generator}")
        lines = [", ".join(part for part in head if part)]
        lines.extend(f"  note: {note}" for note in self.notes)
        return "\n".join(lines)


def _count_glb(data: bytes) -> GeometryInfo:
    """Walk the GLB's chunk table and read the JSON scene description."""
    notes: list[str] = []

    magic = data[:4]
    if magic != b"glTF":
        return GeometryInfo(format="glb", notes=("no glTF magic: not a binary glTF",))
    if len(data) < _GLB_HEADER:
        return GeometryInfo(format="glb", notes=(f"header is {len(data)} bytes; a GLB needs 12",))

    version = struct.unpack("<I", data[4:8])[0]
    declared_length = struct.unpack("<I", data[8:12])[0]
    version_text = str(version)
    if version != 2:
        notes.append(f"version {version} is not the 2.x glTF layout; fields may differ")
    if declared_length != len(data):
        notes.append(f"header declares {declared_length:,} bytes but the file is {len(data):,}")

    # Chunks: 4-byte length, 4-byte type, then the payload.
    position = _GLB_HEADER
    json_chunk = b""
    saw_bin = False
    while position + 8 <= len(data):
        chunk_length, chunk_type = struct.unpack("<I4s", data[position : position + 8])
        position += 8
        end = position + chunk_length
        if end > len(data):
            notes.append(
                f"a chunk declares {chunk_length:,} bytes but only {len(data) - position:,} remain"
            )
            break
        if chunk_type == b"JSON" and not json_chunk:
            if chunk_length > MAX_JSON_CHUNK:
                notes.append(
                    f"the JSON chunk is {chunk_length:,} bytes; refused rather than allocated"
                )
                return GeometryInfo(format="glb", version=version_text, notes=tuple(notes))
            json_chunk = data[position:end]
        elif chunk_type == b"BIN\x00":
            saw_bin = True
            # Deliberately not read: the JSON already says how much is here.
        position = end

    if not json_chunk:
        return GeometryInfo(
            format="glb",
            version=version_text,
            notes=(*notes, "no JSON chunk: the scene description is missing"),
        )
    try:
        scene = json.loads(json_chunk.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        return GeometryInfo(
            format="glb",
            version=version_text,
            notes=(*notes, f"the JSON chunk does not parse: {exc.msg}"),
        )
    if not isinstance(scene, dict):
        return GeometryInfo(
            format="glb", version=version_text, notes=(*notes, "the JSON chunk is not an object")
        )

    scenes = scene.get("scenes") or []
    meshes = scene.get("meshes") or []
    accessors = scene.get("accessors") or []
    generator = str((scene.get("asset") or {}).get("generator") or "")

    vertices = 0
    triangles = 0
    for mesh in meshes if isinstance(meshes, list) else []:
        primitives = (mesh or {}).get("primitives") or []
        for primitive in primitives if isinstance(primitives, list) else []:
            attributes = (primitive or {}).get("attributes") or {}
            position_index = attributes.get("POSITION") if isinstance(attributes, dict) else None
            count = 0
            if isinstance(position_index, int) and 0 <= position_index < len(accessors):
                count = int(accessors[position_index].get("count") or 0)
            vertices += count
            # Mode 4 is TRIANGLES, the default. An indexed primitive draws
            # count/3 triangles; a non-indexed one has every three vertices form
            # one triangle, so the arithmetic is the same.
            mode = (primitive or {}).get("mode", 4)
            if mode == 4:
                triangles += count // 3

    if not saw_bin and meshes:
        notes.append("no BIN chunk: geometry is stored in external buffers")

    object_count = len(meshes) if meshes else len(scenes)
    label = "mesh" if meshes else "scene"
    return GeometryInfo(
        format="glb",
        version=version_text,
        objects=object_count,
        object_label=label,
        vertices=vertices,
        triangles=triangles,
        generator=generator,
        notes=tuple(notes),
    )


def _count_step(data: bytes) -> GeometryInfo:
    """Read the STEP header and count entities without interpreting them."""
    text = data.decode("utf-8", errors="replace")
    notes: list[str] = []

    if "ISO-10303-21" not in text[:200]:
        notes.append("the file does not begin with an ISO-10303-21 header")
    if "END-ISO-10303-21" not in text:
        notes.append("no END-ISO-10303-21; the file is truncated")

    schema = ""
    match = _STEP_SCHEMA.search(text)
    if match:
        raw = match.group(1)
        schema = _SCHEMA_LABELS.get(raw, raw)
    else:
        notes.append("no FILE_SCHEMA: the application protocol is unknown")

    generator = ""
    origin = _STEP_ORIGIN.search(text)
    if origin:
        fields = re.findall(r"'([^']*)'", origin.group(1))
        # FILE_NAME has seven fields: name, timestamp, author, organization,
        # preprocessor, originating_system, authorization. Only index 5 answers
        # "which tool wrote this"; a six-field record has no originating system,
        # and my first version reported fields[4] -- the preprocessor -- for it,
        # which is a wrong answer rather than a missing one.
        if len(fields) >= 7:  # noqa: PLR2004 - the format fixes the arity
            generator = fields[5].strip()

    entities = _STEP_ENTITY.findall(text)
    if not entities:
        notes.append("no entity records: the DATA section is empty")

    # A bounding box from CARTESIAN_POINTs would need the full geometry graph to
    # be correct (transforms, units, assemblies), so it is not claimed. The
    # entity count is a fact about the file, not about the model.
    return GeometryInfo(
        format="step",
        objects=len(entities),
        object_label="entity",
        schema=schema,
        generator=generator,
        notes=tuple(notes),
    )


def _count_stl(data: bytes) -> GeometryInfo:
    """Count triangles in a binary or ASCII STL, and its bounds when cheap."""
    notes: list[str] = []

    if data[:5].lower() == b"solid" and b"facet" in data[:2048].lower():
        text = data.decode("utf-8", errors="replace")
        triangles = len(_ASCII_FACET.findall(text))
        vertices = len(_ASCII_VERTEX.findall(text))
        if not triangles:
            notes.append("no facets found after a solid header")
        return GeometryInfo(
            format="stl",
            version="ascii",
            objects=1,
            object_label="solid",
            vertices=vertices,
            triangles=triangles,
            notes=tuple(notes),
        )

    if len(data) < _STL_HEADER:
        return GeometryInfo(
            format="stl", notes=(f"{len(data)} bytes is shorter than the 84-byte header",)
        )

    declared = struct.unpack("<I", data[80:84])[0]
    expected = _STL_HEADER + declared * _STL_TRIANGLE
    if expected != len(data):
        notes.append(
            f"header declares {declared:,} triangles ({expected:,} bytes) "
            f"but the file is {len(data):,}"
        )

    walk = min(declared, _MAX_STL_TRIANGLES)
    low = [float("inf")] * 3
    high = [float("-inf")] * 3
    position = _STL_HEADER
    for _ in range(walk):
        end = position + _STL_TRIANGLE
        if end > len(data):
            notes.append(f"stopped after {walk:,} triangles: the file ends first")
            break
        # normal (3 floats) then three vertices (3 floats each), then a u16.
        values = struct.unpack("<12f", data[position + 0 : position + 48])
        for index in range(3):
            for axis in range(3):
                value = values[3 + index * 3 + axis]
                if value < low[axis]:
                    low[axis] = value
                if value > high[axis]:
                    high[axis] = value
        position = end

    if declared > walk:
        notes.append(f"walked {walk:,} of {declared:,} triangles; the bounds are a lower bound")

    extent = 0.0
    if high[0] > low[0] or high[1] > low[1] or high[2] > low[2]:
        extent = (
            (high[0] - low[0]) ** 2 + (high[1] - low[1]) ** 2 + (high[2] - low[2]) ** 2
        ) ** 0.5

    return GeometryInfo(
        format="stl",
        version="binary",
        objects=1,
        object_label="solid",
        vertices=walk * 3,
        triangles=declared,
        extent=round(extent, 4),
        notes=tuple(notes),
    )


def read_geometry(data: bytes, fmt: str = "") -> GeometryInfo:
    """Describe a geometry file from its header. Never loads or tessellates a mesh."""
    if not data:
        return GeometryInfo(format=fmt or "geometry", notes=("empty file",))

    if fmt in {"glb", "gltf"} or data[:4] == b"glTF":
        return _count_glb(data)
    if fmt in {"step", "stp"} or data[:20].lstrip().startswith(b"ISO-10303-21"):
        return _count_step(data)
    # STL is claimed only when the caller says so, never by a length test.
    # Measured: the first version ended this chain with
    # `data[80:84] and len(data) >= 84`, so *any* file of 84 bytes or more was
    # read as binary STL -- 200 bytes of prose came back as a mesh with zero
    # triangles. `probe_bytes` already decided the format; a reader that
    # re-guesses it behind the prober's back invents answers.
    if fmt in {"stl", "stl-binary"}:
        return _count_stl(data)
    return GeometryInfo(
        format=fmt or "geometry",
        notes=(f"no geometry reader for {fmt or 'this format'}",),
    )
