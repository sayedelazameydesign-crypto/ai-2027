"""Layer 5, part three: the artifact is handed over in a form the user can use.

Measured before writing this: on nine real artifacts the existing
`infer_artifact_kind` labelled six of them `text` — a docx, an xlsx, a parquet,
a sqlite database, a png and a pdf. A docx delivered to a text viewer renders
as mojibake, and the user reasonably concludes the system broke. The bytes were
correct; the delivery decision was wrong.
"""

from __future__ import annotations

import io
import sqlite3
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from ai2027_protocol.delivery import DeliveryForm, plan_delivery
from ai2027_protocol.ir import describe_file
from ai2027_protocol.tools import ToolOutcome
from ai2027_protocol.verify import Check, Verification, verify_file


def _docx(path: Path) -> Path:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<w:document/>")
    path.write_bytes(buffer.getvalue())
    return path


def _xlsx(path: Path) -> Path:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
    path.write_bytes(buffer.getvalue())
    return path


def _parquet(path: Path) -> Path:
    meta = b"SCHEMA" + b"\x00" * 120
    path.write_bytes(b"PAR1" + b"\x00" * 200 + meta + struct.pack("<I", len(meta)) + b"PAR1")
    return path


def _sqlite(path: Path) -> Path:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE t(a INT)")
        connection.execute("INSERT INTO t VALUES(1)")
        connection.commit()
    finally:
        connection.close()
    return path


class FamilyTests(unittest.TestCase):
    """The form follows the bytes, never the extension."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_docx_is_native_not_text(self) -> None:
        plan = plan_delivery(describe_file(str(_docx(self.tmp / "r.docx"))))
        self.assertIs(plan.primary.form, DeliveryForm.NATIVE)
        self.assertEqual(plan.primary.widget, "docx")

    def test_xlsx_keeps_its_own_identity(self) -> None:
        plan = plan_delivery(describe_file(str(_xlsx(self.tmp / "b.xlsx"))))
        self.assertEqual(plan.primary.widget, "xlsx")

    def test_xlsx_bytes_named_docx_still_report_xlsx(self) -> None:
        # The name lies; the directory does not.
        path = _xlsx(self.tmp / "liar.docx")
        plan = plan_delivery(describe_file(str(path)))
        self.assertEqual(plan.primary.widget, "xlsx")

    def test_parquet_is_a_table(self) -> None:
        plan = plan_delivery(describe_file(str(_parquet(self.tmp / "d.parquet"))))
        self.assertIs(plan.primary.form, DeliveryForm.TABLE)

    def test_sqlite_is_a_table(self) -> None:
        plan = plan_delivery(describe_file(str(_sqlite(self.tmp / "s.db"))))
        self.assertIs(plan.primary.form, DeliveryForm.TABLE)

    def test_markdown_is_text(self) -> None:
        path = self.tmp / "n.md"
        path.write_text("# عنوان\n\n## قسم\nنص\n", encoding="utf-8")
        plan = plan_delivery(describe_file(str(path)))
        self.assertIs(plan.primary.form, DeliveryForm.TEXT)

    def test_png_is_native_and_rendered_as_an_image(self) -> None:
        # NATIVE, not PREVIEW: the user's own tool opens a PNG, so the original
        # file is the best delivery. The widget still says "image", which is
        # what stops a text viewer from receiving it -- the measured defect.
        path = self.tmp / "p.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
        plan = plan_delivery(describe_file(str(path)))
        self.assertIs(plan.primary.form, DeliveryForm.NATIVE)
        self.assertEqual(plan.primary.widget, "image")


class ValidationGateTests(unittest.TestCase):
    """An unproven artifact is never handed over as if it were finished."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_failed_validation_degrades_to_a_report(self) -> None:
        described = describe_file(str(_docx(self.tmp / "r.docx")))
        failed = Verification(
            path="r.docx",
            kind="docx",
            checks=(Check("member CRCs", False, "corrupt"),),
        )
        plan = plan_delivery(described, verification=failed)
        self.assertIs(plan.primary.form, DeliveryForm.REPORT)
        self.assertFalse(plan.is_deliverable)
        self.assertIn("failed validation", plan.primary.reason)

    def test_valid_docx_survives_its_verification(self) -> None:
        path = _docx(self.tmp / "r.docx")
        described = describe_file(str(path))
        plan = plan_delivery(described, verification=verify_file(path, kind="docx"))
        self.assertIs(plan.primary.form, DeliveryForm.NATIVE)

    def test_missing_file_plans_a_report(self) -> None:
        plan = plan_delivery(describe_file(str(self.tmp / "gone.docx")))
        self.assertIs(plan.primary.form, DeliveryForm.REPORT)
        self.assertFalse(plan.is_deliverable)

    def test_stored_sqlite_passes_its_own_integrity_check(self) -> None:
        path = _sqlite(self.tmp / "s.db")
        verdict = verify_file(path, display_path="s.db", kind="sqlite")
        self.assertTrue(verdict.ok, verdict.render())

    def test_corrupt_sqlite_fails_its_own_integrity_check(self) -> None:
        path = _sqlite(self.tmp / "bad.db")
        body = bytearray(path.read_bytes())
        for offset in range(4096, min(len(body), 8192), 41):
            body[offset] ^= 0xFF
        path.write_bytes(bytes(body))
        verdict = verify_file(path, display_path="bad.db", kind="sqlite")
        self.assertFalse(verdict.ok)


class UnknownFamilyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_unknown_family_reports_and_says_why(self) -> None:
        path = self.tmp / "thing.xyz"
        path.write_bytes(bytes(range(256)) * 2)
        plan = plan_delivery(describe_file(str(path)))
        self.assertIs(plan.primary.form, DeliveryForm.REPORT)
        self.assertFalse(plan.is_deliverable)
        self.assertTrue(plan.notes)

    def test_plan_always_has_a_primary_step(self) -> None:
        from ai2027_protocol.delivery import DeliveryPlan

        self.assertIsNotNone(DeliveryPlan(path="x").primary)

    def test_notes_are_carried_from_the_description(self) -> None:
        path = self.tmp / "rand.bin"
        path.write_bytes(bytes(range(256)) * 2)
        plan = plan_delivery(describe_file(str(path)))
        self.assertTrue(plan.notes)

    def test_render_names_the_form_and_the_reason(self) -> None:
        path = self.tmp / "p.zip"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("a.txt", "hi")
        path.write_bytes(buffer.getvalue())
        rendered = plan_delivery(describe_file(str(path))).render()
        self.assertIn("deliver as", rendered)
        self.assertIn("why:", rendered)

    def test_binary_with_no_verifier_is_not_a_pass(self) -> None:
        # Honest gap, not a defect -- and still not a pass. MP4 gained a verifier
        # when the media reader landed, so the example moved to a family that
        # still has none: a CAD file.
        path = self.tmp / "part.step"
        path.write_bytes(b"ISO-10303-21;\n" + b"\x00" * 60)
        verdict = verify_file(path, display_path="part.step", kind="step")
        self.assertFalse(verdict.ok)
        self.assertTrue(any(not check.passed for check in verdict.failures))


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()


class PlanToolTests(unittest.TestCase):
    """The tool must reach the same verdict as the function it wraps."""

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
            ledger_path=self.tmp / "l.jsonl",
            sessions_root=self.tmp / "s",
            kernel_bridge=MagicMock(),
            memory_bridge=MagicMock(),
            connectors_bridge=MagicMock(),
            auditor=MagicMock(),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, **args: str) -> ToolOutcome:
        from ai2027_protocol.contracts import ToolInvocation

        invocation = ToolInvocation(tool="sandbox.plan_delivery", args=args)
        result: ToolOutcome = self.asyncio.run(self.runtime.execute(invocation, self.ctx))
        return result

    def test_docx_plans_native(self) -> None:
        _docx(self.tmp / "r.docx")
        outcome = self._run(path="r.docx")
        self.assertTrue(outcome.ok, outcome.error)
        self.assertIn("deliver as native", outcome.summary)

    def test_missing_file_is_not_deliverable(self) -> None:
        outcome = self._run(path="ghost.pdf")
        self.assertFalse(outcome.ok)
        self.assertIn("not deliverable", outcome.summary)

    def test_empty_path_is_refused(self) -> None:
        outcome = self._run(path="  ")
        self.assertFalse(outcome.ok)
        self.assertIn("non-empty path", outcome.error or "")

    def test_output_includes_the_checks(self) -> None:
        _sqlite(self.tmp / "s.db")
        outcome = self._run(path="s.db")
        self.assertTrue(outcome.ok, outcome.error)
        self.assertIn("sqlite integrity", outcome.output)


class WidgetFamilyTests(unittest.TestCase):
    """Every family must name its own renderer, so no viewer gets the wrong file."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _widget(self, name: str, head: bytes, body: bytes = b"") -> str:
        path = self.tmp / name
        path.write_bytes(head + body)
        return plan_delivery(describe_file(str(path))).primary.widget

    def test_audio_and_video_and_pdf_have_widgets(self) -> None:
        self.assertEqual(self._widget("a.mp3", b"ID3\x03\x00", b"\x00" * 40), "audio")
        self.assertEqual(self._widget("v.mp4", b"\x00\x00\x00\x18ftypmp42", b"\x00" * 40), "video")
        self.assertEqual(self._widget("d.pdf", b"%PDF-1.7", b"\x00" * 40), "pdf")

    def test_svg_and_html_are_previewed(self) -> None:
        self.assertEqual(self._widget("s.svg", b"<svg/>"), "svg")
        self.assertEqual(self._widget("p.html", b"<html></html>"), "html")

    def test_html_is_a_preview_form(self) -> None:
        path = self.tmp / "p.html"
        path.write_text("<html><body>hi</body></html>", encoding="utf-8")
        plan = plan_delivery(describe_file(str(path)))
        self.assertIs(plan.primary.form, DeliveryForm.PREVIEW)

    def test_alternatives_are_rendered_when_there_are_several(self) -> None:
        path = self.tmp / "p.svg"
        path.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
        rendered = plan_delivery(describe_file(str(path))).render()
        # The svg is a preview *and* text; the plan must offer both.
        self.assertIn("else:", rendered)

    def test_empty_plan_renders_honestly(self) -> None:
        from ai2027_protocol.delivery import DeliveryPlan

        self.assertIn("nothing to deliver", DeliveryPlan(path="x").render())

    def test_large_text_plans_the_slice_not_the_whole_file(self) -> None:
        path = self.tmp / "big.md"
        path.write_text("# عنوان\n" + "سطر طويل هنا. " * 40_000, encoding="utf-8")
        plan = plan_delivery(describe_file(str(path)))
        self.assertIn("slice", plan.primary.reason)


class ReportWidgetTests(unittest.TestCase):
    """A report must never be handed to a viewer that expects the real format.

    Measured in an end-to-end run: a corrupted docx planned as REPORT still
    carried `widget='text'`, so a UI reading the widget would have rendered a
    failed document as text -- the same class of mistake this layer exists to
    prevent, one level up.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_failed_artifact_reports_as_a_report(self) -> None:
        described = describe_file(str(_docx(self.tmp / "r.docx")))
        failed = Verification(
            path="r.docx", kind="docx", checks=(Check("member CRCs", False, "x"),)
        )
        plan = plan_delivery(described, verification=failed)
        self.assertEqual(plan.primary.widget, "report")

    def test_missing_file_reports_as_a_report(self) -> None:
        plan = plan_delivery(describe_file(str(self.tmp / "gone.docx")))
        self.assertEqual(plan.primary.widget, "report")

    def test_unknown_family_reports_as_a_report(self) -> None:
        path = self.tmp / "thing.xyz"
        path.write_bytes(bytes(range(256)) * 2)
        self.assertEqual(plan_delivery(describe_file(str(path))).primary.widget, "report")

    def test_default_step_is_a_report_too(self) -> None:
        from ai2027_protocol.delivery import DeliveryPlan

        self.assertEqual(DeliveryPlan(path="x").primary.widget, "report")
