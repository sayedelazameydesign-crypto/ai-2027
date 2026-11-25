"""Loading and integrity checks for the agent's operating-mind prompts.

The measured problem
--------------------
The operating mind of AI-2027 is a set of markdown instruction files under
``prompts/agent/``. A prompt file that is missing, empty, mis-versioned or
missing a required section still *looks* loadable: nothing fails at import
time, and the failure only appears at run time as a degraded or inconsistent
agent. Instruction files are also easy to drift from the behaviour they claim
to describe, because nothing checks them.

What this module does
---------------------
It turns those files into a validated, hash-addressed bundle:

* every shipped prompt file is parsed for its front matter
  (``name``, ``version``, ``role``, ``required``);
* the ``required`` list is checked against the headings actually present in
  the body, so a section that was deleted while the front matter still
  advertised it is a hard error;
* the bundle is hash-addressed, so a prompt change is auditable by hash;
* templates support ``{{ key }}`` substitution and **refuse** unknown or
  leftover placeholders, so a template that was edited without updating its
  caller fails loudly instead of shipping a literal ``{{ typo }}`` to a model.

Dependency honesty
------------------
This module imports the standard library only. It must not import
``contracts``/``router``/anything that pulls in third-party packages, because
the prompt files are also loaded and checked by the dependency-free root test
suite, which runs with ``python -m unittest`` and no installed packages.

Design note: the loader is deliberately a reader of files, not a registry of
prompt *content*. Nothing here interprets the instructions; it only proves they
are present, well-formed and internally consistent.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

#: The operating mind, in the order a run walks it.
PROMPT_FILES: tuple[str, ...] = (
    "SYSTEM.md",
    "UNDERSTANDING.md",
    "REASONING.md",
    "PLANNING.md",
    "REFLECTION.md",
    "VERIFICATION.md",
    "FINAL-REPORT.md",
)

#: Repository root, derived from this file's location:
#: ``python-2027/src/ai2027_protocol/prompts.py`` -> parents[3] is the root.
REPO_ROOT: Path = Path(__file__).resolve().parents[3]

#: Default location of the operating-mind prompt files.
DEFAULT_PROMPTS_DIR: Path = REPO_ROOT / "prompts" / "agent"

#: Front matter must declare these keys, in this shape.
REQUIRED_FRONT_MATTER_KEYS: tuple[str, ...] = ("name", "version", "role", "required")

#: Tokens that mean "this file is not finished". Their presence is a build
#: failure, not a warning, because an unfinished instruction file is exactly
#: the kind of drift that survives review.
FORBIDDEN_TOKENS: tuple[str, ...] = ("TODO", "TBD", "FIXME", "XXX", "Lorem ipsum")

_FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)\s*#*\s*$", re.MULTILINE)
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_BLANK_PLACEHOLDER_RE = re.compile(r"\{\{\s*\}\}")
_KEY_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)[ \t]*:[ \t]*(.*)$")
_LIST_ITEM_RE = re.compile(r"^[ \t]*-[ \t]+(.+?)[ \t]*$")


class PromptLoadError(RuntimeError):
    """Raised when a prompt file cannot be read or does not parse."""


class PromptIntegrityError(PromptLoadError):
    """Raised when a prompt file is present but internally inconsistent."""


class TemplateError(PromptLoadError):
    """Raised when a template is rendered with bad or missing values."""


@dataclass(frozen=True)
class PromptDocument:
    """One validated instruction file: its content, its metadata, its hash."""

    name: str
    path: Path
    text: str
    version: str
    role: str
    required_sections: tuple[str, ...]
    sha256: str
    front_matter: Mapping[str, str | tuple[str, ...]] = field(default_factory=dict)

    @property
    def headings(self) -> tuple[str, ...]:
        """Return the normalised headings present in the document body."""
        return tuple(_heading_text(m.group(2)) for m in _HEADING_RE.finditer(self.text))


@dataclass(frozen=True)
class PromptBundle:
    """The validated operating mind: every prompt document, keyed by name."""

    documents: Mapping[str, PromptDocument]
    root: Path

    def __iter__(self) -> Iterator[PromptDocument]:
        """Iterate documents in the canonical execution order."""
        return iter(self._ordered())

    def _ordered(self) -> tuple[PromptDocument, ...]:
        """Return documents in :data:`PROMPT_FILES` order, then any extras."""
        ordered = [self.documents[name] for name in PROMPT_FILES if name in self.documents]
        extras = [doc for doc in self.documents.values() if doc.name not in PROMPT_FILES]
        return tuple(ordered + sorted(extras, key=lambda doc: doc.name))

    def get(self, name: str) -> PromptDocument:
        """Return the document called ``name`` or raise :class:`PromptLoadError`."""
        try:
            return self.documents[name]
        except KeyError as exc:
            available = ", ".join(sorted(self.documents))
            raise PromptLoadError(f"no prompt named {name!r} (available: {available})") from exc

    def manifest(self) -> dict[str, str]:
        """Return ``{filename: sha256}`` for every document, for audit and PRs."""
        return {doc.name: doc.sha256 for doc in self._ordered()}

    def verify(self) -> list[str]:
        """Re-check every document; return a list of human-readable problems."""
        problems: list[str] = []
        for doc in self._ordered():
            problems.extend(validate_document(doc))
        return problems

    def render(self, name: str, values: Mapping[str, str]) -> str:
        """Render prompt ``name`` with ``values``, refusing unknown placeholders."""
        return render_template(self.get(name).text, values)


def _heading_text(raw: str) -> str:
    """Normalise a heading for comparison: lower case, collapsed whitespace."""
    return " ".join(raw.strip().lower().split())


def parse_front_matter(text: str) -> tuple[dict[str, str | tuple[str, ...]], str]:
    """Split a document into ``(front_matter, body)``.

    Front matter is a leading ``---`` block of ``key: value`` lines. A key with
    no value starts a list of ``- item`` lines. A document with no front matter
    block returns an empty mapping; the caller decides whether that is an
    error.
    """
    match = _FRONT_MATTER_RE.match(text)
    if match is None:
        return {}, text
    block, body = match.group(1), text[match.end() :]
    parsed: dict[str, str | tuple[str, ...]] = {}
    current_key: str | None = None
    items: list[str] = []

    for line in block.splitlines():
        key_match = _KEY_LINE_RE.match(line)
        item_match = _LIST_ITEM_RE.match(line)
        if key_match is not None:
            if current_key is not None:
                parsed[current_key] = tuple(items) if items else ""
            current_key = key_match.group(1)
            rest = key_match.group(2).strip()
            if rest:
                parsed[current_key] = rest
                current_key = None
            else:
                items = []
            continue
        if item_match is not None:
            if current_key is None:
                raise PromptLoadError(
                    f"list item outside a list-valued key: {item_match.group(1).strip()!r}"
                )
            items.append(item_match.group(1).strip())
            continue
        if not line.strip():
            continue
        raise PromptLoadError(f"malformed front matter line {line.strip()!r}")
    if current_key is not None:
        parsed[current_key] = tuple(items) if items else ""
    return parsed, body


def _check_forbidden_tokens(text: str, path: Path) -> list[str]:
    """Return every forbidden token found in ``text`` as a readable problem."""
    problems: list[str] = []
    for token in FORBIDDEN_TOKENS:
        if token in text:
            problems.append(f"{path.name}: forbidden placeholder token {token!r}")
    return problems


def validate_document(doc: PromptDocument) -> list[str]:
    """Return every integrity problem with ``doc``; an empty list means valid."""
    problems: list[str] = []
    for key in REQUIRED_FRONT_MATTER_KEYS:
        if key not in doc.front_matter:
            problems.append(f"{doc.name}: front matter is missing {key!r}")
    if doc.version and not doc.version.replace(".", "").isdigit():
        problems.append(f"{doc.name}: version {doc.version!r} is not numeric")
    required = doc.front_matter.get("required")
    if isinstance(required, tuple):
        headings = set(doc.headings)
        for section in required:
            if _heading_text(section) not in headings:
                problems.append(
                    f"{doc.name}: required section {section!r} is not present as a heading"
                )
    problems.extend(_check_forbidden_tokens(doc.text, doc.path))
    return problems


def load_prompt_document(filename: str, *, root: Path | None = None) -> PromptDocument:
    """Load and validate one prompt file from ``root`` (default: repo prompts)."""
    base = Path(root) if root is not None else DEFAULT_PROMPTS_DIR
    path = base / filename
    if not path.is_file():
        raise PromptLoadError(f"prompt file not found: {path}")
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise PromptIntegrityError(f"prompt file is empty: {path}")
    front_matter, _body = parse_front_matter(text)
    for key in REQUIRED_FRONT_MATTER_KEYS:
        if key not in front_matter:
            raise PromptIntegrityError(f"{filename}: front matter is missing {key!r}")
    required = front_matter.get("required")
    if not isinstance(required, tuple) or not required:
        raise PromptIntegrityError(f"{filename}: 'required' must be a non-empty list")
    document = PromptDocument(
        name=filename,
        path=path,
        text=text,
        version=str(front_matter.get("version", "")),
        role=str(front_matter.get("role", "")),
        required_sections=tuple(str(item) for item in required),
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        front_matter=front_matter,
    )
    problems = validate_document(document)
    if problems:
        raise PromptIntegrityError("; ".join(problems))
    return document


def load_prompt_bundle(
    *,
    names: tuple[str, ...] | None = None,
    root: Path | None = None,
) -> PromptBundle:
    """Load every requested prompt (default: the full operating mind)."""
    selected = PROMPT_FILES if names is None else names
    documents: dict[str, PromptDocument] = {}
    for filename in selected:
        document = load_prompt_document(filename, root=root)
        documents[document.name] = document
    return PromptBundle(documents=documents, root=Path(root) if root else DEFAULT_PROMPTS_DIR)


def render_template(template: str, values: Mapping[str, str]) -> str:
    """Substitute ``{{ key }}`` placeholders, refusing unknown or blank ones.

    Substitution is deliberately non-recursive and value-blind: a value is
    inserted verbatim. This keeps the loader a pure text operation and means a
    value containing ``{{ other }}`` is **not** re-expanded, which would let a
    caller-supplied value inject extra placeholders.

    The unknown-key check is performed against the *template's* placeholders,
    not against the rendered text. Scanning the rendered text would be both
    redundant (the same regex matched every placeholder during substitution) and
    wrong: it would reject a value that legitimately contains placeholder
    syntax, which the verbatim rule above deliberately permits.
    """
    if _BLANK_PLACEHOLDER_RE.search(template):
        raise TemplateError("template contains an empty '{{}}' placeholder")

    placeholder_keys = set(_PLACEHOLDER_RE.findall(template))
    unknown = placeholder_keys - set(values)
    if unknown:
        missing = ", ".join(sorted(unknown))
        raise TemplateError(f"template is missing values for: {missing}")

    def _substitute(match: re.Match[str]) -> str:
        return values[match.group(1)]

    return _PLACEHOLDER_RE.sub(_substitute, template)
