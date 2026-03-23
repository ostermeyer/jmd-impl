"""Lossless roundtrip tests: JSON → JMD → JSON (spec § 1)."""

import json
from typing import Any

from jmd import JMDParser, JMDSerializer


def roundtrip(data: Any, label: str = "Document") -> Any:
    """Serialize data to JMD and parse it back to a Python value."""
    jmd_text = JMDSerializer().serialize(data, label=label)
    return JMDParser().parse(jmd_text)


class TestRoundtrip:
    """Tests for lossless serialization and parsing roundtrips."""

    def test_flat_object(self) -> None:
        """Test that a flat dict with mixed types survives a roundtrip."""
        data = {
            "id": 1, "name": "Test", "active": True, "score": 9.5, "note": None
        }
        assert roundtrip(data) == data

    def test_nested_object(self) -> None:
        """Test that a nested dict survives a roundtrip."""
        data = {"user": {"name": "Anna", "age": 30}, "active": True}
        assert roundtrip(data) == data

    def test_scalar_array(self) -> None:
        """Test that a scalar list value survives a roundtrip."""
        data = {"tags": ["a", "b", "c"]}
        assert roundtrip(data) == data

    def test_object_array(self) -> None:
        """Test that a list of dicts survives a roundtrip."""
        data = {"items": [{"id": 1, "v": "x"}, {"id": 2, "v": "y"}]}
        assert roundtrip(data) == data

    def test_root_array(self) -> None:
        """Test that a root-level list survives a roundtrip."""
        data = [{"id": 1}, {"id": 2}]
        assert roundtrip(data, label="[]") == data

    def test_unicode(self) -> None:
        """Test that Unicode string values survive a roundtrip."""
        data = {"name": "Müller", "city": "Köln"}
        assert roundtrip(data) == data

    def test_special_string_values(self) -> None:
        """Test that strings that look like scalars survive a roundtrip."""
        data = {"zip": "10115", "flag": "true", "empty": ""}
        assert roundtrip(data) == data

    def test_deeply_nested(self) -> None:
        """Test that a deeply nested dict survives a roundtrip."""
        data = {"a": {"b": {"c": {"d": {"v": 42}}}}}
        assert roundtrip(data) == data

    def test_multitype_array(self) -> None:
        """Test that an integer list survives a roundtrip."""
        data = {"ids": [1, 2, 3]}
        assert roundtrip(data) == data

    def test_large_object(self) -> None:
        """Test that an object with many keys survives a roundtrip."""
        data = {f"key_{i}": i for i in range(50)}
        assert roundtrip(data) == data

    def test_empty_dict(self) -> None:
        """Test that an empty dict survives a roundtrip."""
        assert roundtrip({}) == {}

    def test_empty_array(self) -> None:
        """Test that an empty list value survives a roundtrip."""
        assert roundtrip({"items": []}) == {"items": []}

    def test_json_roundtrip(self) -> None:
        """JSON → JMD → JSON preserves the JSON value (spec § 1)."""
        original = {
            "order_id": 4521,
            "status": "confirmed",
            "paid": True,
            "total": 129.75,
            "note": None,
            "customer": {"name": "Anna Müller", "vip": False},
            "items": [
                {"product": "Laptop Stand", "qty": 1, "price": 49.90},
                {"product": "USB Hub", "qty": 2, "price": 19.95},
            ],
        }
        jmd_text = JMDSerializer().serialize(original, label="Order")
        recovered = JMDParser().parse(jmd_text)
        assert json.loads(json.dumps(original)) == recovered
