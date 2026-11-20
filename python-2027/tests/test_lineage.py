"""Layer 5, part two: a claim without a checkable address does not ship.

The gate is deliberately blunt. One uncited sentence makes the whole answer
undeliverable, because the sentence without a source is the one that gets
quoted. The tests below pin that bluntness in place: it is a decision, not an
accident, and a future change that relaxes it will fail here.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ai2027_protocol.lineage import LOCATORS, Claim, Lineage, read_lineage_file
from ai2027_protocol.verify import Check, Verification


def _valid(path: str = "a.md") -> Verification:
    return Verification(
        path=path,
        kind="text",
        checks=(Check("exists", True, "10 bytes"),),
        sha256="deadbeef" * 8,
        bytes_on_disk=10,
    )


def _invalid(path: str = "bad.docx") -> Verification:
    return Verification(
        path=path,
        kind="docx",
        checks=(Check("member CRCs", False, "corrupt"),),
        sha256="cafe" * 16,
        bytes_on_disk=100,
    )


class ClaimTests(unittest.TestCase):
    def test_a_cited_claim_resolves(self) -> None:
        claim = Claim("المبيعات أعلى", "sales.db", "table:orders#total", "workspace.describe_file")
        self.assertTrue(claim.is_cited)
        self.assertTrue(claim.resolves)

    def test_no_locator_is_uncited(self) -> None:
        self.assertFalse(Claim("ادعاء", "sales.db", "").is_cited)

    def test_no_source_is_uncited(self) -> None:
        self.assertFalse(Claim("ادعاء", "  ", "table:orders").is_cited)

    def test_unknown_vocabulary_does_not_resolve(self) -> None:
        claim = Claim("ادعاء", "x.md", "mystery:7")
        self.assertTrue(claim.is_cited)
        self.assertFalse(claim.resolves)

    def test_line_ranges_are_a_real_vocabulary(self) -> None:
        # `ir.py` renders `[lines 9-10]`, so `lines:` must resolve. It did not
        # in the first draft, which reported a perfectly good citation as bad.
        self.assertIn("lines", LOCATORS)
        self.assertTrue(Claim("t", "r.md", "lines:9-10").resolves)

    def test_section_locator_resolves(self) -> None:
        self.assertEqual(Claim("t", "r.md", "section:الأرقام").locator_kind, "section")


class GateTests(unittest.TestCase):
    def test_empty_lineage_is_deliverable(self) -> None:
        # Nothing claimed is not the same as something wrong.
        self.assertTrue(Lineage().is_deliverable)

    def test_one_uncited_claim_blocks_delivery(self) -> None:
        lineage = Lineage(
            claims=(Claim("موثّق", "a.md", "lines:1-2"), Claim("غير موثّق", "a.md", "")),
            verifications=(_valid(),),
        )
        self.assertFalse(lineage.is_deliverable)
        self.assertEqual(len(lineage.uncited), 1)

    def test_unresolvable_citation_blocks_delivery(self) -> None:
        lineage = Lineage(claims=(Claim("t", "a.md", "mystery:1"),))
        self.assertFalse(lineage.is_deliverable)
        self.assertEqual(len(lineage.unresolvable), 1)

    def test_invalid_artifact_blocks_delivery_even_when_cited(self) -> None:
        lineage = Lineage(
            claims=(Claim("النص يقول كذا", "bad.docx", "member:word/document.xml"),),
            verifications=(_invalid(),),
        )
        self.assertFalse(lineage.is_deliverable)
        self.assertEqual([item.path for item in lineage.invalid_artifacts], ["bad.docx"])

    def test_everything_cited_and_valid_is_deliverable(self) -> None:
        lineage = Lineage(
            claims=(Claim("مدعوم", "a.md", "lines:1-3"),),
            verifications=(_valid(),),
        )
        self.assertTrue(lineage.is_deliverable)


class RenderTests(unittest.TestCase):
    def test_verdict_line_is_last(self) -> None:
        rendered = Lineage(claims=(Claim("t", "a.md", "lines:1"),)).render()
        self.assertTrue(rendered.endswith("verdict: deliverable"))

    def test_failure_is_named_in_the_trail(self) -> None:
        rendered = Lineage(verifications=(_invalid(),)).render()
        self.assertIn("[INVALID]", rendered)
        self.assertIn("NOT deliverable", rendered)

    def test_empty_lineage_says_why_it_is_empty(self) -> None:
        self.assertIn("nothing to trace", Lineage().render())

    def test_source_and_locator_both_appear(self) -> None:
        rendered = Lineage(claims=(Claim("t", "sales.db", "table:orders", "describe"),)).render()
        self.assertIn("sales.db", rendered)
        self.assertIn("table:orders", rendered)
        self.assertIn("via describe", rendered)


class JsonTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_json_round_trips_claims(self) -> None:
        lineage = Lineage(
            claims=(Claim("t", "a.md", "lines:1-2", "read"),),
            verifications=(_valid(),),
        )
        path = self.tmp / "lineage.json"
        path.write_text(lineage.to_json(), encoding="utf-8")
        back = read_lineage_file(path)
        self.assertEqual(len(back.claims), 1)
        self.assertEqual(back.claims[0].locator, "lines:1-2")
        self.assertTrue(back.is_deliverable)

    def test_json_keeps_the_bad_citation_visible(self) -> None:
        path = self.tmp / "bad.json"
        path.write_text(
            json.dumps({"claims": [{"text": "t", "source": "s", "locator": "weird:1"}]}),
            encoding="utf-8",
        )
        back = read_lineage_file(path)
        self.assertEqual(len(back.claims), 1)
        self.assertEqual(len(back.unresolvable), 1)

    def test_json_records_checks_not_just_the_verdict(self) -> None:
        payload = json.loads(Lineage(verifications=(_valid(),)).to_json())
        self.assertEqual(payload["artifacts"][0]["checks"][0]["name"], "exists")
        self.assertIs(payload["deliverable"], True)

    def test_json_is_readable_arabic(self) -> None:
        rendered = Lineage(claims=(Claim("نص عربي", "a.md", "lines:1"),)).to_json()
        self.assertIn("نص عربي", rendered)


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
