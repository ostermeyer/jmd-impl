"""JMD (JSON Markdown) — Parser, Serializer, and Tooling.

Implements JMD Specification v0.3 — heading-scope model with blockquotes
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
from ._error import JMDError, JMDErrorItem, is_error_document, parse_error
from ._html import JMDHTMLRenderer
from ._parser import JMDParser
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
from ._serializer import JMDSerializer
from ._streaming import StreamEvent, jmd_stream
from ._tokenizer import Line, tokenize

# ---------------------------------------------------------------------------
# C extension detection — done once at import time, not on every call
# ---------------------------------------------------------------------------

try:
    from ._cparser import parse as _c_parse
    _HAS_CPARSER: bool = True
except ImportError:
    _HAS_CPARSER = False

try:
    from ._cserializer import serialize as _c_serialize
    _HAS_CSERIALIZER: bool = True
except ImportError:
    _HAS_CSERIALIZER = False


# ---------------------------------------------------------------------------
# Public API — C-accelerated by default, Python fallback
# ---------------------------------------------------------------------------

def parse(source: str) -> Any:
    """Parse a JMD document into a Python value.

    Uses the C-accelerated parser if available; falls back to the pure-Python
    :class:`JMDParser` otherwise.

    Args:
        source: Complete JMD document text.

    Returns:
        A Python ``dict``, ``list``, or scalar value.
    """
    if _HAS_CPARSER:
        return _c_parse(source)
    return JMDParser().parse(source)


def serialize(
    data: Any,
    label: str = "Document",
    frontmatter: dict[str, Any] | None = None,
) -> str:
    """Serialize a Python value to a JMD document string.

    Uses the C-accelerated serializer if available; falls back to the
    pure-Python :class:`JMDSerializer` otherwise.  The mode marker is
    carried as a prefix on ``label`` (e.g. ``"- Order"`` for delete,
    ``"? Order"`` for query, ``"! Order"`` for schema); plain data
    documents pass the label unadorned.

    Args:
        data:        Python ``dict``, ``list``, or scalar value.
        label:       Root heading label, optionally mode-prefixed.
        frontmatter: Optional mapping of frontmatter keys to values,
            emitted above the root heading separated by one blank line
            (§3.5).  A value of ``True`` produces a bare key line.

    Returns:
        A JMD document string.
    """
    if _HAS_CSERIALIZER:
        body = str(_c_serialize(data, label))
    else:
        body = JMDSerializer().serialize(data, label=label)
    if not frontmatter:
        return body
    from ._scalars import quote_key, serialize_scalar
    lines: list[str] = []
    for k, v in frontmatter.items():
        if v is True:
            lines.append(quote_key(k))
        else:
            lines.append(f"{quote_key(k)}: {serialize_scalar(v)}")
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


def jmd_mode(source: str) -> str:
    """Detect the document mode of a JMD source string.

    Inspects only the first non-blank heading line; does not parse the full
    document.

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
                    return mode
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
