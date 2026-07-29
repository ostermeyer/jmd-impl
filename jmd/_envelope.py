# SPDX-License-Identifier: Apache-2.0
"""Canonical JMD parser envelope per spec §3.6.

A uniform shape returned by every JMD parser regardless of document
mode. Applications that need only the body inspect ``value``;
document-level metadata is read from ``mode``, ``label``, and
``frontmatter``. Per §3.6 the envelope is the single entry point of
the parser API — no information about the document is conveyed
through side channels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Mode = Literal["data", "schema", "query", "delete"]

_VALID_MODES: frozenset[str] = frozenset({"data", "schema", "query", "delete"})


@dataclass(kw_only=True)
class Envelope:
    """Canonical JMD parse result (§3.6).

    Attributes:
        mode: One of ``"data"``, ``"schema"``, ``"query"``, ``"delete"``
            — derived from the root marker per §3.6.1.
        label: Root heading label, with mode-mark and any trailing
            ``[]`` sigil stripped. The empty string is valid only for a
            sigil-only root array such as ``# []`` or ``#- []``; object roots
            require a non-empty label.
            ``list`` for array roots. Never ``None`` for a validly
            parsed document.
        frontmatter: Map of frontmatter fields preserved verbatim from
            before the root heading. The empty dict ``{}`` when no
            frontmatter is present — never ``None``, never absent.
    """

    mode: Mode
    label: str
    value: Any
    frontmatter: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mode not in _VALID_MODES:
            raise ValueError(
                "Envelope.mode must be one of "
                f"{sorted(_VALID_MODES)!r}; got {self.mode!r}"
            )


def split_mode_label(heading_content: str) -> tuple[Mode, str]:
    """Split a tokenized root-heading content into ``(mode, label)``.

    The tokenizer encodes the four root markers as a leading sigil
    plus a space:

    =================  ==================
    Source heading     Tokenized content
    =================  ==================
    ``# Order``        ``"Order"``
    ``# []``           ``"[]"``
    ``#? Order``       ``"? Order"``
    ``#! Order``       ``"! Order"``
    ``#- Order``       ``"- Order"``
    ``#- []``          ``"- []"``
    =================  ==================

    The returned label has any trailing ``[]`` sigil stripped — the
    array nature of the body is carried by ``value`` being a list,
    per §3.6.1.

    Args:
        heading_content: ``Line.content`` of the first heading line
            as returned by :func:`jmd._tokenizer.tokenize`.

    Returns:
        ``(mode, bare_label)``.
    """
    if heading_content.startswith("? "):
        return "query", _strip_array_sigil(heading_content[2:])
    if heading_content.startswith("! "):
        return "schema", _strip_array_sigil(heading_content[2:])
    if heading_content.startswith("- "):
        return "delete", _strip_array_sigil(heading_content[2:])
    return "data", _strip_array_sigil(heading_content)


def _strip_array_sigil(label: str) -> str:
    """Strip a trailing ``[]`` from a label, if present."""
    return label[:-2] if label.endswith("[]") else label


_MODE_TO_LABEL_PREFIX: dict[Mode, str] = {
    "data": "",
    "schema": "! ",
    "query": "? ",
    "delete": "- ",
}


def mode_to_label_prefix(mode: Mode) -> str:
    """Return the label-prefix carrying a mode mark for the serializer.

    The reference serializer accepts the mode as a prefix on the label
    argument (e.g. ``"- Order"`` for a delete document). This helper
    is the inverse of :func:`split_mode_label` for that interface.
    """
    return _MODE_TO_LABEL_PREFIX[mode]
