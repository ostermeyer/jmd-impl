# SPDX-License-Identifier: Apache-2.0
"""JMD Parser (v0.3.3).

Implements the heading-scope model with blockquote and indentation
continuation support.
"""

from __future__ import annotations

import re
from typing import Any

from ._envelope import Envelope, split_mode_label
from ._scalars import parse_key, parse_scalar, split_kv
from ._tokenizer import Line, is_thematic_break, tokenize

try:
    from jmd._cparser import parse as _c_parse
    _USE_C = True
except ImportError:
    _USE_C = False


# Pattern to detect key: value on an item line or indented continuation
_KV_RE = re.compile(r'^(?:[a-zA-Z0-9_\-]+|"(?:[^"\\]|\\.)*"): ')
_kv_match = _KV_RE.match


# §7.4 — Kind tags for the per-scope sigil lock.
_K_OBJECT = "object"            # ## key  → {...}
_K_ARRAY_SIGIL = "array_sigil"  # ## key[] → [...]  (author declared array)
_K_ARRAY_PROMOTED = "array_promoted"  # promoted from repeated ## key
_K_SCALAR_BARE = "scalar_bare"  # key: value
_K_SCALAR_HEADING = "scalar_heading"  # ## key: value


class JMDParseError(ValueError):
    """A structured parse error per JMD spec §7.4.2 and related rules.

    Subclass of :class:`ValueError` for backward compatibility with callers
    that catch ``ValueError``. Carries structured fields that conforming
    backends report uniformly (see ``conformance/README.md``):

    Attributes:
        kind: Error category — one of ``"sigil_conflict"``,
            ``"repeated_explicit_array"``, ``"repeated_scalar_key"``.
        line: 1-based source line number of the offending occurrence.
        key:  The key whose repetition triggered the error.
        form: Implementation-advisory diagnostic detail with
            ``"existing"`` and ``"new"`` kind tags.
    """

    def __init__(
        self,
        *,
        kind: str,
        line: int,
        key: str,
        form: dict[str, str] | None = None,
        message: str | None = None,
    ) -> None:
        self.kind = kind
        self.line = line
        self.key = key
        self.form = form or {}
        if message is None:
            message = f"Line {line}: {kind} for key {key!r}"
        super().__init__(message)


def _is_object_item_content(content: str) -> bool:
    """Check if content after '- ' looks like a key: value (object item)."""
    return bool(_kv_match(content))


def parse_block_scalar_from(
    lines: list[Line],
    pos: int,
    *,
    folded: bool,
) -> tuple[str, int]:
    """Parse a YAML-style block scalar (§5.2) starting at ``lines[pos]``.

    Consumes consecutive lines indented by at least 2 spaces. The
    indentation of the first indented line sets the strip width;
    subsequent lines are stripped by the same width. Blank lines
    within the block are preserved as paragraph separators.

    Args:
        lines: Tokenized source lines.
        pos: Position of the first candidate line (immediately after the
            ``key: |`` or ``key: >`` opener).
        folded: If True (``>``), fold consecutive non-blank lines with
            spaces; each in-block blank line becomes one newline. If
            False (``|``), preserve newlines.

    Returns:
        A tuple ``(value, new_pos)``: the decoded string and the position
        just past the consumed block.
    """
    parts: list[str] = []
    indent_strip: int | None = None
    while pos < len(lines):
        line = lines[pos]
        if line.heading_depth == -1:
            parts.append("")
            pos += 1
            continue
        raw = line.raw_text
        if not raw or raw[0] != ' ':
            break
        actual_indent = len(raw) - len(raw.lstrip(' '))
        if actual_indent < 2:
            break
        if indent_strip is None:
            indent_strip = actual_indent
        if actual_indent < indent_strip:
            break
        parts.append(raw[indent_strip:])
        pos += 1
    # Drop trailing blank lines (§5.2 chomp behavior).
    while parts and parts[-1] == "":
        parts.pop()
    if not folded:
        return "\n".join(parts), pos
    # Folded mode: fold consecutive non-blank lines with spaces; each
    # in-block blank line becomes one newline.
    out_pieces: list[str] = []
    current_para: list[str] = []
    for part in parts:
        if part == "":
            if current_para:
                out_pieces.append(" ".join(current_para))
                current_para = []
            out_pieces.append("\n")
        else:
            current_para.append(part)
    if current_para:
        out_pieces.append(" ".join(current_para))
    return "".join(out_pieces), pos


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
        key_part, val_part = split_kv(stripped) or (stripped, "")
        return True, key_part, val_part
    return None


class JMDParser:
    r"""Parses JMD v0.3.3 documents into Python dicts/lists.

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

    def _line_no_at(self, pos: int) -> int:
        """Return the 1-based source line number at parser position."""
        if 0 <= pos < len(self._lines):
            return self._lines[pos].number
        return 0

    def _set_object_heading(
        self,
        obj: dict[str, Any],
        kinds: dict[str, str],
        key: str,
        value: dict[str, Any],
        line: int,
    ) -> dict[str, Any]:
        """Apply §7.4 promotion for an object heading ``## key``.

        Returns the dict to fill (either the new dict or the appended one).
        Raises :class:`JMDParseError` on sigil or scalar conflicts.
        """
        existing_kind = kinds.get(key)
        if existing_kind is None:
            obj[key] = value
            kinds[key] = _K_OBJECT
            return value
        if existing_kind == _K_OBJECT:
            obj[key] = [obj[key], value]
            kinds[key] = _K_ARRAY_PROMOTED
            return value
        if existing_kind == _K_ARRAY_PROMOTED:
            obj[key].append(value)
            return value
        if existing_kind == _K_ARRAY_SIGIL:
            raise JMDParseError(
                kind="sigil_conflict", line=line, key=key,
                form={"existing": existing_kind, "new": _K_OBJECT},
            )
        # Existing is scalar (bare or heading): mixed-form conflict.
        raise JMDParseError(
            kind="repeated_scalar_key", line=line, key=key,
            form={"existing": existing_kind, "new": _K_OBJECT},
        )

    def _set_array_sigil(
        self,
        obj: dict[str, Any],
        kinds: dict[str, str],
        key: str,
        value: list[Any],
        line: int,
    ) -> None:
        """Apply §7.4 for an explicit array heading ``## key[]``."""
        existing_kind = kinds.get(key)
        if existing_kind is None:
            obj[key] = value
            kinds[key] = _K_ARRAY_SIGIL
            return
        if existing_kind == _K_ARRAY_SIGIL:
            raise JMDParseError(
                kind="repeated_explicit_array", line=line, key=key,
                form={"existing": existing_kind, "new": _K_ARRAY_SIGIL},
            )
        if existing_kind in (_K_OBJECT, _K_ARRAY_PROMOTED):
            raise JMDParseError(
                kind="sigil_conflict", line=line, key=key,
                form={"existing": existing_kind, "new": _K_ARRAY_SIGIL},
            )
        raise JMDParseError(
            kind="repeated_scalar_key", line=line, key=key,
            form={"existing": existing_kind, "new": _K_ARRAY_SIGIL},
        )

    def _set_scalar(
        self,
        obj: dict[str, Any],
        kinds: dict[str, str],
        key: str,
        value: Any,
        line: int,
        *,
        is_heading: bool,
    ) -> None:
        """Apply §7.4 for a bare field or scalar heading.

        Any repetition of the same key with a scalar in either form, or
        with a heading form, is a structured error.
        """
        new_kind = _K_SCALAR_HEADING if is_heading else _K_SCALAR_BARE
        existing_kind = kinds.get(key)
        if existing_kind is None:
            obj[key] = value
            kinds[key] = new_kind
            return
        if existing_kind == _K_ARRAY_SIGIL:
            raise JMDParseError(
                kind="sigil_conflict", line=line, key=key,
                form={"existing": existing_kind, "new": new_kind},
            )
        # Existing object, promoted array, or scalar: all map to
        # repeated_scalar_key per the §7.4.2(c) extension we agreed on
        # 2026-05-13 (mixed-form scalar/object/promoted-array conflicts
        # are reported under the scalar-key kind).
        raise JMDParseError(
            kind="repeated_scalar_key", line=line, key=key,
            form={"existing": existing_kind, "new": new_kind},
        )

    def parse(self, source: str) -> Envelope:
        """Parse a JMD document string into a canonical :class:`Envelope`.

        Implements the §3.6 parser contract: returns ``{mode, label,
        frontmatter, value}`` for every document, regardless of root
        marker. The ``.frontmatter`` instance attribute is retained as
        internal state but is redundant with ``envelope.frontmatter``.

        Args:
            source: Complete JMD document text.

        Returns:
            An :class:`Envelope` with the parsed body in ``value``.

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

        if first.heading_depth != 1:
            raise ValueError(
                f"Line {first.number}: expected '# <label>' or '# []'"
            )

        mode, label = split_mode_label(first.content)

        if _USE_C:
            # Strip frontmatter: pass only from the first heading line onwards
            body = "\n".join(source.splitlines()[first.number - 1:])
            value: Any = _c_parse(body)
        elif first.content == "[]" or first.content.endswith("[]"):
            # Root array: # [] / # Label[] / #- [] / #? X[] / #! X[]
            self._pos += 1
            value = self._parse_array_body(depth=1)
        else:
            # Root object
            self._pos += 1
            value = self._parse_object_body(depth=1)

        return Envelope(
            mode=mode,
            label=label,
            value=value,
            frontmatter=dict(self.frontmatter),
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
        """Parse frontmatter fields before the first heading.

        Per §3.5.1, lines of three or more hyphens (``---``, ``----``, ...)
        around the frontmatter block are tolerated as decorative noise — a
        reflex inherited from YAML-prefixed Markdown ecosystems. They are
        consumed without semantic effect, both before any frontmatter field
        and between the last field and the root heading.
        """
        while self._pos < len(self._lines):
            line = self._lines[self._pos]
            # Stop at first heading
            if line.heading_depth > 0:
                break
            # Skip blank lines
            if line.heading_depth == -1:
                self._advance()
                continue
            # §3.5.1: tolerate stray --- markers around the frontmatter.
            if is_thematic_break(line):
                self._advance()
                continue
            # Parse key: value
            if ": " in line.content:
                kv = split_kv(line.content) or (line.content, "")
                key_part, val_part = kv
                self.frontmatter[parse_key(key_part)] = parse_scalar(val_part)
                self._advance()
                continue
            # key: (trailing colon, no value) — multi-line blockquote
            # value follows (D12 round-trip for multi-line frontmatter).
            if line.content.endswith(":") and ": " not in line.content:
                key = parse_key(line.content[:-1])
                self._advance()
                nxt = self._cur()
                if (nxt and nxt.heading_depth == 0
                        and nxt.raw_text.strip().startswith(">")):
                    self.frontmatter[key] = self._parse_blockquote()
                else:
                    self.frontmatter[key] = ""
                continue
            # Bare key (no value)
            if (line.content
                    and not line.content.startswith(">")
                    and not line.content.startswith("- ")):
                self.frontmatter[parse_key(line.content)] = True
                self._advance()
                continue
            break

    def _parse_block_scalar(self, *, folded: bool) -> str:
        """Parse a YAML-style block scalar (§5.2) at the current position.

        Thin wrapper around the module-level :func:`parse_block_scalar_from`
        that updates ``self._pos`` and returns just the decoded value.
        """
        value, new_pos = parse_block_scalar_from(
            self._lines, self._pos, folded=folded,
        )
        self._pos = new_pos
        return value

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
        # Join. Only trim trailing newlines — leading ``>`` lines are
        # part of the value (D13: leading-newline lossless roundtrip).
        text = "\n".join(parts)
        return text.rstrip("\n")

    def _parse_object_body(self, depth: int) -> dict[str, Any]:
        """Parse fields belonging to an object scope at the given depth."""
        obj: dict[str, Any] = {}
        # §7.4 sigil lock — tracks per-scope kind tags for conflict detection
        # and array promotion of repeated headings.
        kinds: dict[str, str] = {}
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
                self._parse_heading_into(obj, kinds, depth_plus_1)
                pos = self._pos
                continue

            # Non-heading line (hd == 0)
            content = line.content
            line_no = line.number

            # Bare field: key: value (or key: with blockquote)
            if ": " in content:
                key_part, val_part = split_kv(content) or (content, "")
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
                        value: Any = self._parse_blockquote()
                        pos = self._pos
                    else:
                        value = ""
                    self._set_scalar(
                        obj, kinds, key, value, line_no, is_heading=False,
                    )
                else:
                    # §5.2: ``key: |`` (literal) and ``key: >`` (folded)
                    # open a YAML-style block scalar — parser-tolerant
                    # alternative to the blockquote form.
                    if val_part == "|" or val_part == ">":
                        pos += 1
                        self._pos = pos
                        value = self._parse_block_scalar(
                            folded=(val_part == ">"),
                        )
                        pos = self._pos
                    else:
                        value = parse_scalar(val_part)
                        pos += 1
                    self._set_scalar(
                        obj, kinds, key, value,
                        line_no, is_heading=False,
                    )
                continue

            # key: (colon at end, no space after) — also check for blockquote
            if content[-1:] == ":":
                key = parse_key(content[:-1])
                pos += 1
                self._pos = pos
                peek_line = lines[pos] if pos < lines_len else None
                if (peek_line and peek_line.heading_depth == 0
                        and peek_line.raw_text.strip().startswith(">")):
                    value = self._parse_blockquote()
                    pos = self._pos
                else:
                    value = ""
                self._set_scalar(
                    obj, kinds, key, value, line_no, is_heading=False,
                )
                continue

            break

        self._pos = pos
        return obj

    def _parse_heading_into(
        self, obj: dict[str, Any], kinds: dict[str, str], depth: int,
    ) -> None:
        """Parse a heading line and add its content to the given object."""
        line = self._cur()
        if line is None:
            return
        content = line.content
        line_no = line.number

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
            self._set_array_sigil(
                obj, kinds, key, self._parse_array_body(depth), line_no,
            )
            return

        # Scalar heading: ## key: value
        if ": " in content:
            key_part, val_part = split_kv(content) or (content, "")
            key = parse_key(key_part)
            if val_part == "":
                # Check for blockquote
                nxt = self._cur()
                if (nxt and nxt.heading_depth == 0
                        and nxt.raw_text.strip().startswith(">")):
                    value: Any = self._parse_blockquote()
                else:
                    value = ""
            elif val_part == "|" or val_part == ">":
                # §5.2 block scalar (literal or folded).
                value = self._parse_block_scalar(folded=(val_part == ">"))
            else:
                value = parse_scalar(val_part)
            self._set_scalar(
                obj, kinds, key, value, line_no, is_heading=True,
            )
            return

        # Scalar heading with trailing colon: ## key:
        if content.endswith(":") and ": " not in content:
            key = parse_key(content[:-1])
            nxt = self._cur()
            if (nxt and nxt.heading_depth == 0
                    and nxt.raw_text.strip().startswith(">")):
                value = self._parse_blockquote()
            else:
                value = ""
            self._set_scalar(
                obj, kinds, key, value, line_no, is_heading=True,
            )
            return

        # Object heading: ## key — apply promote-to-array on repetition.
        key = parse_key(content)
        child = self._parse_object_body(depth)
        self._set_object_heading(obj, kinds, key, child, line_no)

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
                        kv = split_kv(content_after) or (content_after, "")
                        key_part, val_part = kv
                        initial = {parse_key(key_part): parse_scalar(val_part)}
                        items_append(self._parse_item_object(
                            depth, initial_fields=initial))
                        pos = self._pos
                        continue
                    # Depth-qualified scalar item: ## - value
                    items_append(parse_scalar(content_after))
                    pos += 1
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
                    kv = split_kv(content_after) or (content_after, "")
                    key_part, val_part = kv
                    initial = {parse_key(key_part): parse_scalar(val_part)}
                    items_append(self._parse_item_object(
                        depth, initial_fields=initial))
                    pos = self._pos
                    continue
                # Depth+1 qualified scalar item: ### - value (§8.6b form)
                items_append(parse_scalar(content_after))
                pos += 1
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
                    kv = split_kv(content_after) or (content_after, "")
                    key_part, val_part = kv
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
        # §7.4 sigil lock for this item's scope. Seed from initial_fields:
        # the first field on the `- ` line is a bare scalar at item-level.
        kinds: dict[str, str] = (
            {k: _K_SCALAR_BARE for k in initial_fields}
            if initial_fields else {}
        )
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
                        kv = split_kv(stripped) or (stripped, "")
                        key_part, val_part = kv
                        self._set_scalar(
                            obj, kinds, parse_key(key_part),
                            parse_scalar(val_part),
                            line.number, is_heading=False,
                        )
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
                self._parse_heading_into(obj, kinds, child_depth)
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
                    key_part, val_part = split_kv(content) or (content, "")
                    self._set_scalar(
                        obj, kinds, parse_key(key_part),
                        parse_scalar(val_part),
                        line.number, is_heading=False,
                    )
                    pos += 1
                    continue

            break

        self._pos = pos
        return obj
