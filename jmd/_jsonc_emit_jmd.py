# SPDX-License-Identifier: Apache-2.0
""":mod:`._jsonc_ast` AST → JMD-text serializer.

Walk the AST and produce a JMD document conforming to the
companion's syntax extensions: mandatory ``---``-framed
frontmatter, depth-prefixed comment markers (``#/`` line, ``#*``
block), and the standard JMD heading-and-bullet patterns for
sections, fields, and arrays.

Generator-strict per the workspace's parser-tolerant /
generator-strict design principle: this serializer always emits
the canonical block-comment form (``#* Comment`` with capital
label), regardless of which of the three valid input forms a
parser accepted.
"""

from __future__ import annotations

import json as _json
from typing import Any

from ._jsonc_ast import (
    BlockComment,
    Element,
    Field,
    JsoncDoc,
    LineComment,
    ObjectArray,
    ScalarArray,
    Section,
)


def to_jmd(doc: JsoncDoc) -> str:
    """Serialize a :class:`JsoncDoc` to JMD-text per spec §4.

    Output begins with a mandatory ``---``-framed frontmatter,
    even when ``doc.frontmatter`` is empty (spec §4.0). Body is
    the root section at depth 1.

    Args:
        doc: The document AST to serialize.

    Returns:
        JMD source text. Trailing newline guaranteed.
    """
    out: list[str] = []
    _emit_frontmatter(out, doc.frontmatter)
    _emit_section(out, doc.root, depth=1)
    text = "".join(out)
    if not text.endswith("\n"):
        text += "\n"
    return text


def _emit_frontmatter(
    out: list[str],
    fm: dict[str, Any],
) -> None:
    """Emit the mandatory ``---``-framed frontmatter block.

    Empty frontmatter still emits both delimiters on consecutive
    lines per spec §4.0 — the visual frame survives even with no
    fields between.
    """
    out.append("---\n")
    for key, value in fm.items():
        out.append(
            f"{_emit_key(key)}: {_emit_scalar_value(value)}\n",
        )
    out.append("---\n")


def _emit_section(
    out: list[str],
    section: Section,
    *,
    depth: int,
) -> None:
    """Emit a section heading + content sequence at ``depth``."""
    label = section.label
    if "\n" in label or "\r" in label:
        # D11: a label spanning multiple lines would corrupt the
        # document structure on round-trip; surface the gap loudly.
        raise ValueError(
            "JMD section labels must not contain newline characters; "
            f"got {label!r}"
        )
    out.append(f"{'#' * depth} {label}\n")
    _emit_children(out, section.children, depth=depth)


def _emit_children(
    out: list[str],
    children: list[Element],
    *,
    depth: int,
) -> None:
    """Emit a content sequence at the given depth.

    Walks the list in order, dispatching on element kind.
    Visual-readability rules: a blank line precedes any
    sub-section / sub-array heading; each ``#*`` block-comment
    is followed by a blank line per spec §4.2.

    Once a sub-section (Section / ObjectArray) has been emitted
    at this depth, any subsequent flat children (Field,
    ScalarArray, comments) are emitted in **scalar-heading form**
    so the parser knows they belong to *this* depth, not to the
    just-emitted sub-section. Without this prefix, JMD's
    heading-depth semantics would attach them to the sub-section.
    Spec §2.5 / §4.
    """
    seen_section = False
    for child in children:
        if isinstance(child, Field):
            if seen_section:
                out.append(
                    f"{'#' * depth} "
                    f"{_emit_key(child.key)}: "
                    f"{_emit_scalar_value(child.value)}\n"
                )
            else:
                _emit_field(out, child)
        elif isinstance(child, ScalarArray):
            if seen_section:
                out.append(f"{'#' * depth} {_emit_key(child.key)}[]:\n")
                for item in child.items:
                    out.append(f"- {_emit_scalar_value(item)}\n")
            else:
                _emit_scalar_array(out, child)
        elif isinstance(child, ObjectArray):
            _ensure_blank_line(out)
            _emit_object_array(out, child, depth=depth + 1)
            seen_section = True
        elif isinstance(child, Section):
            _ensure_blank_line(out)
            _emit_section(out, child, depth=depth + 1)
            seen_section = True
        elif isinstance(child, LineComment):
            # Comment marker carries depth-prefix unconditionally;
            # after a sibling sub-section the parser uses the
            # depth to re-attach to *this* level.
            out.append(f"{'#' * depth}/ {child.text}\n")
        elif isinstance(child, BlockComment):
            _ensure_blank_line(out)
            out.append(f"{'#' * depth}* Comment\n")
            for line in child.body_lines:
                # Per-line rstrip enforces the shared body-lines
                # invariant defensively at emit time, in case
                # in-memory mutation introduced trailing
                # whitespace after the parser's normalization.
                line = line.rstrip()
                if line:
                    out.append(f"> {line}\n")
                else:
                    out.append(">\n")
            out.append("\n")
            out.append("\n")
        else:
            raise TypeError(
                f"unexpected element: {type(child).__name__}",
            )


def _emit_field(out: list[str], f: Field) -> None:
    """Emit a single ``key: value`` field."""
    out.append(
        f"{_emit_key(f.key)}: {_emit_scalar_value(f.value)}\n",
    )


def _emit_scalar_array(out: list[str], a: ScalarArray) -> None:
    """Emit a ``key[]:`` field followed by per-item bullets."""
    out.append(f"{_emit_key(a.key)}[]:\n")
    for item in a.items:
        out.append(f"- {_emit_scalar_value(item)}\n")


def _emit_object_array(
    out: list[str],
    a: ObjectArray,
    *,
    depth: int,
) -> None:
    """Emit a ``## key[]`` heading + per-item bullet objects.

    Each item is a :class:`Section` whose fields fold into a
    bullet's continuation-indent block. Nested objects /
    object-arrays inside an item land on the spec's §6
    open-questions list — they raise NotImplementedError here so
    the gap is visible rather than silently corrupt.
    """
    out.append(f"{'#' * depth} {_emit_key(a.key)}[]\n")
    for item in a.items:
        if not item.children:
            out.append("-\n")
            continue
        first = True
        for sub in item.children:
            if isinstance(sub, Field):
                value = _emit_scalar_value(sub.value)
                line = f"{_emit_key(sub.key)}: {value}\n"
                out.append(
                    ("- " if first else "  ") + line,
                )
                first = False
            elif isinstance(sub, ScalarArray):
                head = f"{_emit_key(sub.key)}[]:\n"
                out.append(("- " if first else "  ") + head)
                first = False
                for it in sub.items:
                    out.append(
                        f"  - {_emit_scalar_value(it)}\n",
                    )
            else:
                raise NotImplementedError(
                    "nested objects / arrays inside an "
                    "ObjectArray item are an open question "
                    "in spec §6; got "
                    f"{type(sub).__name__}",
                )


def _emit_key(key: str) -> str:
    """Emit a key bare or JSON-quoted as JMD requires."""
    if _key_needs_quote(key):
        return _json.dumps(key)
    return key


def _key_needs_quote(key: str) -> bool:
    """Return True if ``key`` cannot be emitted bare.

    Bare-form requires alphanumeric + ``_`` / ``-`` only, must
    not be empty, and must not begin with a digit (collision
    with bullet/numeric forms). Anything else gets the safe
    JSON-quoted form.
    """
    if not key:
        return True
    if key[0].isdigit():
        return True
    return not all(c.isalnum() or c in "_-" for c in key)


def _emit_scalar_value(value: Any) -> str:
    """Emit a scalar in JMD canonical form.

    Strings are emitted bare when unambiguous, JSON-quoted
    otherwise. Numbers, bools, null take their canonical JMD
    forms.
    """
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        if _string_needs_quote(value):
            return _json.dumps(value)
        return value
    raise TypeError(
        f"cannot serialize scalar {type(value).__name__}",
    )


def _string_needs_quote(s: str) -> bool:
    """Return True if ``s`` cannot be emitted bare in value pos.

    Quotes triggered by: empty, reserved word (null/true/false),
    a numeric-looking form, a leading structural-prefix
    character (``#``, ``-``), or any character that breaks
    line/quote semantics (newline, tab, double-quote,
    backslash).
    """
    if s == "":
        return True
    if s in ("null", "true", "false"):
        return True
    try:
        int(s)
        return True
    except ValueError:
        pass
    try:
        float(s)
        return True
    except ValueError:
        pass
    if s[0] in "#-":
        return True
    if any(c in s for c in '\n\t\r"\\'):
        return True
    return False


def _ensure_blank_line(out: list[str]) -> None:
    """Append a newline if the buffer doesn't already end with one.

    Idempotent — repeated calls don't stack blank lines. Used
    before sub-section / sub-array headings and around block
    comments to keep visual-rhythm consistent.
    """
    if not out:
        return
    if out[-1] == "\n":
        return
    if out[-1].endswith("\n\n"):
        return
    out.append("\n")


__all__ = ["to_jmd"]
