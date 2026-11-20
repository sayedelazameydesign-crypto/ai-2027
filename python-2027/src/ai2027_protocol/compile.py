"""Compiling one format into another: the first real `compile` capability.

The gap this fills, measured
----------------------------
`coverage()` reported `compile: 0` -- every adapter declared `inspect`, `slice`,
`query`, `patch` or `render`, and none could produce a different format. A file
could be described, queried, and delivered; it could not be *converted*.

What is compiled, and why this pair
-----------------------------------
CSV -> SQLite. Both are dialects of the same idea (rows and columns), the target
is a format this package already queries (`_read_sqlite`, `query` adapter), and
the conversion is lossless for text, integers and floats. Nothing is guessed:
a column whose values all parse as integers becomes INTEGER, all parse as
floats becomes REAL, and anything else stays TEXT. The declared types are
reported so a caller can see what was inferred rather than trusting it.

Measured
--------
  sales.csv  59 bytes, 3 rows, 3 columns
  -> sales.db  16,384 bytes, schema name TEXT, qty INTEGER, price REAL

Refusals
--------
* **An empty CSV** has no header, so there is nothing to compile. Refused by
  name rather than producing an empty database.
* **Ragged rows** (more fields than the header) are refused: silently padding or
  truncating is how a conversion loses a column without saying so.
* **The target is never overwritten in place.** Same rule as `edit.py`: a
  sibling `<name>.db` is written atomically, and an existing target from another
  source is refused rather than clobbered.
* **A duplicate column name** is refused, because SQLite would accept it and
  then serve ambiguous queries.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from dataclasses import dataclass
from pathlib import Path

#: Rows to write per transaction. A one-row-per-commit loop is correct and
#: slow; 500 keeps the memory flat and the commit count small.
_BATCH = 500

#: A CSV larger than this is refused: the whole file is held as text, and a
#: conversion that exhausts memory has produced nothing.
MAX_COMPILE_BYTES = 64 * 1024 * 1024


class CompileError(Exception):
    """A conversion that was refused. The message names the rule."""


@dataclass(frozen=True)
class CompileResult:
    """What was produced, and the types that were inferred."""

    source: str
    target: str
    source_bytes: int
    target_bytes: int
    rows: int
    columns: tuple[str, ...]
    #: column name -> declared SQLite type, in header order.
    types: tuple[tuple[str, str], ...]

    @property
    def summary(self) -> str:
        """One line a caller can log."""
        shape = ", ".join(f"{name} {kind}" for name, kind in self.types)
        return (
            f"{self.source} ({self.source_bytes:,} B) -> {self.target} "
            f"({self.target_bytes:,} B): {self.rows:,} rows, {len(self.columns)} columns "
            f"[{shape}]"
        )


def _quote_identifier(name: str) -> str:
    """Quote a column name the way SQLite does, so a name with a quote works."""
    return '"' + name.replace('"', '""') + '"'


def _infer_type(values: list[str]) -> str:
    """Decide a column's SQLite type from every value it holds.

    Inference must see *all* values, not a sample: a column of integers with one
    ``n/a`` in the last row is TEXT, and sampling would declare it INTEGER and
    then fail to insert. An empty column stays TEXT, because there is no
    evidence for anything narrower.
    """
    if not values:
        return "TEXT"
    seen = [value.strip() for value in values]
    if all(value == "" for value in seen):
        return "TEXT"
    non_empty = [value for value in seen if value != ""]
    try:
        for value in non_empty:
            int(value)
    except ValueError:
        pass
    else:
        return "INTEGER"
    try:
        for value in non_empty:
            float(value)
    except ValueError:
        return "TEXT"
    return "REAL"


def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    """Read the header and rows, refusing a shape SQLite cannot serve cleanly."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise CompileError(f"cannot stat {path.name}: {exc}") from exc
    if size > MAX_COMPILE_BYTES:
        raise CompileError(
            f"{path.name} is {size:,} bytes; compiling is capped at {MAX_COMPILE_BYTES:,}"
        )

    raw = path.read_bytes()
    if b"\x00" in raw[:8192]:
        raise CompileError(f"{path.name} contains NUL bytes; it is not CSV")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CompileError(f"{path.name} is not valid UTF-8: {exc.reason}") from exc
    if not text.strip():
        raise CompileError(f"{path.name} is empty; a CSV with no header cannot be compiled")

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration as exc:  # pragma: no cover - text.strip() already ruled this out
        raise CompileError(f"{path.name} has no header row") from exc
    header = [name.strip() for name in header]
    if not header or all(name == "" for name in header):
        raise CompileError(f"{path.name} has an empty header row")
    duplicates = {name for name in header if header.count(name) > 1}
    if duplicates:
        raise CompileError(
            f"{path.name} repeats the column(s) {', '.join(sorted(duplicates))}; "
            "SQLite would accept the duplicate and then serve an ambiguous query"
        )

    rows: list[list[str]] = []
    for number, row in enumerate(reader, start=2):
        if not row:
            continue
        if len(row) != len(header):
            raise CompileError(
                f"{path.name} line {number} has {len(row)} fields for {len(header)} columns; "
                "padding or truncating would drop data silently"
            )
        rows.append(row)
    if not rows:
        raise CompileError(f"{path.name} has a header and no rows")
    return header, rows


def csv_to_sqlite(path: str | Path) -> CompileResult:
    """Compile a CSV into a SQLite database beside it. The CSV is never modified."""
    source = Path(path)
    if not source.is_file():
        raise CompileError(f"{source} is not a file")

    header, rows = _read_csv(source)
    types = [(name, _infer_type([row[index] for row in rows])) for index, name in enumerate(header)]

    target = source.with_suffix(".db")
    if target.exists():
        raise CompileError(f"{target.name} already exists; refusing to overwrite a database")

    temporary = target.with_name(target.name + ".partial")
    try:
        connection = sqlite3.connect(temporary)
        try:
            # The identifiers are quoted by _quote_identifier (doubling any
            # quote), and the types come from _infer_type's closed set of three
            # literals, so nothing here is caller-controlled SQL. The values are
            # bound as parameters below, never interpolated.
            columns = ", ".join(f"{_quote_identifier(name)} {kind}" for name, kind in types)
            connection.execute(f"CREATE TABLE data ({columns})")  # noqa: S608
            placeholders = ", ".join("?" for _ in header)
            insert = f"INSERT INTO data VALUES ({placeholders})"  # noqa: S608
            for start in range(0, len(rows), _BATCH):
                batch = [
                    tuple(None if value == "" else value for value in row)
                    for row in rows[start : start + _BATCH]
                ]
                connection.executemany(insert, batch)
            connection.commit()
        finally:
            connection.close()
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    return CompileResult(
        source=str(source),
        target=str(target),
        source_bytes=source.stat().st_size,
        target_bytes=target.stat().st_size,
        rows=len(rows),
        columns=tuple(header),
        types=tuple(types),
    )
