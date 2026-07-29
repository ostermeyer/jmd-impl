# SPDX-License-Identifier: Apache-2.0
"""Event vocabulary for incremental JMD parsing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._envelope import Mode


@dataclass
class StreamEvent:
    """A semantic event emitted for one completed JMD construct.

    DOCUMENT_START alone carries the envelope header. Body events use key and
    value as applicable; FIELD_CONTENT stores its current content line in
    value.

    Attributes:
        type: Stable uppercase event type from JMD specification section 18.
        key: Field, object, array, or document label when applicable.
        value: Parsed scalar or incremental multiline content when applicable.
        mode: Document mode on DOCUMENT_START only.
        frontmatter: Complete frontmatter on DOCUMENT_START only.
    """

    type: str
    key: str | None = None
    value: Any = None
    mode: Mode | None = None
    frontmatter: dict[str, Any] | None = None

    def __repr__(self) -> str:
        """Return a compact diagnostic representation."""
        if self.type == "DOCUMENT_START":
            return (
                f"StreamEvent(DOCUMENT_START, mode={self.mode!r}, "
                f"label={self.key!r}, frontmatter={self.frontmatter!r})"
            )
        if self.value is not None:
            return (
                f"StreamEvent({self.type}, key={self.key!r}, "
                f"value={self.value!r})"
            )
        if self.key:
            return f"StreamEvent({self.type}, key={self.key!r})"
        return f"StreamEvent({self.type})"
