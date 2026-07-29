# SPDX-License-Identifier: Apache-2.0
"""Public pure-Python JMD parser facade.

Envelope-header parsing, stateful body traversal, and shared grammar primitives
have separate module owners. This facade preserves the established
``JMDParser`` API and compatibility imports while coordinating those parts.
"""

from __future__ import annotations

from typing import Any

from ._envelope import Envelope, Mode
from ._parser_body import JMDBodyParser
from ._parser_common import (
    _K_ARRAY_PROMOTED,
    _K_ARRAY_SIGIL,
    _K_OBJECT,
    _K_SCALAR_BARE,
    _K_SCALAR_HEADING,
    _is_indent_field,
    _is_object_item_content,
    _kv_match,
    parse_block_scalar_from,
)
from ._parser_common import JMDParseError as JMDParseError
from ._parser_header import parse_document_header
from ._tokenizer import Line

# Compatibility re-exports: streaming, query, and schema code historically
# imported these private primitives from ``jmd._parser``. Their implementation
# now belongs to ``_parser_common``, but the old paths remain valid.
_COMPATIBILITY_EXPORTS = (
    _K_ARRAY_PROMOTED,
    _K_ARRAY_SIGIL,
    _K_OBJECT,
    _K_SCALAR_BARE,
    _K_SCALAR_HEADING,
    JMDParseError,
    _is_indent_field,
    _is_object_item_content,
    _kv_match,
    parse_block_scalar_from,
)


class JMDParser:
    """Parse JMD documents into canonical envelopes using Python only.

    Backend dispatch belongs to :func:`jmd.parse`; this class never delegates
    body parsing to the C accelerator.
    """

    def __init__(self) -> None:
        """Initialize an empty parser instance."""
        self._lines: list[Line] = []
        self._pos = 0
        self.frontmatter: dict[str, Any] = {}

    def _line_no_at(self, pos: int) -> int:
        """Return the 1-based source line number at a parser position."""
        if 0 <= pos < len(self._lines):
            return self._lines[pos].number
        return 0

    def parse_header(
        self,
        source: str,
    ) -> tuple[Mode, str, dict[str, Any], int]:
        """Parse the envelope header without parsing the body.

        Args:
            source: Complete JMD document text.

        Returns:
            ``(mode, label, frontmatter, body_start_line)``. The instance is
            left tokenized and positioned at the root heading so body parsing
            can continue without repeating header work.
        """
        header = parse_document_header(source)
        self._lines = header.lines
        self._pos = header.root_pos
        self.frontmatter = dict(header.frontmatter)
        return (
            header.mode,
            header.label,
            dict(self.frontmatter),
            header.body_line,
        )

    def parse(self, source: str) -> Envelope:
        """Parse a complete JMD document into its canonical envelope.

        Args:
            source: Complete JMD document text.

        Returns:
            Envelope carrying mode, label, frontmatter, and parsed body.
        """
        mode, label, frontmatter, _ = self.parse_header(source)
        first = self._lines[self._pos]
        root_is_array = first.content == "[]" or first.content.endswith("[]")

        body_parser = JMDBodyParser(self._lines, self._pos + 1)
        value = body_parser.parse(root_is_array=root_is_array)
        self._pos = body_parser.pos

        return Envelope(
            mode=mode,
            label=label,
            value=value,
            frontmatter=frontmatter,
        )
