"""Tests for JMDParser — data document parsing (spec §§ 2–11)."""

from typing import Any

from jmd import JMDParser


def parse(source: str) -> Any:
    """Parse a JMD document string and return the body value."""
    return JMDParser().parse(source)


# ---------------------------------------------------------------------------
# Scalar types (spec § 5)
# ---------------------------------------------------------------------------

class TestScalars:
    """Tests for scalar value parsing."""

    def test_integer(self) -> None:
        """Test that an unquoted integer is parsed as int."""
        assert parse("# X\ncount: 42") == {"count": 42}

    def test_negative_integer(self) -> None:
        """Test that a negative integer is parsed as int."""
        assert parse("# X\nvalue: -7") == {"value": -7}

    def test_float(self) -> None:
        """Test that a decimal number is parsed as float."""
        assert parse("# X\nprice: 9.99") == {"price": 9.99}

    def test_float_scientific(self) -> None:
        """Test that scientific notation is parsed as float."""
        assert parse("# X\nv: 1.5e3") == {"v": 1500.0}

    def test_true(self) -> None:
        """Test that the bare word true is parsed as bool True."""
        assert parse("# X\nflag: true") == {"flag": True}

    def test_false(self) -> None:
        """Test that the bare word false is parsed as bool False."""
        assert parse("# X\nflag: false") == {"flag": False}

    def test_null(self) -> None:
        """Test that the bare word null is parsed as None."""
        assert parse("# X\nval: null") == {"val": None}

    def test_string_unquoted(self) -> None:
        """Test that an unquoted non-keyword value is parsed as string."""
        assert parse("# X\nstatus: pending") == {"status": "pending"}

    def test_string_quoted_integer(self) -> None:
        """Test that a quoted integer-like value is parsed as string."""
        assert parse('# X\nzip: "10115"') == {"zip": "10115"}

    def test_string_quoted_bool(self) -> None:
        """Test that a quoted boolean-like value is parsed as string."""
        assert parse('# X\nval: "true"') == {"val": "true"}

    def test_string_quoted_null(self) -> None:
        """Test that a quoted null-like value is parsed as string."""
        assert parse('# X\nval: "null"') == {"val": "null"}

    def test_string_with_escapes(self) -> None:
        """Test that escape sequences inside quoted strings are interpreted."""
        assert parse('# X\nval: "line1\\nline2"') == {"val": "line1\nline2"}

    def test_unicode_value(self) -> None:
        """Test that unquoted Unicode values are parsed correctly."""
        assert parse("# X\nname: Müller") == {"name": "Müller"}

    def test_unicode_quoted(self) -> None:
        """Test that quoted Unicode values are parsed correctly."""
        assert parse('# X\nname: "Ö"') == {"name": "Ö"}


# ---------------------------------------------------------------------------
# Keys (spec § 4)
# ---------------------------------------------------------------------------

class TestKeys:
    """Tests for key syntax variants."""

    def test_bare_key(self) -> None:
        """Test that a bare snake_case key is accepted."""
        assert parse("# X\nmy_key: 1") == {"my_key": 1}

    def test_hyphen_key(self) -> None:
        """Test that a hyphenated key is accepted."""
        assert parse("# X\nmy-key: 1") == {"my-key": 1}

    def test_quoted_key(self) -> None:
        """Test that a quoted key with spaces is accepted."""
        assert parse('# X\n"my key": 1') == {"my key": 1}

    def test_quoted_key_with_special_chars(self) -> None:
        """Test that a quoted key with colons is accepted."""
        assert parse('# X\n"key:with:colons": 1') == {"key:with:colons": 1}


# ---------------------------------------------------------------------------
# Nested objects (spec § 7)
# ---------------------------------------------------------------------------

class TestNesting:
    """Tests for nested object parsing via heading levels."""

    def test_one_level(self) -> None:
        """Test that a level-2 heading creates a one-level nested object."""
        result = parse("# Order\n## address\ncity: Berlin")
        assert result == {"address": {"city": "Berlin"}}

    def test_two_levels(self) -> None:
        """Test that level-2 and level-3 headings create two-level nesting."""
        result = parse("# Order\n## address\n### geo\nlat: 52.5")
        assert result == {"address": {"geo": {"lat": 52.5}}}

    def test_sibling_objects(self) -> None:
        """Test that two level-2 headings create two sibling objects."""
        result = parse("# X\n## a\nv: 1\n## b\nv: 2")
        assert result == {"a": {"v": 1}, "b": {"v": 2}}

    def test_fields_before_and_after_nested(self) -> None:
        """Test that fields after a nested heading belong to the child scope."""
        # Bare fields without a heading belong to the current (child) scope.
        # To return to root, a blank line scope reset is required.
        result = parse("# X\nroot: 1\n## child\nv: 2\nroot2: 3")
        assert result["root"] == 1
        assert result["child"] == {"v": 2, "root2": 3}

    def test_blank_line_scope_reset(self) -> None:
        """Test that a blank line resets scope to root level."""
        result = parse("# X\n## a\nv: 1\n\nroot: 2")
        assert result == {"a": {"v": 1}, "root": 2}


# ---------------------------------------------------------------------------
# Arrays (spec § 8)
# ---------------------------------------------------------------------------

class TestArrays:
    """Tests for array parsing."""

    def test_scalar_array(self) -> None:
        """Test that a scalar array is parsed as a list of values."""
        result = parse("# X\n## tags[]\n- python\n- jmd\n- llm")
        assert result == {"tags": ["python", "jmd", "llm"]}

    def test_object_array(self) -> None:
        """Test that an object array is parsed as a list of dicts."""
        result = parse(
            "# X\n## items[]\n- name: A\n  qty: 1\n- name: B\n  qty: 2"
        )
        assert result == {
            "items": [{"name": "A", "qty": 1}, {"name": "B", "qty": 2}]
        }

    def test_root_array(self) -> None:
        """Test that a root array document is parsed as a list."""
        result = parse("# []\n- a\n- b\n- c")
        assert result == ["a", "b", "c"]

    def test_root_object_array(self) -> None:
        """Test that a root array of objects is parsed as a list of dicts."""
        result = parse("# []\n- id: 1\n  v: x\n- id: 2\n  v: y")
        assert result == [{"id": 1, "v": "x"}, {"id": 2, "v": "y"}]

    def test_thematic_break_between_items(self) -> None:
        """Test that blank lines between array items are accepted."""
        # Thematic break between array object items (blank-line separated form)
        result = parse(
            "# X\n## items[]\n- name: A\n  qty: 1\n\n- name: B\n  qty: 2"
        )
        assert result == {
            "items": [{"name": "A", "qty": 1}, {"name": "B", "qty": 2}]
        }

    def test_mixed_scalar_array_integers(self) -> None:
        """Test that an array of integer strings is parsed as a list of ints."""
        result = parse("# X\n## ids[]\n- 1\n- 2\n- 3")
        assert result == {"ids": [1, 2, 3]}

    def test_empty_array(self) -> None:
        """Test that an array section with no items produces an empty list."""
        result = parse("# X\n## items[]\n")
        assert result == {"items": []}


# ---------------------------------------------------------------------------
# Blockquote multiline strings (spec § 9)
# ---------------------------------------------------------------------------

class TestBlockquotes:
    """Tests for blockquote multiline string parsing."""

    def test_single_line_blockquote(self) -> None:
        """Test that a single blockquote line is parsed as a plain string."""
        result = parse("# X\nnote:\n> Hello world")
        assert result == {"note": "Hello world"}

    def test_multiline_blockquote(self) -> None:
        """Test that consecutive blockquote lines are joined with newlines."""
        result = parse("# X\nnote:\n> Line one\n> Line two")
        assert result == {"note": "Line one\nLine two"}

    def test_blockquote_paragraph_break(self) -> None:
        """Test that an empty blockquote line produces a paragraph break."""
        result = parse("# X\nnote:\n> Para one\n>\n> Para two")
        assert result == {"note": "Para one\n\nPara two"}


# ---------------------------------------------------------------------------
# Frontmatter (spec § 3.5)
# ---------------------------------------------------------------------------

class TestFrontmatter:
    """Tests for frontmatter parsing."""

    def test_epistemic_frontmatter_parsed(self) -> None:
        """Test that frontmatter fields are available on the parser instance."""
        p = JMDParser()
        p.parse("confidence: 0.9\nsource: database\n# X\nval: 1")
        assert p.frontmatter.get("confidence") == 0.9
        assert p.frontmatter.get("source") == "database"

    def test_frontmatter_not_in_body(self) -> None:
        """Test that frontmatter keys are not included in the document body."""
        p = JMDParser()
        result = p.parse("confidence: 0.9\n# X\nval: 1")
        assert "confidence" not in result

    def test_unknown_frontmatter_accepted(self) -> None:
        """Test that unknown frontmatter keys are accepted without error."""
        p = JMDParser()
        p.parse("custom_field: hello\n# X\nval: 1")
        assert p.frontmatter.get("custom_field") == "hello"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_empty_label(self) -> None:
        """Test that a heading with no label text is accepted."""
        result = parse("# \nval: 1")
        assert result == {"val": 1}

    def test_single_field(self) -> None:
        """Test that a document with a single field is parsed correctly."""
        assert parse("# X\nk: v") == {"k": "v"}

    def test_numeric_zero(self) -> None:
        """Test that zero is parsed as int 0, not as a falsy string."""
        assert parse("# X\nv: 0") == {"v": 0}

    def test_empty_string_quoted(self) -> None:
        """Test that an empty quoted string is parsed as an empty string."""
        assert parse('# X\nv: ""') == {"v": ""}

    def test_deeply_nested(self) -> None:
        """Test that four levels of heading nesting are parsed correctly."""
        result = parse("# X\n## a\n### b\n#### c\nv: deep")
        assert result == {"a": {"b": {"c": {"v": "deep"}}}}
