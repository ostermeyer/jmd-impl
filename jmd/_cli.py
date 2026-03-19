"""JMD CLI commands and sample data."""

from __future__ import annotations

import difflib
import json
import sys
from typing import Any

from ._parser import JMDParser
from ._serializer import JMDSerializer
from ._html import JMDHTMLRenderer
from ._streaming import jmd_stream
from ._query import JMDQueryParser, JMDQueryExecutor
from ._schema import JMDSchemaParser


# ---------------------------------------------------------------------------
# Public API — top-level convenience functions
# ---------------------------------------------------------------------------

def jmd_to_json(jmd_source: str, indent: int = 2) -> str:
    """Parse JMD source and return a formatted JSON string."""
    data = JMDParser().parse(jmd_source)
    return json.dumps(data, indent=indent, ensure_ascii=False)


def json_to_jmd(json_source: str, label: str = "Document") -> str:
    """Parse a JSON string and return a JMD document."""
    data = json.loads(json_source)
    return JMDSerializer().serialize(data, label=label)


def jmd_to_dict(jmd_source: str) -> Any:
    """Parse JMD source and return a Python dict or list."""
    return JMDParser().parse(jmd_source)


def dict_to_jmd(data: Any, label: str = "Document") -> str:
    """Serialize a Python dict or list to a JMD document."""
    return JMDSerializer().serialize(data, label=label)


def jmd_query(query_source: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Execute a JMD QBE query against a list of dicts."""
    q = JMDQueryParser().parse(query_source)
    return JMDQueryExecutor().execute(q, records)


def jmd_parse_schema(schema_source: str) -> Any:
    """Parse a JMD Schema document into a JMDSchema object."""
    return JMDSchemaParser().parse(schema_source)


def jmd_schema_to_json_schema(schema_source: str) -> str:
    """Convert a JMD Schema document to a JSON Schema string."""
    schema = JMDSchemaParser().parse(schema_source)
    return json.dumps(schema.to_json_schema(), indent=2, ensure_ascii=False)


def json_schema_to_jmd_schema(json_schema_source: str) -> str:
    """Convert a JSON Schema string to a JMD Schema document."""
    from ._schema import json_schema_to_jmd_schema as _convert
    return _convert(json_schema_source)


# ---------------------------------------------------------------------------
# Sample data (v0.2 syntax)
# ---------------------------------------------------------------------------

SAMPLE_JMD = """\
# Order
id: 42
status: "pending"
paid: false
notes: null
description:
> Ships within 2 business days
> from our central warehouse.
>
> Handle with care.

## address
street: Hauptstraße 1
city: Berlin
zip: "10115"
### geo
lat: 52.52
lng: 13.40

## tags[]
- express
- fragile

## items[]
- sku: A1
  qty: 2
  price: 29.99
- sku: B3
  qty: 1
  price: 24.99

## matrix[]
### []
- 1
- 2
### []
- 3
- 4

## total: 84.99
"""

SAMPLE_QUERY = """\
#? Order
status: pending|processing
total: >50.0
## tags[]
- express
## items[]
- sku: ?
  qty: ?
  price: >10.0
## address
city: ?
?: ?
"""

SAMPLE_SCHEMA = """\
#! Order
id: integer
status: string(pending|active|shipped|cancelled)
total: number
paid: boolean
notes: string?
description: string?

## address
street: string
city: string
zip: string
### geo
lat: number
lng: number

## tags[]: string

## items[]: object
- sku: string
  qty: integer
  price: number
"""

SAMPLE_RECORDS: list[dict[str, Any]] = [
    {
        "id": 1, "status": "pending", "total": 84.99,
        "tags": ["express", "fragile"],
        "items": [
            {"sku": "A1", "qty": 2, "price": 29.99},
            {"sku": "B3", "qty": 1, "price": 8.00},
        ],
        "address": {
            "city": "Berlin", "street": "Hauptstr. 1", "zip": "10115",
        },
    },
    {
        "id": 2, "status": "shipped", "total": 120.00,
        "tags": ["express"],
        "items": [{"sku": "C2", "qty": 1, "price": 120.00}],
        "address": {
            "city": "Hamburg", "street": "Allee 5", "zip": "20095",
        },
    },
    {
        "id": 3, "status": "processing", "total": 35.00,
        "tags": ["express"],
        "items": [{"sku": "D4", "qty": 3, "price": 11.66}],
        "address": {
            "city": "München", "street": "Marienplatz 1", "zip": "80331",
        },
    },
    {
        "id": 4, "status": "pending", "total": 200.00,
        "tags": ["standard"],
        "items": [{"sku": "E5", "qty": 1, "price": 200.00}],
        "address": {
            "city": "Berlin", "street": "Unter den Linden 1", "zip": "10117",
        },
    },
    {
        "id": 5, "status": "pending", "total": 67.50,
        "tags": ["express"],
        "items": [{"sku": "F6", "qty": 2, "price": 33.75}],
        "address": {
            "city": "Köln", "street": "Dom 1", "zip": "50667",
        },
    },
]


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def _read_file(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _cmd_to_json(source: str, output: str | None, indent: int) -> None:
    result = jmd_to_json(source, indent=indent)
    if output:
        with open(output, "w", encoding="utf-8") as fh:
            fh.write(result + "\n")
        print(f"JSON written to {output}", file=sys.stderr)
    else:
        print(result)


def _cmd_from_json(source: str, output: str | None, label: str) -> None:
    result = json_to_jmd(source, label=label)
    if output:
        with open(output, "w", encoding="utf-8") as fh:
            fh.write(result + "\n")
        print(f"JMD written to {output}", file=sys.stderr)
    else:
        print(result)


def _cmd_render(source: str, output: str | None = None) -> None:
    html = JMDHTMLRenderer().render(source)
    if output:
        with open(output, "w", encoding="utf-8") as fh:
            fh.write(html)
        print(f"HTML written to {output}", file=sys.stderr)
    else:
        print(html)


def _cmd_roundtrip(source: str) -> None:
    data1 = JMDParser().parse(source)
    jmd2 = JMDSerializer().serialize(data1)
    data2 = JMDParser().parse(jmd2)
    j1 = json.dumps(data1, sort_keys=True, ensure_ascii=False)
    j2 = json.dumps(data2, sort_keys=True, ensure_ascii=False)
    if j1 == j2:
        print("Roundtrip OK - JSON output identical")
    else:
        print("Roundtrip FAILED")
        diff = difflib.unified_diff(
            j1.splitlines(), j2.splitlines(),
            lineterm="", fromfile="pass-1", tofile="pass-2",
        )
        print("\n".join(diff))
        sys.exit(1)


def _qbe_demo() -> None:
    print("Query:\n")
    print(SAMPLE_QUERY)
    q = JMDQueryParser().parse(SAMPLE_QUERY)
    print(f"Parsed: {q}\n")
    results = jmd_query(SAMPLE_QUERY, SAMPLE_RECORDS)
    print(f"Matching records: {len(results)}\n")
    print("Result as JMD:\n")
    print(dict_to_jmd(results, label="Results"))
    print("\nResult as JSON:\n")
    print(json.dumps(results, indent=2, ensure_ascii=False))


def _schema_demo() -> None:
    schema = jmd_parse_schema(SAMPLE_SCHEMA)
    print(f"Schema: {schema.label}, {len(schema.fields)} top-level fields")
    json_schema_str = jmd_schema_to_json_schema(SAMPLE_SCHEMA)
    print("\nJMD Schema -> JSON Schema:\n")
    print(json_schema_str)
    jmd_schema_back = json_schema_to_jmd_schema(json_schema_str)
    print("\nJSON Schema -> JMD Schema (roundtrip):\n")
    print(jmd_schema_back)
    json_schema_2 = jmd_schema_to_json_schema(jmd_schema_back)
    if json.loads(json_schema_str) == json.loads(json_schema_2):
        print("\nSchema roundtrip OK - JSON Schema output identical")
    else:
        print("\nSchema roundtrip FAILED")


def _streaming_demo() -> None:
    print("Stream events from Order document:\n")
    for event in jmd_stream(SAMPLE_JMD):
        print(f"  {event}")


def _flag(args: list[str], name: str, default: str | None = None) -> str | None:
    if name in args:
        idx = args.index(name)
        if idx + 1 < len(args):
            return args[idx + 1]
    return default


_USAGE = """\
JMD - JSON Markdown converter (v0.3)

Usage:
  python -m jmd to-json   <input.jmd>  [-o output.json] [--indent N]
  python -m jmd from-json <input.json> [-o output.jmd]  [--label Label]
  python -m jmd render    <input.jmd>  [-o output.html]
  python -m jmd roundtrip <input.jmd>

  No arguments: run built-in demo

Library use:
  from jmd import JMDParser, JMDSerializer, jmd_to_json, json_to_jmd
"""


def main() -> None:
    """Entry point for the JMD command-line interface."""
    args = sys.argv[1:]
    if not args:
        print("=== JMD v0.3 Demo: JMD -> JSON ===\n")
        print(jmd_to_json(SAMPLE_JMD))
        print("\n=== JMD v0.3 Demo: JSON -> JMD ===\n")
        print(json_to_jmd(jmd_to_json(SAMPLE_JMD), label="Order"))
        print("\n=== Roundtrip Test ===")
        _cmd_roundtrip(SAMPLE_JMD)
        print("\n=== Schema Demo ===\n")
        _schema_demo()
        print("\n=== Streaming Demo ===\n")
        _streaming_demo()
        print("\n=== QBE Demo ===\n")
        _qbe_demo()
        print("\n=== Rendering HTML ===")
        _cmd_render(SAMPLE_JMD, "jmd_demo.html")
        return

    cmd = args[0]
    file_path = (
        args[1]
        if len(args) > 1 and not args[1].startswith("-")
        else None
    )
    out = _flag(args, "-o")

    if cmd == "to-json":
        source = _read_file(file_path) if file_path else SAMPLE_JMD
        indent = int(_flag(args, "--indent", "2") or "2")
        _cmd_to_json(source, out, indent)

    elif cmd == "from-json":
        if file_path:
            source = _read_file(file_path)
        else:
            source = json.dumps({"hello": "world"})
        label = _flag(args, "--label", "Document") or "Document"
        _cmd_from_json(source, out, label)

    elif cmd == "render":
        source = _read_file(file_path) if file_path else SAMPLE_JMD
        _cmd_render(source, out)

    elif cmd == "roundtrip":
        source = _read_file(file_path) if file_path else SAMPLE_JMD
        _cmd_roundtrip(source)

    elif cmd in ("-h", "--help", "help"):
        print(_USAGE)

    else:
        print(f"Unknown command: {cmd}\n", file=sys.stderr)
        print(_USAGE, file=sys.stderr)
        sys.exit(1)
