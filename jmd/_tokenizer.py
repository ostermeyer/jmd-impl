"""JMD line tokenizer.

Splits a JMD document into Line tokens with heading depth information.
"""

from __future__ import annotations

import re


class Line:
    """A single tokenized line from a JMD document."""

    __slots__ = ("number", "raw_text", "heading_depth", "content")

    def __init__(
        self,
        number: int,
        raw_text: str,
        heading_depth: int,
        content: str,
    ) -> None:
        self.number = number
        self.raw_text = raw_text
        self.heading_depth = heading_depth
        self.content = content


_HEADING_RE = re.compile(r"^(#{1,})\s+(.*)")
_ROOT_MARKER_RE = re.compile(r"^#([?!\-])\s+(.*)")
_THEMATIC_BREAK_RE = re.compile(r"^-{3,}$")
_BARE_HEADING_RE = re.compile(r"^(#{1,})$")


def is_thematic_break(line: Line) -> bool:
    """Check if a line is a thematic break (``---`` or more hyphens)."""
    return (
        line.heading_depth == 0
        and bool(_THEMATIC_BREAK_RE.match(line.content))
    )


# Pre-bind compiled pattern methods for hot-path use
_root_marker_match = _ROOT_MARKER_RE.match
_heading_match = _HEADING_RE.match
_bare_heading_match = _BARE_HEADING_RE.match


def _parse_line(number: int, raw: str, text: str) -> Line:
    """Parse a raw text line into a Line token.

    Determines heading depth (0 for non-heading lines) and strips the
    heading prefix from content.  Special root markers ``#?``, ``#!``,
    and ``#-`` are recognized as depth-1 headings with content
    ``? label``, ``! label``, and ``- label`` respectively.

    Args:
        number: 1-based line number.
        raw: Raw line text (without trailing newline).
        text: Stripped line text.

    Returns:
        A Line instance.
    """
    # Fast path: lines not starting with '#' are never headings
    if text[0] != '#':
        return Line(number, raw, 0, text)
    # Special root markers: #? Label, #! Label
    m = _root_marker_match(text)
    if m:
        return Line(number, raw, 1, f"{m.group(1)} {m.group(2)}")
    # Standard headings: # ... ## ... ### ...
    m = _heading_match(text)
    if m:
        return Line(number, raw, len(m.group(1)), m.group(2))
    # Bare heading markers (e.g. "# " stripped to "#", "##" etc.) — LLMs
    # sometimes omit the label.  Treat as a heading with an empty label.
    bare = _bare_heading_match(text)
    if bare:
        return Line(number, raw, len(bare.group(1)), "")
    return Line(number, raw, 0, text)


_BLANK_LINE = Line(0, "", -1, "")  # Sentinel for blank lines


def tokenize(source: str) -> list[Line]:
    """Tokenize a JMD source string into a list of Line objects.

    Blank lines are preserved as sentinel tokens (heading_depth == -1)
    so the parser can implement scope reset.

    Args:
        source: Complete JMD document text.

    Returns:
        List of Line tokens including blank-line sentinels.
    """
    result: list[Line] = []
    _result_append = result.append
    _line = Line
    _pl = _parse_line
    for i, raw in enumerate(source.splitlines()):
        text = raw.strip()
        if text:
            _result_append(_pl(i + 1, raw, text))
        else:
            _result_append(_line(i + 1, "", -1, ""))
    return result
