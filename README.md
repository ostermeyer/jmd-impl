# jmd-format — Python Reference Implementation

Python reference implementation of the [JMD specification](https://github.com/ostermeyer/jmd-spec) (v0.3.6). Includes a C-accelerated parser and serializer, plus lossless XML↔JMD and JSONC↔JMD conversion. Requires Python 3.11 or later.

## Installation

Install from [PyPI](https://pypi.org/project/jmd-format/):

```bash
pip install jmd-format
```

Pin a release, or add the optional XML support (`lxml`):

```bash
pip install "jmd-format==0.11.0"
pip install "jmd-format[xml]"
```

PyPI carries pre-built wheels with the C extensions for CPython 3.11–3.14
on Linux (x86_64, glibc 2.28 or later), macOS (Apple silicon), and Windows.
Everywhere else pip installs from the source distribution: the C extensions
are compiled when a C compiler is available, and the pure-Python fallback is
used transparently otherwise.

## Quick Start

```python
from jmd import parse, serialize

doc = parse("""
# Order
id: 42
status: pending

## customer
name: Anna Müller
email: anna@example.com
""")

print(doc.mode, doc.label)
# data Order
print(doc.value)
# {'id': 42, 'status': 'pending', 'customer': {'name': 'Anna Müller', 'email': 'anna@example.com'}}

print(serialize(doc))                       # label and frontmatter from the envelope
print(serialize(doc.value, label="Order"))  # or from a plain value
```

`parse()` returns an `Envelope` carrying `mode`, `label`, `frontmatter`, and
the parsed body in `value`.

## Document Modes

```python
from jmd import (
    JMDDeleteParser,
    JMDParser,
    JMDQueryParser,
    JMDSchemaParser,
    jmd_mode,
    parse_error,
)

# Detect mode without full parse
mode = jmd_mode(source)   # 'data' | 'query' | 'schema' | 'delete'

# Query by Example (#?)
query = JMDQueryParser().parse("#? Order\nstatus: pending")

# Schema (#!) — canonical structural parser; values remain raw strings
schema = JMDParser().parse("#! Order\nid: integer readonly\nstatus: string")

# Optional legacy type-expression dialect
typed_schema = JMDSchemaParser().parse(
    "#! Order\nid: integer readonly\nstatus: string"
)

# Delete (#-)
delete = JMDDeleteParser().parse("#- Order\nid: 42")

# Error (# Error)
error = parse_error("# Error\nstatus: 404\ncode: not_found\nmessage: Not found")
```

## Streaming

Use `jmd_stream()` when the complete source is already available:

```python
from jmd import jmd_stream

for event in jmd_stream(source):
    print(event)
```

For incremental input, consume the events returned by every
`process_line()` call. `finish()` emits only events still pending at end of
input:

```python
from jmd import JMDStreamParser

parser = JMDStreamParser()
for line in source_lines:
    for event in parser.process_line(line):
        print(event)
for event in parser.finish():
    print(event)
```

Canonical multiline fields emit `FIELD_START` followed by one
`FIELD_CONTENT` event per blockquote line. The `stream_events()` and
`to_lines()` async adapters preserve the same incremental behavior.

## XML Mapping

Lossless conversion between data XML and JMD (requires `lxml`):

```python
from jmd.xml import xml_to_jmd, jmd_to_xml

jmd_source = xml_to_jmd(xml_bytes_or_str)  # XML → JMD string
xml_output  = jmd_to_xml(jmd_source)        # JMD → XML bytes
```

Targets data XML — OOXML (WordprocessingML, DrawingML, SpreadsheetML),
SOAP, XBRL, XRechnung, and similar formats. Mixed-content XML (ODF, XHTML)
is out of scope.

See the [JMD over XML companion specification](https://github.com/ostermeyer/jmd-spec/blob/main/jmd-over-xml.md) for the full mapping rules.

## JSONC Mapping

Lossless round-trip between JSONC (JSON with comments and trailing commas)
and JMD, standard library only:

```python
from jmd.jsonc import from_jmd, parse_jsonc, serialize_jsonc, to_jmd

doc = parse_jsonc(jsonc_source, root_label="Settings")  # JSONC → AST
jmd_source = to_jmd(doc)                                  # AST → JMD
jsonc_output = serialize_jsonc(from_jmd(jmd_source))      # JMD → JSONC
```

Comments survive at their original positions: the JMD form carries them
as `#/` line and `#*` block markers. The mapping follows the
[JMD over JSONC companion specification](https://github.com/ostermeyer/jmd-spec/blob/main/jmd-over-jsonc.md),
which is still a draft.

## C Extensions

Build manually if needed:

```bash
python build_ext.py build_ext --inplace
```

## Specification

See [jmd-spec](https://github.com/ostermeyer/jmd-spec) for the full format specification, benchmark results, and design documentation.

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE).

The JMD format specification is licensed separately under [CC BY 4.0](https://github.com/ostermeyer/jmd-spec/blob/main/LICENSE).
