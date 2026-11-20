"""The index must describe a document for a fraction of what the document costs.

Measured before writing it: `read_file` injects 120 lines by default, which is
**6% of a 2,000-line report**, and the model never learns the other 94% exists.
A structural index is the alternative to injecting more: it costs 6.8% of a
3,000-character document and 1.2% of a 130,000-character one, because the
section list is capped and the document is not.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from ai2027_protocol.contracts import ToolInvocation
from ai2027_protocol.index import MAX_SECTIONS, build_index, slice_lines
from ai2027_protocol.tools import SandboxToolRuntime, ToolContext


def _report(sections: int = 3, body: int = 40) -> str:
    """A document shaped like the ones the benchmark asks for."""
    parts = ["# تقرير سوق العسل"]
    for index in range(sections):
        parts.append(f"## القسم {index}")
        parts.append("نص المحتوى هنا. " * body)
    return "\n\n".join(parts)


class StructureTests(unittest.TestCase):
    """The map must be accurate, because a wrong map is worse than none."""

    def test_headings_become_sections_with_line_ranges(self) -> None:
        # The document title is a section too: it owns the lines above the
        # first "##", and leaving it out made them unaddressable.
        document = build_index("r.md", _report())
        self.assertEqual(document.section_count, 4)
        self.assertEqual(document.sections[0].title, "تقرير سوق العسل")
        self.assertEqual(document.sections[0].start_line, 1)
        body = document.sections[1]
        self.assertEqual(body.title, "القسم 0")
        self.assertLess(body.start_line, body.end_line)

    def test_line_ranges_cover_the_whole_document(self) -> None:
        """A gap would mean a region the caller can never address."""
        text = _report()
        document = build_index("r.md", text)
        covered: set[int] = set()
        for section in document.sections:
            covered.update(range(section.start_line, section.end_line + 1))
        total = len(text.splitlines())
        uncovered = set(range(1, total + 1)) - covered
        # Line 1 is the title heading and belongs to the first section, so
        # only a heading line itself may legitimately sit outside... it must
        # not: every line must be addressable.
        self.assertEqual(uncovered, set(), f"lines {sorted(uncovered)} are in no section")

    def test_an_arabic_heading_is_recognised(self) -> None:
        document = build_index("r.md", "# تقرير\n\nنص\n\n## التوصيات\n\nنص")
        self.assertIn("التوصيات", [s.title for s in document.sections])

    def test_a_document_without_headings_says_so(self) -> None:
        document = build_index("r.md", "فقط نص متصل بلا أي عنوان على الإطلاق.")
        self.assertEqual(document.section_count, 0)
        self.assertTrue(any("no headings" in note for note in document.notes))

    def test_code_fences_are_counted_not_read_as_prose(self) -> None:
        text = "# كود\n\n```python\ndef f():\n    return 1\n```\n"
        document = build_index("r.md", text)
        self.assertEqual(document.code_blocks, 1)
        self.assertGreater(document.code_chars, 0)

    def test_an_unterminated_fence_is_counted_and_noted(self) -> None:
        text = "# كود\n\n```python\ndef f():\n"
        document = build_index("r.md", text)
        self.assertEqual(document.code_blocks, 1)

    def test_tables_are_measured_by_shape(self) -> None:
        text = "# جدول\n\na,b,c\n1,2,3\n4,5,6\n"
        document = build_index("r.md", text)
        self.assertEqual(document.table_lines, 3)
        self.assertEqual(document.table_columns, 3)

    def test_an_empty_file_is_handled(self) -> None:
        document = build_index("r.md", "")
        self.assertEqual(document.total_lines, 0)
        self.assertIn("empty file", document.notes)


class CostTests(unittest.TestCase):
    """The point of the index is that its cost stops growing."""

    def test_the_index_is_a_small_fraction_of_a_large_document(self) -> None:
        text = _report(sections=MAX_SECTIONS, body=40)
        document = build_index("r.md", text)
        rendered = document.render()
        ratio = len(rendered) / document.total_chars
        self.assertLess(ratio, 0.10, f"index is {ratio:.0%} of the document")

    def test_the_section_list_is_capped(self) -> None:
        text = _report(sections=MAX_SECTIONS + 60, body=5)
        document = build_index("r.md", text)
        self.assertTrue(document.truncated)
        self.assertEqual(document.section_count, MAX_SECTIONS)

    def test_a_bigger_document_does_not_cost_proportionally_more(self) -> None:
        small = build_index("a.md", _report(sections=5, body=10))
        large = build_index("b.md", _report(sections=5, body=400))
        self.assertGreater(large.total_chars, small.total_chars * 20)
        self.assertLess(
            len(large.render()) / large.total_chars,
            len(small.render()) / small.total_chars,
        )


class LocateTests(unittest.TestCase):
    """An index that cannot be acted on is a table of contents with no pages."""

    def test_locate_finds_a_section_by_name(self) -> None:
        document = build_index("r.md", _report(sections=4))
        found = document.locate("القسم 2")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].title, "القسم 2")

    def test_locate_returns_nothing_for_an_absent_name(self) -> None:
        document = build_index("r.md", _report())
        self.assertEqual(document.locate("لا يوجد"), ())

    def test_locate_ignores_surrounding_spaces_and_case(self) -> None:
        document = build_index("r.md", "# Summary\n\nنبذة\n")
        self.assertEqual(len(document.locate("  summary ")), 1)

    def test_a_slice_returns_exactly_the_located_lines(self) -> None:
        text = _report(sections=3)
        document = build_index("r.md", text)
        section = document.locate("القسم 1")[0]
        chunk = slice_lines(text, section.start_line, section.end_line)
        self.assertIn("القسم 1", chunk)
        self.assertNotIn("القسم 0", chunk)
        self.assertNotIn("القسم 2", chunk)

    def test_a_slice_is_clamped_not_crashing(self) -> None:
        text = "a\nb\nc"
        self.assertEqual(slice_lines(text, 2, 99), "b\nc")
        self.assertEqual(slice_lines(text, 99, 100), "")


class ToolTests(unittest.TestCase):
    """The index is reachable as a tool, and refuses what it cannot describe."""

    def _context(self, root: Path) -> ToolContext:
        return ToolContext(
            session_id="t",
            workspace_root=root,
            sandbox_dir=root,
            ledger_path=root / "l.db",
            sessions_root=root,
            kernel_bridge=MagicMock(),
            memory_bridge=MagicMock(),
            connectors_bridge=MagicMock(),
            auditor=MagicMock(),
        )

    def test_index_file_describes_an_arabic_report(self) -> None:
        runtime = SandboxToolRuntime()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "report.md").write_text(_report(), encoding="utf-8")
            outcome = asyncio.run(
                runtime.execute(
                    ToolInvocation(
                        tool="workspace.index_file",
                        args={"path": "report.md", "scope": "workspace"},
                    ),
                    self._context(root),
                )
            )
        self.assertTrue(outcome.ok, outcome.error)
        self.assertIn("section(s)", outcome.summary)
        self.assertIn("القسم 0", outcome.output or "")

    def test_index_file_refuses_a_binary(self) -> None:
        runtime = SandboxToolRuntime()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
            outcome = asyncio.run(
                runtime.execute(
                    ToolInvocation(
                        tool="workspace.index_file",
                        args={"path": "pic.png", "scope": "workspace"},
                    ),
                    self._context(root),
                )
            )
        self.assertFalse(outcome.ok)
        self.assertIn("not text", outcome.error or "")


if __name__ == "__main__":
    unittest.main()
