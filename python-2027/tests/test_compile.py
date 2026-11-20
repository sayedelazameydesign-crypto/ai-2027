"""CSV -> SQLite: the first real `compile`, measured end to end.

The claim: a CSV becomes a database this package can then query, the types are
inferred from every value rather than a sample, and a refused conversion leaves
no file behind.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from ai2027_protocol.compile import CompileError, _infer_type, csv_to_sqlite


class InferTypeTests(unittest.TestCase):
    def test_all_integers(self) -> None:
        self.assertEqual(_infer_type(["1", "2", "3"]), "INTEGER")

    def test_all_floats(self) -> None:
        self.assertEqual(_infer_type(["1.5", "2.5"]), "REAL")

    def test_a_float_in_an_integer_column_makes_it_real(self) -> None:
        self.assertEqual(_infer_type(["1", "2", "3.5"]), "REAL")

    def test_one_non_numeric_value_makes_the_column_text(self) -> None:
        # Inference must see every value: a sample that stopped at row 2 would
        # declare INTEGER and then fail to insert "n/a".
        self.assertEqual(_infer_type(["1", "2", "n/a"]), "TEXT")

    def test_empty_values_are_ignored_for_the_type(self) -> None:
        self.assertEqual(_infer_type(["1", "", "3"]), "INTEGER")

    def test_an_all_empty_column_stays_text(self) -> None:
        self.assertEqual(_infer_type(["", ""]), "TEXT")

    def test_no_values_stays_text(self) -> None:
        self.assertEqual(_infer_type([]), "TEXT")

    def test_whitespace_is_trimmed_before_inference(self) -> None:
        self.assertEqual(_infer_type([" 1 ", " 2 "]), "INTEGER")


class ConvertTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _csv(self, body: str, name: str = "sales.csv") -> Path:
        path = self.dir / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_three_columns_are_typed(self) -> None:
        path = self._csv("name,qty,price\nwidget,10,2.50\nbolt,200,0.15\n")
        result = csv_to_sqlite(path)
        self.assertEqual(result.rows, 2)
        self.assertEqual(result.columns, ("name", "qty", "price"))
        self.assertEqual(dict(result.types), {"name": "TEXT", "qty": "INTEGER", "price": "REAL"})

    def test_the_source_is_untouched(self) -> None:
        path = self._csv("a,b\n1,2\n")
        before = path.read_bytes()
        csv_to_sqlite(path)
        self.assertEqual(path.read_bytes(), before)

    def test_the_database_holds_the_rows(self) -> None:
        path = self._csv("a,b\n1,x\n2,y\n3,z\n")
        result = csv_to_sqlite(path)
        with sqlite3.connect(result.target) as connection:
            rows = connection.execute("SELECT a, b FROM data ORDER BY a").fetchall()
        self.assertEqual(rows, [(1, "x"), (2, "y"), (3, "z")])

    def test_the_database_is_readable_by_the_ir(self) -> None:
        # The point of compiling to SQLite: layer 3 already queries it.
        from ai2027_protocol.ir import describe_file

        path = self._csv("name,qty\nwidget,10\nbolt,200\n")
        csv_to_sqlite(path)
        rendered = describe_file(str(path.with_suffix(".db"))).render()
        self.assertIn("1 table(s)", rendered)
        self.assertIn("name:TEXT", rendered)

    def test_the_summary_names_the_types(self) -> None:
        path = self._csv("n,v\n1,2\n")
        summary = csv_to_sqlite(path).summary
        self.assertIn("n INTEGER", summary)
        self.assertIn("1 rows", summary)

    def test_a_quoted_column_name_round_trips(self) -> None:
        path = self._csv('weird"name,v\n1,2\n')
        result = csv_to_sqlite(path)
        with sqlite3.connect(result.target) as connection:
            columns = [row[1] for row in connection.execute("PRAGMA table_info(data)")]
        self.assertEqual(columns, ['weird"name', "v"])

    def test_many_rows_are_batched(self) -> None:
        body = "n\n" + "".join(f"{index}\n" for index in range(1200))
        result = csv_to_sqlite(self._csv(body))
        self.assertEqual(result.rows, 1200)


class RefusalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _csv(self, body: str, name: str = "a.csv") -> Path:
        path = self.dir / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_an_empty_file_is_refused(self) -> None:
        with self.assertRaises(CompileError):
            csv_to_sqlite(self._csv("   \n"))

    def test_a_ragged_row_is_refused(self) -> None:
        with self.assertRaises(CompileError) as raised:
            csv_to_sqlite(self._csv("a,b\n1,2\n3\n"))
        self.assertIn("fields for 2 columns", str(raised.exception))

    def test_a_duplicate_column_is_refused(self) -> None:
        with self.assertRaises(CompileError) as raised:
            csv_to_sqlite(self._csv("a,a\n1,2\n"))
        self.assertIn("repeats", str(raised.exception))

    def test_a_header_with_no_rows_is_refused(self) -> None:
        with self.assertRaises(CompileError) as raised:
            csv_to_sqlite(self._csv("a,b\n"))
        self.assertIn("no rows", str(raised.exception))

    def test_an_empty_header_is_refused(self) -> None:
        with self.assertRaises(CompileError):
            csv_to_sqlite(self._csv(",,\n1,2,3\n"))

    def test_binary_content_is_refused(self) -> None:
        path = self.dir / "b.csv"
        path.write_bytes(b"\x00\x01,2\n3,4\n")
        with self.assertRaises(CompileError):
            csv_to_sqlite(path)

    def test_invalid_utf8_is_refused(self) -> None:
        path = self.dir / "l.csv"
        path.write_bytes(b"caf\xe9,2\n")
        with self.assertRaises(CompileError):
            csv_to_sqlite(path)

    def test_an_existing_target_is_not_overwritten(self) -> None:
        path = self._csv("a\n1\n")
        path.with_suffix(".db").write_bytes(b"existing database")
        with self.assertRaises(CompileError) as raised:
            csv_to_sqlite(path)
        self.assertIn("already exists", str(raised.exception))
        self.assertEqual(path.with_suffix(".db").read_bytes(), b"existing database")

    def test_a_missing_file_is_refused(self) -> None:
        with self.assertRaises(CompileError):
            csv_to_sqlite(self.dir / "nope.csv")

    def test_nothing_is_left_behind_after_a_refusal(self) -> None:
        path = self._csv("a,b\n1\n")
        with self.assertRaises(CompileError):
            csv_to_sqlite(path)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["a.csv"])


class EdgeTests(unittest.TestCase):
    """Branches that only a deliberately odd input reaches."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_blank_line_between_rows_is_skipped(self) -> None:
        path = self.dir / "a.csv"
        path.write_text("a,b\n1,2\n\n3,4\n", encoding="utf-8")
        self.assertEqual(csv_to_sqlite(path).rows, 2)

    def test_an_oversized_file_is_refused(self) -> None:
        from unittest import mock

        path = self.dir / "a.csv"
        path.write_text("a\n1\n", encoding="utf-8")
        with mock.patch("ai2027_protocol.compile.MAX_COMPILE_BYTES", 2):
            with self.assertRaises(CompileError):
                csv_to_sqlite(path)

    def test_a_failure_mid_write_leaves_no_partial_file(self) -> None:
        from unittest import mock

        path = self.dir / "a.csv"
        path.write_text("a\n1\n", encoding="utf-8")
        with mock.patch("ai2027_protocol.compile.Path.replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                csv_to_sqlite(path)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["a.csv"])

    def test_a_quoted_field_containing_a_comma_is_kept_whole(self) -> None:
        path = self.dir / "a.csv"
        path.write_text('name,note\nwidget,"one, two"\n', encoding="utf-8")
        result = csv_to_sqlite(path)
        with sqlite3.connect(result.target) as connection:
            row = connection.execute("SELECT note FROM data").fetchone()
        self.assertEqual(row[0], "one, two")


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
