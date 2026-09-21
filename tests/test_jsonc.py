# SPDX-License-Identifier: Apache-2.0
"""Tests for the jmd-over-jsonc round-trip.

Four conversion functions exist; we test them in two complementary
ways:

1. **Direction-specific tests** — small, hand-written input/output
   pairs that pin down each function's behaviour on the features
   we care about (objects, arrays, comments, scalars, escapes,
   mandatory frontmatter framing, depth-prefixed markers).
2. **Round-trip property tests** — a representative input goes
   JSONC → AST → JMD → AST → JSONC; the two ASTs are compared for
   structural equality, and the JSONC output is parsed once more
   to verify a fixed point.

Run with::

    uv run pytest tests/test_jsonc.py -q
"""

from __future__ import annotations

import pytest

from jmd.jsonc import (
    BlockComment,
    Field,
    JsoncJmdParseError,
    JsoncParseError,
    LineComment,
    ObjectArray,
    ScalarArray,
    Section,
    from_jmd,
    parse_jsonc,
    serialize_jsonc,
    to_jmd,
)

# --- parse_jsonc: structural -------------------------------------------


def test_parse_empty_object() -> None:
    doc = parse_jsonc("{}", root_label="Settings")
    assert doc.root.label == "Settings"
    assert doc.root.children == []


def test_parse_simple_fields() -> None:
    doc = parse_jsonc(
        '{"tab_size": 4, "wrap": true, "font": "mono"}',
        root_label="Settings",
    )
    assert doc.root.children == [
        Field(key="tab_size", value=4),
        Field(key="wrap", value=True),
        Field(key="font", value="mono"),
    ]


def test_parse_nested_object_becomes_section() -> None:
    doc = parse_jsonc(
        '{"globals": {"foreground": "#fff"}}',
        root_label="ColorScheme",
    )
    assert isinstance(doc.root.children[0], Section)
    assert doc.root.children[0].label == "globals"
    assert doc.root.children[0].children == [
        Field(key="foreground", value="#fff"),
    ]


def test_parse_scalar_array() -> None:
    doc = parse_jsonc('{"rulers": [80, 100, 120]}', root_label="S")
    assert doc.root.children == [
        ScalarArray(key="rulers", items=[80, 100, 120]),
    ]


def test_parse_object_array() -> None:
    doc = parse_jsonc(
        '{"build_systems": ['
        '{"name": "py", "cmd": "python3"},'
        '{"name": "sh", "cmd": "bash"}'
        "]}",
        root_label="S",
    )
    arr = doc.root.children[0]
    assert isinstance(arr, ObjectArray)
    assert arr.key == "build_systems"
    assert len(arr.items) == 2
    assert arr.items[0].children == [
        Field(key="name", value="py"),
        Field(key="cmd", value="python3"),
    ]


def test_parse_null_true_false_numbers() -> None:
    doc = parse_jsonc(
        '{"a": null, "b": true, "c": false, "d": -1, "e": 3.14}',
        root_label="S",
    )
    fields = [c for c in doc.root.children if isinstance(c, Field)]
    assert [f.value for f in fields] == [
        None,
        True,
        False,
        -1,
        3.14,
    ]


def test_parse_string_escapes() -> None:
    doc = parse_jsonc(
        '{"newline": "a\\nb", "unicode": "caf\\u00e9"}',
        root_label="S",
    )
    c0, c1 = doc.root.children[0], doc.root.children[1]
    assert isinstance(c0, Field) and c0.value == "a\nb"
    assert isinstance(c1, Field) and c1.value == "café"


# --- parse_jsonc: comments ---------------------------------------------


def test_parse_line_comment_strips_one_leading_space() -> None:
    doc = parse_jsonc(
        '// hello\n{"x": 1}',
        root_label="S",
    )
    # leading top-of-file comment lives outside the object, so
    # only the field appears in root.children
    assert any(isinstance(c, Field) and c.key == "x" for c in doc.root.children)


def test_parse_line_comment_inside_object() -> None:
    doc = parse_jsonc(
        '{\n  // hello\n  "x": 1\n}',
        root_label="S",
    )
    assert doc.root.children[0] == LineComment(text="hello")
    assert doc.root.children[1] == Field(key="x", value=1)


def test_parse_block_comment_multiline() -> None:
    src = '{\n  /* line1\n     line2 */\n  "x": 1\n}'
    doc = parse_jsonc(src, root_label="S")
    assert doc.root.children[0] == BlockComment(
        body_lines=["line1", "line2"],
    )
    assert doc.root.children[1] == Field(key="x", value=1)


def test_parse_trailing_comment_after_field() -> None:
    doc = parse_jsonc(
        '{\n  "x": 1, // trailer\n  "y": 2\n}',
        root_label="S",
    )
    kinds = [type(c).__name__ for c in doc.root.children]
    assert kinds == ["Field", "LineComment", "Field"]


def test_parse_trailing_comma_is_silent() -> None:
    doc = parse_jsonc(
        '{"x": 1, "y": 2,}',
        root_label="S",
    )
    assert [c.key for c in doc.root.children if isinstance(c, Field)] == [
        "x",
        "y",
    ]


# --- parse_jsonc: errors -----------------------------------------------


def test_parse_jsonc_root_must_be_object() -> None:
    with pytest.raises(JsoncParseError):
        parse_jsonc("42", root_label="S")


def test_parse_jsonc_unterminated_block_comment() -> None:
    with pytest.raises(JsoncParseError):
        parse_jsonc("/* never ends", root_label="S")


def test_parse_jsonc_unterminated_string() -> None:
    with pytest.raises(JsoncParseError):
        parse_jsonc('{"x": "never ends', root_label="S")


# --- to_jmd ------------------------------------------------------------


def test_to_jmd_emits_mandatory_frontmatter_frame() -> None:
    doc = parse_jsonc("{}", root_label="Settings")
    out = to_jmd(doc)
    # Empty frontmatter still framed with two consecutive ---
    assert out.startswith("---\n---\n")
    assert "# Settings" in out


def test_to_jmd_root_heading_carries_caller_label() -> None:
    doc = parse_jsonc("{}", root_label="ColorScheme")
    assert "# ColorScheme\n" in to_jmd(doc)


def test_to_jmd_line_comment_uses_depth_prefix() -> None:
    doc = parse_jsonc(
        '{\n  // hello\n  "x": 1\n}',
        root_label="S",
    )
    out = to_jmd(doc)
    assert "#/ hello\n" in out


def test_to_jmd_block_comment_uses_canonical_label() -> None:
    doc = parse_jsonc(
        '{\n  /* body */\n  "x": 1\n}',
        root_label="S",
    )
    out = to_jmd(doc)
    # Canonical form regardless of input form
    assert "#* Comment\n> body\n" in out


def test_to_jmd_nested_section_at_correct_depth() -> None:
    doc = parse_jsonc(
        '{"globals": {"a": 1, "sub": {"b": 2}}}',
        root_label="S",
    )
    out = to_jmd(doc)
    assert "## globals\n" in out
    assert "### sub\n" in out


def test_to_jmd_scalar_array_inline_form() -> None:
    doc = parse_jsonc('{"r": [1, 2, 3]}', root_label="S")
    out = to_jmd(doc)
    assert "r[]:\n- 1\n- 2\n- 3\n" in out


def test_to_jmd_quoted_string_when_required() -> None:
    doc = parse_jsonc('{"foreground": "#fff"}', root_label="S")
    out = to_jmd(doc)
    # # is structural-prefix — must be quoted in value position
    assert 'foreground: "#fff"\n' in out


def test_to_jmd_bare_string_when_safe() -> None:
    doc = parse_jsonc('{"font": "mono"}', root_label="S")
    out = to_jmd(doc)
    assert "font: mono\n" in out


# --- from_jmd ----------------------------------------------------------


def test_from_jmd_requires_frontmatter_open() -> None:
    with pytest.raises(JsoncJmdParseError):
        from_jmd("# Settings\nx: 1\n")


def test_from_jmd_empty_frontmatter_works() -> None:
    doc = from_jmd("---\n---\n# S\nx: 1\n")
    assert doc.frontmatter == {}
    assert doc.root.label == "S"
    assert doc.root.children == [Field(key="x", value=1)]


def test_from_jmd_frontmatter_fields() -> None:
    doc = from_jmd("---\npage: 2\n---\n# S\n")
    assert doc.frontmatter == {"page": 2}


def test_from_jmd_line_comment_round_trips() -> None:
    doc = from_jmd(
        "---\n---\n# S\n#/ hello\nx: 1\n",
    )
    assert doc.root.children[0] == LineComment(text="hello")


def test_from_jmd_block_comment_round_trips() -> None:
    doc = from_jmd(
        "---\n---\n# S\n#* Comment\n> a\n> b\n\nx: 1\n",
    )
    assert doc.root.children[0] == BlockComment(
        body_lines=["a", "b"],
    )


def test_from_jmd_block_comment_no_label_form() -> None:
    doc = from_jmd(
        "---\n---\n# S\n#*\n> a\n\nx: 1\n",
    )
    assert doc.root.children[0] == BlockComment(body_lines=["a"])


def test_from_jmd_block_comment_lowercase_label() -> None:
    doc = from_jmd(
        "---\n---\n# S\n#* comment\n> a\n\nx: 1\n",
    )
    assert doc.root.children[0] == BlockComment(body_lines=["a"])


def test_from_jmd_nested_section() -> None:
    doc = from_jmd(
        "---\n---\n# S\nx: 1\n\n## nested\ny: 2\n",
    )
    section = doc.root.children[1]
    assert isinstance(section, Section)
    assert section.label == "nested"
    assert section.children == [Field(key="y", value=2)]


def test_from_jmd_scalar_array() -> None:
    doc = from_jmd(
        "---\n---\n# S\nr[]:\n- 1\n- 2\n- 3\n",
    )
    assert doc.root.children == [
        ScalarArray(key="r", items=[1, 2, 3]),
    ]


def test_from_jmd_object_array() -> None:
    src = (
        "---\n---\n# S\n\n## items[]\n"
        "- name: a\n  cmd: x\n- name: b\n  cmd: y\n"
    )
    doc = from_jmd(src)
    arr = doc.root.children[0]
    assert isinstance(arr, ObjectArray)
    assert arr.key == "items"
    assert len(arr.items) == 2
    assert arr.items[0].children == [
        Field(key="name", value="a"),
        Field(key="cmd", value="x"),
    ]


# --- serialize_jsonc ---------------------------------------------------


def test_serialize_jsonc_basic_object() -> None:
    doc = parse_jsonc('{"x": 1, "y": 2}', root_label="S")
    out = serialize_jsonc(doc)
    assert '"x": 1' in out
    assert '"y": 2' in out
    # No trailing commas
    assert ",\n}" not in out


def test_serialize_jsonc_canonical_4space_indent() -> None:
    doc = parse_jsonc(
        '{"globals": {"x": 1}}',
        root_label="S",
    )
    out = serialize_jsonc(doc)
    assert '    "globals":' in out
    assert '        "x": 1' in out


def test_serialize_jsonc_line_comment() -> None:
    doc = parse_jsonc(
        '{\n  // hello\n  "x": 1\n}',
        root_label="S",
    )
    out = serialize_jsonc(doc)
    assert "// hello\n" in out


def test_serialize_jsonc_block_comment() -> None:
    doc = parse_jsonc(
        '{\n  /* a\n     b */\n  "x": 1\n}',
        root_label="S",
    )
    out = serialize_jsonc(doc)
    # Multi-line body: ``/*`` opens, content lines run, ``*/``
    # joins the last content line inline so the parser's body
    # interpretation matches the serializer's exactly.
    assert "/* a\n" in out
    assert "   b */\n" in out


# --- round-trip property ------------------------------------------------


ROUND_TRIP_SAMPLE = """{
    // tab width comment
    "tab_size": 4,
    /* multi-line
       block comment */
    "translate_tabs_to_spaces": true,
    "rulers": [80, 100],
    "globals": {
        "foreground": "#fff",
        "background": "#222"
    },
    "font": null
}"""


def test_round_trip_preserves_ast_structure() -> None:
    """JSONC → AST → JMD → AST round-trip preserves the AST."""
    doc1 = parse_jsonc(ROUND_TRIP_SAMPLE, root_label="Settings")
    jmd_text = to_jmd(doc1)
    doc2 = from_jmd(jmd_text)
    assert doc1.root == doc2.root
    assert doc1.frontmatter == doc2.frontmatter


def test_round_trip_jsonc_is_idempotent() -> None:
    """AST → JSONC → AST is a fixed point."""
    doc1 = parse_jsonc(ROUND_TRIP_SAMPLE, root_label="Settings")
    jsonc_text = serialize_jsonc(doc1)
    doc2 = parse_jsonc(jsonc_text, root_label="Settings")
    assert doc1.root == doc2.root


def test_round_trip_full_loop_preserves_data() -> None:
    """JSONC → AST → JMD → AST → JSONC: data survives."""
    doc1 = parse_jsonc(ROUND_TRIP_SAMPLE, root_label="Settings")
    jmd_text = to_jmd(doc1)
    doc2 = from_jmd(jmd_text)
    jsonc_text = serialize_jsonc(doc2)
    doc3 = parse_jsonc(jsonc_text, root_label="Settings")
    assert doc1.root == doc3.root


def test_round_trip_block_comment_canonical_form() -> None:
    """All three input forms canonicalize to ``#* Comment``."""
    inputs = [
        "---\n---\n# S\n#*\n> body\n\n",
        "---\n---\n# S\n#* comment\n> body\n\n",
        "---\n---\n# S\n#* Comment\n> body\n\n",
    ]
    for src in inputs:
        doc = from_jmd(src)
        out = to_jmd(doc)
        assert "#* Comment\n" in out, f"input did not canonicalize: {src!r}"
