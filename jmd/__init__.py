# SPDX-License-Identifier: Apache-2.0
"""JMD (JSON Markdown) — Parser, Serializer, and Tooling.

Implements JMD Specification v0.3.3 — heading-scope model with blockquotes
and indentation continuation.

Usage:
    python -m jmd                          # demo + roundtrip test
    python -m jmd to-json input.jmd        # pretty-print JSON
    python -m jmd from-json input.json     # convert JSON to JMD
    python -m jmd render input.jmd         # render HTML to stdout
    python -m jmd roundtrip input.jmd      # JMD -> JSON -> JMD, assert lossless

    As a library:
        from jmd import parse, serialize
        data = parse(text)
        jmd  = serialize(data, label="Order")
"""

from __future__ import annotations

from typing import Any

from ._cli import (
    SAMPLE_JMD,
    SAMPLE_QUERY,
    SAMPLE_RECORDS,
    SAMPLE_SCHEMA,
    dict_to_jmd,
    jmd_parse_schema,
    jmd_query,
    jmd_schema_to_json_schema,
    jmd_to_dict,
    jmd_to_json,
    json_schema_to_jmd_schema,
    json_to_jmd,
)
from ._delete import JMDDelete, JMDDeleteParser
from ._envelope import Envelope, Mode, mode_to_label_prefix
from ._error import JMDError, JMDErrorItem, is_error_document, parse_error
from ._html import JMDHTMLRenderer
from ._parser import JMDParser
from ._parser_header import normalize_document_source
from ._query import (
    Condition,
    JMDQuery,
    JMDQueryExecutor,
    JMDQueryParser,
    QueryArray,
    QueryField,
    QueryObject,
)
from ._scalars import parse_key, parse_scalar, quote_key, serialize_scalar
from ._schema import (
    JMDSchema,
    JMDSchemaParser,
    SchemaArray,
    SchemaField,
    SchemaObject,
    SchemaRef,
)
from ._serializer import JMDSerializer, validate_label
from ._streaming import (
    JMDStreamParser,
    StreamEvent,
    jmd_stream,
    to_lines,
)
from ._streaming import (
    events as stream_events,
)
from ._tokenizer import Line, tokenize

# ---------------------------------------------------------------------------
# C extension detection — done once at import time, not on every call
# ---------------------------------------------------------------------------

try:
    from ._cparser import parse as _c_parse_body
    _HAS_CPARSER: bool = True
except ImportError:
    _HAS_CPARSER = False

try:
    from ._cserializer import serialize as _c_serialize
    _HAS_CSERIALIZER: bool = True
except ImportError:
    _HAS_CSERIALIZER = False


# ---------------------------------------------------------------------------
# Public API — explicit backend dispatch at the API boundary
#
# Both backends are observable from outside:
#
#   * :func:`parse` picks the C-accelerated body parser if compiled,
#     and falls back to :class:`JMDParser` (pure Python) otherwise.
#     The choice is visible in the ``_HAS_CPARSER`` flag.
#
#   * :class:`JMDParser` is a Python-only guarantee — it never
#     delegates to C. Use it directly for debugging, deterministic
#     fallback in tests, or any context where the C path is
#     undesirable.
#
# This is a deliberate change from earlier versions that hid a
# C-dispatch inside ``JMDParser.parse``: ``Python ist Python und C
# ist C — keine verdeckten Operationen``.
# ---------------------------------------------------------------------------

def parse(source: str) -> Envelope:
    """Parse a JMD document into a canonical :class:`Envelope` (§3.6).

    The envelope carries ``mode``, ``label``, ``frontmatter``, and the
    parsed body in ``value`` — the single entry point of the parser
    API. Applications that need only the body inspect ``envelope.value``.

    Backend selection happens here and is observable via the
    module-level ``_HAS_CPARSER`` flag: when the C accelerator is
    importable, body parsing is delegated to it; otherwise the pure-
    Python :class:`JMDParser` handles the entire document. Header
    extraction (tokenize + frontmatter + mode/label) is always Python.

    Args:
        source: Complete JMD document text.

    Returns:
        An :class:`Envelope` with mode, label, frontmatter, and value.
    """
    if _HAS_CPARSER:
        return _parse_with_c_body(source)
    return JMDParser().parse(source)


def _parse_with_c_body(source: str) -> Envelope:
    """Parse using the C body accelerator (envelope header is Python).

    The Python parser handles tokenization, frontmatter, and root-
    heading extraction (cheap and unavoidable — the C accelerator
    parses bodies only, not full documents). The body slice from the
    root heading onwards is then passed to the C ``parse`` function.
    Both parts are assembled into a canonical :class:`Envelope`.
    """
    source = normalize_document_source(source)
    parser = JMDParser()
    mode, label, frontmatter, body_line = parser.parse_header(source)
    body = "\n".join(source.splitlines()[body_line - 1:])
    value = _c_parse_body(body)
    return Envelope(
        mode=mode,
        label=label,
        value=value,
        frontmatter=frontmatter,
    )


def serialize(
    obj: Envelope | Any,
    label: str = "Document",
    frontmatter: dict[str, Any] | None = None,
) -> str:
    """Serialize an :class:`Envelope` (canonical) or a value to JMD.

    Canonical form per §3.6.3 takes an :class:`Envelope` directly::

        serialize(envelope) -> str

    For an :class:`Envelope` input, the ``label`` and ``frontmatter``
    keyword arguments are ignored — they are taken from the envelope.

    Convenience form for callers that have not adopted the envelope::

        serialize(value, label="Order", frontmatter={"page": 1})

    The mode marker may be carried as a prefix on ``label``
    (e.g. ``"- Order"`` for delete, ``"? Order"`` for query,
    ``"! Order"`` for schema); plain data documents pass the label
    unadorned.

    Args:
        obj: An :class:`Envelope` or a raw body value (``dict``,
            ``list``, or scalar).
        label: Root heading label, optionally mode-prefixed
            (convenience form only).
        frontmatter: Optional mapping of frontmatter keys to values,
            emitted above the root heading separated by one blank line
            (§3.5). A value of ``True`` produces a bare key line.
            (Convenience form only.)

    Returns:
        A JMD document string.
    """
    if isinstance(obj, Envelope):
        return _serialize_internal(
            obj.value,
            label=mode_to_label_prefix(obj.mode) + obj.label,
            frontmatter=obj.frontmatter or None,
        )
    return _serialize_internal(obj, label=label, frontmatter=frontmatter)


def _serialize_internal(
    data: Any,
    *,
    label: str,
    frontmatter: dict[str, Any] | None,
) -> str:
    """Shared body for :func:`serialize` (both envelope and convenience)."""
    # D11: validate/normalize label at the public entry point so both
    # the C-accelerated and pure-Python paths behave consistently.
    label = validate_label(label)
    if _HAS_CSERIALIZER:
        body = str(_c_serialize(data, label))
    else:
        body = JMDSerializer().serialize(data, label=label)
    if not frontmatter:
        return body
    from ._scalars import quote_key, serialize_scalar
    lines: list[str] = []
    for k, v in frontmatter.items():
        qk = quote_key(k)
        if v is True:
            lines.append(qk)
        elif isinstance(v, str) and "\n" in v:
            # D12: multi-line values go in the blockquote form, matching
            # the body serializer's handling of multi-line scalars (§9.1).
            lines.append(f"{qk}:")
            for part in v.split("\n"):
                lines.append(">" if part == "" else f"> {part}")
        else:
            lines.append(f"{qk}: {serialize_scalar(v)}")
    lines.append("")  # blank line separating frontmatter from heading
    lines.append(body)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Document mode detection
# ---------------------------------------------------------------------------

_MODE_PREFIXES = {
    "? ": "query",
    "! ": "schema",
    "- ": "delete",
}


def jmd_mode(source: str) -> Mode:
    """Detect the document mode of a JMD source string.

    Inspects only the first non-blank heading line; does not parse the
    full document. Equivalent to ``parse(source).mode`` but cheaper —
    no body is parsed.

    Args:
        source: JMD document text.

    Returns:
        One of ``'data'``, ``'query'``, ``'schema'``, or ``'delete'``.
        Returns ``'data'`` for ``# Error`` documents (error documents are
        standard data documents with a reserved label).
    """
    lines = tokenize(source)
    for line in lines:
        if line.heading_depth == 1:
            for prefix, mode in _MODE_PREFIXES.items():
                if line.content.startswith(prefix):
                    return mode  # type: ignore[return-value]
            return "data"
    return "data"


__all__ = [
    # Tokenizer
    "Line",
    "tokenize",
    # Scalars
    "parse_scalar",
    "parse_key",
    "serialize_scalar",
    "quote_key",
    # Envelope (§3.6)
    "Envelope",
    "Mode",
    # Parser & Serializer (Python classes)
    "JMDParser",
    "JMDSerializer",
    # Top-level API (C-accelerated by default)
    "parse",
    "serialize",
    # Mode detection
    "jmd_mode",
    # HTML
    "JMDHTMLRenderer",
    # Streaming
    "StreamEvent",
    "jmd_stream",
    "JMDStreamParser",
    "stream_events",
    "to_lines",
    # QBE
    "Condition",
    "QueryField",
    "QueryObject",
    "QueryArray",
    "JMDQuery",
    "JMDQueryParser",
    "JMDQueryExecutor",
    # Schema
    "SchemaField",
    "SchemaObject",
    "SchemaArray",
    "SchemaRef",
    "JMDSchema",
    "JMDSchemaParser",
    # Delete
    "JMDDelete",
    "JMDDeleteParser",
    # Error
    "JMDError",
    "JMDErrorItem",
    "is_error_document",
    "parse_error",
    # Convenience functions
    "jmd_to_json",
    "json_to_jmd",
    "jmd_to_dict",
    "dict_to_jmd",
    "jmd_query",
    "jmd_parse_schema",
    "jmd_schema_to_json_schema",
    "json_schema_to_jmd_schema",
    # Sample data
    "SAMPLE_JMD",
    "SAMPLE_QUERY",
    "SAMPLE_SCHEMA",
    "SAMPLE_RECORDS",
]
