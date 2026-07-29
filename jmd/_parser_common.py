# SPDX-License-Identifier: Apache-2.0
"""Shared primitives for JMD batch and streaming parsers.

This module owns stateless syntax recognition, structured parse errors,
per-scope kind locks, and multiline decoding. Stateful document traversal
belongs to :mod:`jmd._parser_body` and envelope-header parsing belongs to
:mod:`jmd._parser_header`.
"""

from __future__ import annotations

import re
from typing import Any

from ._scalars import split_kv
from ._tokenizer import Line

# Pattern to detect key: value on an item line or indented continuation.
_KV_RE = re.compile(r'^(?:[a-zA-Z0-9_\-]+|"(?:[^"\\]|\\.)*"): ')
_kv_match = _KV_RE.match

# §7.4 kind tags for the per-scope sigil lock.
_K_OBJECT = "object"
_K_ARRAY_SIGIL = "array_sigil"
_K_ARRAY_PROMOTED = "array_promoted"
_K_SCALAR_BARE = "scalar_bare"
_K_SCALAR_HEADING = "scalar_heading"


class JMDParseError(ValueError):
    """Structured parse error for JMD grammar violations.

    Attributes:
        kind: Stable error category.
        line: 1-based source line number of the offending occurrence.
        key: Key associated with the error, or an empty string when the
            violation is structural rather than key-specific.
        form: Advisory implementation detail for conflicting forms.
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
        """Initialize a structured parse error."""
        self.kind = kind
        self.line = line
        self.key = key
        self.form = form or {}
        if message is None:
            message = f"Line {line}: {kind} for key {key!r}"
        super().__init__(message)


def _is_object_item_content(content: str) -> bool:
    """Return whether array-item content starts with a key-value field."""
    return bool(_kv_match(content))


def _is_indent_field(raw_text: str) -> tuple[bool, str, str] | None:
    """Recognize a two-space-or-greater continuation field.

    Args:
        raw_text: Original source line, including indentation.

    Returns:
        ``(True, key_part, value_part)`` for a continuation field, otherwise
        ``None``.
    """
    if len(raw_text) < 2 or raw_text[0] != " " or raw_text[1] != " ":
        return None
    stripped = raw_text.lstrip(" ")
    if _kv_match(stripped):
        key_part, val_part = split_kv(stripped) or (stripped, "")
        return True, key_part, val_part
    return None


def parse_block_scalar_from(
    lines: list[Line],
    pos: int,
    *,
    folded: bool,
) -> tuple[str, int]:
    """Parse a tolerated YAML-style block scalar starting at ``pos``.

    Args:
        lines: Tokenized source lines.
        pos: Position immediately after the block-scalar opener.
        folded: Whether to fold consecutive non-blank lines.

    Returns:
        Decoded value and the position after the consumed block.
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
        if not raw or raw[0] != " ":
            break
        actual_indent = len(raw) - len(raw.lstrip(" "))
        if actual_indent < 2:
            break
        if indent_strip is None:
            indent_strip = actual_indent
        if actual_indent < indent_strip:
            break
        parts.append(raw[indent_strip:])
        pos += 1

    while parts and parts[-1] == "":
        parts.pop()
    if not folded:
        return "\n".join(parts), pos

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


def parse_blockquote_from(lines: list[Line], pos: int) -> tuple[str, int]:
    """Parse consecutive blockquote lines into a multiline string.

    Args:
        lines: Tokenized source lines.
        pos: Position of the first blockquote line.

    Returns:
        Decoded value and the position after the blockquote.
    """
    parts: list[str] = []
    while pos < len(lines):
        line = lines[pos]
        if line.heading_depth != 0:
            break
        raw = line.raw_text.strip()
        if raw == ">":
            parts.append("")
            pos += 1
        elif raw.startswith("> "):
            parts.append(raw[2:])
            pos += 1
        else:
            break
    return "\n".join(parts).rstrip("\n"), pos


def set_object_heading(
    obj: dict[str, Any],
    kinds: dict[str, str],
    key: str,
    value: dict[str, Any],
    line: int,
) -> dict[str, Any]:
    """Apply §7.4 promotion for an object heading."""
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
            kind="sigil_conflict",
            line=line,
            key=key,
            form={"existing": existing_kind, "new": _K_OBJECT},
        )
    raise JMDParseError(
        kind="repeated_scalar_key",
        line=line,
        key=key,
        form={"existing": existing_kind, "new": _K_OBJECT},
    )


def set_array_sigil(
    obj: dict[str, Any],
    kinds: dict[str, str],
    key: str,
    value: list[Any],
    line: int,
) -> None:
    """Apply §7.4 kind locking for an explicit array heading."""
    existing_kind = kinds.get(key)
    if existing_kind is None:
        obj[key] = value
        kinds[key] = _K_ARRAY_SIGIL
        return
    if existing_kind == _K_ARRAY_SIGIL:
        raise JMDParseError(
            kind="repeated_explicit_array",
            line=line,
            key=key,
            form={"existing": existing_kind, "new": _K_ARRAY_SIGIL},
        )
    if existing_kind in (_K_OBJECT, _K_ARRAY_PROMOTED):
        raise JMDParseError(
            kind="sigil_conflict",
            line=line,
            key=key,
            form={"existing": existing_kind, "new": _K_ARRAY_SIGIL},
        )
    raise JMDParseError(
        kind="repeated_scalar_key",
        line=line,
        key=key,
        form={"existing": existing_kind, "new": _K_ARRAY_SIGIL},
    )


def set_scalar(
    obj: dict[str, Any],
    kinds: dict[str, str],
    key: str,
    value: Any,
    line: int,
    *,
    is_heading: bool,
) -> None:
    """Apply §7.4 kind locking for a bare field or scalar heading."""
    new_kind = _K_SCALAR_HEADING if is_heading else _K_SCALAR_BARE
    existing_kind = kinds.get(key)
    if existing_kind is None:
        obj[key] = value
        kinds[key] = new_kind
        return
    if existing_kind == _K_ARRAY_SIGIL:
        raise JMDParseError(
            kind="sigil_conflict",
            line=line,
            key=key,
            form={"existing": existing_kind, "new": new_kind},
        )
    raise JMDParseError(
        kind="repeated_scalar_key",
        line=line,
        key=key,
        form={"existing": existing_kind, "new": new_kind},
    )
