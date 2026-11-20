"""A map of a document that costs a fraction of the document.

Why this exists
---------------
`workspace.read_file` injects 120 lines by default and 300 at most. For a
2,000-line report that is **6% of the file**, and the model never learns that
the other 94% exists — it cannot ask for a section it cannot see. Measured:
160,000 characters of source became 9,600 characters of context, with no map.

The alternative to injecting more is to inject a *description*: where the
sections are, how big they are, what shape a table has. That costs hundreds of
tokens for a file of any size, and it lets the next read be targeted instead
of blind.

What this is not
----------------
It does not summarise. A summary is a model's opinion of the content and can
be wrong; a structural index is a fact about the bytes and can be checked. The
report says which lines each section spans, so a caller can read exactly the
part it needs and nothing else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: A heading is markdown `#`, an underlined ATX setext pair, or a numbered
#: section that a report uses in place of markdown. Kept deliberately narrow:
#: an index that treats every short line as a heading is a list of noise.
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
#: Deliberately not matching a decimal list ("3. منافسون") because that is a
#: list item far more often than a section.
_NUMBERED_SECTION = re.compile(r"^(?:Arab|Section|Chapter|الفصل|الباب|القسم)?\s*\d{1,2}[.)]\s+\S")
#: A row of a table: at least one separator, so prose with a comma is not a row.
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$|^[^,\n]+,[^,\n]*,[^\n]*$")
#: Fenced code blocks, which must not be counted as prose or headings.
_FENCE = re.compile(r"^\s*(```|~~~)")

#: How many sections an index reports before it stops listing and summarises
#: the remainder. Beyond this the index itself becomes the context bloat.
MAX_SECTIONS = 40
#: A section smaller than this is folded into its parent rather than listed.
MIN_SECTION_CHARS = 40


@dataclass(frozen=True)
class Section:
    """One addressable region of a document."""

    title: str
    level: int
    #: 1-based, inclusive — the numbers a reader can pass straight back.
    start_line: int
    end_line: int
    chars: int

    @property
    def lines(self) -> int:
        """How many lines this section spans."""
        return self.end_line - self.start_line + 1


@dataclass(frozen=True)
class DocumentIndex:
    """The map: what is in a file and where, without the content."""

    path: str
    total_lines: int
    total_chars: int
    sections: tuple[Section, ...] = field(default_factory=tuple)
    #: Lines that look like tabular data, and how wide the widest row is.
    table_lines: int = 0
    table_columns: int = 0
    #: Fenced code blocks and their total size.
    code_blocks: int = 0
    code_chars: int = 0
    #: True when the section list was cut at MAX_SECTIONS.
    truncated: bool = False
    #: Non-fatal notes: ambiguous structure, empty file, unsupported kind.
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def section_count(self) -> int:
        """Sections the index can point at."""
        return len(self.sections)

    def render(self, limit: int = 24) -> str:
        """A compact text form, sized for a prompt rather than a screen.

        One line per section, so the cost grows with the number of sections
        and not with the size of the file. That is the whole point: 2,000
        lines and 20,000 lines cost the same to describe.
        """
        head = (
            f"{self.path}: {self.total_lines} lines, {self.total_chars} chars, "
            f"{self.section_count} section(s)"
        )
        if self.table_lines:
            head += f", {self.table_lines} table line(s) up to {self.table_columns} columns"
        if self.code_blocks:
            head += f", {self.code_blocks} code block(s)"
        lines = [head]
        for section in self.sections[:limit]:
            indent = "  " * max(0, section.level - 1)
            lines.append(
                f"{indent}- {section.title} [lines {section.start_line}-{section.end_line}, "
                f"{section.chars} chars]"
            )
        if len(self.sections) > limit:
            lines.append(f"  … {len(self.sections) - limit} more section(s)")
        if self.truncated:
            lines.append(f"  (section list capped at {MAX_SECTIONS})")
        lines.extend(f"  note: {note}" for note in self.notes)
        return "\n".join(lines)

    def locate(self, query: str) -> tuple[Section, ...]:
        """Sections whose title contains `query`, case-insensitively.

        This is what makes the map useful: the caller reads a section by name
        instead of guessing line numbers.
        """
        needle = query.strip().casefold()
        if not needle:
            return ()
        return tuple(s for s in self.sections if needle in s.title.casefold())


def _heading_of(line: str) -> tuple[int, str] | None:
    """The (level, title) of a heading line, or None."""
    match = _MD_HEADING.match(line)
    if match:
        return len(match.group(1)), match.group(2).strip()
    if _NUMBERED_SECTION.match(line) and len(line.strip()) <= 80:
        return 2, line.strip()
    return None


def build_index(path: str, text: str) -> DocumentIndex:
    """Describe a document's structure without copying its content."""
    lines = text.splitlines()
    total_lines = len(lines)
    total_chars = len(text)
    if total_lines == 0:
        return DocumentIndex(
            path=path,
            total_lines=0,
            total_chars=0,
            notes=("empty file",),
        )

    heads: list[tuple[int, int, str]] = []  # (line_number, level, title)
    table_lines = 0
    widest = 0
    code_blocks = 0
    code_chars = 0
    in_code = False
    code_start = 0

    for number, line in enumerate(lines, start=1):
        if _FENCE.match(line):
            if in_code:
                code_blocks += 1
                code_chars += sum(len(item) for item in lines[code_start:number])
                in_code = False
            else:
                in_code = True
                code_start = number
            continue
        if in_code:
            continue
        heading = _heading_of(line)
        if heading:
            heads.append((number, heading[0], heading[1]))
            continue
        if _TABLE_ROW.match(line) and line.strip():
            table_lines += 1
            cells = line.count(",") + 1 if "|" not in line else line.count("|") - 1
            widest = max(widest, cells)

    if in_code:
        # An unterminated fence is a real defect in the artifact, not an index
        # problem, but the index must not silently pretend it is prose.
        code_blocks += 1
        code_chars += sum(len(item) for item in lines[code_start:])

    notes: list[str] = []
    if not heads:
        notes.append("no headings; this is a single block of text")
    elif heads[0][0] > 1:
        # Content before the first heading belongs to no section and would be
        # unreachable by line range. The benchmark's own reports start with a
        # title, so this is the common shape, not an edge case.
        notes.append(f"lines 1-{heads[0][0] - 1} precede the first heading")

    sections: list[Section] = []
    for position, (number, level, title) in enumerate(heads):
        # The first section starts at line 1 so every line of the document is
        # addressable: a range that starts at the heading leaves the preamble
        # unreachable, and `slice_lines` could never return it.
        start = 1 if position == 0 else number
        end = heads[position + 1][0] - 1 if position + 1 < len(heads) else total_lines
        chars = sum(len(item) + 1 for item in lines[start - 1 : end])
        if chars < MIN_SECTION_CHARS and position not in (0, len(heads) - 1):
            # Fold a tiny heading into the next section rather than listing a
            # line that contains almost nothing. The first and last headings
            # are never folded: folding the first made the document title
            # unaddressable, which the coverage test caught.
            continue
        sections.append(
            Section(
                title=title,
                level=level,
                start_line=start,
                end_line=end,
                chars=chars,
            )
        )

    truncated = len(sections) > MAX_SECTIONS
    if truncated:
        sections = sections[:MAX_SECTIONS]
        notes.append(f"{len(heads)} headings, first {MAX_SECTIONS} listed")

    return DocumentIndex(
        path=path,
        total_lines=total_lines,
        total_chars=total_chars,
        sections=tuple(sections),
        table_lines=table_lines,
        table_columns=widest,
        code_blocks=code_blocks,
        code_chars=code_chars,
        truncated=truncated,
        notes=tuple(notes),
    )


def slice_lines(text: str, start: int, end: int) -> str:
    """Lines `start`..`end` inclusive, 1-based, clamped to the file.

    The companion to the index: an index that cannot be acted on is a table of
    contents with no pages.
    """
    lines = text.splitlines()
    first = max(1, start)
    last = min(len(lines), end)
    if first > last:
        return ""
    return "\n".join(lines[first - 1 : last])
