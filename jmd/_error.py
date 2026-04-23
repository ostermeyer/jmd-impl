# SPDX-License-Identifier: Apache-2.0
"""JMD Error Documents (# Error) — v0.3."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from ._parser import JMDParser

_ERROR_LABEL = "Error"


@dataclass
class JMDErrorItem:
    """A single field-level validation error from an ``errors[]`` array.

    Attributes:
        field:  Path to the offending field (e.g. ``'items[0].qty'``).
        reason: Machine- or human-readable cause.
        value:  The rejected value as a string.
        extra:  Any additional fields present in the error item.
    """

    field: str
    reason: str
    value: str
    extra: dict[str, Any] = dc_field(default_factory=dict)


@dataclass
class JMDError:
    """A parsed JMD error document (``# Error``).

    Attributes:
        status:     Numeric status code (e.g. HTTP status).
        code:       Machine-readable error identifier in snake_case.
        message:    Human-readable error description.
        suggestion: Free-form remediation hint.
        context:    Free-form additional context.
        errors:     Field-level validation errors from an ``errors[]`` array.
        extra:      Any additional root-level fields present in the document.
    """

    status: int | None = None
    code: str | None = None
    message: str | None = None
    suggestion: str | None = None
    context: str | None = None
    errors: list[JMDErrorItem] = dc_field(default_factory=list)
    extra: dict[str, Any] = dc_field(default_factory=dict)


def is_error_document(source: str) -> bool:
    """Return ``True`` if *source* is a JMD error document (``# Error``).

    Inspects only the first non-blank line; does not parse the full document.

    Args:
        source: JMD document text.

    Returns:
        ``True`` if the document starts with ``# Error``.
    """
    for line in source.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped == "# Error"
    return False


def parse_error(source: str) -> JMDError:
    """Parse a JMD error document into a :class:`JMDError` object.

    Args:
        source: Complete JMD error document text starting with ``# Error``.

    Returns:
        A :class:`JMDError` instance.

    Raises:
        ValueError: If *source* is not a JMD error document.
    """
    if not is_error_document(source):
        raise ValueError("Document is not a JMD error document (# Error)")

    raw = JMDParser().parse(source)
    if not isinstance(raw, dict):
        raise ValueError("Error document body must be an object")

    # Pop known fields; remainder goes into extra
    status_raw = raw.pop("status", None)
    status = int(status_raw) if status_raw is not None else None

    err_items: list[JMDErrorItem] = []
    raw_errors = raw.pop("errors", [])
    if isinstance(raw_errors, list):
        for item in raw_errors:
            if isinstance(item, dict):
                item_copy = dict(item)
                err_items.append(JMDErrorItem(
                    field=str(item_copy.pop("field", "")),
                    reason=str(item_copy.pop("reason", "")),
                    value=str(item_copy.pop("value", "")),
                    extra=item_copy,
                ))

    return JMDError(
        status=status,
        code=raw.pop("code", None),
        message=raw.pop("message", None),
        suggestion=raw.pop("suggestion", None),
        context=raw.pop("context", None),
        errors=err_items,
        extra=raw,
    )
