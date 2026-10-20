# SPDX-License-Identifier: Apache-2.0
"""Only LF/CRLF delimit JMD lines; Unicode separators remain string data."""

import json

import pytest

import jmd
from jmd import JMDDeleteParser, JMDParser, JMDQueryParser, JMDSchemaParser
from jmd._parser_common import JMDParseError
from jmd._tokenizer import split_source_lines, tokenize
from jmd.jsonc import from_jmd, parse_jsonc, serialize_jsonc, to_jmd

SEPARATORS = ("\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029")


@pytest.mark.parametrize("separator", SEPARATORS)
@pytest.mark.parametrize("ending", ["\n", "\r\n"])
@pytest.mark.parametrize("native", [False, True])
def test_values_keys_and_blockquotes(
    separator: str, ending: str, native: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if native:
        assert jmd._HAS_CPARSER and jmd._HAS_CSERIALIZER
    else:
        monkeypatch.setattr(jmd, "_HAS_CPARSER", False)
        monkeypatch.setattr(jmd, "_HAS_CSERIALIZER", False)
    value = f"first{separator}last"
    expected = {
        "plain": value,
        "quoted": f"{separator}boundary",
        value: "key survives",
        "message": f"{value}\nsecond line",
        "items": [{"message": value}, {"message": "following item"}],
    }
    source = jmd.serialize(
        expected, blockquote_paths=("/items/*/message",),
    ).replace("\n", ending)
    assert jmd.parse(source).value == expected
    assert JMDParser().parse(source).value == expected
    events = list(jmd.jmd_stream(source))
    assert any(
        event.type == "FIELD" and event.key == "plain" and event.value == value
        for event in events
    )
    content = [
        event.value for event in events if event.type == "FIELD_CONTENT"
    ]
    assert content == [value, "second line", value, "following item"]


@pytest.mark.parametrize("separator", SEPARATORS)
@pytest.mark.parametrize("ending", ["\n", "\r\n"])
@pytest.mark.parametrize("root", ["#", "#?", "#!", "#-"])
def test_frontmatter_does_not_invent_keys(
    separator: str, ending: str, root: str,
) -> None:
    value = f"{separator}first\n{separator}last"
    key = f"key{separator}suffix"
    quoted = json.dumps(value, ensure_ascii=False)
    quoted_key = json.dumps(key, ensure_ascii=False)
    source = (
        f"{quoted_key}: {quoted}\n"
        f"note:\n> first{separator}last\n> final\n"
        f"{root} Example\nfield: string"
    ).replace("\n", ending)
    expected = {key: value, "note": f"first{separator}last\nfinal"}
    assert jmd.parse(source).frontmatter == expected
    assert JMDParser().parse(source).frontmatter == expected
    assert list(jmd.jmd_stream(source))[0].frontmatter == expected
    if root == "#?":
        query_parser = JMDQueryParser()
        assert query_parser.parse(source).label == "Example"
        assert query_parser.frontmatter == expected
    elif root == "#!":
        schema_parser = JMDSchemaParser()
        assert schema_parser.parse(source).label == "Example"
        assert schema_parser.frontmatter == expected
    elif root == "#-":
        assert JMDDeleteParser().parse(source).identifiers == {
            "field": "string",
        }


@pytest.mark.parametrize("separator", SEPARATORS)
def test_error_line_number_counts_only_physical_lines(separator: str) -> None:
    source = f"# Example\nvalue: first{separator}last\nvalue: duplicate"
    for parser in (jmd.parse, JMDParser().parse):
        with pytest.raises(JMDParseError) as caught:
            parser(source)
        assert caught.value.kind == "repeated_scalar_key"
        assert caught.value.key == "value"
        assert caught.value.line == 3


@pytest.mark.parametrize("separator", SEPARATORS)
def test_error_detection_does_not_truncate_label(separator: str) -> None:
    assert not jmd.is_error_document(f"# Error{separator}suffix\nvalue: x")
    assert jmd.is_error_document(f"# Error\nmessage: first{separator}last")


@pytest.mark.parametrize("separator", SEPARATORS)
@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_jsonc_companion_preserves_separators(
    separator: str, ending: str,
) -> None:
    value = f"first{separator}last"
    expected = {value: value, "items": [value], "nested": {"value": value}}
    original = parse_jsonc(json.dumps(expected, ensure_ascii=False))
    source = to_jmd(original).replace("\n", ending)
    restored = from_jmd(source)
    assert json.loads(serialize_jsonc(restored)) == expected


@pytest.mark.parametrize(
    "source,expected",
    [
        ("", []),
        ("\n", [""]),
        ("a", ["a"]),
        ("a\n", ["a"]),
        ("a\n\n", ["a", ""]),
        ("a\r\nb\r\n", ["a", "b"]),
        ("a\r", ["a\r"]),
        ("a\u2028b\nnext", ["a\u2028b", "next"]),
    ],
)
def test_splitter_preserves_physical_line_boundaries(
    source: str, expected: list[str],
) -> None:
    assert split_source_lines(source) == expected
    assert [line.number for line in tokenize(source)] == list(
        range(1, len(expected) + 1),
    )


@pytest.mark.parametrize(
    "source,line",
    [
        ("# Example\nvalue: text\r", 2),
        ("# Example\nvalue: text\rnext", 2),
        ("# Example\nvalue: text\rnext\r\n", 2),
        ("note: text\rnext\r\n# Example\nvalue: x", 1),
        ("note: text\r\r\n# Example\nvalue: x", 1),
        ("note: text\r", 1),
        ("# Example\r", 1),
    ],
)
def test_lone_cr_is_rejected_everywhere(source: str, line: int) -> None:
    for parser in (jmd.parse, JMDParser().parse):
        with pytest.raises(JMDParseError) as caught:
            parser(source)
        assert caught.value.kind == "lone_carriage_return"
        assert caught.value.line == line
    with pytest.raises(JMDParseError) as caught:
        list(jmd.jmd_stream(source))
    assert caught.value.kind == "lone_carriage_return"
    assert caught.value.line == line
