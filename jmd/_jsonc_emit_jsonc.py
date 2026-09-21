# SPDX-License-Identifier: Apache-2.0
""":mod:`._jsonc_ast` AST → JSONC source serializer.

Walk the AST and emit JSONC. Comments come back to their original
``//`` and ``/* */`` forms; trailing commas are not emitted (we
drop them on parse and don't re-emit them per spec §2.7).
Indentation is canonical 4-space.

Frontmatter is dropped here — JSONC has no slot for it; the JMD
intermediate is the only place frontmatter survives.
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


def serialize_jsonc(doc: JsoncDoc) -> str:
    """Serialize a :class:`JsoncDoc` to JSONC source text.

    Output is the root section's content as a top-level
    ``{ … }`` object with 4-space indentation and comments at
    original sequence positions. Frontmatter is dropped (no JSONC
    slot for it).

    Args:
        doc: The document AST to serialize.

    Returns:
        JSONC source text. Trailing newline guaranteed.
    """
    out: list[str] = []
    _emit_jsonc_object(out, doc.root.children, indent=0)
    if not out or not out[-1].endswith("\n"):
        out.append("\n")
    return "".join(out)


def _emit_jsonc_object(
    out: list[str],
    children: list[Element],
    *,
    indent: int,
) -> None:
    """Emit ``{ … }`` for a Section's children at indent + 1."""
    out.append("{\n")
    data_indices = [
        k
        for k, c in enumerate(children)
        if not isinstance(c, (LineComment, BlockComment))
    ]
    last_data_idx = data_indices[-1] if data_indices else -1
    for k, child in enumerate(children):
        is_data = not isinstance(
            child,
            (LineComment, BlockComment),
        )
        comma = is_data and (k != last_data_idx)
        _emit_jsonc_child(
            out,
            child,
            indent=indent + 1,
            comma=comma,
        )
    out.append("    " * indent + "}")


def _emit_jsonc_child(
    out: list[str],
    child: Element,
    *,
    indent: int,
    comma: bool,
) -> None:
    """Emit one element with appropriate indent + trailing comma."""
    pad = "    " * indent
    tail = "," if comma else ""
    if isinstance(child, Field):
        key = _jsonc_string(child.key)
        out.append(f"{pad}{key}: {_jsonc_value(child.value)}{tail}\n")
    elif isinstance(child, ScalarArray):
        items_text = ", ".join(_jsonc_value(v) for v in child.items)
        out.append(
            f"{pad}{_jsonc_string(child.key)}: [{items_text}]{tail}\n",
        )
    elif isinstance(child, ObjectArray):
        out.append(f"{pad}{_jsonc_string(child.key)}: [\n")
        n = len(child.items)
        for j, item in enumerate(child.items):
            _emit_jsonc_object_item(
                out,
                item,
                indent=indent + 1,
                comma=(j != n - 1),
            )
        out.append(f"{pad}]{tail}\n")
    elif isinstance(child, Section):
        out.append(f"{pad}{_jsonc_string(child.label)}: ")
        _emit_jsonc_object(
            out,
            child.children,
            indent=indent,
        )
        out.append(f"{tail}\n")
    elif isinstance(child, LineComment):
        out.append(f"{pad}// {child.text}\n")
    elif isinstance(child, BlockComment):
        # Inline ``*/`` at the end of the last content line so the
        # body the parser sees matches what the serializer wrote
        # — no pad-whitespace between the last content line and
        # ``*/`` to round-trip into a spurious trailing empty
        # body line. Single-line bodies fold into ``/* X */``.
        # Per-line rstrip is defensive: the parsers normalise the
        # AST invariant on input, but in-memory mutation could
        # still introduce trailing whitespace; we strip on emit
        # so the JSONC source stays clean either way.
        lines = [ln.rstrip() for ln in child.body_lines]
        if not lines:
            out.append(f"{pad}/* */\n")
        elif len(lines) == 1:
            out.append(f"{pad}/* {lines[0]} */\n")
        else:
            out.append(f"{pad}/* {lines[0]}\n")
            for line in lines[1:-1]:
                out.append(f"{pad}   {line}\n")
            out.append(f"{pad}   {lines[-1]} */\n")
    else:
        raise TypeError(
            f"unexpected element: {type(child).__name__}",
        )


def _emit_jsonc_object_item(
    out: list[str],
    item: Section,
    *,
    indent: int,
    comma: bool,
) -> None:
    """Emit one ObjectArray item as an inline ``{ … }``."""
    pad = "    " * indent
    out.append(pad)
    _emit_jsonc_object(out, item.children, indent=indent)
    out.append(f"{',' if comma else ''}\n")


def _jsonc_string(s: str) -> str:
    """Emit a JSONC-quoted string (always quoted, JSON form)."""
    return _json.dumps(s)


def _jsonc_value(value: Any) -> str:
    """Emit a JSONC scalar or compound value."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float, str)):
        return _json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(_jsonc_value(v) for v in value) + "]"
    raise TypeError(
        f"cannot serialize value {type(value).__name__}",
    )


__all__ = ["serialize_jsonc"]
