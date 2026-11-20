"""Stable data contracts for canonical file descriptions."""

from __future__ import annotations

from dataclasses import dataclass, field

from .geometry import GeometryInfo
from .images import ImageInfo
from .index import DocumentIndex
from .ingest import Family
from .media import MediaInfo
from .ole import OleInfo
from .pdf import PdfInfo


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
    #: Geometry files declare their object and vertex counts in a header.
    geometry: GeometryInfo | None = None
    #: OLE2 compound files carry a directory that names the document format.
    ole: OleInfo | None = None
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

        if self.geometry is not None:
            geometry_lines = self.geometry.render().splitlines()
            lines.extend("  " + line for line in geometry_lines)

        if self.ole is not None:
            ole_lines = self.ole.render().splitlines()
            lines.extend("  " + line for line in ole_lines)

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
            or (self.geometry is not None and self.geometry.readable)
            or (self.ole is not None and self.ole.readable)
        )
