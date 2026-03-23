"""Tests for JMDSerializer (spec §§ 2–11)."""

from typing import Any

from jmd import JMDSerializer


def serialize(data: Any, label: str = "X") -> str:
    """Serialize data to a JMD string with the given label."""
    return JMDSerializer().serialize(data, label=label)


class TestScalarSerialization:
    """Tests for scalar value serialization."""

    def test_integer(self) -> None:
        """Test that an integer is serialized without quotes."""
        assert "v: 42" in serialize({"v": 42})

    def test_float(self) -> None:
        """Test that a float is serialized without quotes."""
        assert "v: 9.99" in serialize({"v": 9.99})

    def test_true(self) -> None:
        """Test that True is serialized as the bare word true."""
        assert "flag: true" in serialize({"flag": True})

    def test_false(self) -> None:
        """Test that False is serialized as the bare word false."""
        assert "flag: false" in serialize({"flag": False})

    def test_null(self) -> None:
        """Test that None is serialized as the bare word null."""
        assert "val: null" in serialize({"val": None})

    def test_plain_string(self) -> None:
        """Test that a plain string is serialized without quotes."""
        assert "status: pending" in serialize({"status": "pending"})

    def test_string_that_looks_like_int(self) -> None:
        """Test that a string that looks like an integer is quoted."""
        out = serialize({"zip": "10115"})
        assert 'zip: "10115"' in out

    def test_string_true_quoted(self) -> None:
        """Test that the string 'true' is quoted to distinguish it from bool."""
        out = serialize({"v": "true"})
        assert 'v: "true"' in out

    def test_string_null_quoted(self) -> None:
        """Test that the string 'null' is quoted to distinguish it from None."""
        out = serialize({"v": "null"})
        assert 'v: "null"' in out

    def test_string_with_newline(self) -> None:
        """Test that a string with newlines is serialized as a blockquote."""
        # Multiline strings are serialized as blockquotes
        out = serialize({"v": "line1\nline2"})
        assert "> line1" in out
        assert "> line2" in out


class TestRootHeading:
    """Tests for the root heading line in serialized output."""

    def test_root_label(self) -> None:
        """Test that the root label appears as a level-1 heading."""
        out = serialize({"k": 1}, label="Order")
        assert out.startswith("# Order\n")

    def test_root_array(self) -> None:
        """Test that a root array is serialized with the [] label."""
        out = serialize(["a", "b"], label="[]")
        assert out.startswith("# []\n")
        assert "- a" in out
        assert "- b" in out


class TestNestingSerialization:
    """Tests for nested object serialization."""

    def test_nested_object(self) -> None:
        """Test that a nested dict is serialized with a level-2 heading."""
        out = serialize({"address": {"city": "Berlin"}})
        assert "## address\n" in out
        assert "city: Berlin" in out

    def test_deeply_nested(self) -> None:
        """Test that two nesting levels produce level-2 and level-3 headings."""
        out = serialize({"a": {"b": {"v": 1}}})
        assert "## a\n" in out
        assert "### b\n" in out
        assert "v: 1" in out


class TestArraySerialization:
    """Tests for array serialization."""

    def test_scalar_array(self) -> None:
        """Test that a scalar list is serialized with an array heading."""
        out = serialize({"tags": ["a", "b"]})
        assert "## tags[]\n" in out
        assert "- a\n" in out

    def test_object_array(self) -> None:
        """Test that a list of dicts is serialized with indented pairs."""
        out = serialize({"items": [{"name": "X", "qty": 1}]})
        assert "## items[]\n" in out
        assert "- name: X\n" in out
        assert "  qty: 1" in out


class TestKeyQuoting:
    """Tests for key quoting rules."""

    def test_key_with_space_quoted(self) -> None:
        """Test that a key containing a space is quoted."""
        out = serialize({"my key": 1})
        assert '"my key": 1' in out

    def test_normal_key_not_quoted(self) -> None:
        """Test that a normal snake_case key is not quoted."""
        out = serialize({"my_key": 1})
        assert "my_key: 1" in out
        assert '"my_key"' not in out
