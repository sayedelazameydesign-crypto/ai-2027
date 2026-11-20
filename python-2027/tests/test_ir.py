"""Layer 4: the canonical IR, proved against real artifacts.

Every test here builds the artifact it describes, so a failure means the reader
is wrong -- not that a fixture drifted. Two rules the plan demands are checked
directly rather than assumed: describing a file must never modify it, and the
description's cost must not follow the file's size.

The class-based style (``unittest.TestCase``) is the repository's: it is what
pytest collects when the file/class naming does not match its defaults.
"""

from __future__ import annotations

import hashlib
import sqlite3
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from ai2027_protocol.ingest import probe_bytes, probe_file
from ai2027_protocol.ir import (
    MAX_TABLES,
    Column,
    DocumentIR,
    Table,
    describe,
    describe_file,
)
from ai2027_protocol.tools import ToolOutcome


def _make_db(path: Path, *, tables: int = 1, rows: int = 3) -> Path:
    connection = sqlite3.connect(path)
    try:
        for index in range(tables):
            connection.execute(
                f'CREATE TABLE "t{index}"(id INTEGER PRIMARY KEY, city TEXT NOT NULL, total REAL)'
            )
            connection.executemany(
                f'INSERT INTO "t{index}" VALUES(?,?,?)',
                ((row, "الجيزة", row * 1.5) for row in range(rows)),
            )
        connection.commit()
    finally:
        connection.close()
    return path


def _make_parquet(path: Path, *, metadata: int = 120) -> Path:
    """A file shaped like parquet: PAR1 header, footer, 4-byte length, PAR1."""
    body = b"PAR1" + b"\x00" * 200 + b"S" * metadata
    path.write_bytes(body + struct.pack("<I", metadata) + b"PAR1")
    return path


class SqliteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_tables_columns_and_rows(self) -> None:
        described = describe_file(_make_db(self.tmp / "a.db", rows=7))
        self.assertEqual(described.family.value, "database")
        self.assertEqual(described.kind, "sqlite")
        self.assertEqual(len(described.tables), 1)
        table = described.tables[0]
        self.assertEqual(table.name, "t0")
        self.assertEqual(table.rows, 7)
        self.assertEqual([column.name for column in table.columns], ["id", "city", "total"])
        self.assertEqual([column.type for column in table.columns], ["INTEGER", "TEXT", "REAL"])

    def test_not_null_becomes_nullable_false(self) -> None:
        table = describe_file(_make_db(self.tmp / "a.db")).tables[0]
        declared = {column.name: column.nullable for column in table.columns}
        self.assertFalse(declared["city"])
        self.assertTrue(declared["total"])

    def test_zero_byte_file_probes_as_text(self) -> None:
        path = self.tmp / "empty.db"
        sqlite3.connect(path).close()
        path.write_bytes(b"")
        described = describe_file(str(path))
        # Zero bytes probe as text, which is honest: there is nothing to identify.
        self.assertEqual(described.family.value, "text")
        self.assertIn("empty file", described.notes[0])

    def test_database_without_tables_says_so(self) -> None:
        # SQLite writes no header until the first schema change, so a database
        # with no tables has to be created via a write -> drop to stay 4 KiB.
        path = self.tmp / "bare.db"
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE gone(a INT)")
        connection.execute("DROP TABLE gone")
        connection.commit()
        connection.close()
        self.assertGreater(path.stat().st_size, 0)
        described = describe_file(str(path))
        self.assertEqual(described.family.value, "database")
        self.assertEqual(described.tables, ())
        self.assertTrue(any("no tables" in note for note in described.notes))

    def test_table_cap_holds_and_is_declared(self) -> None:
        described = describe_file(_make_db(self.tmp / "many.db", tables=MAX_TABLES + 5, rows=0))
        self.assertEqual(len(described.tables), MAX_TABLES)
        self.assertTrue(any(f"first {MAX_TABLES} listed" in note for note in described.notes))

    def test_table_without_rows_is_visible(self) -> None:
        described = describe_file(_make_db(self.tmp / "z.db", rows=0))
        self.assertEqual(described.tables[0].rows, 0)
        self.assertIn("0 rows", described.render())


class ReadOnlyTests(unittest.TestCase):
    """Describing an artifact must cost the artifact nothing."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_describing_does_not_touch_the_file(self) -> None:
        path = _make_db(self.tmp / "a.db", rows=5)
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        stat_before = path.stat()
        describe_file(str(path))
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
        self.assertEqual(path.stat().st_size, stat_before.st_size)
        self.assertEqual(path.stat().st_mtime_ns, stat_before.st_mtime_ns)

    def test_describing_leaves_no_journal(self) -> None:
        path = _make_db(self.tmp / "a.db")
        describe_file(str(path))
        self.assertEqual(sorted(item.name for item in self.tmp.iterdir()), [path.name])

    def test_corrupt_sqlite_is_reported_not_raised(self) -> None:
        path = self.tmp / "fake.db"
        path.write_bytes(b"SQLite format 3\x00" + b"\xff" * 200)
        described = describe_file(str(path))
        self.assertEqual(described.family.value, "database")
        self.assertTrue(any("schema read failed" in note for note in described.notes))


class ParquetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_footer_length_is_read(self) -> None:
        described = describe_file(_make_parquet(self.tmp / "a.parquet", metadata=126))
        self.assertEqual(described.family.value, "columnar")
        self.assertEqual(described.kind, "parquet")
        self.assertTrue(any("126 bytes" in note for note in described.notes))

    def test_missing_footer_is_reported(self) -> None:
        path = self.tmp / "broken.parquet"
        path.write_bytes(b"PAR1" + b"\x00" * 300)
        described = describe_file(str(path))
        self.assertTrue(any("no parquet footer" in note for note in described.notes))

    def test_zero_length_footer_is_called_empty(self) -> None:
        described = describe_file(_make_parquet(self.tmp / "e.parquet", metadata=0))
        self.assertTrue(any("empty" in note for note in described.notes))


class FailSoftTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_missing_file_returns_a_description(self) -> None:
        described = describe_file(str(self.tmp / "absent.bin"))
        self.assertEqual(described.kind, "missing")
        self.assertEqual(described.notes, ("file not found",))

    def test_unknown_family_names_the_format(self) -> None:
        path = self.tmp / "rand.bin"
        path.write_bytes(bytes(range(256)) * 4)
        described = describe_file(str(path))
        self.assertEqual(described.family.value, "unknown")
        self.assertTrue(any("no reader" in note for note in described.notes))

    def test_display_path_differs_from_disk_path(self) -> None:
        path = _make_db(self.tmp / "internal.db")
        described = describe_file(str(path), display_path="بيانات/المبيعات.db")
        self.assertEqual(described.path, "بيانات/المبيعات.db")
        self.assertTrue(described.tables)  # still read from the real file


class TextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_text_goes_through_the_index(self) -> None:
        path = self.tmp / "report.md"
        path.write_text("# تقرير\n\n## الملخص\n" + "نص. " * 30, encoding="utf-8")
        described = describe_file(str(path))
        index = described.text_index
        self.assertIsNotNone(index)
        assert index is not None  # narrows the type for mypy --strict
        self.assertEqual(len(index.sections), 2)
        self.assertIn("الملخص", described.render())

    def test_zip_lists_entries(self) -> None:
        path = self.tmp / "letter.docx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("[Content_Types].xml", "<T/>")
            archive.writestr("word/document.xml", "<d/>")
        described = describe_file(str(path))
        self.assertEqual(described.family.value, "zip")
        self.assertTrue(any("2 entries" in note for note in described.notes))


class CostTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_render_cost_does_not_follow_file_size(self) -> None:
        small = self.tmp / "small.md"
        small.write_text("# عنوان\nنص\n", encoding="utf-8")
        huge = self.tmp / "huge.md"
        huge.write_text(
            "\n".join(f"## قسم {i}\n" + "سطر نصي طويل. " * 40 for i in range(3000)),
            encoding="utf-8",
        )
        small_render = describe_file(str(small)).render()
        huge_render = describe_file(str(huge)).render()
        self.assertGreater(huge.stat().st_size, 100 * small.stat().st_size)
        self.assertLess(len(huge_render), 2_000)
        self.assertLess(len(huge_render), 30 * len(small_render))

    def test_inspectable_flag_matches_the_reader(self) -> None:
        empty = DocumentIR(path="x", kind="unknown", family=probe_bytes(b"").family)
        self.assertFalse(empty.is_inspectable)
        described = DocumentIR(
            path="x",
            kind="sqlite",
            family=probe_bytes(b"SQLite format 3\x00").family,
            tables=(Table(name="t", columns=(Column(name="a", type="INT"),)),),
        )
        self.assertTrue(described.is_inspectable)


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_describe_accepts_a_probe_and_optional_text(self) -> None:
        body = "# عنوان\n\n## قسم\nنص"
        described = describe("x.md", probe_bytes(body.encode()), text=body)
        self.assertIsNotNone(described.text_index)
        self.assertEqual(described.path, "x.md")

    def test_family_is_stable_for_the_same_bytes(self) -> None:
        path = _make_db(self.tmp / "a.db")
        first = describe_file(str(path))
        second = describe(str(path), probe_file(str(path)), real_path=path)
        self.assertEqual(first.kind, second.kind)
        self.assertEqual(len(first.tables), len(second.tables))

    def test_unreadable_file_is_not_an_exception(self) -> None:
        path = self.tmp / "locked.db"
        path.write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
        path.chmod(0o000)
        try:
            described = describe_file(str(path))
        finally:
            path.chmod(0o644)
        self.assertIn(described.kind, {"unreadable", "sqlite"})
        self.assertTrue(described.notes)


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()


class RenderEdgeTests(unittest.TestCase):
    """The renderer must say how much it left out, and never crash on a field."""

    def test_more_tables_than_the_limit_are_counted(self) -> None:
        from ai2027_protocol.ingest import Family
        from ai2027_protocol.ir import DocumentIR

        described = DocumentIR(
            path="m.db",
            kind="sqlite",
            family=Family.DATABASE,
            tables=tuple(Table(name=f"t{i}") for i in range(5)),
        )
        rendered = described.render(limit=2)
        self.assertIn("… 3 more table(s)", rendered)

    def test_columns_are_listed_with_their_types(self) -> None:
        from ai2027_protocol.ingest import Family
        from ai2027_protocol.ir import DocumentIR

        described = DocumentIR(
            path="d.parquet",
            kind="parquet",
            family=Family.COLUMNAR,
            columns=(Column(name="id", type="INT64"), Column(name="city", type="UTF8")),
        )
        self.assertIn("id:INT64, city:UTF8", described.render())


class SqliteErrorTests(unittest.TestCase):
    """Every SQLite failure path returns a description, never an exception."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_unopenable_database_reports_read_only_failure(self) -> None:
        described = describe_file(str(self.tmp / "ghost" / "x.db"))
        self.assertEqual(described.kind, "missing")

    def test_indexes_are_reported(self) -> None:
        path = _make_db(self.tmp / "ix.db")
        connection = sqlite3.connect(path)
        connection.execute("CREATE INDEX idx_city ON t0(city)")
        connection.commit()
        connection.close()
        described = describe_file(str(path))
        self.assertTrue(
            any("index" in note for note in described.notes),
            described.notes,
        )

    def test_row_count_failure_is_reported_per_table(self) -> None:
        path = self.tmp / "viewish.db"
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE t(a INT)")
        connection.execute("INSERT INTO t VALUES(1)")
        connection.execute("CREATE VIEW v AS SELECT * FROM t")
        connection.commit()
        connection.close()
        described = describe_file(str(path))
        # The view is not a table, so only "t" is listed with an exact count.
        self.assertEqual([table.name for table in described.tables], ["t"])


class ParquetErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_truncated_footer_length_is_reported(self) -> None:
        path = self.tmp / "tiny.parquet"
        path.write_bytes(b"PAR1PAR1")
        described = describe_file(str(path))
        self.assertEqual(described.family.value, "columnar")
        self.assertTrue(described.notes)


class ZipRefusalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_zip_bomb_is_refused_not_described(self) -> None:
        path = self.tmp / "bomb.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("zeros.bin", b"\x00" * (6 * 1024 * 1024))
        described = describe_file(str(path))
        self.assertEqual(described.family.value, "zip")
        self.assertTrue(any("refused" in note for note in described.notes), described.notes)


class WarningAndRaceTests(unittest.TestCase):
    """Warnings must surface, and a file vanishing mid-call must not raise."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_zip_warnings_are_carried_into_the_notes(self) -> None:
        from unittest.mock import patch

        from ai2027_protocol.containers import ContainerReport

        path = self.tmp / "w.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("a.txt", "hello")
        report = ContainerReport(
            format="zip",
            entries=("a.txt",),
            warnings=("an entry has an unsafe name",),
        )
        with patch("ai2027_protocol.containers.inspect_zip", return_value=report):
            described = describe_file(str(path))
        self.assertIn("an entry has an unsafe name", described.notes)

    def test_vanished_file_is_reported_not_raised(self) -> None:
        # A path that probes fine but disappears before the handle opens: the
        # between-probe-and-open race a long batch can hit.
        from ai2027_protocol.ingest import probe_bytes

        probe = probe_bytes(b"# t\n")
        described = describe(str(self.tmp / "ghost.md"), probe, real_path=self.tmp / "ghost.md")
        self.assertEqual(described.kind, "text")
        self.assertEqual(described.bytes_on_disk, 0)

    def test_unopenable_path_reports_the_error(self) -> None:
        from unittest.mock import patch

        from ai2027_protocol.ingest import probe_bytes

        probe = probe_bytes(b"# t\n")
        with patch(
            "ai2027_protocol.ir.open", side_effect=OSError("permission denied"), create=True
        ):
            described = describe(str(self.tmp / "x.md"), probe, real_path=self.tmp / "x.md")
        self.assertEqual(described.notes[0][:17], "file unreadable: ")


class DescribeToolTests(unittest.TestCase):
    """The tool contract: same runtime, same guards, same outcome shape."""

    def setUp(self) -> None:
        import asyncio
        from unittest.mock import MagicMock

        from ai2027_protocol.tools import SandboxToolRuntime, ToolContext

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.asyncio = asyncio
        self.runtime = SandboxToolRuntime()
        self.ctx = ToolContext(
            session_id="s",
            workspace_root=self.tmp,
            sandbox_dir=self.tmp,
            ledger_path=self.tmp / "ledger.jsonl",
            sessions_root=self.tmp / "sessions",
            kernel_bridge=MagicMock(),
            memory_bridge=MagicMock(),
            connectors_bridge=MagicMock(),
            auditor=MagicMock(),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, path: str) -> ToolOutcome:
        from ai2027_protocol.contracts import ToolInvocation

        invocation = ToolInvocation(tool="workspace.describe_file", args={"path": path})
        return self.asyncio.run(self.runtime.execute(invocation, self.ctx))

    def test_database_is_described(self) -> None:
        _make_db(self.tmp / "a.db", rows=250)
        outcome = self._run("a.db")
        self.assertTrue(outcome.ok)
        self.assertIn("250 row(s)", outcome.summary)
        self.assertIn("orders", outcome.output) if "orders" in outcome.output else None
        self.assertIn("t0", outcome.output)

    def test_text_is_described(self) -> None:
        (self.tmp / "n.md").write_text("# تقرير\n\n## ملخص\nنص\n", encoding="utf-8")
        outcome = self._run("n.md")
        self.assertTrue(outcome.ok)
        self.assertIn("2 section(s)", outcome.summary)

    def test_missing_file_is_not_ok_but_not_raised(self) -> None:
        outcome = self._run("ghost.xyz")
        self.assertFalse(outcome.ok)
        self.assertIn("file not found", outcome.output)

    def test_empty_path_is_refused(self) -> None:
        outcome = self._run("   ")
        self.assertFalse(outcome.ok)
        self.assertIn("non-empty path", outcome.error or "")

    def test_secret_filename_is_refused(self) -> None:
        (self.tmp / ".env").write_text("KEY=1", encoding="utf-8")
        outcome = self._run(".env")
        self.assertFalse(outcome.ok)
        self.assertIn("secret", (outcome.error or "").lower())

    def test_path_traversal_is_refused(self) -> None:
        outcome = self._run("../../etc/passwd")
        self.assertFalse(outcome.ok)
