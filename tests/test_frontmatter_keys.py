# SPDX-License-Identifier: Apache-2.0
"""Frontmatter scope and decoded-key invariants across parser entry points."""

import json
from collections.abc import Callable

import pytest

import jmd
from jmd import JMDDeleteParser, JMDParser, JMDQueryParser, JMDSchemaParser
from jmd._parser_common import JMDParseError
from jmd._streaming import JMDStreamParser

Parser = Callable[[str], object]
ROOTS = ("#", "#?", "#!", "#-")
ROUTES = [
    f"{backend}:{root}"
    for backend in ("python", "public-c", "public-python", "stream")
    for root in ROOTS
] + ["query:#?", "schema:#!", "delete:#-"]


def _stream_events(source: str) -> list[jmd.StreamEvent]:
    return list(jmd.jmd_stream(source))


@pytest.fixture(params=ROUTES)
def parser_route(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Parser, str]:
    backend, root = str(request.param).split(":")
    parser: Parser
    if backend == "public-c":
        if not jmd._HAS_CPARSER:
            pytest.skip("C parser extension unavailable")
        parser = jmd.parse
    elif backend == "public-python":
        monkeypatch.setattr(jmd, "_HAS_CPARSER", False)
        parser = jmd.parse
    elif backend == "python":
        parser = JMDParser().parse
    elif backend == "stream":
        parser = _stream_events
    elif backend == "query":
        parser = JMDQueryParser().parse
    elif backend == "schema":
        parser = JMDSchemaParser().parse
    else:
        parser = JMDDeleteParser().parse
    return parser, root


@pytest.mark.parametrize(
    ("header", "key", "line"),
    [
        ("x: 1\nx: 2", "x", 2),
        ("x: 1\nx: 1", "x", 2),
        ('x: 1\n"x": 2', "x", 2),
        ('"x": 1\nx: 2', "x", 2),
        ('x: 1\n"\\u0078": 2', "x", 2),
        ('"": 1\n"": 2', "", 2),
        ("x\nx", "x", 2),
        ("x\nx: false", "x", 2),
        ("x: false\nx", "x", 2),
        ("x:\nx: 2", "x", 2),
        ("x: 1\nx:", "x", 2),
        ("x:\n> first\n> second\nx: 2", "x", 4),
        ("x: 1\nx:\n> second", "x", 2),
        ("x:\n> first\nx:\n> second", "x", 3),
        ("x: null\n\n---\n\nx: null", "x", 5),
        ('"x: y":\n"x: y": 2', "x: y", 2),
        ('"x: y"\n"x: y": 2', "x: y", 2),
        ('"x: y":\n> first\n"x: y":', "x: y", 3),
    ],
)
@pytest.mark.parametrize("encoding", ["lf", "bom-crlf"])
def test_repeated_frontmatter_key(
    parser_route: tuple[Parser, str], header: str, key: str, line: int,
    encoding: str,
) -> None:
    parser, root = parser_route
    source = f"{header}\n{root} Example\nid: 1\n"
    if encoding == "bom-crlf":
        source = "\ufeff" + source.replace("\n", "\r\n")
    with pytest.raises(JMDParseError) as caught:
        parser(source)
    assert caught.value.kind == "repeated_scalar_key"
    assert caught.value.key == key
    assert caught.value.line == line


@pytest.mark.parametrize("key", ["a b", 'a"b', "a\\b", "a: b", "ä", ""])
def test_decoded_key_identity(
    parser_route: tuple[Parser, str], key: str,
) -> None:
    parser, root = parser_route
    quoted = json.dumps(key, ensure_ascii=False)
    escaped = '"' + "".join(f"\\u{ord(char):04x}" for char in key) + '"'
    with pytest.raises(JMDParseError) as caught:
        parser(f"{quoted}: 1\n{escaped}: 2\n{root} Example\nid: 1")
    assert (caught.value.kind, caught.value.key, caught.value.line) == (
        "repeated_scalar_key", key, 2,
    )


@pytest.mark.parametrize("root", ROOTS)
@pytest.mark.parametrize("backend", ["python", "public", "stream"])
def test_frontmatter_and_body_are_separate(
    root: str, backend: str,
) -> None:
    source = f'x: 1\n{root} Example\n"x": 2'
    if backend == "stream":
        events = list(jmd.jmd_stream(source))
        assert events[0].frontmatter == {"x": 1}
        assert any(
            event.type == "FIELD" and event.key == "x" and event.value == 2
            for event in events
        )
    else:
        parsed = (jmd.parse if backend == "public" else JMDParser().parse)(
            source,
        )
        assert parsed.frontmatter == {"x": 1}
        assert parsed.value == {"x": 2}


@pytest.mark.parametrize("backend", ["python", "public", "stream",
                                     "query", "schema"])
def test_unique_frontmatter_preserves_values(backend: str) -> None:
    header = (
        'x: 1\nX: 2\n" x": 3\nempty:\nflag\n'
        '"colon: empty":\n"colon: text":\n> first\n> second\n'
        '"colon: flag"\n'
    )
    expected = {
        "x": 1, "X": 2, " x": 3, "empty": "", "flag": True,
        "colon: empty": "", "colon: text": "first\nsecond",
        "colon: flag": True,
    }
    actual: object
    if backend == "query":
        query_parser = JMDQueryParser()
        query_parser.parse(header + "#? Example\nid: ?")
        actual = query_parser.frontmatter
    elif backend == "schema":
        schema_parser = JMDSchemaParser()
        schema_parser.parse(header + "#! Example\nid: int")
        actual = schema_parser.frontmatter
    elif backend == "stream":
        actual = list(jmd.jmd_stream(header + "# Example\nid: 1"))[
            0
        ].frontmatter
    else:
        actual = (
            jmd.parse if backend == "public" else JMDParser().parse
        )(header + "# Example\nid: 1").frontmatter
    assert actual == expected


@pytest.mark.parametrize("first", ["x: 1", "x", "x:", "x:\n> text"])
def test_stream_rejects_before_emitting_header(first: str) -> None:
    parser = JMDStreamParser()
    for line in first.splitlines():
        assert parser.process_line(line) == []
    with pytest.raises(JMDParseError) as caught:
        parser.process_line('"x":')
    assert caught.value.kind == "repeated_scalar_key"
    assert caught.value.line == len(first.splitlines()) + 1


@pytest.mark.parametrize("parser_class", [JMDParser, JMDQueryParser,
                                        JMDSchemaParser])
def test_parser_reuse_starts_a_new_frontmatter_scope(
    parser_class: type[JMDParser] | type[JMDQueryParser]
    | type[JMDSchemaParser],
) -> None:
    parser = parser_class()
    root = (
        "#?" if parser_class is JMDQueryParser
        else "#!" if parser_class is JMDSchemaParser else "#"
    )
    source = f"x: 1\n{root} Example\nid: int"
    parser.parse(source)
    with pytest.raises(JMDParseError):
        parser.parse("x: 2\n" + source)
    parser.parse(source)
