"""Tests for document mode detection and top-level API (spec § 19)."""

import pytest
from jmd import jmd_mode, parse, serialize


class TestModeDetection:
    def test_data_mode(self):
        assert jmd_mode("# Order\nid: 42") == "data"

    def test_query_mode(self):
        assert jmd_mode("#? Order\nstatus: active") == "query"

    def test_schema_mode(self):
        assert jmd_mode("#! Order\nid: integer") == "schema"

    def test_delete_mode(self):
        assert jmd_mode("#- Order\nid: 42") == "delete"

    def test_error_is_data_mode(self):
        # Error documents are standard data documents with reserved label
        assert jmd_mode("# Error\nstatus: 404") == "data"

    def test_leading_blank_lines(self):
        assert jmd_mode("\n\n# Order\nid: 1") == "data"

    def test_frontmatter_before_heading(self):
        assert jmd_mode("confidence: 0.9\n# Order\nid: 1") == "data"


class TestTopLevelAPI:
    def test_parse_returns_dict(self):
        result = parse("# X\nk: v")
        assert result == {"k": "v"}

    def test_serialize_returns_string(self):
        out = serialize({"k": "v"}, label="X")
        assert out.startswith("# X\n")
        assert "k: v" in out

    def test_parse_serialize_roundtrip(self):
        data = {"id": 1, "name": "Test", "active": True}
        assert parse(serialize(data, label="X")) == data
