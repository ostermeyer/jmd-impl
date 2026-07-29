# SPDX-License-Identifier: Apache-2.0
"""Focused JMD v0.3.5 evidence beyond the vendored fixture corpus."""

from __future__ import annotations

from typing import Literal

import pytest

import jmd
from jmd._parser import JMDParseError

ParserBackend = Literal["c", "py", "direct-py"]
SerializerBackend = Literal["c", "py"]

_C_PARSER_AVAILABLE = jmd._HAS_CPARSER
_C_SERIALIZER_AVAILABLE = jmd._HAS_CSERIALIZER


def _parse(
    backend: ParserBackend,
    source: str,
    monkeypatch: pytest.MonkeyPatch,
) -> jmd.Envelope:
    """Parse through one explicitly selected batch surface."""
    if backend == "c":
        if not _C_PARSER_AVAILABLE:
            pytest.skip("C parser unavailable; c-labelled case not executed")
        monkeypatch.setattr(jmd, "_HAS_CPARSER", True)
        return jmd.parse(source)
    if backend == "py":
        monkeypatch.setattr(jmd, "_HAS_CPARSER", False)
        return jmd.parse(source)
    return jmd.JMDParser().parse(source)


def _serialize(
    backend: SerializerBackend,
    envelope: jmd.Envelope,
    monkeypatch: pytest.MonkeyPatch,
) -> str:
    """Serialize through one explicitly selected public backend."""
    if backend == "c" and not _C_SERIALIZER_AVAILABLE:
        pytest.skip("C serializer unavailable; c-labelled case not executed")
    monkeypatch.setattr(jmd, "_HAS_CSERIALIZER", backend == "c")
    return jmd.serialize(envelope)


_PARSE_CASES = (
    pytest.param(
        '# Doc\n## ""\nx: 1',
        jmd.Envelope(mode="data", label="Doc", value={"": {"x": 1}}),
        id="rt-002-empty-string-key",
    ),
    pytest.param(
        '# X\nv: "quote: \\" slash: \\\\ nl: \\n tab: \\t u: \\u0041"\n'
        "path: C:\\temp\\file",
        jmd.Envelope(
            mode="data",
            label="X",
            value={
                "v": 'quote: " slash: \\ nl: \n tab: \t u: A',
                "path": "C:\\temp\\file",
            },
        ),
        id="rt-003-040-string-escapes",
    ),
    pytest.param(
        "# X\n## object\n## array[]\n## tail\nv: 1",
        jmd.Envelope(
            mode="data",
            label="X",
            value={"object": {}, "array": [], "tail": {"v": 1}},
        ),
        id="rt-004-empty-nested-structures",
    ),
    pytest.param(
        "# X\n## a\n### b\n#### c\n##### d\n###### e\nv: 1",
        jmd.Envelope(
            mode="data",
            label="X",
            value={"a": {"b": {"c": {"d": {"e": {"v": 1}}}}}},
        ),
        id="rt-005-six-level-object",
    ),
    pytest.param(
        "# X\n## a\n### b\nv: 1\n## root: done",
        jmd.Envelope(
            mode="data",
            label="X",
            value={"a": {"b": {"v": 1}}, "root": "done"},
        ),
        id="rt-006-scalar-heading-return",
    ),
    pytest.param(
        "# X\n## a\nv: 1\n\nroot: 2",
        jmd.Envelope(
            mode="data",
            label="X",
            value={"a": {"v": 1}, "root": 2},
        ),
        id="rt-007-blank-scope-return",
    ),
    pytest.param(
        "# X\n## a\nv: 1\n\n## b\nv: 2",
        jmd.Envelope(
            mode="data",
            label="X",
            value={"a": {"v": 1}, "b": {"v": 2}},
        ),
        id="rt-008-cosmetic-blank-before-heading",
    ),
    pytest.param(
        "# X\n## a[]\n- 1\n\n- 2",
        jmd.Envelope(mode="data", label="X", value={"a": [1, 2]}),
        id="rt-009-cosmetic-blank-in-array",
    ),
    pytest.param(
        "# X\n## a[]\n- 1\n\nroot: ok",
        jmd.Envelope(
            mode="data",
            label="X",
            value={"a": [1], "root": "ok"},
        ),
        id="rt-010-blank-closes-array",
    ),
    pytest.param(
        "# O[]\n- a: 1\n qty: 2\n\tprice: 3\n    note: ok",
        jmd.Envelope(
            mode="data",
            label="O",
            value=[{"a": 1, "qty": 2, "price": 3, "note": "ok"}],
        ),
        id="rt-014-indentation-width-and-tabs",
    ),
    pytest.param(
        "page: 1\npage-size: 50\ncount\nverbose\n\n#? X\na: b",
        jmd.Envelope(
            mode="query",
            label="X",
            frontmatter={
                "page": 1,
                "page-size": 50,
                "count": True,
                "verbose": True,
            },
            value={"a": "b"},
        ),
        id="rt-019-024-request-frontmatter",
    ),
    pytest.param(
        "total: 4832\npage: 1\npages: 97\npage-size: 50\n\n"
        "# Orders\nid: 1",
        jmd.Envelope(
            mode="data",
            label="Orders",
            frontmatter={
                "total": 4832,
                "page": 1,
                "pages": 97,
                "page-size": 50,
            },
            value={"id": 1},
        ),
        id="rt-020-response-frontmatter",
    ),
    pytest.param(
        "ignored-keys: dry-run, limit\n\n# Result\nstatus: ok",
        jmd.Envelope(
            mode="data",
            label="Result",
            frontmatter={"ignored-keys": "dry-run, limit"},
            value={"status": "ok"},
        ),
        id="rt-026-ignored-keys-short",
    ),
    pytest.param(
        "# X\n## outer[]\n- id: 1\n### inner[]\n- a\n---\n- b",
        jmd.Envelope(
            mode="data",
            label="X",
            value={"outer": [{"id": 1, "inner": ["a", "b"]}]},
        ),
        id="rt-033-thematic-break-nested",
    ),
    pytest.param(
        "# X\n## matrix[]\n### []\n- 1\n- 2\n### []\n- 3",
        jmd.Envelope(
            mode="data",
            label="X",
            value={"matrix": [[1, 2], [3]]},
        ),
        id="rt-037-anonymous-subarrays",
    ),
    pytest.param(
        "# X\n## mixed[]\n- 42\n- hello\n- true\n- null\n"
        "- name: Alice\n### []\n- 1\n- 2",
        jmd.Envelope(
            mode="data",
            label="X",
            value={
                "mixed": [42, "hello", True, None, {"name": "Alice"}, [1, 2]]
            },
        ),
        id="rt-038-heterogeneous-array",
    ),
    pytest.param(
        "# X\na: 0\nb: -0\nc: 1e10\nd: 1E10\ne: -2.5e-3\nf: 3.14",
        jmd.Envelope(
            mode="data",
            label="X",
            value={
                "a": 0,
                "b": 0,
                "c": 1e10,
                "d": 1e10,
                "e": -2.5e-3,
                "f": 3.14,
            },
        ),
        id="rt-039-numeric-edges",
    ),
    pytest.param(
        "# X\n## child\nnote:\n> **bold** and `code`\n> [link](url)\n\nroot: b",
        jmd.Envelope(
            mode="data",
            label="X",
            value={
                "child": {"note": "**bold** and `code`\n[link](url)"},
                "root": "b",
            },
        ),
        id="rt-045-046-050-blockquote-termination",
    ),
    pytest.param(
        "# X\na: The **Ultimate** Stand\nb: 2 * x\nc: **ptr\n"
        "d: ^[a-z]+$\ne: https://example.com/*",
        jmd.Envelope(
            mode="data",
            label="X",
            value={
                "a": "The **Ultimate** Stand",
                "b": "2 * x",
                "c": "**ptr",
                "d": "^[a-z]+$",
                "e": "https://example.com/*",
            },
        ),
        id="rt-048-051-markdown-literal",
    ),
    pytest.param(
        "# X\na: !true\nb: > urgent\nc: ~berlin\nd: >= 5",
        jmd.Envelope(
            mode="data",
            label="X",
            value={"a": "!true", "b": "> urgent", "c": "~berlin", "d": ">= 5"},
        ),
        id="rt-052-filter-prefixes-in-data",
    ),
    pytest.param(
        "#! X\na: integer\nb: -> Customer\nc: object(x: string)",
        jmd.Envelope(
            mode="schema",
            label="X",
            value={
                "a": "integer",
                "b": "-> Customer",
                "c": "object(x: string)",
            },
        ),
        id="rt-054-059-061-schema-raw",
    ),
    pytest.param(
        "#? X\na: > 50\nb: ?",
        jmd.Envelope(
            mode="query",
            label="X",
            value={"a": "> 50", "b": "?"},
        ),
        id="rt-069-071-query-raw",
    ),
    pytest.param(
        "# Error\nstatus: 400\ncode: invalid\nsuggestion: retry\n"
        "context: sample\n\n## errors[]\n- field: x\n  reason: y\n  value: z",
        jmd.Envelope(
            mode="data",
            label="Error",
            value={
                "status": 400,
                "code": "invalid",
                "suggestion": "retry",
                "context": "sample",
                "errors": [{"field": "x", "reason": "y", "value": "z"}],
            },
        ),
        id="rt-065-067-error-document",
    ),
    pytest.param(
        "#- X\nid: 42\n## child\nnote:\n> a",
        jmd.Envelope(
            mode="delete",
            label="X",
            value={"id": 42, "child": {"note": "a"}},
        ),
        id="rt-072-076-delete-body",
    ),
    pytest.param(
        "# X\n\n\t\n",
        jmd.Envelope(mode="data", label="X", value={}),
        id="rt-058-whitespace-only-body",
    ),
)


@pytest.mark.parametrize("backend", ("c", "py", "direct-py"))
@pytest.mark.parametrize(("source", "expected"), _PARSE_CASES)
def test_v035_recommended_parse_cases(
    backend: ParserBackend,
    source: str,
    expected: jmd.Envelope,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover recommended §22.2 parser cases absent from the corpus."""
    assert _parse(backend, source, monkeypatch) == expected


@pytest.mark.parametrize("backend", ("c", "py", "direct-py"))
def test_v035_key_order_is_semantically_irrelevant(
    backend: ParserBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treat object key order as irrelevant to the represented JSON value."""
    first = _parse(backend, "# X\na: 1\nb: 2", monkeypatch)
    second = _parse(backend, "# X\nb: 2\na: 1", monkeypatch)
    assert first == second


@pytest.mark.parametrize("backend", ("c", "py", "direct-py"))
def test_v035_bare_and_heading_fields_are_equivalent(
    backend: ParserBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return identical values for bare and scalar-heading field forms."""
    bare = _parse(backend, "# X\na: 1\nb: 2", monkeypatch)
    headed = _parse(backend, "# X\na: 1\n## b: 2", monkeypatch)
    assert bare == headed


@pytest.mark.parametrize("backend", ("c", "py", "direct-py"))
def test_v035_plain_prose_is_rejected(
    backend: ParserBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject non-structural body prose with its source line."""
    with pytest.raises(JMDParseError) as exc:
        _parse(backend, "# Answer\n\n42\n", monkeypatch)
    assert exc.value.kind == "prose_in_body"
    assert exc.value.line == 3


_SERIALIZER_CASES = (
    pytest.param(
        jmd.Envelope(mode="data", label="Order", value={"id": 42}),
        "# Order\nid: 42",
        id="g-001-labelled-object",
    ),
    pytest.param(
        jmd.Envelope(mode="data", label="Orders", value=[1, 2]),
        "# Orders[]\n- 1\n- 2",
        id="g-002-labelled-array",
    ),
    pytest.param(
        jmd.Envelope(mode="data", label="", value=[1, 2]),
        "# []\n- 1\n- 2",
        id="g-002-sigil-only-array",
    ),
    pytest.param(
        jmd.Envelope(mode="query", label="", value=["pending"]),
        "#? []\n- pending",
        id="g-003-mode-sigil-only-array",
    ),
    pytest.param(
        jmd.Envelope(
            mode="data",
            label="Rows",
            value=[{"id": 1, "child": ["a"]}, {"id": 2}],
        ),
        "# Rows[]\n- id: 1\n\n## child[]\n- a\n#\n- id: 2",
        id="g-004-level-pop",
    ),
    pytest.param(
        jmd.Envelope(mode="data", label="Matrix", value=[[1, 2], [3]]),
        "# Matrix[]\n## []\n- 1\n- 2\n## []\n- 3",
        id="g-004-anonymous-subarrays",
    ),
)


@pytest.mark.parametrize("backend", ("c", "py"))
@pytest.mark.parametrize(("envelope", "expected"), _SERIALIZER_CASES)
def test_v035_strict_generator_cases(
    backend: SerializerBackend,
    envelope: jmd.Envelope,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Emit canonical bytes without parser-tolerance constructs."""
    output = _serialize(backend, envelope, monkeypatch)
    assert output == expected
    assert "---" not in output
    assert "## -" not in output
    assert not output.startswith("\ufeff")
    assert "\r" not in output
