"""JMD Parser (v0.3).

Implements the heading-scope model with blockquote and indentation
continuation support.
"""

from __future__ import annotations

import re
from typing import Any

from ._scalars import parse_key, parse_scalar
from ._tokenizer import Line, is_thematic_break, tokenize

try:
    from jmd._cparser import parse as _c_parse
    _USE_C = True
except ImportError:
    _USE_C = False


# Pattern to detect key: value on an item line or indented continuation
_KV_RE = re.compile(r'^(?:[a-zA-Z0-9_\-]+|"(?:[^"\\]|\\.)*"): ')
_kv_match = _KV_RE.match


def _is_object_item_content(content: str) -> bool:
    """Check if content after '- ' looks like a key: value (object item)."""
    return bool(_kv_match(content))


def _is_indent_field(raw_text: str) -> tuple[bool, str, str] | None:
    """Check if a raw line is an indented continuation field.

    Matches lines starting with 2+ spaces followed by a key: value pair.
    Returns (True, key_part, val_part) if it matches, None otherwise.
    """
    # Fast reject: must start with 2+ spaces
    if len(raw_text) < 2 or raw_text[0] != ' ' or raw_text[1] != ' ':
        return None
    stripped = raw_text.lstrip(' ')
    if _kv_match(stripped):
        key_part, _, val_part = stripped.partition(": ")
        return True, key_part, val_part
    return None


class JMDParser:
    r"""Parses JMD v0.3 documents into Python dicts/lists.

    Uses a scope stack driven by heading depth. Supports:
    - Blockquote multiline strings (> prefix)
    - Indentation continuation for array object items
    - Frontmatter (metadata before first heading, not serialized)

    Example:
        >>> data = JMDParser().parse("# Order\nid: 42\nstatus: pending")
        >>> data
        {'id': 42, 'status': 'pending'}
    """

    def __init__(self) -> None:
        self._lines: list[Line] = []
        self._pos: int = 0
        self.frontmatter: dict[str, Any] = {}

    def parse(self, source: str) -> Any:
        """Parse a JMD document string into a Python value.

        Args:
            source: Complete JMD document text.

        Returns:
            A Python dict (for object documents) or list (for array documents).

        Raises:
            ValueError: If the document is empty or has an invalid root marker.
        """
        self._lines = tokenize(source)
        self._pos = 0
        self.frontmatter = {}

        if not self._lines:
            raise ValueError("Empty document")

        # Parse frontmatter (lines before first heading)
        self._parse_frontmatter()

        if self._pos >= len(self._lines):
            raise ValueError("No root heading found")

        first = self._lines[self._pos]

        if _USE_C:
            # Strip frontmatter: pass only from the first heading line onwards
            body = "\n".join(source.splitlines()[first.number - 1:])
            return _c_parse(body)

        # Root array: # [] or # Label[]
        if first.heading_depth == 1 and (
            first.content == "[]" or first.content.endswith("[]")
        ):
            self._pos += 1
            return self._parse_array_body(depth=1)

        # Root object: # Label
        if first.heading_depth == 1:
            self._pos += 1
            return self._parse_object_body(depth=1)

        raise ValueError(
            f"Line {first.number}: expected '# <label>' or '# []'"
        )

    def _cur(self) -> Line | None:
        if self._pos < len(self._lines):
            return self._lines[self._pos]
        return None

    def _advance(self) -> None:
        self._pos += 1

    def _raw_text(self) -> str:
        """Get the raw text of the current line (preserving indentation)."""
        if self._pos < len(self._lines):
            # We need the original raw text before stripping
            return self._lines[self._pos].raw_text
        return ""

    def _parse_frontmatter(self) -> None:
        """Parse frontmatter fields before the first heading."""
        while self._pos < len(self._lines):
            line = self._lines[self._pos]
            # Stop at first heading
            if line.heading_depth > 0:
                break
            # Skip blank lines
            if line.heading_depth == -1:
                self._advance()
                continue
            # Parse key: value
            if ": " in line.content:
                key_part, _, val_part = line.content.partition(": ")
                self.frontmatter[parse_key(key_part)] = parse_scalar(val_part)
                self._advance()
                continue
            # Bare key (no value)
            if (line.content
                    and not line.content.startswith(">")
                    and not line.content.startswith("- ")):
                self.frontmatter[parse_key(line.content)] = True
                self._advance()
                continue
            break

    def _parse_blockquote(self) -> str:
        """Parse blockquote lines into a multiline string.

        Consumes all consecutive lines starting with '>' from current position.
        '> text' → text content, '>' alone → paragraph break (blank line).
        """
        parts: list[str] = []
        while self._pos < len(self._lines):
            line = self._lines[self._pos]
            if line.heading_depth != 0:
                break
            raw = line.raw_text.strip()
            if raw == ">":
                parts.append("")
                self._advance()
            elif raw.startswith("> "):
                parts.append(raw[2:])
                self._advance()
            else:
                break
        # Join and trim leading/trailing blank lines
        text = "\n".join(parts)
        return text.strip("\n")

    def _parse_object_body(self, depth: int) -> dict[str, Any]:
        """Parse fields belonging to an object scope at the given depth."""
        obj: dict[str, Any] = {}
        lines = self._lines
        lines_len = len(lines)
        pos = self._pos
        depth_plus_1 = depth + 1

        while pos < lines_len:
            line = lines[pos]
            hd = line.heading_depth

            # Blank line handling (Section 7.2a).
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
                else:
                    break

            # Heading at depth or shallower: scope ends.
            if hd > 0 and hd <= depth:
                break

            # Heading at depth+1: child scope.
            if hd == depth_plus_1:
                self._pos = pos
                self._parse_heading_into(obj, depth_plus_1)
                pos = self._pos
                continue

            # Non-heading line (hd == 0)
            content = line.content

            # Bare field: key: value (or key: with blockquote)
            if ": " in content:
                key_part, _, val_part = content.partition(": ")
                key = parse_key(key_part)
                if val_part == "":
                    # Check for blockquote multiline
                    pos += 1
                    self._pos = pos
                    peek_line: Line | None = (
                        lines[pos] if pos < lines_len else None
                    )
                    if (peek_line and peek_line.heading_depth == 0
                            and peek_line.raw_text.strip().startswith(">")):
                        obj[key] = self._parse_blockquote()
                        pos = self._pos
                    else:
                        obj[key] = ""
                else:
                    obj[key] = parse_scalar(val_part)
                    pos += 1
                continue

            # key: (colon at end, no space after) — also check for blockquote
            if content[-1:] == ":":
                key = parse_key(content[:-1])
                pos += 1
                self._pos = pos
                peek_line = lines[pos] if pos < lines_len else None
                if (peek_line and peek_line.heading_depth == 0
                        and peek_line.raw_text.strip().startswith(">")):
                    obj[key] = self._parse_blockquote()
                    pos = self._pos
                else:
                    obj[key] = ""
                continue

            break

        self._pos = pos
        return obj

    def _parse_heading_into(self, obj: dict[str, Any], depth: int) -> None:
        """Parse a heading line and add its content to the given object."""
        line = self._cur()
        if line is None:
            return
        content = line.content

        # Depth-qualified array item: ## -
        if content == "-":
            return

        # Anonymous sub-array: ## []
        if content == "[]":
            return

        self._advance()

        # Array heading: ## key[]
        if content.endswith("[]"):
            key = parse_key(content[:-2])
            obj[key] = self._parse_array_body(depth)
            return

        # Scalar heading: ## key: value
        if ": " in content:
            key_part, _, val_part = content.partition(": ")
            key = parse_key(key_part)
            if val_part == "":
                # Check for blockquote
                nxt = self._cur()
                if (nxt and nxt.heading_depth == 0
                        and nxt.raw_text.strip().startswith(">")):
                    obj[key] = self._parse_blockquote()
                else:
                    obj[key] = ""
            else:
                obj[key] = parse_scalar(val_part)
            return

        # Scalar heading with trailing colon: ## key:
        if content.endswith(":") and ": " not in content:
            key = parse_key(content[:-1])
            nxt = self._cur()
            if (nxt and nxt.heading_depth == 0
                    and nxt.raw_text.strip().startswith(">")):
                obj[key] = self._parse_blockquote()
            else:
                obj[key] = ""
            return

        # Object heading: ## key
        key = parse_key(content)
        obj[key] = self._parse_object_body(depth)

    def _parse_array_body(self, depth: int) -> list[Any]:
        """Parse items belonging to an array scope at the given depth."""
        items: list[Any] = []
        items_append = items.append
        lines = self._lines
        lines_len = len(lines)
        pos = self._pos
        depth_plus_1 = depth + 1

        while pos < lines_len:
            line = lines[pos]
            hd = line.heading_depth

            # Blank line: cosmetic between items, otherwise scope reset.
            if hd == -1:
                peek = pos + 1
                while peek < lines_len and lines[peek].heading_depth == -1:
                    peek += 1
                if peek < lines_len:
                    nxt = lines[peek]
                    nhd = nxt.heading_depth
                    nc = nxt.content
                    _nc_is_item = (
                        nc == "-"
                        or (len(nc) > 1 and nc[0] == '-' and nc[1] == ' ')
                    )
                    is_item = (
                        (nhd == 0 and _nc_is_item)
                        or (nhd == depth and _nc_is_item)
                        or (nhd == depth_plus_1
                            and (nc == "[]" or nc == "-"
                                 or (len(nc) > 1
                                     and nc[0] == '-' and nc[1] == ' ')))
                    )
                    if is_item:
                        pos += 1
                        continue
                    # Thematic break (---): continue only if this array
                    # owns nested-object items (i.e. acts as the item
                    # separator for items with sub-structures).
                    if (is_thematic_break(nxt)
                            and items
                            and isinstance(items[-1], dict)
                            and any(isinstance(v, (dict, list))
                                    for v in items[-1].values())):
                        pos += 1
                        continue
                break

            content = line.content

            # Heading at same depth or shallower.
            if hd > 0 and hd <= depth:
                # Depth-qualified item at same depth: ## -
                if hd == depth and content == "-":
                    pos += 1
                    self._pos = pos
                    items_append(self._parse_item_object(depth))
                    pos = self._pos
                    continue
                if (hd == depth
                        and len(content) > 1
                        and content[0] == '-' and content[1] == ' '):
                    content_after = content[2:]
                    if _kv_match(content_after):
                        pos += 1
                        self._pos = pos
                        key_part, _, val_part = content_after.partition(": ")
                        initial = {parse_key(key_part): parse_scalar(val_part)}
                        items_append(self._parse_item_object(
                            depth, initial_fields=initial))
                        pos = self._pos
                        continue
                break

            # Sub-array heading at depth+1: ### []
            if hd == depth_plus_1 and content == "[]":
                pos += 1
                self._pos = pos
                items_append(self._parse_array_body(depth_plus_1))
                pos = self._pos
                continue

            # Depth-qualified item at depth+1
            if hd == depth_plus_1 and content == "-":
                pos += 1
                self._pos = pos
                items_append(self._parse_item_object(depth))
                pos = self._pos
                continue
            if (hd == depth_plus_1
                    and len(content) > 1
                    and content[0] == '-' and content[1] == ' '):
                content_after = content[2:]
                if _kv_match(content_after):
                    pos += 1
                    self._pos = pos
                    key_part, _, val_part = content_after.partition(": ")
                    initial = {parse_key(key_part): parse_scalar(val_part)}
                    items_append(self._parse_item_object(
                        depth, initial_fields=initial))
                    pos = self._pos
                    continue

            # Heading at depth+1 that is not [], -, or - ...: stop.
            if hd == depth_plus_1:
                break

            # Deeper heading: stop.
            if hd > depth_plus_1:
                break

            # Non-heading lines (hd == 0)
            # Bare `-` (object item start).
            if content == "-":
                pos += 1
                self._pos = pos
                items_append(self._parse_item_object(depth))
                pos = self._pos
                continue

            # `- ...`: object item or scalar item
            if len(content) > 1 and content[0] == '-' and content[1] == ' ':
                content_after = content[2:]
                if _kv_match(content_after):
                    # Object item with first field
                    pos += 1
                    self._pos = pos
                    key_part, _, val_part = content_after.partition(": ")
                    initial = {parse_key(key_part): parse_scalar(val_part)}
                    items_append(self._parse_item_object(
                        depth, initial_fields=initial))
                    pos = self._pos
                else:
                    # Scalar item
                    items_append(parse_scalar(content_after))
                    pos += 1
                continue

            # Thematic break (---): visual separator between array items.
            # Only consumed by arrays whose items contain nested structures.
            if is_thematic_break(line):
                if (items
                        and isinstance(items[-1], dict)
                        and any(isinstance(v, (dict, list))
                                for v in items[-1].values())):
                    pos += 1
                    continue
                break

            break

        self._pos = pos
        return items

    def _parse_item_object(
        self,
        array_depth: int,
        initial_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Parse an object item within an array.

        Consumes indented continuation fields, bare fields, and child
        headings at array_depth+1.
        """
        obj: dict[str, Any] = dict(initial_fields) if initial_fields else {}
        child_depth = array_depth + 1
        lines = self._lines
        lines_len = len(lines)
        pos = self._pos

        # First: consume indented continuation fields (2+ spaces + key: value)
        # Fast check: if current line doesn't start with space, skip loop
        if (pos < lines_len
                and lines[pos].raw_text
                and lines[pos].raw_text[0] == ' '):
            while pos < lines_len:
                line = lines[pos]
                raw = line.raw_text

                # Check for indented continuation field
                if raw and raw[0] == ' ' and len(raw) >= 3 and raw[1] == ' ':
                    stripped = raw.lstrip(' ')
                    if _kv_match(stripped):
                        key_part, _, val_part = stripped.partition(": ")
                        obj[parse_key(key_part)] = parse_scalar(val_part)
                        pos += 1
                        continue

                # Blank line between indented fields — peek ahead
                if line.heading_depth == -1:
                    peek = pos + 1
                    while peek < lines_len and lines[peek].heading_depth == -1:
                        peek += 1
                    if peek < lines_len:
                        nxt_line = lines[peek]
                        # If next non-blank is indented, skip blank
                        nxt_raw = nxt_line.raw_text
                        if (nxt_raw and len(nxt_raw) >= 3
                                and nxt_raw[0] == ' ' and nxt_raw[1] == ' '
                                and _kv_match(nxt_raw.lstrip(' '))):
                            pos += 1
                            continue
                        # If next is a child heading, skip blank (cosmetic)
                        if nxt_line.heading_depth == child_depth:
                            pos += 1
                            continue
                    break

                # Thematic break ends the current item.
                if is_thematic_break(line):
                    break

                # After indented fields, also accept bare fields and headings
                break

        self._pos = pos

        # Then: consume bare fields and child headings (same as v0.2)
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
                if (line.content == "-" or line.content == "[]"
                        or line.content.startswith("- ")):
                    break
                self._pos = pos
                self._parse_heading_into(obj, child_depth)
                pos = self._pos
                continue

            if line.heading_depth > child_depth:
                break

            # Thematic break: end current item.
            if is_thematic_break(line):
                break

            # Next item marker: stop
            hd = line.heading_depth
            if hd == 0:
                content = line.content
                if (content == "-"
                        or (len(content) > 1
                            and content[0] == '-' and content[1] == ' ')):
                    break

                # Bare field: key: value
                if ": " in content:
                    key_part, _, val_part = content.partition(": ")
                    obj[parse_key(key_part)] = parse_scalar(val_part)
                    pos += 1
                    continue

            break

        self._pos = pos
        return obj
