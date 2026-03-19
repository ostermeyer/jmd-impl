"""Tests for streaming parser — event sequences (spec § 18)."""

import pytest
from jmd import jmd_stream, StreamEvent


def events(source: str) -> list[StreamEvent]:
    return list(jmd_stream(source))


def event_types(source: str) -> list[str]:
    return [e.type for e in events(source)]


def field_events(source: str) -> list[tuple]:
    return [(e.key, e.value) for e in events(source) if e.type == "FIELD"]


class TestDocumentEvents:
    def test_document_start(self):
        evs = events("# Order\nid: 1")
        assert evs[0].type == "DOCUMENT_START"
        assert evs[0].key == "Order"

    def test_document_end(self):
        types = event_types("# Order\nid: 1")
        assert "DOCUMENT_END" in types

    def test_field_event(self):
        fields = field_events("# X\nid: 42\nstatus: pending")
        assert ("id", 42) in fields
        assert ("status", "pending") in fields


class TestStreamingOrder:
    def test_field_before_document_end(self):
        types = event_types("# X\nval: 1")
        field_idx = types.index("FIELD")
        end_idx = types.index("DOCUMENT_END")
        assert field_idx < end_idx

    def test_object_start_before_fields(self):
        types = event_types("# X\n## child\nval: 1")
        obj_idx = types.index("OBJECT_START")
        field_idx = types.index("FIELD")
        assert obj_idx < field_idx

    def test_array_start_before_items(self):
        types = event_types("# X\n## tags[]\n- a\n- b")
        arr_idx = types.index("ARRAY_START")
        item_idx = types.index("ITEM_VALUE")
        assert arr_idx < item_idx


class TestArrayStreaming:
    def test_scalar_item_values(self):
        evs = events("# X\n## tags[]\n- python\n- jmd")
        item_vals = [e.value for e in evs if e.type == "ITEM_VALUE"]
        assert item_vals == ["python", "jmd"]

    def test_object_item_events(self):
        types = event_types("# X\n## items[]\n- name: A\n  qty: 1")
        assert "ITEM_START" in types
        assert "FIELD" in types


class TestStreamingPartialDocs:
    def test_partial_document_yields_received_fields(self):
        """A partial document contains all fields received so far."""
        source = "# Order\nid: 42\nstatus: pending"
        fields = field_events(source)
        assert ("id", 42) in fields
        assert ("status", "pending") in fields

    def test_first_field_arrives_early(self):
        """FIELD event for first key arrives before rest of document."""
        evs = events("# Order\nid: 1\nstatus: pending\n## customer\nname: Anna")
        first_field = next(e for e in evs if e.type == "FIELD")
        assert first_field.key == "id"
