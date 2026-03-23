"""Tests for JMD Delete Documents (spec § 15)."""

import pytest

from jmd import JMDDelete, JMDDeleteParser


def parse_delete(source: str) -> JMDDelete:
    """Parse a delete document from source text."""
    return JMDDeleteParser().parse(source)


class TestDeleteParsing:
    """Tests for JMDDeleteParser."""

    def test_single_resource_label(self) -> None:
        """Test that a single-resource delete document has the correct label."""
        d = parse_delete("#- Order\nid: 42")
        assert d.label == "Order"

    def test_single_resource_identifiers(self) -> None:
        """Test that a single-resource delete has the correct identifiers."""
        d = parse_delete("#- Order\nid: 42")
        assert d.identifiers == {"id": 42}
        assert d.is_bulk is False

    def test_composite_key(self) -> None:
        """Test that a composite key is parsed as a dict with all key fields."""
        d = parse_delete("#- Order\ntable: orders\nid: 42")
        assert d.identifiers == {"table": "orders", "id": 42}

    def test_bulk_deletion_is_bulk(self) -> None:
        """Test that a bulk delete document is identified as bulk."""
        d = parse_delete("#- []\n- abc123\n- def456")
        assert d.is_bulk is True
        assert d.label == ""

    def test_bulk_deletion_scalar_ids(self) -> None:
        """Test that scalar IDs in a bulk delete are parsed as a list."""
        d = parse_delete("#- []\n- abc123\n- def456\n- ghi789")
        assert d.identifiers == ["abc123", "def456", "ghi789"]

    def test_bulk_deletion_object_ids(self) -> None:
        """Test that object IDs in a bulk delete are parsed as dicts."""
        d = parse_delete(
            "#- []\n- table: orders\n  id: 42\n- table: orders\n  id: 43"
        )
        assert len(d.identifiers) == 2
        assert d.identifiers[0] == {"table": "orders", "id": 42}

    def test_wrong_marker_raises(self) -> None:
        """Test that a data document marker raises ValueError."""
        with pytest.raises(ValueError):
            parse_delete("# Order\nid: 42")

    def test_query_marker_raises(self) -> None:
        """Test that a query document marker raises ValueError."""
        with pytest.raises(ValueError):
            parse_delete("#? Order\nid: 42")
