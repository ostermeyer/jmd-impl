# SPDX-License-Identifier: Apache-2.0
"""JMD-text → :mod:`._jsonc_ast` AST parser.

Parses JMD documents conforming to the companion (mandatory
``---`` frontmatter, depth-prefixed comment markers, the canonical
heading / field / array forms our :func:`._jsonc_emit_jmd.to_jmd`
emits) back into the AST. Deliberately handles only the subset our
serializer produces — not the full breadth of core JMD. That keeps
the parser small and the round-trip testable in isolation; if a
document drifts outside the supported subset, an early
:class:`JsoncJmdParseError` surfaces the gap.
"""

from __future__ import annotations

import json as _json
from dataclasses import dataclass
from typing import Any, cast

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


class JsoncJmdParseError(ValueError):
    """Raised for malformed JMD-companion-text input.

    Carries a ``line`` attribute (1-based) so callers can surface
    a position; the parser uses 0-based indices internally and
    converts on raise.
    """

    def __init__(self, message: str, line: int) -> None:
        super().__init__(f"{message} (at line {line})")
        self.line = line


@dataclass
class _LineCursor:
    """Tiny line-by-line cursor with peek/advance helpers."""

    lines: list[str]
    i: int = 0

    def peek(self) -> str | None:
        if self.i >= len(self.lines):
            return None
        return self.lines[self.i]

    def advance(self) -> str | None:
        if self.i >= len(self.lines):
            return None
        line = self.lines[self.i]
        self.i += 1
        return line


def from_jmd(text: str) -> JsoncDoc:
    """Parse JMD-text (companion form) back into a :class:`JsoncDoc`.

    Expected input shape: mandatory ``---``-framed frontmatter
    (possibly empty) followed by a single root section ``# Label``.
    Body uses the depth-prefixed heading / field / array / comment
    forms our :func:`._jsonc_emit_jmd.to_jmd` emits. Anything
    outside that subset raises :class:`JsoncJmdParseError` with a
    1-based line number.
    """
    lines = text.splitlines()
    cur = _LineCursor(lines=lines)
    _skip_blank(cur)
    frontmatter = _parse_jmd_frontmatter(cur)
    _skip_blank(cur)
    root = _parse_jmd_root_section(cur)
    return JsoncDoc(root=root, frontmatter=frontmatter)


def _skip_blank(cur: _LineCursor) -> None:
    """Advance past consecutive blank lines."""
    while True:
        line = cur.peek()
        if line is None or line.strip() != "":
            return
        cur.advance()


def _parse_jmd_frontmatter(
    cur: _LineCursor,
) -> dict[str, Any]:
    """Consume ``---`` open + body + ``---`` close.

    Empty frontmatter still requires both delimiters per spec
    §4.0; the body between them may be empty.
    """
    line = cur.advance()
    if line is None or line.rstrip() != "---":
        raise JsoncJmdParseError(
            "expected '---' frontmatter open",
            cur.i,
        )
    fm: dict[str, Any] = {}
    while True:
        line = cur.advance()
        if line is None:
            raise JsoncJmdParseError(
                "unterminated frontmatter",
                cur.i,
            )
        if line.rstrip() == "---":
            return fm
        if not line.strip():
            continue
        split = _split_kv_colon(line)
        if split is None:
            raise JsoncJmdParseError(
                f"frontmatter line lacks ':': {line!r}",
                cur.i,
            )
        key_part, value_part = split
        fm[_decode_jmd_key(key_part.strip())] = _parse_jmd_scalar(
            value_part.strip()
        )


def _parse_jmd_root_section(cur: _LineCursor) -> Section:
    """Consume ``# Label`` and its body sequence."""
    line = cur.advance()
    if line is None:
        raise JsoncJmdParseError("expected root heading", cur.i)
    kind, depth = _classify_heading_line(line)
    if kind != "section" or depth != 1:
        raise JsoncJmdParseError(
            f"expected '# Label' root heading, got {line!r}",
            cur.i,
        )
    label = _heading_label(line, depth)
    children = _parse_jmd_children(cur, depth=1)
    return Section(label=label, children=children)


def _parse_jmd_children(
    cur: _LineCursor,
    *,
    depth: int,
) -> list[Element]:
    """Parse the content sequence of a section at ``depth``.

    Heading-depth rules (companion-extension semantics):

    * Section heading at hdepth ≤ depth → sibling-or-ancestor,
      hand back to the parent without consuming.
    * Scalar-heading at hdepth < depth → ancestor's field, hand
      back without consuming. At hdepth == depth → it's a field
      of *this* section (the one whose children we're parsing),
      consume and append.
    * Comment marker at hdepth < depth → ancestor's comment,
      hand back. At hdepth ≥ depth → attach to current.

    The depth-aware comment rule is what lets a serializer place
    comments after sub-sections (with depth-prefix matching the
    parent depth) and have them re-attach to the right level on
    parse.
    """
    children: list[Element] = []
    while True:
        line = cur.peek()
        if line is None:
            return children
        if line.strip() == "":
            cur.advance()
            continue
        if line.startswith("#"):
            kind, hdepth = _classify_heading_line(line)
            if kind == "section":
                if hdepth <= depth:
                    return children
                cur.advance()
                label = _heading_label(line, hdepth)
                if label.endswith("[]"):
                    items = _parse_object_array_items(cur)
                    children.append(
                        ObjectArray(
                            key=label[:-2],
                            items=items,
                        ),
                    )
                else:
                    sub_children = _parse_jmd_children(
                        cur,
                        depth=hdepth,
                    )
                    children.append(
                        Section(
                            label=label,
                            children=sub_children,
                        ),
                    )
                continue
            if kind == "scalar-heading":
                if hdepth < depth:
                    return children
                cur.advance()
                body = _heading_label(line, hdepth)
                _attach_scalar_heading_field(
                    children,
                    body,
                    cur,
                )
                continue
            if kind == "line-comment":
                if hdepth < depth:
                    return children
                cur.advance()
                tail = line[hdepth + 1 :]
                if tail.startswith(" "):
                    tail = tail[1:]
                children.append(LineComment(text=tail))
                continue
            if kind == "block-comment":
                if hdepth < depth:
                    return children
                cur.advance()
                body_lines = _parse_jmd_block_comment_body(cur)
                children.append(
                    BlockComment(body_lines=body_lines),
                )
                continue
        if line.startswith("- ") or line.rstrip() == "-":
            raise JsoncJmdParseError(
                f"unexpected bullet outside an array: {line!r}",
                cur.i + 1,
            )
        if ":" not in line:
            raise JsoncJmdParseError(
                f"unexpected non-field line: {line!r}",
                cur.i + 1,
            )
        cur.advance()
        _attach_scalar_heading_field(children, line, cur)


def _attach_scalar_heading_field(
    children: list[Element],
    body: str,
    cur: _LineCursor,
) -> None:
    """Append a Field or ScalarArray parsed from ``key: value`` body.

    Used both for plain field-lines (``key: value`` / ``key[]:``)
    and for scalar-heading-form (``# key: value``) where the
    caller has already stripped the heading-prefix and passes
    just the ``key: value`` body. The trailing array-bullets
    are picked up from ``cur`` for the ``key[]:`` form.
    """
    split = _split_kv_colon(body)
    if split is None:
        raise JsoncJmdParseError(
            f"scalar-heading body needs ':': {body!r}",
            cur.i,
        )
    key_part, value_part = split
    key = _decode_jmd_key(key_part.strip())
    value_part = value_part.strip()
    if key.endswith("[]"):
        arr_items = _parse_jmd_scalar_array_items(cur)
        children.append(
            ScalarArray(key=key[:-2], items=arr_items),
        )
    else:
        children.append(
            Field(
                key=key,
                value=_parse_jmd_scalar(value_part),
            ),
        )


def _classify_heading_line(line: str) -> tuple[str, int]:
    """Return ``(kind, depth)`` for a ``#``-prefixed line.

    Kind is one of:

    * ``'section'``        — ``# label`` heading with no ``:``
      in the label part. Opens a new sub-section.
    * ``'scalar-heading'`` — ``# key: value`` heading. A field
      with explicit depth annotation. The depth-prefix tells
      the parser which section the field belongs to,
      independently of the textual position. Used by the
      emitter to express ``field-after-sub-section`` patterns
      that JMD cannot otherwise represent.
    * ``'line-comment'``   — ``#/ text``.
    * ``'block-comment'``  — ``#* [Comment]``.

    Depth is the number of leading ``#``. Anonymous heading
    (``###`` alone, no label) is classified as section with the
    same depth.
    """
    i = 0
    while i < len(line) and line[i] == "#":
        i += 1
    depth = i
    if i >= len(line):
        return ("section", depth)
    next_c = line[i]
    if next_c == "/":
        return ("line-comment", depth)
    if next_c == "*":
        return ("block-comment", depth)
    # ``section`` vs ``scalar-heading``: distinguish by presence
    # of ``:`` in the label part. Both ``# foo: bar`` and
    # ``# foo[]:`` count as scalar-heading; the array-suffix
    # case still has the ``:`` and the parser's later branch
    # handles the array form.
    rest = line[depth:]
    if ":" in rest:
        return ("scalar-heading", depth)
    return ("section", depth)


def _heading_label(line: str, depth: int) -> str:
    """Extract the label after a ``#``-prefix of given depth."""
    if len(line) <= depth:
        return ""
    return line[depth:].lstrip()


def _parse_jmd_block_comment_body(
    cur: _LineCursor,
) -> list[str]:
    """Read consecutive ``>``-prefixed lines as block-comment body.

    Each line is rstripped to enforce the shared body-lines
    invariant: the AST representation never carries per-line
    trailing whitespace. Both parsers agree on the canonical
    form so the serializers can trust their input.
    """
    body: list[str] = []
    while True:
        line = cur.peek()
        if line is None or not line.startswith(">"):
            return body
        cur.advance()
        if line == ">":
            body.append("")
        elif line.startswith("> "):
            body.append(line[2:].rstrip())
        else:
            body.append(line[1:].rstrip())


def _parse_jmd_scalar_array_items(
    cur: _LineCursor,
) -> list[Any]:
    """Parse the bullet-item lines following a ``key[]:`` field."""
    items: list[Any] = []
    while True:
        line = cur.peek()
        if line is None or not line.startswith("- "):
            return items
        cur.advance()
        items.append(_parse_jmd_scalar(line[2:].strip()))


def _parse_object_array_items(
    cur: _LineCursor,
) -> list[Section]:
    """Parse bullet-prefixed object items after a ``## key[]``.

    Each bullet starts an anonymous Section; continuation-indented
    lines (two-space indent) become its children. An empty bullet
    ``-`` produces an empty Section.
    """
    items: list[Section] = []
    while True:
        line = cur.peek()
        if line is None:
            return items
        if line.strip() == "":
            cur.advance()
            continue
        if line.startswith("#"):
            return items
        if line.startswith("- "):
            cur.advance()
            item = Section(label="", children=[])
            _attach_one(item, line[2:], cur)
            while True:
                cont = cur.peek()
                if cont is None or not cont.startswith("  "):
                    break
                body = cont[2:]
                if body.startswith("- "):
                    break
                cur.advance()
                _attach_one(item, body, cur)
            items.append(item)
            continue
        if line.rstrip() == "-":
            cur.advance()
            items.append(Section(label="", children=[]))
            continue
        return items


def _attach_one(
    item: Section,
    body: str,
    cur: _LineCursor,
) -> None:
    """Attach a single field-or-scalar-array entry to an item."""
    split = _split_kv_colon(body)
    if split is None:
        raise JsoncJmdParseError(
            f"unexpected bullet body: {body!r}",
            cur.i,
        )
    key_part, value_part = split
    key = _decode_jmd_key(key_part.strip())
    value_part = value_part.strip()
    if key.endswith("[]"):
        arr_items: list[Any] = []
        while True:
            nxt = cur.peek()
            if nxt is None:
                break
            if nxt.startswith("  - "):
                cur.advance()
                arr_items.append(
                    _parse_jmd_scalar(nxt[4:].strip()),
                )
                continue
            break
        item.children.append(
            ScalarArray(key=key[:-2], items=arr_items),
        )
    else:
        item.children.append(
            Field(
                key=key,
                value=_parse_jmd_scalar(value_part),
            ),
        )


def _split_kv_colon(content: str) -> tuple[str, str] | None:
    """Split a ``key:value`` line on the first colon following the key (D10).

    Respects a leading JSON-quoted key that may itself contain colons
    (e.g. ``"http://x": value``). For bare keys, falls back to
    ``str.partition(':')``.

    Returns:
        ``(key_raw, value_raw)`` where ``key_raw`` keeps surrounding
        quotes if the key was quoted; ``value_raw`` is the substring
        after the splitting colon. ``None`` if no colon follows the
        key.
    """
    if not content:
        return None
    if content[0] == '"':
        i = 1
        n = len(content)
        while i < n:
            ch = content[i]
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                j = i + 1
                if j < n and content[j] == ":":
                    return content[: i + 1], content[j + 1 :]
                return None
            i += 1
        return None
    key_part, sep, value_part = content.partition(":")
    if not sep:
        return None
    return key_part, value_part


def _decode_jmd_key(key: str) -> str:
    """Decode a possibly JSON-quoted JMD key."""
    if len(key) >= 2 and key.startswith('"') and key.endswith('"'):
        return cast(str, _json.loads(key))
    return key


def _parse_jmd_scalar(text: str) -> Any:
    """Parse a JMD scalar value string back into a Python value.

    Inverse of :func:`._jsonc_emit_jmd._emit_scalar_value`.
    Quoted form first, then null/true/false reserved words, then
    int / float, then bare string fallback.
    """
    if text == "null":
        return None
    if text == "true":
        return True
    if text == "false":
        return False
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        return _json.loads(text)
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


__all__ = [
    "JsoncJmdParseError",
    "from_jmd",
]
