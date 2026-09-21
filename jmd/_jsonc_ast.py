# SPDX-License-Identifier: Apache-2.0
"""AST dataclasses shared by the jmd-over-jsonc submodules.

A document is a frontmatter dict plus a root section. Sections,
arrays, fields, and comments form the body's content sequence.
The AST is deliberately concrete (no abstract base hierarchy
beyond the tagged-union pattern) — a parser-flat representation
is easier to reason about than a polymorphic tree, especially in
round-trip code where we look up node kinds by tag.

This module imports nothing from its siblings: the AST is the
common foundation everyone else builds on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypeAlias


@dataclass
class Field:
    """A scalar key-value pair, e.g. ``tab_size: 4``."""

    key: str
    value: Any  # str, int, float, bool, None


@dataclass
class ScalarArray:
    """An array of scalars, emitted as ``key[]:`` + bullet items."""

    key: str
    items: list[Any] = field(default_factory=list)


@dataclass
class ObjectArray:
    """An array of objects, emitted as ``## key[]`` + bullets.

    Each item is a :class:`Section` whose children form the object's
    content sequence. Anonymous label — items in an array do not
    carry their own heading.
    """

    key: str
    items: list[Section] = field(default_factory=list)


@dataclass
class Section:
    """A nested object, emitted as ``## label`` heading + content.

    The root document's body is also a :class:`Section`; its label
    is the root heading label supplied by the caller of
    :func:`parse_jsonc`.
    """

    label: str
    children: list[Element] = field(default_factory=list)


@dataclass
class LineComment:
    """A single-line comment, sourced from JSONC ``// text``."""

    text: str


@dataclass
class BlockComment:
    """A multi-line block comment, sourced from JSONC ``/* … */``.

    ``body_lines`` carries the textual content split per source
    line, without the JSONC ``/*`` / ``*/`` framing or the JMD
    ``> `` prefix. Blank source lines come through as empty
    strings, preserving visual structure across the round-trip.
    """

    body_lines: list[str] = field(default_factory=list)


# Element is the tagged-union of everything that appears in a
# section's content sequence. We use a typing alias rather than a
# base class so the dataclasses stay flat and easy to introspect.
Element: TypeAlias = (
    Field | ScalarArray | ObjectArray | Section | LineComment | BlockComment
)


@dataclass
class JsoncDoc:
    """Top-level document: frontmatter + root section.

    Frontmatter is empty (``{}``) for documents originating from
    JSONC sources, since JSONC has no frontmatter slot. JMD-
    originating documents may carry frontmatter content; conformant
    JMD-text output always frames it with ``---`` delimiters per
    spec §4.0.
    """

    root: Section
    frontmatter: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "BlockComment",
    "Element",
    "Field",
    "JsoncDoc",
    "LineComment",
    "ObjectArray",
    "ScalarArray",
    "Section",
]
