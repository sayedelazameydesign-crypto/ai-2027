"""Layer 5, part two: where every fact came from.

Validation answers "is the output sound?". Lineage answers "why should I believe
this sentence?" — and they are one layer because a verdict without provenance is
an opinion, and a provenance trail for an unsound artifact is a well-documented
mistake.

The shape
---------
A :class:`Claim` is one assertion the system makes, plus the address inside the
artifact that supports it. It is deliberately not free text: a claim that cannot
name a `source` is not recorded, because a citation that points nowhere is worse
than no citation at all.

    Claim(text="المبيعات 12% أعلى", source="sales.db", locator="orders#total")

`locator` uses the address vocabulary the IR already established
(`section`, `line`, `table#column`, `member`), so a claim can be traced back
through `ir.py` to a slice of the original file.

Why this is a separate module and not a wrapper around the ledger
----------------------------------------------------------------
The kernel ledger records *tool calls*: what ran, in what order, with what hash.
It is append-only and hash-chained, which is exactly what an audit needs and
exactly what a reader does not want to consume — a ledger entry per call is
thousands of lines. This module records *the claims that survive verification*,
in an order a reader can follow, and each one points at the ledger entry that
produced it. Two audiences, two shapes: the ledger proves the process, the
lineage explains the result.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .verify import Verification

#: Address vocabularies a claim may use. Anything else is a broken citation.
#: These are exactly the forms `ir.py` and `index.py` emit -- ``lines 9-10`` in a
#: rendered index, ``table:orders#total`` from an IR table, ``member:...`` from a
#: container directory -- so a locator that resolves here can be followed by the
#: tools that produced it. Adding a word here without a reader that understands
#: it would turn "resolvable" into "spelled plausibly".
LOCATORS = frozenset(
    {
        "section",
        "sections",
        "line",
        "lines",
        "table",
        "tables",
        "column",
        "columns",
        "member",
        "members",
        "query",
        "cell",
    }
)


@dataclass(frozen=True)
class Claim:
    """One assertion, and the address inside an artifact that supports it."""

    text: str
    source: str
    #: ``order`` shape: ``table#column``, ``lines:12-18``, ``member:word/document.xml``.
    locator: str = ""
    #: The tool that produced the supporting observation.
    tool: str = ""

    @property
    def is_cited(self) -> bool:
        """A claim with a source and a locator can be checked by someone else."""
        return bool(self.source.strip()) and bool(self.locator.strip())

    @property
    def locator_kind(self) -> str:
        """The leading word of the locator, which must be one we can resolve."""
        head = self.locator.split(":", 1)[0].split("#", 1)[0].strip()
        return head

    @property
    def resolves(self) -> bool:
        """Whether the locator uses an address vocabulary the IR can follow."""
        return self.locator_kind in LOCATORS


@dataclass(frozen=True)
class Lineage:
    """The ordered trail behind one delivered answer."""

    claims: tuple[Claim, ...] = field(default_factory=tuple)
    verifications: tuple[Verification, ...] = field(default_factory=tuple)

    @property
    def uncited(self) -> tuple[Claim, ...]:
        """Claims a reader could not check — the ones that should not ship."""
        return tuple(claim for claim in self.claims if not claim.is_cited)

    @property
    def unresolvable(self) -> tuple[Claim, ...]:
        """Cited claims whose address vocabulary the system cannot follow."""
        return tuple(claim for claim in self.claims if claim.is_cited and not claim.resolves)

    @property
    def invalid_artifacts(self) -> tuple[Verification, ...]:
        """Artifacts whose checks did not all pass."""
        return tuple(item for item in self.verifications if not item.ok)

    @property
    def is_deliverable(self) -> bool:
        """The gate: every claim cited, every citation resolvable, every file valid.

        Note what this does *not* require: a perfect score, or an artifact for
        every claim. A report with no artifacts and no claims is deliverable —
        empty is not the same as wrong — but a report with one uncited claim is
        not, because that one sentence is the one that will be quoted.
        """
        return not self.uncited and not self.unresolvable and not self.invalid_artifacts

    def render(self) -> str:
        """The audit trail as a reader can follow it, not as it was logged."""
        lines: list[str] = []
        if self.verifications:
            lines.append("artifacts:")
            for item in self.verifications:
                mark = "ok" if item.ok else "INVALID"
                lines.append(f"  [{mark}] {item.path} ({item.kind}) {item.sha256[:12]}")
        if self.claims:
            lines.append("claims:")
            for claim in self.claims:
                where = f"{claim.source}" + (f" {claim.locator}" if claim.locator else "")
                mark = "ok" if claim.is_cited and claim.resolves else "UNCITED"
                lines.append(f"  [{mark}] {claim.text}")
                lines.append(f"         <- {where}" + (f" via {claim.tool}" if claim.tool else ""))
        if not lines:
            lines.append("nothing to trace: no artifact was produced and no claim was made")
        lines.append("verdict: " + ("deliverable" if self.is_deliverable else "NOT deliverable"))
        return "\n".join(lines)

    def to_json(self) -> str:
        """Machine-readable form, so a UI can link every sentence to its source."""
        payload = {
            "deliverable": self.is_deliverable,
            "claims": [
                {
                    "text": claim.text,
                    "source": claim.source,
                    "locator": claim.locator,
                    "tool": claim.tool,
                    "cited": claim.is_cited,
                    "resolves": claim.resolves,
                }
                for claim in self.claims
            ],
            "artifacts": [
                {
                    "path": item.path,
                    "kind": item.kind,
                    "ok": item.ok,
                    "sha256": item.sha256,
                    "notes": list(item.notes),
                    "checks": [
                        {"name": check.name, "passed": check.passed, "detail": check.detail}
                        for check in item.checks
                    ],
                }
                for item in self.verifications
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)


def read_lineage_file(path: Path) -> Lineage:
    """Load an audit trail written by :meth:`Lineage.to_json`.

    Kept intentionally strict: an unknown locator is preserved rather than
    dropped, so a reviewer sees the bad citation instead of a shorter list.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    claims = tuple(
        Claim(
            text=str(item.get("text", "")),
            source=str(item.get("source", "")),
            locator=str(item.get("locator", "")),
            tool=str(item.get("tool", "")),
        )
        for item in payload.get("claims", [])
        if isinstance(item, dict)
    )
    return Lineage(claims=claims)
