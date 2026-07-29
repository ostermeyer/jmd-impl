# SPDX-License-Identifier: Apache-2.0
"""Stateful recursive-descent engine for JMD document bodies.

Object, heading, array, and item parsing deliberately share one module because
they are mutually recursive and operate on one cursor and token vector.
Envelope headers and shared stateless primitives have separate owners.
"""

from __future__ import annotations

from typing import Any

from ._parser_common import (
    _K_SCALAR_BARE,
    JMDParseError,
    _kv_match,
    parse_block_scalar_from,
    parse_blockquote_from,
    set_array_sigil,
    set_object_heading,
    set_scalar,
)
from ._scalars import parse_key, parse_scalar, split_kv
from ._tokenizer import Line, is_thematic_break


def _is_item_field(content: str) -> bool:
    """Return whether array-item content starts an object field."""
    return _kv_match(content) is not None or content[-1:] == ":"


def _split_item_field(content: str) -> tuple[str, str]:
    """Split an array-item field, including an empty multiline opener."""
    split = split_kv(content)
    if split is not None:
        return split
    return content[:-1], ""


class JMDBodyParser:
    """Parse one tokenized JMD body using a single mutable cursor."""

    def __init__(self, lines: list[Line], pos: int) -> None:
        """Initialize the body parser at the first line after the root."""
        self._lines = lines
        self._pos = pos

    @property
    def pos(self) -> int:
        """Return the current token position."""
        return self._pos

    def parse(self, *, root_is_array: bool) -> Any:
        """Parse a root object or array from the current position."""
        if root_is_array:
            return self._parse_array_body(depth=1)
        return self._parse_object_body(depth=1)

    def _cur(self) -> Line | None:
        if self._pos < len(self._lines):
            return self._lines[self._pos]
        return None

    def _advance(self) -> None:
        self._pos += 1

    def _parse_block_scalar(self, *, folded: bool) -> str:
        """Parse a tolerated YAML-style block scalar at the cursor."""
        value, self._pos = parse_block_scalar_from(
            self._lines,
            self._pos,
            folded=folded,
        )
        return value

    def _parse_blockquote(self) -> str:
        """Parse consecutive blockquote lines at the cursor."""
        value, self._pos = parse_blockquote_from(self._lines, self._pos)
        return value

    def _parse_field_value(self, value_text: str) -> Any:
        """Parse a scalar or multiline field value at the current cursor."""
        if value_text == "":
            next_line = self._cur()
            if (
                next_line
                and next_line.heading_depth == 0
                and next_line.raw_text.strip().startswith(">")
            ):
                return self._parse_blockquote()
            return ""
        if value_text == "|" or value_text == ">":
            return self._parse_block_scalar(folded=(value_text == ">"))
        return parse_scalar(value_text)

    def _parse_item_with_first_field(
        self,
        array_depth: int,
        content: str,
    ) -> dict[str, Any]:
        """Parse an object item whose dash line carries its first field."""
        key_part, value_text = _split_item_field(content)
        initial = {
            parse_key(key_part): self._parse_field_value(value_text),
        }
        return self._parse_item_object(array_depth, initial_fields=initial)

    def _parse_object_body(self, depth: int) -> dict[str, Any]:
        """Parse fields belonging to an object scope at ``depth``."""
        obj: dict[str, Any] = {}
        kinds: dict[str, str] = {}
        lines = self._lines
        lines_len = len(lines)
        pos = self._pos
        depth_plus_1 = depth + 1

        while pos < lines_len:
            line = lines[pos]
            hd = line.heading_depth

            if hd == -1:
                peek = pos + 1
                while peek < lines_len and lines[peek].heading_depth == -1:
                    peek += 1
                if peek < lines_len:
                    nxt: Line = lines[peek]
                    if nxt.heading_depth > 0:
                        pos += 1
                        continue
                if depth == 1:
                    pos += 1
                    continue
                break

            if hd > 0 and hd <= depth:
                break

            if hd == depth_plus_1:
                self._pos = pos
                self._parse_heading_into(obj, kinds, depth_plus_1)
                pos = self._pos
                continue

            content = line.content
            line_no = line.number
            if line.raw_text.startswith((" ", "\t")):
                raise JMDParseError(
                    kind="prose_in_body",
                    line=line_no,
                    key="",
                )

            if ": " in content:
                key_part, val_part = split_kv(content) or (content, "")
                key = parse_key(key_part)
                if val_part == "":
                    pos += 1
                    self._pos = pos
                    peek_line: Line | None = (
                        lines[pos] if pos < lines_len else None
                    )
                    if (
                        peek_line
                        and peek_line.heading_depth == 0
                        and peek_line.raw_text.strip().startswith(">")
                    ):
                        value: Any = self._parse_blockquote()
                        pos = self._pos
                    else:
                        value = ""
                    set_scalar(
                        obj,
                        kinds,
                        key,
                        value,
                        line_no,
                        is_heading=False,
                    )
                else:
                    if val_part == "|" or val_part == ">":
                        pos += 1
                        self._pos = pos
                        value = self._parse_block_scalar(
                            folded=(val_part == ">")
                        )
                        pos = self._pos
                    else:
                        value = parse_scalar(val_part)
                        pos += 1
                    set_scalar(
                        obj,
                        kinds,
                        key,
                        value,
                        line_no,
                        is_heading=False,
                    )
                continue

            if content[-1:] == ":":
                key = parse_key(content[:-1])
                pos += 1
                self._pos = pos
                peek_line = lines[pos] if pos < lines_len else None
                if (
                    peek_line
                    and peek_line.heading_depth == 0
                    and peek_line.raw_text.strip().startswith(">")
                ):
                    value = self._parse_blockquote()
                    pos = self._pos
                else:
                    value = ""
                set_scalar(
                    obj,
                    kinds,
                    key,
                    value,
                    line_no,
                    is_heading=False,
                )
                continue

            break

        self._pos = pos
        return obj

    def _parse_heading_into(
        self,
        obj: dict[str, Any],
        kinds: dict[str, str],
        depth: int,
    ) -> None:
        """Parse a heading and add its content to ``obj``."""
        line = self._cur()
        if line is None:
            return
        content = line.content
        line_no = line.number

        if content == "-" or content == "[]":
            return

        self._advance()

        if content.endswith("[]"):
            key = parse_key(content[:-2])
            set_array_sigil(
                obj,
                kinds,
                key,
                self._parse_array_body(depth),
                line_no,
            )
            return

        if ": " in content:
            key_part, val_part = split_kv(content) or (content, "")
            key = parse_key(key_part)
            if val_part == "":
                nxt = self._cur()
                if (
                    nxt
                    and nxt.heading_depth == 0
                    and nxt.raw_text.strip().startswith(">")
                ):
                    value: Any = self._parse_blockquote()
                else:
                    value = ""
            elif val_part == "|" or val_part == ">":
                value = self._parse_block_scalar(folded=(val_part == ">"))
            else:
                value = parse_scalar(val_part)
            set_scalar(
                obj,
                kinds,
                key,
                value,
                line_no,
                is_heading=True,
            )
            return

        if content.endswith(":") and ": " not in content:
            key = parse_key(content[:-1])
            nxt = self._cur()
            if (
                nxt
                and nxt.heading_depth == 0
                and nxt.raw_text.strip().startswith(">")
            ):
                value = self._parse_blockquote()
            else:
                value = ""
            set_scalar(
                obj,
                kinds,
                key,
                value,
                line_no,
                is_heading=True,
            )
            return

        key = parse_key(content)
        child = self._parse_object_body(depth)
        set_object_heading(obj, kinds, key, child, line_no)

    def _parse_array_body(self, depth: int) -> list[Any]:
        """Parse items belonging to an array scope at ``depth``."""
        items: list[Any] = []
        items_append = items.append
        lines = self._lines
        lines_len = len(lines)
        pos = self._pos
        depth_plus_1 = depth + 1

        while pos < lines_len:
            line = lines[pos]
            hd = line.heading_depth

            if hd == -1:
                peek = pos + 1
                while peek < lines_len and lines[peek].heading_depth == -1:
                    peek += 1
                if peek < lines_len:
                    nxt = lines[peek]
                    nhd = nxt.heading_depth
                    nc = nxt.content
                    nc_is_item = (
                        nc == "-"
                        or (len(nc) > 1 and nc[0] == "-" and nc[1] == " ")
                    )
                    is_item = (
                        (nhd == 0 and nc_is_item)
                        or (nhd == depth and nc_is_item)
                        or (
                            nhd == depth_plus_1
                            and (
                                nc == "[]"
                                or nc == "-"
                                or (
                                    len(nc) > 1
                                    and nc[0] == "-"
                                    and nc[1] == " "
                                )
                            )
                        )
                    )
                    if is_item:
                        pos += 1
                        continue
                    if is_thematic_break(nxt):
                        pos += 1
                        continue
                break

            content = line.content

            if hd > 0 and hd <= depth:
                if hd == depth and content == "":
                    pos += 1
                    continue
                if hd == depth and content == "-":
                    pos += 1
                    self._pos = pos
                    items_append(self._parse_item_object(depth))
                    pos = self._pos
                    continue
                if (
                    hd == depth
                    and len(content) > 1
                    and content[0] == "-"
                    and content[1] == " "
                ):
                    content_after = content[2:]
                    if _is_item_field(content_after):
                        pos += 1
                        self._pos = pos
                        items_append(
                            self._parse_item_with_first_field(
                                depth,
                                content_after,
                            )
                        )
                        pos = self._pos
                        continue
                    items_append(parse_scalar(content_after))
                    pos += 1
                    continue
                break

            if hd == depth_plus_1 and content == "[]":
                pos += 1
                self._pos = pos
                items_append(self._parse_array_body(depth_plus_1))
                pos = self._pos
                continue

            if hd == depth_plus_1 and content == "-":
                pos += 1
                self._pos = pos
                items_append(self._parse_item_object(depth))
                pos = self._pos
                continue
            if (
                hd == depth_plus_1
                and len(content) > 1
                and content[0] == "-"
                and content[1] == " "
            ):
                content_after = content[2:]
                if _is_item_field(content_after):
                    pos += 1
                    self._pos = pos
                    items_append(
                        self._parse_item_with_first_field(
                            depth,
                            content_after,
                        )
                    )
                    pos = self._pos
                    continue
                items_append(parse_scalar(content_after))
                pos += 1
                continue

            if hd == depth_plus_1 or hd > depth_plus_1:
                break

            if content == "-":
                pos += 1
                self._pos = pos
                items_append(self._parse_item_object(depth))
                pos = self._pos
                continue

            if (
                len(content) > 1
                and content[0] == "-"
                and content[1] == " "
            ):
                content_after = content[2:]
                if _is_item_field(content_after):
                    pos += 1
                    self._pos = pos
                    items_append(
                        self._parse_item_with_first_field(
                            depth,
                            content_after,
                        )
                    )
                    pos = self._pos
                else:
                    items_append(parse_scalar(content_after))
                    pos += 1
                continue

            if is_thematic_break(line):
                pos += 1
                continue

            break

        self._pos = pos
        return items

    def _parse_item_object(
        self,
        array_depth: int,
        initial_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Parse one object item within an array."""
        obj: dict[str, Any] = dict(initial_fields) if initial_fields else {}
        kinds: dict[str, str] = (
            {key: _K_SCALAR_BARE for key in initial_fields}
            if initial_fields
            else {}
        )
        child_depth = array_depth + 1
        lines = self._lines
        lines_len = len(lines)
        pos = self._pos

        if (
            pos < lines_len
            and lines[pos].raw_text
            and lines[pos].raw_text[0] == " "
        ):
            while pos < lines_len:
                line = lines[pos]
                raw = line.raw_text

                if (
                    raw
                    and raw[0] == " "
                    and len(raw) >= 3
                    and raw[1] == " "
                ):
                    stripped = raw.lstrip(" ")
                    if _is_item_field(stripped):
                        key_part, value_text = _split_item_field(stripped)
                        pos += 1
                        self._pos = pos
                        value = self._parse_field_value(value_text)
                        set_scalar(
                            obj,
                            kinds,
                            parse_key(key_part),
                            value,
                            line.number,
                            is_heading=False,
                        )
                        pos = self._pos
                        continue

                if line.heading_depth == -1:
                    peek = pos + 1
                    while (
                        peek < lines_len
                        and lines[peek].heading_depth == -1
                    ):
                        peek += 1
                    if peek < lines_len:
                        nxt_line = lines[peek]
                        nxt_raw = nxt_line.raw_text
                        if (
                            nxt_raw
                            and len(nxt_raw) >= 3
                            and nxt_raw[0] == " "
                            and nxt_raw[1] == " "
                            and _is_item_field(nxt_raw.lstrip(" "))
                        ):
                            pos += 1
                            continue
                        if nxt_line.heading_depth == child_depth:
                            pos += 1
                            continue
                    break

                if is_thematic_break(line):
                    pos += 1
                    continue
                break

        self._pos = pos

        while pos < lines_len:
            line = lines[pos]

            if line.heading_depth == -1:
                peek = pos + 1
                while peek < lines_len and lines[peek].heading_depth == -1:
                    peek += 1
                if peek < lines_len:
                    nxt = lines[peek]
                    if nxt.heading_depth == child_depth:
                        pos += 1
                        self._pos = pos
                        continue
                break

            if line.heading_depth > 0 and line.heading_depth <= array_depth:
                break

            if line.heading_depth == child_depth:
                if (
                    line.content == "-"
                    or line.content == "[]"
                    or line.content.startswith("- ")
                ):
                    break
                self._pos = pos
                self._parse_heading_into(obj, kinds, child_depth)
                pos = self._pos
                continue

            if line.heading_depth > child_depth:
                break

            if is_thematic_break(line):
                pos += 1
                self._pos = pos
                continue

            hd = line.heading_depth
            if hd == 0:
                content = line.content
                if (
                    content == "-"
                    or (
                        len(content) > 1
                        and content[0] == "-"
                        and content[1] == " "
                    )
                ):
                    break

                if ": " in content:
                    key_part, val_part = split_kv(content) or (content, "")
                    set_scalar(
                        obj,
                        kinds,
                        parse_key(key_part),
                        parse_scalar(val_part),
                        line.number,
                        is_heading=False,
                    )
                    pos += 1
                    continue

            break

        self._pos = pos
        return obj
