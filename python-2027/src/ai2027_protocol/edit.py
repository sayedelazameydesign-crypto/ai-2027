"""Editing without corrupting: the original is never opened for writing.

The gap this fills, measured
----------------------------
`coverage()` reported `patch: 1` because the text adapter *declared* the
operation. Nothing in the package implemented it. A capability that is claimed
and not delivered is worse than an absent one: a caller who asks the mesh
"who can patch text?" gets an answer, and the answer is a promise no function
keeps.

The contract, in the order it fails
-----------------------------------
1. **The original is opened read-only.** Not "opened and not written" -- opened
   with mode `"rb"` and never with a write mode, so a bug lower down cannot
   reach it. `sha256` is taken before and after every operation and compared.
2. **The edit lands in a sibling file**, `<name>.new`, written to a temporary
   file in the same directory and then renamed. A crash mid-write leaves the
   original untouched and no half-written `.new`.
3. **A failed edit leaves nothing behind.** Any exception removes the temporary
   file, so "it did not work" is visible as a missing output rather than as a
   corrupt one.
4. **The difference is reported**, not just applied: lines added and removed, so
   a caller can refuse an edit that changed more than it meant to.

Refusals
--------
* Editing a file that is not text is refused by name, not attempted.
* A replacement whose search text is not found fails; it never "best matches".
* A replacement that would make the file identical fails, so a no-op cannot be
  reported as a successful edit.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

#: Text files larger than this are refused rather than loaded. A patch reads the
#: whole file into memory twice (before and after), so the cap is the memory
#: budget, not a performance guess.
MAX_EDIT_BYTES = 16 * 1024 * 1024

#: The suffix a successful edit writes. Deliberately a sibling, so the original
#: path in the caller's records still means what it meant before.
NEW_SUFFIX = ".new"


class EditError(Exception):
    """An edit that was refused. The message says which rule stopped it."""


@dataclass(frozen=True)
class EditResult:
    """What an edit did, and the proof that it did only that."""

    source: str
    target: str
    #: SHA-256 of the original, before and after. Equal means untouched.
    source_sha_before: str
    source_sha_after: str
    #: Lines added and removed relative to the original.
    added: int
    removed: int
    #: The fraction of the original's lines that the edit touched.
    #:
    #: Measured while building this: the first version computed
    #: |len(new) - len(old)| / len(old), so replacing "beta" with "BETA" reported
    #: "0.0% changed" next to "+1/-1 lines". Both numbers were true and they
    #: contradicted each other, because one measured size and the other measured
    #: churn. A reviewer cannot act on two metrics that disagree, so this is the
    #: churn figure only.
    changed_ratio: float

    @property
    def source_untouched(self) -> bool:
        """Whether the original is bit-for-bit what it was."""
        return self.source_sha_before == self.source_sha_after

    @property
    def summary(self) -> str:
        """One line a caller can log or show."""
        state = "intact" if self.source_untouched else "MODIFIED"
        return (
            f"{self.source} -> {self.target}: "
            f"+{self.added}/-{self.removed} lines, "
            f"{self.changed_ratio:.1%} changed, original {state}"
        )


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_text(path: Path) -> str:
    """Read a text file, refusing anything that would not survive a rewrite."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise EditError(f"cannot stat {path.name}: {exc}") from exc
    if size > MAX_EDIT_BYTES:
        raise EditError(f"{path.name} is {size:,} bytes; editing is capped at {MAX_EDIT_BYTES:,}")
    raw = path.read_bytes()
    if b"\x00" in raw[:8192]:
        raise EditError(f"{path.name} contains NUL bytes; it is not text")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EditError(f"{path.name} is not valid UTF-8: {exc.reason}") from exc


def _churn_ratio(before: str, after: str, added: int, removed: int) -> float:
    """What fraction of the original's lines the edit touched."""
    total = len(before.splitlines())
    if total == 0:
        return 1.0 if after.strip() else 0.0
    return min(1.0, (added + removed) / total)


def _line_delta(before: str, after: str) -> tuple[int, int]:
    """Count added and removed lines without a diff library.

    A line-level count is what a reviewer asks for ("how much changed"), and it
    is computable from two multisets without aligning them. Alignment would give
    a nicer hunk listing, but it can disagree with the character ratio below,
    and two numbers that disagree are worse than one that is approximate.
    """
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    before_set: dict[str, int] = {}
    for line in before_lines:
        before_set[line] = before_set.get(line, 0) + 1
    added = 0
    for line in after_lines:
        remaining = before_set.get(line, 0)
        if remaining:
            before_set[line] = remaining - 1
        else:
            added += 1
    removed = sum(before_set.values())
    return added, removed


def _write_atomic(target: Path, text: str) -> None:
    """Write to a sibling temp file, then rename. A crash leaves no partial file."""
    temporary = target.with_name(target.name + ".partial")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def replace_text(path: str | Path, old: str, new: str, *, count: int = 1) -> EditResult:
    """Replace ``old`` with ``new`` in a copy, leaving the original untouched.

    ``count`` is how many occurrences to replace, matching `str.replace`. The
    default of one is deliberate: an edit that silently rewrites every occurrence
    of a common phrase is the classic way a "small fix" becomes a rewording.
    """
    source = Path(path)
    if not source.is_file():
        raise EditError(f"{source} is not a file")
    if not old:
        raise EditError("the search text is empty; refusing to guess a position")

    before_sha = _sha256_of(source)
    text = _read_text(source)

    occurrences = text.count(old)
    if occurrences == 0:
        raise EditError(f"{old!r} does not appear in {source.name}")
    if count <= 0:
        raise EditError("count must be at least 1")

    edited = text.replace(old, new, count)
    if edited == text:
        raise EditError(f"replacing {old!r} with {new!r} would change nothing")

    target = source.with_name(source.name + NEW_SUFFIX)
    added, removed = _line_delta(text, edited)
    _write_atomic(target, edited)

    after_sha = _sha256_of(source)
    result = EditResult(
        source=str(source),
        target=str(target),
        source_sha_before=before_sha,
        source_sha_after=after_sha,
        added=added,
        removed=removed,
        changed_ratio=_churn_ratio(text, edited, added, removed),
    )
    if not result.source_untouched:  # pragma: no cover - the whole point of the module
        raise EditError(
            f"the original changed during the edit ({before_sha[:12]} -> {after_sha[:12]}); "
            "this is a bug in the editor, not in the file"
        )
    return result


def append_line(path: str | Path, line: str) -> EditResult:
    """Append one line to a copy of a text file, with the same guarantees."""
    source = Path(path)
    if not source.is_file():
        raise EditError(f"{source} is not a file")
    before_sha = _sha256_of(source)
    text = _read_text(source)
    if text and not text.endswith("\n"):
        text += "\n"
    edited = text + line.rstrip("\n") + "\n"

    target = source.with_name(source.name + NEW_SUFFIX)
    _write_atomic(target, edited)
    added, removed = _line_delta(text, edited)
    after_sha = _sha256_of(source)
    result = EditResult(
        source=str(source),
        target=str(target),
        source_sha_before=before_sha,
        source_sha_after=after_sha,
        added=added,
        removed=removed,
        changed_ratio=_churn_ratio(text, edited, added, removed),
    )
    if not result.source_untouched:  # pragma: no cover - defensive
        raise EditError("the original changed during the edit")
    return result
