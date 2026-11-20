"""Layer 5, part three: deliver the artifact in the form the user can use.

The gap this fills, measured
---------------------------
`infer_artifact_kind` decides which Studio widget renders a file. It is an
extension lookup, and on nine real artifacts it labelled six of them `text`:

    r.docx      -> text    (bytes: zip, and a docx)
    book.xlsx   -> text    (bytes: zip)
    d.parquet   -> text    (bytes: parquet)
    s.db        -> text    (bytes: sqlite)
    pic.png     -> text    (bytes: png)
    x.pdf       -> text    (bytes: pdf)

So a delivered docx was handed to a text viewer, which is how a perfectly valid
document renders as mojibake and the user concludes the system broke. The bytes
were right the whole time; the delivery decision was wrong.

What this module decides
------------------------
Three questions, in this order, because the answer to each constrains the next:

1. **What is it?** From the bytes (`ir.describe_file`), never the name.
2. **Can the user open it in their own tool?** A docx they can edit in Word is
   better than a summary; a summary is better than a binary they cannot open.
3. **Is it proven?** An artifact that fails `verify` is not deliverable in any
   form, so the plan falls back to a report that says why.

The output is a :class:`DeliveryPlan`: an ordered list of forms, most usable
first, plus the reason. It is a *plan*, not an action -- this module never
writes anything. Deciding and doing stay separate so the decision can be tested
without a filesystem, and so a caller can override it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .ir import DocumentIR
from .verify import Verification


class DeliveryForm(StrEnum):
    """How an artifact can reach the user, most faithful first."""

    #: The original file, untouched. Best when the user's own tool can open it.
    NATIVE = "native"
    #: A rendered view for formats with no local viewer (parquet, sqlite).
    TABLE = "table"
    #: Text the user can read and copy (markdown, logs, source).
    TEXT = "text"
    #: An inline preview for things meant to be looked at (svg, html, images).
    PREVIEW = "preview"
    #: A written report when the artifact itself cannot be delivered.
    REPORT = "report"


#: Families the user can open in a tool they already have. Delivering the file
#: wins over any rendering, because it keeps every property the rendering drops.
_NATIVE_FAMILIES: frozenset[str] = frozenset({"zip", "pdf", "ole", "image", "audio", "video"})

#: Formats with no viewer on a normal machine: a table is the usable form.
_TABLE_FORMATS: frozenset[str] = frozenset({"sqlite", "parquet"})

#: Rendered inline rather than opened separately.
_PREVIEW_SUFFIXES: frozenset[str] = frozenset({".svg", ".html", ".htm"})

#: Source and prose: reading it as text is the whole point.
_TEXT_SUFFIXES: frozenset[str] = frozenset(
    {".md", ".txt", ".py", ".json", ".yaml", ".yml", ".csv", ".log", ".toml", ".ini"}
)


@dataclass(frozen=True)
class DeliveryStep:
    """One way to hand the artifact over, and when to prefer it."""

    form: DeliveryForm
    reason: str
    #: What the receiving view should do with it. Mirrors `infer_artifact_kind`
    #: so an existing renderer keeps working, but derived from the bytes.
    widget: str = "text"


@dataclass(frozen=True)
class DeliveryPlan:
    """The ordered delivery choices for one artifact, and why."""

    path: str
    #: Most usable first. Never empty: `REPORT` is always a fallback.
    steps: tuple[DeliveryStep, ...] = field(default_factory=tuple)
    #: What the system could not determine, named rather than smoothed over.
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def primary(self) -> DeliveryStep:
        """The best form available. The plan always has at least one."""
        return self.steps[0] if self.steps else DeliveryStep(DeliveryForm.REPORT, "no plan")

    @property
    def is_deliverable(self) -> bool:
        """Whether any form can be handed over at all."""
        return bool(self.steps) and self.steps[0].form is not DeliveryForm.REPORT

    def render(self) -> str:
        """The plan as the user would hear it: what they get, and why that form."""
        if not self.steps:
            return f"{self.path}: nothing to deliver"
        lines = [f"{self.path}: deliver as {self.primary.form.value} ({self.primary.widget})"]
        lines.append(f"  why: {self.primary.reason}")
        for step in self.steps[1:]:
            lines.append(f"  else: {step.form.value} ({step.widget}) — {step.reason}")
        lines.extend(f"  note: {note}" for note in self.notes)
        return "\n".join(lines)


def _widget_for(described: DocumentIR) -> str:
    """The renderer name for this artifact, decided from bytes then family."""
    suffix = described.path.lower().rsplit(".", 1)[-1] if "." in described.path else ""
    if described.family.value == "image":
        return "image"
    if described.family.value == "audio":
        return "audio"
    if described.family.value == "video":
        return "video"
    if described.family.value == "pdf":
        return "pdf"
    if described.family.value == "database":
        return "table"
    if described.family.value == "columnar":
        return "table"
    if f".{suffix}" in _PREVIEW_SUFFIXES:
        return "svg" if suffix == "svg" else "html"
    if described.kind in {"docx", "xlsx", "pptx", "odt", "ods", "odp", "epub"}:
        return described.kind
    if described.text_index is not None:
        return "markdown" if suffix in {"md", "markdown"} else "text"
    return "unknown"


def plan_delivery(
    described: DocumentIR,
    *,
    verification: Verification | None = None,
) -> DeliveryPlan:
    """Choose the best form for one artifact, given what it is and whether it holds.

    `verification` is optional because a plan can be made before an artifact is
    finished. When it is supplied and fails, the plan degrades to a report --
    an unproven file is not delivered as if it were sound.
    """
    notes: list[str] = []
    widget = _widget_for(described)
    family = described.family.value
    suffix = described.path.lower().rsplit(".", 1)[-1] if "." in described.path else ""

    if verification is not None and not verification.ok:
        failed = ", ".join(check.name for check in verification.failures) or "unknown check"
        return DeliveryPlan(
            path=described.path,
            steps=(
                DeliveryStep(
                    DeliveryForm.REPORT,
                    f"the artifact failed validation ({failed}); delivering it would "
                    f"present a broken file as a finished one",
                ),
            ),
            notes=tuple(notes),
        )

    if described.kind == "missing":
        return DeliveryPlan(
            path=described.path,
            steps=(DeliveryStep(DeliveryForm.REPORT, "there is no file to deliver"),),
            notes=tuple(notes),
        )

    steps: list[DeliveryStep] = []

    if family in _NATIVE_FAMILIES:
        steps.append(
            DeliveryStep(
                DeliveryForm.NATIVE,
                f"{described.kind} is a format the user can open and edit in their own tool",
                widget,
            )
        )
    elif described.kind in _TABLE_FORMATS:
        steps.append(
            DeliveryStep(
                DeliveryForm.TABLE,
                f"{described.kind} has no local viewer; the tables are the usable part",
                "table",
            )
        )
    elif f".{suffix}" in _PREVIEW_SUFFIXES:
        steps.append(
            DeliveryStep(
                DeliveryForm.PREVIEW,
                f"{described.kind} is meant to be looked at inline",
                widget,
            )
        )

    if described.text_index is not None:
        # Both are worth offering when the artifact has structure: the map for
        # navigation, the file for use.
        steps.append(
            DeliveryStep(
                DeliveryForm.TEXT,
                f"{described.kind} is text; deliver it whole at {described.bytes_on_disk:,} bytes"
                if described.bytes_on_disk < 200_000
                else f"{described.kind} is text but {described.bytes_on_disk:,} bytes; "
                "deliver the index and the requested slice instead",
                widget,
            )
        )

    if not steps:
        steps.append(
            DeliveryStep(
                DeliveryForm.REPORT,
                f"no delivery path is known for the {family} family yet",
            )
        )
        notes.append(f"the {family} family has no delivery form; report only")

    if described.notes:
        notes.extend(described.notes)

    return DeliveryPlan(path=described.path, steps=tuple(steps), notes=tuple(notes))
