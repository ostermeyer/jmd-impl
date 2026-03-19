"""Tests for JMDSerializer (spec §§ 2–11)."""

import pytest
from jmd import JMDSerializer


def serialize(data, label="X"):
    return JMDSerializer().serialize(data, label=label)


class TestScalarSerialization:
    def test_integer(self):
        assert "v: 42" in serialize({"v": 42})

    def test_float(self):
        assert "v: 9.99" in serialize({"v": 9.99})

    def test_true(self):
        assert "flag: true" in serialize({"flag": True})

    def test_false(self):
        assert "flag: false" in serialize({"flag": False})

    def test_null(self):
        assert "val: null" in serialize({"val": None})

    def test_plain_string(self):
        assert "status: pending" in serialize({"status": "pending"})

    def test_string_that_looks_like_int(self):
        out = serialize({"zip": "10115"})
        assert 'zip: "10115"' in out

    def test_string_true_quoted(self):
        out = serialize({"v": "true"})
        assert 'v: "true"' in out

    def test_string_null_quoted(self):
        out = serialize({"v": "null"})
        assert 'v: "null"' in out

    def test_string_with_newline(self):
        # Multiline strings are serialized as blockquotes
        out = serialize({"v": "line1\nline2"})
        assert "> line1" in out
        assert "> line2" in out


class TestRootHeading:
    def test_root_label(self):
        out = serialize({"k": 1}, label="Order")
        assert out.startswith("# Order\n")

    def test_root_array(self):
        out = serialize(["a", "b"], label="[]")
        assert out.startswith("# []\n")
        assert "- a" in out
        assert "- b" in out


class TestNestingSerialization:
    def test_nested_object(self):
        out = serialize({"address": {"city": "Berlin"}})
        assert "## address\n" in out
        assert "city: Berlin" in out

    def test_deeply_nested(self):
        out = serialize({"a": {"b": {"v": 1}}})
        assert "## a\n" in out
        assert "### b\n" in out
        assert "v: 1" in out


class TestArraySerialization:
    def test_scalar_array(self):
        out = serialize({"tags": ["a", "b"]})
        assert "## tags[]\n" in out
        assert "- a\n" in out

    def test_object_array(self):
        out = serialize({"items": [{"name": "X", "qty": 1}]})
        assert "## items[]\n" in out
        assert "- name: X\n" in out
        assert "  qty: 1" in out


class TestKeyQuoting:
    def test_key_with_space_quoted(self):
        out = serialize({"my key": 1})
        assert '"my key": 1' in out

    def test_normal_key_not_quoted(self):
        out = serialize({"my_key": 1})
        assert "my_key: 1" in out
        assert '"my_key"' not in out
