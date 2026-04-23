# SPDX-License-Identifier: Apache-2.0
"""JMD Delete Documents (#- Label) — v0.3."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._parser import JMDParser
from ._tokenizer import tokenize


@dataclass
class JMDDelete:
    """A parsed JMD delete document (#-).

    Attributes:
        label:       Resource type label (e.g. ``'Order'``).
                     Empty string for bulk root arrays (``#- []``).
        identifiers: Identifier fields as a ``dict`` (single resource)
                     or ``list`` (bulk deletion).
        is_bulk:     ``True`` when the document uses ``#- []``.
    """

    label: str
    identifiers: Any
    is_bulk: bool


class JMDDeleteParser:
    r"""Parses JMD delete documents (``#-``) into :class:`JMDDelete` objects.

    A delete document reuses the data document body grammar — the ``#-`` root
    marker is the only syntactic difference from a data document.  The parser
    delegates body parsing to :class:`JMDParser` and wraps the result.

    Example::

        doc = JMDDeleteParser().parse("#- Order\nid: 42")
        assert doc.label == "Order"
        assert doc.identifiers == {"id": 42}
        assert not doc.is_bulk

        bulk = JMDDeleteParser().parse("#- []\n- abc123\n- def456")
        assert bulk.is_bulk
        assert bulk.identifiers == ["abc123", "def456"]
    """

    def parse(self, source: str) -> JMDDelete:
        """Parse a JMD delete document into a :class:`JMDDelete` object.

        Args:
            source: Complete JMD delete document text starting with ``#-``.

        Returns:
            A :class:`JMDDelete` instance.

        Raises:
            ValueError: If the document does not start with a ``#-`` marker.
        """
        lines = tokenize(source)

        first = None
        for line in lines:
            if line.heading_depth > 0:
                first = line
                break

        if first is None or first.heading_depth != 1:
            raise ValueError(
                "Delete document must start with a '#-' root marker"
            )

        if not first.content.startswith("- "):
            raise ValueError(
                "Expected '#-' root marker, got heading content: "
                f"{first.content!r}"
            )

        label_part = first.content[2:].strip()
        is_bulk = label_part == "[]" or label_part.endswith("[]")
        label = (
            ""
            if label_part == "[]"
            else label_part[:-2]
            if is_bulk
            else label_part
        )

        body = JMDParser().parse(source)

        return JMDDelete(label=label, identifiers=body, is_bulk=is_bulk)
