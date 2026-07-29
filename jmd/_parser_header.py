# SPDX-License-Identifier: Apache-2.0
"""Envelope-header and frontmatter parsing for JMD documents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._envelope import Mode, split_mode_label
from ._parser_common import JMDParseError, parse_blockquote_from
from ._scalars import parse_key, parse_scalar, split_kv
from ._tokenizer import Line, is_thematic_break, tokenize


@dataclass(frozen=True)
class ParsedHeader:
    """Parsed envelope header plus body-parser starting state."""

    mode: Mode
    label: str
    frontmatter: dict[str, Any]
    lines: list[Line]
    root_pos: int
    body_line: int


def normalize_document_source(source: str) -> str:
    """Validate line endings and consume one tolerated leading BOM."""
    for position, character in enumerate(source):
        if character != "\r":
            continue
        if position + 1 < len(source) and source[position + 1] == "\n":
            continue
        raise JMDParseError(
            kind="lone_carriage_return",
            line=source.count("\n", 0, position) + 1,
            key="",
        )
    return source.removeprefix("\ufeff")


def parse_document_header(source: str) -> ParsedHeader:
    """Parse frontmatter and the single root heading.

    Args:
        source: Complete JMD document text.

    Returns:
        Header values and tokenized state positioned at the root heading.

    Raises:
        ValueError: If the document is empty, lacks a root heading, or the
            first structural heading is not at depth one.
    """
    lines = tokenize(normalize_document_source(source))
    if not lines:
        raise ValueError("Empty document")

    frontmatter, pos = _parse_frontmatter(lines)
    if pos >= len(lines):
        raise ValueError("No root heading found")

    first = lines[pos]
    if first.heading_depth != 1:
        raise ValueError(
            f"Line {first.number}: expected '# <label>' or '# []'"
        )

    mode, label = split_mode_label(first.content)
    return ParsedHeader(
        mode=mode,
        label=label,
        frontmatter=frontmatter,
        lines=lines,
        root_pos=pos,
        body_line=first.number,
    )


def _parse_frontmatter(lines: list[Line]) -> tuple[dict[str, Any], int]:
    """Parse fields before the first heading and return the root position."""
    frontmatter: dict[str, Any] = {}
    pos = 0
    while pos < len(lines):
        line = lines[pos]
        if line.heading_depth > 0:
            break
        if line.heading_depth == -1 or is_thematic_break(line):
            pos += 1
            continue
        if ": " in line.content:
            key_part, val_part = split_kv(line.content) or (line.content, "")
            frontmatter[parse_key(key_part)] = parse_scalar(val_part)
            pos += 1
            continue
        if line.content.endswith(":") and ": " not in line.content:
            key = parse_key(line.content[:-1])
            pos += 1
            if _starts_blockquote(lines, pos):
                frontmatter[key], pos = parse_blockquote_from(lines, pos)
            else:
                frontmatter[key] = ""
            continue
        if (
            line.content
            and not line.content.startswith(">")
            and not line.content.startswith("- ")
        ):
            frontmatter[parse_key(line.content)] = True
            pos += 1
            continue
        break
    return frontmatter, pos


def _starts_blockquote(lines: list[Line], pos: int) -> bool:
    """Return whether ``pos`` identifies a top-level blockquote line."""
    if pos >= len(lines):
        return False
    line = lines[pos]
    return (
        line.heading_depth == 0
        and line.raw_text.strip().startswith(">")
    )
