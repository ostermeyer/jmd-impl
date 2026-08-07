# SPDX-License-Identifier: Apache-2.0
"""Bounded multiline and structural state for incremental JMD parsing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ._parser_common import (
    _K_ARRAY_PROMOTED,
    _K_ARRAY_SIGIL,
    _K_OBJECT,
    JMDParseError,
)
from ._scalars import split_kv
from ._stream_events import StreamEvent

_ScopeKind = Literal["doc", "object", "array", "item"]


@dataclass
class Scope:
    """One open structural scope and its per-object kind lock."""

    kind: _ScopeKind
    key: str | None
    depth: int
    kinds: dict[str, str] = field(default_factory=dict)


@dataclass
class Blockquote:
    """One canonical blockquote field currently receiving content."""

    key: str
    frontmatter: bool = False
    parts: list[str] = field(default_factory=list)


@dataclass
class BlockScalar:
    """One tolerated YAML-style block scalar currently receiving lines."""

    key: str
    folded: bool
    parts: list[str] = field(default_factory=list)
    indent: int | None = None


def split_field(content: str) -> tuple[str, str] | None:
    """Return a field's raw key and value, including the empty-value form."""
    pair = split_kv(content)
    if pair is not None:
        return pair
    if len(content) > 1 and content.endswith(":"):
        return content[:-1], ""
    return None


def fold_block_lines(parts: list[str]) -> str:
    """Apply the JMD section 5.2 folded-block tolerance."""
    output: list[str] = []
    paragraph: list[str] = []
    for part in parts:
        if part:
            paragraph.append(part)
            continue
        if paragraph:
            output.append(" ".join(paragraph))
            paragraph = []
        output.append("\n")
    if paragraph:
        output.append(" ".join(paragraph))
    return "".join(output)


def register_key(
    scopes: list[Scope],
    key: str,
    new_kind: str,
    line: int,
) -> None:
    """Apply section 7.4 kind locks to the nearest key-owning scope."""
    for scope in reversed(scopes):
        if scope.kind not in ("doc", "object", "item"):
            continue
        existing = scope.kinds.get(key)
        if existing is None:
            scope.kinds[key] = new_kind
            return
        if existing == _K_OBJECT and new_kind == _K_OBJECT:
            scope.kinds[key] = _K_ARRAY_PROMOTED
            return
        if existing == _K_ARRAY_PROMOTED and new_kind == _K_OBJECT:
            return
        if existing == _K_ARRAY_SIGIL and new_kind == _K_ARRAY_SIGIL:
            _raise_key_error(
                "repeated_explicit_array", line, key, existing, new_kind
            )
        if _K_ARRAY_SIGIL in (existing, new_kind):
            _raise_key_error("sigil_conflict", line, key, existing, new_kind)
        _raise_key_error("repeated_scalar_key", line, key, existing, new_kind)
    raise JMDParseError(kind="invalid_structure", line=line, key=key)


def reset_scopes(scopes: list[Scope], events: list[StreamEvent]) -> None:
    """Close every scope above the root after a semantic blank line."""
    # §18.2: a blank line returns to the root (§7.2a); it does not end the
    # document, so the root survives and is closed only before
    # DOCUMENT_END. The root is always scopes[0] — testing for the "doc"
    # kind instead missed a root *array*, which is a plain array scope and
    # was being closed out from under the rest of the document.
    close_count = max(len(scopes) - 1, 0)
    if close_count == 0:
        return
    events.append(StreamEvent("SCOPE_RESET"))
    for _ in range(close_count):
        close_top_scope(scopes, events)


def close_scopes_to(
    scopes: list[Scope],
    target_depth: int,
    events: list[StreamEvent],
) -> None:
    """Close scopes at or deeper than the target heading depth."""
    while scopes and scopes[-1].depth >= target_depth:
        close_top_scope(scopes, events)


def close_current_item(
    scopes: list[Scope],
    depth: int,
    events: list[StreamEvent],
) -> None:
    """Close a same-depth item before a qualified successor item."""
    if (
        scopes
        and scopes[-1].kind == "item"
        and scopes[-1].depth == depth
    ):
        close_top_scope(scopes, events)


def close_top_scope(
    scopes: list[Scope],
    events: list[StreamEvent],
) -> None:
    """Close the innermost scope and emit its terminal event."""
    scope = scopes.pop()
    if scope.kind in ("doc", "object"):
        # "doc" is the root object scope; §18.2 closes it with a keyless
        # OBJECT_END, symmetric with the OBJECT_START that opened it.
        events.append(StreamEvent("OBJECT_END", key=scope.key))
    elif scope.kind == "array":
        events.append(StreamEvent("ARRAY_END", key=scope.key))
    elif scope.kind == "item":
        events.append(StreamEvent("ITEM_END"))


def has_open_array(scopes: list[Scope]) -> bool:
    """Return whether any array scope is open."""
    return any(scope.kind == "array" for scope in scopes)


def _raise_key_error(
    kind: str,
    line: int,
    key: str,
    existing: str,
    new_kind: str,
) -> None:
    """Raise one structured repeated-key error."""
    raise JMDParseError(
        kind=kind,
        line=line,
        key=key,
        form={"existing": existing, "new": new_kind},
    )
