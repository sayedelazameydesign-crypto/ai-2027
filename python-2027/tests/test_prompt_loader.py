"""Tests for the operating-mind prompt loader (``ai2027_protocol.prompts``).

These complement the dependency-free root suite in ``tests/test_prompt_loader.py``
by exercising the loader through the installed package, checking the paths it
derives from its own location, and covering the branches the root suite leaves
alone.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ai2027_protocol.prompts import (
    DEFAULT_PROMPTS_DIR,
    PROMPT_FILES,
    REPO_ROOT,
    PromptBundle,
    PromptDocument,
    PromptIntegrityError,
    PromptLoadError,
    TemplateError,
    load_prompt_bundle,
    load_prompt_document,
    parse_front_matter,
    render_template,
    validate_document,
)


def test_repo_root_is_the_repository_root() -> None:
    """The loader derives the repository root from its own file location."""
    assert REPO_ROOT == Path(__file__).resolve().parents[2]
    assert (REPO_ROOT / "python-2027" / "src" / "ai2027_protocol" / "prompts.py").is_file()


def test_default_prompts_dir_points_at_the_shipped_prompts() -> None:
    assert DEFAULT_PROMPTS_DIR == REPO_ROOT / "prompts" / "agent"
    for filename in PROMPT_FILES:
        assert (DEFAULT_PROMPTS_DIR / filename).is_file(), filename


def test_bundle_loads_every_operating_mind_document() -> None:
    bundle = load_prompt_bundle()
    assert set(bundle.documents) == set(PROMPT_FILES)
    assert bundle.verify() == []


def test_documents_carry_their_front_matter_and_hash() -> None:
    bundle = load_prompt_bundle()
    for filename in PROMPT_FILES:
        doc = bundle.get(filename)
        assert doc.name == filename
        assert doc.version.count(".") >= 1
        assert doc.role == "operating-mind"
        assert doc.required_sections
        assert doc.sha256 == hashlib.sha256(doc.text.encode("utf-8")).hexdigest()


def test_iteration_follows_the_canonical_order() -> None:
    bundle = load_prompt_bundle()
    assert [doc.name for doc in bundle] == list(PROMPT_FILES)


def test_manifest_is_hash_addressed_and_reproducible() -> None:
    first = load_prompt_bundle().manifest()
    second = load_prompt_bundle().manifest()
    assert first == second
    assert set(first) == set(PROMPT_FILES)
    assert all(len(digest) == 64 for digest in first.values())


def test_bundle_render_substitutes_values() -> None:
    bundle = load_prompt_bundle()
    rendered = bundle.render("SYSTEM.md", {"name": "AI-2027"})
    assert "AI-2027" in rendered


def test_bundle_render_refuses_an_unknown_document() -> None:
    bundle = load_prompt_bundle()
    with pytest.raises(PromptLoadError):
        bundle.render("NOPE.md", {})


def test_parse_front_matter_without_a_block() -> None:
    front_matter, body = parse_front_matter("# Title\n\nbody\n")
    assert front_matter == {}
    assert body.startswith("# Title")


def test_parse_front_matter_scalar_and_list() -> None:
    text = (
        "---\nname: system\nversion: 1.0.0\nrole: operating-mind\n"
        "required:\n  - Identity\n  - Loop\n---\n\n# Identity\n"
    )
    front_matter, body = parse_front_matter(text)
    assert front_matter["name"] == "system"
    assert front_matter["required"] == ("Identity", "Loop")
    assert body.lstrip().startswith("# Identity")


def test_parse_front_matter_rejects_a_malformed_line() -> None:
    with pytest.raises(PromptLoadError):
        parse_front_matter(
            "---\nname: system\nthis line is not valid\nrequired:\n  - A\n---\n\n# A\n"
        )


def test_validate_document_reports_a_missing_section() -> None:
    doc = PromptDocument(
        name="X.md",
        path=DEFAULT_PROMPTS_DIR / "X.md",
        text="# Identity\n",
        version="1.0.0",
        role="operating-mind",
        required_sections=("Identity", "Missing"),
        sha256="0" * 64,
        front_matter={
            "name": "X.md",
            "version": "1.0.0",
            "role": "operating-mind",
            "required": ("Identity", "Missing"),
        },
    )
    problems = validate_document(doc)
    assert any("Missing" in problem for problem in problems)


def test_validate_document_rejects_a_non_numeric_version() -> None:
    doc = PromptDocument(
        name="X.md",
        path=DEFAULT_PROMPTS_DIR / "X.md",
        text="# Identity\n",
        version="not-a-version",
        role="operating-mind",
        required_sections=("Identity",),
        sha256="0" * 64,
        front_matter={},
    )
    assert any("version" in problem for problem in validate_document(doc))


def test_load_prompt_document_missing_file() -> None:
    with pytest.raises(PromptLoadError):
        load_prompt_document("DOES-NOT-EXIST.md", root=DEFAULT_PROMPTS_DIR)


def test_load_prompt_document_empty_file(tmp_path: Path) -> None:
    (tmp_path / "EMPTY.md").write_text("", encoding="utf-8")
    with pytest.raises(PromptIntegrityError):
        load_prompt_document("EMPTY.md", root=tmp_path)


def test_load_prompt_document_missing_front_matter_key(tmp_path: Path) -> None:
    (tmp_path / "BAD.md").write_text(
        "---\nname: bad\nrole: operating-mind\nrequired:\n  - Identity\n---\n\n# Identity\n",
        encoding="utf-8",
    )
    with pytest.raises(PromptIntegrityError):
        load_prompt_document("BAD.md", root=tmp_path)


def test_load_prompt_document_rejects_a_scalar_required(tmp_path: Path) -> None:
    (tmp_path / "BAD.md").write_text(
        "---\nname: bad\nversion: 1.0.0\nrole: operating-mind\n"
        "required: Identity\n---\n\n# Identity\n",
        encoding="utf-8",
    )
    with pytest.raises(PromptIntegrityError):
        load_prompt_document("BAD.md", root=tmp_path)


def test_load_prompt_bundle_subset(tmp_path: Path) -> None:
    source = DEFAULT_PROMPTS_DIR / "SYSTEM.md"
    (tmp_path / "SYSTEM.md").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    bundle = load_prompt_bundle(names=("SYSTEM.md",), root=tmp_path)
    assert isinstance(bundle, PromptBundle)
    assert set(bundle.documents) == {"SYSTEM.md"}


def test_render_template_substitutes_known_values() -> None:
    assert render_template("a {{ x }} b", {"x": "y"}) == "a y b"


def test_render_template_refuses_unknown_values() -> None:
    with pytest.raises(TemplateError):
        render_template("a {{ x }} b", {})


def test_render_template_refuses_a_blank_placeholder() -> None:
    with pytest.raises(TemplateError):
        render_template("a {{}} b", {})


def test_render_template_keeps_placeholder_syntax_in_values_verbatim() -> None:
    assert render_template("{{ x }}", {"x": "{{ y }}"}) == "{{ y }}"


def test_prompt_files_are_the_canonical_seven() -> None:
    assert len(PROMPT_FILES) == 7
    assert "FINAL-REPORT.md" in PROMPT_FILES
