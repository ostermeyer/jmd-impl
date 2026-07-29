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


@dataclass(frozen=True)
class ScannedHeader:
    """Frontmatter plus the untouched body slice for the C fast path."""

    mode: Mode
    label: str
    frontmatter: dict[str, Any]
    body: str
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


def scan_document_header(source: str) -> ScannedHeader:
    """Scan only the envelope prefix for the C parser fast path.

    The returned body starts byte-for-byte at the first line whose first
    non-whitespace character is ``#``. The C parser owns body tokenization and
    structural validation; Python parses only the lenient frontmatter prefix
    and extracts the root mode and label needed by :class:`Envelope`.

    Args:
        source: Complete JMD document text.

    Returns:
        Parsed frontmatter and an untouched body slice with its source line.

    Raises:
        JMDParseError: If the prefix contains a lone carriage return or no
            candidate root heading exists.
        ValueError: If the document is empty or the candidate root does not
            use a depth-one heading form.
    """
    source = source.removeprefix("\ufeff")
    if not source:
        raise ValueError("Empty document")

    body_offset, body_line = _find_root_boundary(source)
    frontmatter_text = source[:body_offset]
    frontmatter, _ = _parse_frontmatter(tokenize(frontmatter_text))
    body = source[body_offset:]

    root_line = body.split("\n", 1)[0].removesuffix("\r")
    root_tokens = tokenize(root_line)
    if not root_tokens or root_tokens[0].heading_depth != 1:
        raise ValueError(
            f"Line {body_line}: expected '# <label>' or '# []'"
        )
    mode, label = split_mode_label(root_tokens[0].content)
    return ScannedHeader(
        mode=mode,
        label=label,
        frontmatter=frontmatter,
        body=body,
        body_line=body_line,
    )


def _find_root_boundary(source: str) -> tuple[int, int]:
    """Return the candidate root offset and line while validating the prefix."""
    line_start = 0
    line_number = 1
    while True:
        newline = source.find("\n", line_start)
        line_end = len(source) if newline < 0 else newline
        raw_line = source[line_start:line_end]
        if raw_line.endswith("\r"):
            candidate = raw_line[:-1]
        else:
            carriage_return = raw_line.find("\r")
            if carriage_return >= 0:
                raise JMDParseError(
                    kind="lone_carriage_return",
                    line=line_number,
                    key="",
                )
            candidate = raw_line

        if candidate.lstrip(" \t").startswith("#"):
            return line_start, line_number
        if newline < 0:
            raise JMDParseError(
                kind="no_root_heading",
                line=1,
                key="",
            )
        line_start = newline + 1
        line_number += 1


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
    _validate_root_heading(first)
    _validate_single_document(lines, pos)

    mode, label = split_mode_label(first.content)
    return ParsedHeader(
        mode=mode,
        label=label,
        frontmatter=frontmatter,
        lines=lines,
        root_pos=pos,
        body_line=first.number,
    )


def _validate_root_heading(root: Line) -> None:
    """Reject an indented or anonymous document root."""
    if root.raw_text.startswith((" ", "\t")) or root.content == "":
        raise JMDParseError(
            kind="no_root_heading",
            line=root.number,
            key="",
        )


def _validate_single_document(lines: list[Line], root_pos: int) -> None:
    """Reject a second root or mode marker after the document root."""
    root_content = lines[root_pos].content
    root_is_array = root_content == "[]" or root_content.endswith("[]")
    for line in lines[root_pos + 1 :]:
        if line.heading_depth != 1 or line.content == "":
            continue
        raw = line.raw_text.lstrip(" \t")
        if raw.startswith(("#? ", "#! ", "#- ")):
            raise JMDParseError(
                kind="mode_marker_mid_document",
                line=line.number,
                key="",
            )
        if root_is_array and (
            line.content == "-" or line.content.startswith("- ")
        ):
            continue
        raise JMDParseError(
            kind="second_root_heading",
            line=line.number,
            key="",
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
