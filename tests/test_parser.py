"""Tests for JMDParser — data document parsing (spec §§ 2–11)."""

import pytest
from jmd import JMDParser


def parse(source: str):
    return JMDParser().parse(source)


# ---------------------------------------------------------------------------
# Scalar types (spec § 5)
# ---------------------------------------------------------------------------

class TestScalars:
    def test_integer(self):
        assert parse("# X\ncount: 42") == {"count": 42}

    def test_negative_integer(self):
        assert parse("# X\nvalue: -7") == {"value": -7}

    def test_float(self):
        assert parse("# X\nprice: 9.99") == {"price": 9.99}

    def test_float_scientific(self):
        assert parse("# X\nv: 1.5e3") == {"v": 1500.0}

    def test_true(self):
        assert parse("# X\nflag: true") == {"flag": True}

    def test_false(self):
        assert parse("# X\nflag: false") == {"flag": False}

    def test_null(self):
        assert parse("# X\nval: null") == {"val": None}

    def test_string_unquoted(self):
        assert parse("# X\nstatus: pending") == {"status": "pending"}

    def test_string_quoted_integer(self):
        assert parse('# X\nzip: "10115"') == {"zip": "10115"}

    def test_string_quoted_bool(self):
        assert parse('# X\nval: "true"') == {"val": "true"}

    def test_string_quoted_null(self):
        assert parse('# X\nval: "null"') == {"val": "null"}

    def test_string_with_escapes(self):
        assert parse('# X\nval: "line1\\nline2"') == {"val": "line1\nline2"}

    def test_unicode_value(self):
        assert parse("# X\nname: Müller") == {"name": "Müller"}

    def test_unicode_quoted(self):
        assert parse('# X\nname: "Ö"') == {"name": "Ö"}


# ---------------------------------------------------------------------------
# Keys (spec § 4)
# ---------------------------------------------------------------------------

class TestKeys:
    def test_bare_key(self):
        assert parse("# X\nmy_key: 1") == {"my_key": 1}

    def test_hyphen_key(self):
        assert parse("# X\nmy-key: 1") == {"my-key": 1}

    def test_quoted_key(self):
        assert parse('# X\n"my key": 1') == {"my key": 1}

    def test_quoted_key_with_special_chars(self):
        assert parse('# X\n"key:with:colons": 1') == {"key:with:colons": 1}


# ---------------------------------------------------------------------------
# Nested objects (spec § 7)
# ---------------------------------------------------------------------------

class TestNesting:
    def test_one_level(self):
        result = parse("# Order\n## address\ncity: Berlin")
        assert result == {"address": {"city": "Berlin"}}

    def test_two_levels(self):
        result = parse("# Order\n## address\n### geo\nlat: 52.5")
        assert result == {"address": {"geo": {"lat": 52.5}}}

    def test_sibling_objects(self):
        result = parse("# X\n## a\nv: 1\n## b\nv: 2")
        assert result == {"a": {"v": 1}, "b": {"v": 2}}

    def test_fields_before_and_after_nested(self):
        # Bare fields without a heading belong to the current (child) scope.
        # To return to root, a blank line scope reset is required.
        result = parse("# X\nroot: 1\n## child\nv: 2\nroot2: 3")
        assert result["root"] == 1
        assert result["child"] == {"v": 2, "root2": 3}

    def test_blank_line_scope_reset(self):
        result = parse("# X\n## a\nv: 1\n\nroot: 2")
        assert result == {"a": {"v": 1}, "root": 2}


# ---------------------------------------------------------------------------
# Arrays (spec § 8)
# ---------------------------------------------------------------------------

class TestArrays:
    def test_scalar_array(self):
        result = parse("# X\n## tags[]\n- python\n- jmd\n- llm")
        assert result == {"tags": ["python", "jmd", "llm"]}

    def test_object_array(self):
        result = parse("# X\n## items[]\n- name: A\n  qty: 1\n- name: B\n  qty: 2")
        assert result == {"items": [{"name": "A", "qty": 1}, {"name": "B", "qty": 2}]}

    def test_root_array(self):
        result = parse("# []\n- a\n- b\n- c")
        assert result == ["a", "b", "c"]

    def test_root_object_array(self):
        result = parse("# []\n- id: 1\n  v: x\n- id: 2\n  v: y")
        assert result == [{"id": 1, "v": "x"}, {"id": 2, "v": "y"}]

    def test_thematic_break_between_items(self):
        # Thematic break between array object items (blank-line separated form)
        result = parse("# X\n## items[]\n- name: A\n  qty: 1\n\n- name: B\n  qty: 2")
        assert result == {"items": [{"name": "A", "qty": 1}, {"name": "B", "qty": 2}]}

    def test_mixed_scalar_array_integers(self):
        result = parse("# X\n## ids[]\n- 1\n- 2\n- 3")
        assert result == {"ids": [1, 2, 3]}

    def test_empty_array(self):
        result = parse("# X\n## items[]\n")
        assert result == {"items": []}


# ---------------------------------------------------------------------------
# Blockquote multiline strings (spec § 9)
# ---------------------------------------------------------------------------

class TestBlockquotes:
    def test_single_line_blockquote(self):
        result = parse("# X\nnote:\n> Hello world")
        assert result == {"note": "Hello world"}

    def test_multiline_blockquote(self):
        result = parse("# X\nnote:\n> Line one\n> Line two")
        assert result == {"note": "Line one\nLine two"}

    def test_blockquote_paragraph_break(self):
        result = parse("# X\nnote:\n> Para one\n>\n> Para two")
        assert result == {"note": "Para one\n\nPara two"}


# ---------------------------------------------------------------------------
# Frontmatter (spec § 3.5)
# ---------------------------------------------------------------------------

class TestFrontmatter:
    def test_epistemic_frontmatter_parsed(self):
        p = JMDParser()
        p.parse("confidence: 0.9\nsource: database\n# X\nval: 1")
        assert p.frontmatter.get("confidence") == 0.9
        assert p.frontmatter.get("source") == "database"

    def test_frontmatter_not_in_body(self):
        p = JMDParser()
        result = p.parse("confidence: 0.9\n# X\nval: 1")
        assert "confidence" not in result

    def test_unknown_frontmatter_accepted(self):
        p = JMDParser()
        p.parse("custom_field: hello\n# X\nval: 1")
        assert p.frontmatter.get("custom_field") == "hello"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_label(self):
        result = parse("# \nval: 1")
        assert result == {"val": 1}

    def test_single_field(self):
        assert parse("# X\nk: v") == {"k": "v"}

    def test_numeric_zero(self):
        assert parse("# X\nv: 0") == {"v": 0}

    def test_empty_string_quoted(self):
        assert parse('# X\nv: ""') == {"v": ""}

    def test_deeply_nested(self):
        result = parse("# X\n## a\n### b\n#### c\nv: deep")
        assert result == {"a": {"b": {"c": {"v": "deep"}}}}
