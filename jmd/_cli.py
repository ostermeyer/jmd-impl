# SPDX-License-Identifier: Apache-2.0
"""JMD command-line interface and top-level convenience functions."""

from __future__ import annotations

import difflib
import json
import sys
from typing import Any

from ._html import JMDHTMLRenderer
from ._parser import JMDParser
from ._query import JMDQueryExecutor, JMDQueryParser
from ._schema import JMDSchemaParser
from ._serializer import JMDSerializer

# ---------------------------------------------------------------------------
# Public API — top-level convenience functions
# ---------------------------------------------------------------------------


def jmd_to_json(jmd_source: str, indent: int = 2) -> str:
    """Parse JMD source and return a formatted JSON string.

    Only the document body is serialized; mode, label, and frontmatter
    are dropped. Use :func:`jmd.parse` for the full envelope.
    """
    data = JMDParser().parse(jmd_source).value
    return json.dumps(data, indent=indent, ensure_ascii=False)


def json_to_jmd(json_source: str, label: str = "Document") -> str:
    """Parse a JSON string and return a JMD document."""
    data = json.loads(json_source)
    return JMDSerializer().serialize(data, label=label)


def jmd_to_dict(jmd_source: str) -> Any:
    """Parse JMD source and return the body value as a dict or list.

    Convenience for the common case of ignoring envelope metadata
    (mode, label, frontmatter). For the full canonical shape, use
    :func:`jmd.parse`, which returns an :class:`Envelope` (§3.6).
    """
    return JMDParser().parse(jmd_source).value


def dict_to_jmd(data: Any, label: str = "Document") -> str:
    """Serialize a Python dict or list to a JMD document."""
    return JMDSerializer().serialize(data, label=label)


def jmd_query(
    query_source: str,
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
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
# CLI commands
# ---------------------------------------------------------------------------

# Exit status for invocation errors (unknown command, no input), kept
# apart from 1, which reports a failed roundtrip.
_EXIT_USAGE = 2

_USAGE = """\
jmd - convert and check JMD (JSON Markdown) documents

Usage:
  jmd to-json   [input.jmd]  [-o output.json] [--indent N]
  jmd from-json [input.json] [-o output.jmd]  [--label Label]
  jmd render    [input.jmd]  [-o output.html]
  jmd roundtrip [input.jmd]
  jmd --help

Without an input file, or with "-", the input is read from stdin.

Specification: https://github.com/ostermeyer/jmd-spec
Library:       https://github.com/ostermeyer/jmd-impl
"""


class _NoInputError(Exception):
    """Raised when a command has neither an input file nor piped stdin."""


def _read_input(path: str | None) -> str:
    """Return the command input from *path*, or from stdin.

    Stdin is read only when it is not an interactive terminal: a
    forgotten file argument must fail fast instead of waiting for
    keyboard input.

    Raises:
        _NoInputError: No file was named and stdin is a terminal.
    """
    if path is not None and path != "-":
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    if sys.stdin is None or sys.stdin.isatty():
        raise _NoInputError
    return sys.stdin.read()


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


def _cmd_roundtrip(source: str) -> int:
    data1 = JMDParser().parse(source).value
    jmd2 = JMDSerializer().serialize(data1)
    data2 = JMDParser().parse(jmd2).value
    j1 = json.dumps(data1, sort_keys=True, ensure_ascii=False)
    j2 = json.dumps(data2, sort_keys=True, ensure_ascii=False)
    if j1 == j2:
        print("Roundtrip OK - JSON output identical")
        return 0
    print("Roundtrip FAILED")
    diff = difflib.unified_diff(
        j1.splitlines(),
        j2.splitlines(),
        lineterm="",
        fromfile="pass-1",
        tofile="pass-2",
    )
    print("\n".join(diff))
    return 1


def _flag(args: list[str], name: str, default: str | None = None) -> str | None:
    if name in args:
        idx = args.index(name)
        if idx + 1 < len(args):
            return args[idx + 1]
    return default


def main(argv: list[str] | None = None) -> int:
    """Run the JMD command-line interface.

    Without arguments, and for ``-h``, ``--help`` or ``help``, prints
    the usage text and does nothing else.

    Args:
        argv: Arguments without the program name; ``sys.argv[1:]`` when
            omitted.

    Returns:
        The process exit status: 0 on success, 1 for a failed roundtrip,
        2 for an unknown command or missing input.
    """
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] in ("-h", "--help", "help"):
        print(_USAGE, end="")
        return 0

    cmd = args[0]
    if cmd not in ("to-json", "from-json", "render", "roundtrip"):
        print(f"Unknown command: {cmd}\n", file=sys.stderr)
        print(_USAGE, end="", file=sys.stderr)
        return _EXIT_USAGE

    file_path = (
        args[1] if len(args) > 1 and not args[1].startswith("-") else None
    )
    out = _flag(args, "-o")
    try:
        source = _read_input(file_path)
    except _NoInputError:
        print(
            f"jmd {cmd}: no input - name a file or pipe the document "
            "into stdin (see jmd --help)",
            file=sys.stderr,
        )
        return _EXIT_USAGE

    if cmd == "to-json":
        indent = int(_flag(args, "--indent", "2") or "2")
        _cmd_to_json(source, out, indent)
    elif cmd == "from-json":
        label = _flag(args, "--label", "Document") or "Document"
        _cmd_from_json(source, out, label)
    elif cmd == "render":
        _cmd_render(source, out)
    else:
        return _cmd_roundtrip(source)
    return 0
