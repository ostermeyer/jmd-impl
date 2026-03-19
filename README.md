# jmd-format — Python Reference Implementation

Python reference implementation of the [JMD specification](https://github.com/ostermeyer/jmd-spec) (v0.3). Includes a C-accelerated parser and serializer.

## Installation

```bash
pip install jmd-format
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

## C Extensions

Build manually if needed:

```bash
python build_ext.py build_ext --inplace
```

## Specification

See [jmd-spec](https://github.com/ostermeyer/jmd-spec) for the full format specification, benchmark results, and design documentation.

## License

Licensed under the MIT License. See [LICENSE](LICENSE).

The JMD format specification is licensed separately under CC BY-NC-SA 4.0.
