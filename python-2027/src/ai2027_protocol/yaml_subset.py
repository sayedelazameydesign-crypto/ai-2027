"""A strict, dependency-free parser for the YAML subset used by AI-2027 configs.

The measured problem
--------------------
``config/routing_profiles.yaml`` needs to be parsed, but PyYAML is **not** a
declared dependency of this project. The root ``requirements.txt`` is
deliberately empty and that emptiness is a measured claim guarded by CI AST
gates; adding a full YAML library to parse one configuration file would break
the zero-dependency posture for a benefit the config does not need.

What this module provides
-------------------------
A strict parser for the subset of YAML the shipped configuration actually uses:

* block mappings (``key: value`` and nested ``key:`` blocks);
* block sequences of scalars (``- item``);
* quoted or bare scalars (strings, booleans, ``null``, integers, floats);
* ``#`` comments, inline and whole-line.

The two empty flow literals (``[]`` and ``{}``) are accepted because they are
the unambiguous way to declare an empty list or mapping. Everything else that
full YAML allows — anchors, aliases, tags, flow collections, and multi-line
scalars — is **not** supported, and a document that needs them raises
:class:`YamlSubsetError` rather than being silently misread. A parser that
guesses at a shape it does not understand turns a configuration typo into a
subtle runtime surprise; refusing is the honest behaviour.

Why this is its own module
--------------------------
It is a generic, reusable utility with no dependency on the routing domain, and
keeping it separate keeps :mod:`.routing_profiles` within the small-module
budget the project's own auditor enforces.

Dependency honesty: standard library only, so it loads in the dependency-free
root test suite as well as inside the protocol package.
"""

from __future__ import annotations

import re
from typing import TypeAlias

#: The value tree this parser produces: scalars, lists and mappings. Declared
#: as a recursive alias so callers can be fully typed without ``Any``, which
#: this project's lint forbids in ``src``. The ``type`` statement is not used
#: because this package still supports Python 3.11, where it is a syntax error.
Jsonish: TypeAlias = "dict[str, Jsonish] | list[Jsonish] | str | int | float | bool | None"  # noqa: UP040


class YamlSubsetError(RuntimeError):
    """Raised when a document uses a YAML feature this parser does not support."""


def _strip_comment(line: str) -> str:
    """Remove a trailing ``#`` comment, honouring single and double quotes."""
    quote: str | None = None
    for index, char in enumerate(line):
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
        elif char == "#" and (index == 0 or line[index - 1].isspace()):
            return line[:index].rstrip()
    return line.rstrip()


def _parse_scalar(raw: str) -> Jsonish:
    """Parse one scalar: quoted string, boolean, null, int, float or bare text.

    The two empty flow literals (``[]`` and ``{}``) are accepted because they
    are the conventional, unambiguous way to declare an empty list or mapping,
    and refusing them would force a shape the config has no reason to avoid.
    Any *non-empty* flow collection is refused explicitly: this parser is a
    documented subset, and silently reading ``[a, b]`` as the literal string
    ``"[a, b]"`` would turn a configuration mistake into a subtle runtime
    surprise.
    """
    text = raw.strip()
    if not text:
        return None
    if text in {"[]", "{}"}:
        return [] if text == "[]" else {}
    if text[0] in "[{" or text[-1] in "]}":
        raise YamlSubsetError(
            f"flow collections are not supported by this parser: {text!r} "
            "(use block sequences and block mappings)"
        )
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    lowered = text.lower()
    if lowered in {"true", "yes"}:
        return True
    if lowered in {"false", "no"}:
        return False
    if lowered in {"null", "~", "none"}:
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def parse_simple_yaml(text: str) -> dict[str, Jsonish]:
    """Parse the strict YAML subset used by ``config/routing_profiles.yaml``.

    Supported: block mappings, block sequences of scalars, ``key: value``
    scalars, and ``#`` comments (inline and whole-line). Not supported, by
    design: anchors, aliases, tags, flow collections (``{}``/``[]``), and
    multi-line scalars. A document using them raises
    :class:`YamlSubsetError` rather than being misread.
    """
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        content = _strip_comment(raw)
        if not content.strip():
            continue
        indent = len(content) - len(content.lstrip(" "))
        if "\t" in content[: indent + 1]:
            raise YamlSubsetError("tabs are not allowed for indentation")
        lines.append((indent, content.strip()))

    if not lines:
        return {}
    parsed, index = _parse_node(lines, 0, lines[0][0])
    if index != len(lines):
        raise YamlSubsetError(
            f"unexpected content at indentation {lines[index][0]}: {lines[index][1]!r}"
        )
    if not isinstance(parsed, dict):
        raise YamlSubsetError("the configuration root must be a mapping")
    return parsed


def _parse_node(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[Jsonish, int]:
    """Parse one mapping or sequence block starting at ``lines[index]``."""
    if lines[index][1].startswith("- "):
        return _parse_sequence(lines, index, indent)
    return _parse_mapping(lines, index, indent)


def _parse_mapping(
    lines: list[tuple[int, str]], index: int, indent: int
) -> tuple[dict[str, Jsonish], int]:
    """Parse consecutive ``key: value`` lines at one indentation level."""
    result: dict[str, Jsonish] = {}
    while index < len(lines):
        line_indent, content = lines[index]
        if line_indent < indent:
            break
        if line_indent > indent:
            raise YamlSubsetError(f"unexpected indentation for key at line: {content!r}")
        if content.startswith("- "):
            break
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)[ \t]*:[ \t]*(.*)$", content)
        if match is None:
            raise YamlSubsetError(f"expected 'key: value' but found: {content!r}")
        key, rest = match.group(1), match.group(2).strip()
        index += 1
        if rest:
            result[key] = _parse_scalar(rest)
            continue
        if index < len(lines) and lines[index][0] > indent:
            child, index = _parse_node(lines, index, lines[index][0])
            result[key] = child
        else:
            result[key] = {}
    return result, index


def _parse_sequence(
    lines: list[tuple[int, str]], index: int, indent: int
) -> tuple[list[Jsonish], int]:
    """Parse consecutive ``- item`` lines at one indentation level."""
    result: list[Jsonish] = []
    while index < len(lines):
        line_indent, content = lines[index]
        if line_indent < indent or not content.startswith("- "):
            break
        if line_indent > indent:
            raise YamlSubsetError(f"unexpected indentation in sequence: {content!r}")
        item = content[2:].strip()
        index += 1
        if item:
            result.append(_parse_scalar(item))
        elif index < len(lines) and lines[index][0] > indent:
            child, index = _parse_node(lines, index, lines[index][0])
            result.append(child)
        else:
            raise YamlSubsetError(f"empty sequence item at: {content!r}")
    return result, index
