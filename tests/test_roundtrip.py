"""Lossless roundtrip tests: JSON → JMD → JSON (spec § 1)."""

import json
import pytest
from jmd import JMDParser, JMDSerializer


def roundtrip(data, label="Document"):
    jmd_text = JMDSerializer().serialize(data, label=label)
    return JMDParser().parse(jmd_text)


class TestRoundtrip:
    def test_flat_object(self):
        data = {"id": 1, "name": "Test", "active": True, "score": 9.5, "note": None}
        assert roundtrip(data) == data

    def test_nested_object(self):
        data = {"user": {"name": "Anna", "age": 30}, "active": True}
        assert roundtrip(data) == data

    def test_scalar_array(self):
        data = {"tags": ["a", "b", "c"]}
        assert roundtrip(data) == data

    def test_object_array(self):
        data = {"items": [{"id": 1, "v": "x"}, {"id": 2, "v": "y"}]}
        assert roundtrip(data) == data

    def test_root_array(self):
        data = [{"id": 1}, {"id": 2}]
        assert roundtrip(data, label="[]") == data

    def test_unicode(self):
        data = {"name": "Müller", "city": "Köln"}
        assert roundtrip(data) == data

    def test_special_string_values(self):
        data = {"zip": "10115", "flag": "true", "empty": ""}
        assert roundtrip(data) == data

    def test_deeply_nested(self):
        data = {"a": {"b": {"c": {"d": {"v": 42}}}}}
        assert roundtrip(data) == data

    def test_multitype_array(self):
        data = {"ids": [1, 2, 3]}
        assert roundtrip(data) == data

    def test_large_object(self):
        data = {f"key_{i}": i for i in range(50)}
        assert roundtrip(data) == data

    def test_empty_dict(self):
        assert roundtrip({}) == {}

    def test_empty_array(self):
        assert roundtrip({"items": []}) == {"items": []}

    def test_json_roundtrip(self):
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
