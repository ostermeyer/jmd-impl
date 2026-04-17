# jmd-format — Python Reference Implementation

Python reference implementation of the [JMD specification](https://github.com/ostermeyer/jmd-spec) (v0.4). Includes a C-accelerated parser and serializer, plus lossless XML↔JMD conversion.

## Installation

Install the latest version directly from GitHub:

```bash
pip install git+https://github.com/ostermeyer/jmd-impl.git
```

Or pin a specific release:

```bash
pip install git+https://github.com/ostermeyer/jmd-impl.git@v0.4
```

Pre-built wheels for Linux, macOS, and Windows are attached to each
[GitHub Release](https://github.com/ostermeyer/jmd-impl/releases) and can be
installed directly:

```bash
pip install https://github.com/ostermeyer/jmd-impl/releases/download/v0.4/jmd_format-0.3-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl
```

The C extensions are built automatically during installation if a C compiler is available. If not, the pure-Python fallback is used transparently.

## Quick Start

```python
from jmd import parse, serialize

data = parse("""
# Order
id: 42
status: pending

## customer
name: Anna Müller
email: anna@example.com
""")

print(data)
# {'id': 42, 'status': 'pending', 'customer': {'name': 'Anna Müller', 'email': 'anna@example.com'}}

print(serialize(data, label="Order"))
```

## Document Modes

```python
from jmd import jmd_mode, JMDQueryParser, JMDSchemaParser, JMDDeleteParser, parse_error

# Detect mode without full parse
mode = jmd_mode(source)   # 'data' | 'query' | 'schema' | 'delete'

# Query by Example (#?)
query = JMDQueryParser().parse("#? Order\nstatus: pending")

# Schema (#!)
schema = JMDSchemaParser().parse("#! Order\nid: integer readonly\nstatus: string")

# Delete (#-)
delete = JMDDeleteParser().parse("#- Order\nid: 42")

# Error (# Error)
error = parse_error("# Error\nstatus: 404\ncode: not_found\nmessage: Not found")
```

## Streaming

```python
from jmd import jmd_stream

for event in jmd_stream(source):
    print(event)
```

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
