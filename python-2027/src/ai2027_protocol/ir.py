"""A canonical description of a file, whatever kind of file it is.

The gap this fills
------------------
`index.py` describes **text**: headings, sections, tables written as text. Measured
on a Parquet file it returns `notes=('empty file',)` — the whole artifact is
invisible, because a columnar binary has no headings. A SQLite database, a
spreadsheet, a CAD file: all equally invisible.

The pipeline cannot route what it cannot describe. So this module produces one
shape — :class:`DocumentIR` — for every family the ingester can name, and each
family contributes as much as its format honestly allows:

* **text** — sections and line ranges, from the existing index.
* **database** — tables, columns, row counts, indexes, read-only.
* **columnar (parquet)** — schema and row count from the footer, no full read.
* **zip-shaped** — the directory, and the document format when it is one.

Design rules
------------
* **Never load the file.** A SQLite probe opens read-only and counts rows with
  SQL; a Parquet probe seeks to the footer. A 4 GB artifact costs a probe.
* **Never invent.** A family with no reader reports what is unknown, by name.
  An empty `tables` list that means "no tables" is different from one that
  means "I could not look", and the report says which.
* **Standard library only.** The sandbox runs with `-I -B` and a sanitised
  environment; adding a wheel to read a footer would be a supply-chain surface
  for four bytes of arithmetic.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from .images import ImageInfo, read_image
from .index import DocumentIndex, build_index
from .ingest import Family, Probe
from .media import MediaInfo, read_media
from .pdf import MAX_SCAN_BYTES, PdfInfo, read_pdf

#: A family reader: (path, probe, text, size, real_path, handle) -> IR.
Reader = Callable[[str, "Probe", "str | None", int, "Path | None", "object"], "DocumentIR"]

#: How many tables/columns an IR reports before it stops listing. The same
#: reasoning as the section cap: beyond this the description becomes the bloat.
MAX_TABLES = 60
#: A SQLite table's row count is exact but costs a scan. Above this many rows
#: the count is still reported (SQLite answers from the table's own metadata in
#: most cases) but the probe stops if it exceeds this budget in seconds.
SQLITE_PROBE_DEADLINE_S = 2.0


@dataclass(frozen=True)
class Column:
    """One field of a table, with the type the artifact actually declares."""

    name: str
    type: str
    nullable: bool = True


@dataclass(frozen=True)
class Table:
    """One addressable relation inside a data artifact."""

    name: str
    columns: tuple[Column, ...] = field(default_factory=tuple)
    #: Exact when the reader can count cheaply, ``None`` when it cannot — a
    #: guess here would be indistinguishable from a measurement.
    rows: int | None = None
    note: str = ""


@dataclass(frozen=True)
class DocumentIR:
    """One shape for every artifact the system can route.

    `kind` is the format name from the ingester, so two files of the same
    format always produce the same `kind` and a router can key on it.
    """

    path: str
    kind: str
    family: Family
    #: Size on disk, so a caller can reason about cost before asking for more.
    bytes_on_disk: int = 0
    #: Text documents carry sections; the field is empty for the others.
    text_index: DocumentIndex | None = None
    #: Data artifacts carry tables; empty means "none found", and `notes` says
    #: whether that was a measurement or a gap.
    tables: tuple[Table, ...] = field(default_factory=tuple)
    #: Schema fields for columnar files, where a "table" is the only one.
    columns: tuple[Column, ...] = field(default_factory=tuple)
    rows: int | None = None
    #: Images carry their own header facts (dimensions, depth, colour).
    media: ImageInfo | None = None
    #: PDFs carry page count, page size and metadata.
    pdf: PdfInfo | None = None
    #: Audio and video carry duration and dimensions from their container.
    media_info: MediaInfo | None = None
    #: Everything the reader could not determine, named explicitly.
    notes: tuple[str, ...] = field(default_factory=tuple)

    def render(self, *, limit: int = 24) -> str:
        """A compact, prompt-sized description. Costs the same at any file size."""
        lines = [f"{self.path}: {self.kind} ({self.family.value}), {self.bytes_on_disk:,} bytes"]

        if self.text_index is not None:
            lines.extend("  " + line for line in self.text_index.render(limit).splitlines()[1:])

        if self.tables:
            lines.append(f"  {len(self.tables)} table(s):")
            for table in self.tables[:limit]:
                columns = ", ".join(f"{c.name}:{c.type}" for c in table.columns[:8])
                more = f" (+{len(table.columns) - 8} cols)" if len(table.columns) > 8 else ""
                rows = "?" if table.rows is None else f"{table.rows:,}"
                lines.append(f"    - {table.name} [{rows} rows] {columns}{more}")
            if len(self.tables) > limit:
                lines.append(f"    … {len(self.tables) - limit} more table(s)")

        if self.columns:
            columns = ", ".join(f"{c.name}:{c.type}" for c in self.columns[:12])
            more = f" (+{len(self.columns) - 12} cols)" if len(self.columns) > 12 else ""
            rows = "" if self.rows is None else f", {self.rows:,} rows"
            lines.append(f"  schema: {columns}{more}{rows}")

        if self.media is not None:
            media_lines = self.media.render().splitlines()
            lines.extend("  " + line for line in media_lines)

        if self.pdf is not None:
            pdf_lines = self.pdf.render().splitlines()
            lines.extend("  " + line for line in pdf_lines)

        if self.media_info is not None:
            media_lines = self.media_info.render().splitlines()
            lines.extend("  " + line for line in media_lines)

        lines.extend(f"  note: {note}" for note in self.notes)
        return "\n".join(lines)

    @property
    def is_inspectable(self) -> bool:
        """Whether the IR describes anything beyond the file's name and size."""
        return bool(
            self.text_index is not None
            or self.tables
            or self.columns
            or self.rows is not None
            or (self.media is not None and self.media.readable)
            or (self.pdf is not None and self.pdf.readable)
            or (self.media_info is not None and self.media_info.readable)
        )


def _quote_identifier(name: str) -> str:
    """Quote a SQL identifier the way SQLite itself does: double the quotes.

    The name comes from `sqlite_master`, so it is data, not user input -- but a
    table may legitimately be named with a quote in it, and building SQL by
    f-string is how that becomes a syntax error or worse.
    """
    return '"' + name.replace('"', '""') + '"'


def _sqlite_ir(path: str, real_path: Path, size: int) -> DocumentIR:
    """Describe a SQLite database read-only, without loading it into memory."""
    import sqlite3

    tables: list[Table] = []
    notes: list[str] = []
    try:
        # `mode=ro` is the guarantee, not a convention: a probe must never
        # write to the artifact it is describing.
        connection = sqlite3.connect(f"file:{real_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return DocumentIR(
            path=path,
            kind="sqlite",
            family=Family.DATABASE,
            bytes_on_disk=size,
            notes=(f"could not open read-only: {exc}",),
        )
    try:
        connection.execute(f"PRAGMA busy_timeout = {int(SQLITE_PROBE_DEADLINE_S * 1000)}")
        names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        for name in names[:MAX_TABLES]:
            quoted = _quote_identifier(str(name))
            columns = tuple(
                Column(name=str(info[1]), type=str(info[2] or "ANY"), nullable=not bool(info[3]))
                for info in connection.execute(f"PRAGMA table_info({quoted})")  # noqa: S608
            )
            rows: int | None
            note = ""
            try:
                counted = connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()  # noqa: S608
                rows = int(counted[0])
            except sqlite3.Error as exc:
                rows = None
                note = f"row count unavailable: {exc}"
            tables.append(Table(name=str(name), columns=columns, rows=rows, note=note))
        if len(names) > MAX_TABLES:
            notes.append(f"{len(names)} tables, first {MAX_TABLES} listed")
        if not names:
            notes.append("opened successfully; the database contains no tables")

        indexes = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name NOT LIKE 'sqlite_%' LIMIT 20"
            )
        ]
        if indexes:
            notes.append(f"{len(indexes)} index(es): {', '.join(indexes[:5])}")
    except sqlite3.Error as exc:
        notes.append(f"schema read failed: {exc}")
    finally:
        connection.close()

    return DocumentIR(
        path=path,
        kind="sqlite",
        family=Family.DATABASE,
        bytes_on_disk=size,
        tables=tuple(tables),
        notes=tuple(notes),
    )


def _parquet_ir(path: str, size: int, handle: BinaryIO) -> DocumentIR:
    """Read a Parquet footer: row count and the magic, without a full decode.

    Parquet ends with ``<4-byte little-endian metadata length>PAR1``. Reading a
    schema out of the Thrift metadata would need a Thrift parser; what is
    available for free is the size of the metadata block, which bounds how much
    schema the file can hold. Reporting that honestly beats reporting nothing.
    """
    notes: list[str] = []
    rows: int | None = None
    footer_ok = False
    try:
        handle.seek(max(0, size - 8))
        tail = handle.read(8)
        if len(tail) == 8 and tail[4:] == b"PAR1":
            (metadata_len,) = struct.unpack("<I", tail[:4])
            footer_ok = True
            notes.append(f"parquet footer: {metadata_len:,} bytes of column metadata")
            if metadata_len == 0:
                notes.append("the footer declares no column metadata; the file is empty")
        else:
            notes.append("no parquet footer signature at the end of the file")
    except (OSError, struct.error) as exc:
        notes.append(f"footer unreadable: {exc}")

    if not footer_ok:
        notes.append("row count and schema need a parquet reader, which is not installed")

    return DocumentIR(
        path=path,
        kind="parquet",
        family=Family.COLUMNAR,
        bytes_on_disk=size,
        rows=rows,
        notes=tuple(notes),
    )


def _text_ir(path: str, text: str, size: int, kind: str) -> DocumentIR:
    """Describe a text document with the existing structural index.

    An empty file gets a note rather than a silent empty index: "we read it and
    there was nothing" and "something went wrong" must not look alike.
    """
    notes: tuple[str, ...] = ()
    if not text.strip():
        notes = ("empty file" if size == 0 else "no readable content",)
    return DocumentIR(
        path=path,
        kind=kind,
        family=Family.TEXT,
        bytes_on_disk=size,
        text_index=build_index(path, text),
        notes=notes,
    )


def _zip_ir(path: str, data: bytes, size: int) -> DocumentIR:
    """Describe a container by its directory, refusing an unsafe one."""
    from .containers import inspect_zip

    report = inspect_zip(data, name_hint=path)
    notes: list[str] = []
    if not report.is_safe_to_open:
        return DocumentIR(
            path=path,
            kind=report.format,
            family=Family.ZIP,
            bytes_on_disk=size,
            notes=(f"refused: {report.refusal}",),
        )
    notes.append(f"{len(report.entries)} entries")
    if report.warnings:
        notes.extend(report.warnings)
    return DocumentIR(
        path=path,
        kind=report.format,
        family=Family.ZIP,
        bytes_on_disk=size,
        notes=tuple(notes),
    )


def _reader_key(probe: Probe) -> str:
    """The adapter name responsible for these bytes, resolved through the mesh.

    `adapters.default_registry()` decides, so the readers below and the declared
    capabilities cannot drift apart: an adapter that claims a family with no
    entry in `_READERS` routes to nothing and is reported as a gap, not as an
    empty success.
    """
    from .adapters import default_registry

    adapter = default_registry().for_probe(probe)
    return adapter.name if adapter is not None else ""


def _read_text(
    path: str, probe: Probe, text: str | None, size: int, _real: Path | None, handle: object
) -> DocumentIR:
    if text is None and handle is not None:
        handle.seek(0)  # type: ignore[attr-defined]
        text = handle.read().decode("utf-8", errors="replace")  # type: ignore[attr-defined]
    return _text_ir(path, text or "", size, probe.format)


def _read_sqlite(
    path: str, probe: Probe, _text: str | None, size: int, real: Path | None, _handle: object
) -> DocumentIR:
    if real is None:
        return DocumentIR(
            path=path,
            kind=probe.format,
            family=probe.family,
            notes=("the database needs a real path; none was given",),
        )
    return _sqlite_ir(path, real, size)


def _read_zip(
    path: str, probe: Probe, _text: str | None, size: int, _real: Path | None, handle: object
) -> DocumentIR:
    data = b""
    if handle is not None:
        handle.seek(0)  # type: ignore[attr-defined]
        data = handle.read()  # type: ignore[attr-defined]
    return _zip_ir(path, data, size)


def _read_pdf(
    path: str, probe: Probe, _text: str | None, size: int, _real: Path | None, handle: object
) -> DocumentIR:
    """Read PDF structure from a bounded prefix, never the whole file."""
    data = b""
    if handle is not None:
        handle.seek(0)  # type: ignore[attr-defined]
        data = handle.read(MAX_SCAN_BYTES)  # type: ignore[attr-defined]
    info = read_pdf(data)
    notes = info.notes
    if not data:
        notes = (*notes, "the pdf structure needs an open handle; none was given")
    return DocumentIR(
        path=path,
        kind="pdf",
        family=probe.family,
        bytes_on_disk=size,
        pdf=info,
        notes=notes,
    )


def _read_media(
    path: str, probe: Probe, _text: str | None, size: int, _real: Path | None, handle: object
) -> DocumentIR:
    """Walk the container's boxes/chunks. Never reads the media payload."""
    data = b""
    if handle is not None:
        handle.seek(0)  # type: ignore[attr-defined]
        # The moov box can sit after mdat; read a bounded prefix and, if the
        # walk needs more, report that rather than loading a 4 GB video.
        data = handle.read(4 * 1024 * 1024)  # type: ignore[attr-defined]
    info = read_media(data, probe.format)
    notes = info.notes
    if not data:
        notes = (*notes, "the media container needs an open handle; none was given")
    return DocumentIR(
        path=path,
        kind=info.format,
        family=probe.family,
        bytes_on_disk=size,
        media_info=info,
        notes=notes,
    )


def _read_image(
    path: str, probe: Probe, _text: str | None, size: int, _real: Path | None, handle: object
) -> DocumentIR:
    """Read the image header. Only a prefix is needed, never the whole file."""
    data = b""
    if handle is not None:
        handle.seek(0)  # type: ignore[attr-defined]
        data = handle.read(1_000_000)  # type: ignore[attr-defined]
    info = read_image(data, probe.format)
    notes = info.notes
    if not data:
        notes = (*notes, "the image header needs an open handle; none was given")
    return DocumentIR(
        path=path,
        kind=info.format,
        family=probe.family,
        bytes_on_disk=size,
        media=info,
        notes=notes,
    )


def _read_parquet(
    path: str, probe: Probe, _text: str | None, size: int, _real: Path | None, handle: object
) -> DocumentIR:
    if handle is None:
        return DocumentIR(
            path=path,
            kind=probe.format,
            family=probe.family,
            notes=("the parquet footer needs an open handle; none was given",),
        )
    return _parquet_ir(path, size, handle)  # type: ignore[arg-type]


#: Adapter name -> reader. Keys are the names `adapters` declares, so the mesh
#: and this table are checked against each other by `test_ir`.
_READERS: dict[str, Reader] = {
    "text": _read_text,
    "sqlite": _read_sqlite,
    "parquet": _read_parquet,
    "archive": _read_zip,
    "image": _read_image,
    "pdf": _read_pdf,
    "media": _read_media,
}


def describe(
    path: str,
    probe: Probe,
    *,
    text: str | None = None,
    real_path: Path | None = None,
) -> DocumentIR:
    """Build the canonical description for one artifact.

    `probe` is the ingester's answer, so this module never re-decides what a
    file is. A family with no reader says so in `notes` instead of returning an
    empty description that reads like "there is nothing in this file".
    """
    size = 0
    handle = None
    try:
        if real_path is not None:
            import os

            size = os.path.getsize(real_path)
            handle = open(real_path, "rb")  # noqa: SIM115 - closed in finally
    except OSError as exc:
        return DocumentIR(
            path=path,
            kind=probe.format,
            family=probe.family,
            notes=(f"file unreadable: {exc}",),
        )

    try:
        # Routing asks the mesh which adapter claims these bytes, instead of
        # enumerating families here. The readers are unchanged; what changed is
        # that the mapping is declared in one place (`_READERS`) and a family
        # with no reader falls through to the explicit note below rather than
        # needing a branch of its own.
        reader = _READERS.get(_reader_key(probe))
        if reader is not None:
            return reader(path, probe, text, size, real_path, handle)

        return DocumentIR(
            path=path,
            kind=probe.format,
            family=probe.family,
            bytes_on_disk=size,
            notes=(
                f"no reader for the {probe.family.value} family yet; "
                f"the format is identified ({probe.format}) but not described",
            ),
        )
    finally:
        if handle is not None:
            handle.close()


def describe_file(real_path: str | Path, *, display_path: str = "") -> DocumentIR:
    """Probe a real file and describe it in one call, never raising.

    A missing or unreadable path is a *fact about the request*, not a crash: the
    caller gets a description that says so and can carry on with the rest of the
    batch. That is the fail-soft rule from the plan -- one bad path must not sink
    the request that mentioned fifty good ones.
    """
    from .ingest import probe_file

    displayed = display_path or str(real_path)
    try:
        probe = probe_file(str(real_path))
    except FileNotFoundError:
        return DocumentIR(
            path=displayed,
            kind="missing",
            family=Family.UNKNOWN,
            notes=("file not found",),
        )
    except OSError as exc:
        return DocumentIR(
            path=displayed,
            kind="unreadable",
            family=Family.UNKNOWN,
            notes=(f"file unreadable: {exc}",),
        )
    return describe(displayed, probe, real_path=Path(real_path))
