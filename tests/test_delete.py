"""Tests for JMD Delete Documents (spec § 15)."""

import pytest
from jmd import JMDDeleteParser, JMDDelete


def parse_delete(source: str) -> JMDDelete:
    return JMDDeleteParser().parse(source)


class TestDeleteParsing:
    def test_single_resource_label(self):
        d = parse_delete("#- Order\nid: 42")
        assert d.label == "Order"

    def test_single_resource_identifiers(self):
        d = parse_delete("#- Order\nid: 42")
        assert d.identifiers == {"id": 42}
        assert d.is_bulk is False

    def test_composite_key(self):
        d = parse_delete("#- Order\ntable: orders\nid: 42")
        assert d.identifiers == {"table": "orders", "id": 42}

    def test_bulk_deletion_is_bulk(self):
        d = parse_delete("#- []\n- abc123\n- def456")
        assert d.is_bulk is True
        assert d.label == ""

    def test_bulk_deletion_scalar_ids(self):
        d = parse_delete("#- []\n- abc123\n- def456\n- ghi789")
        assert d.identifiers == ["abc123", "def456", "ghi789"]

    def test_bulk_deletion_object_ids(self):
        d = parse_delete("#- []\n- table: orders\n  id: 42\n- table: orders\n  id: 43")
        assert len(d.identifiers) == 2
        assert d.identifiers[0] == {"table": "orders", "id": 42}

    def test_wrong_marker_raises(self):
        with pytest.raises(ValueError):
            parse_delete("# Order\nid: 42")

    def test_query_marker_raises(self):
        with pytest.raises(ValueError):
            parse_delete("#? Order\nid: 42")
