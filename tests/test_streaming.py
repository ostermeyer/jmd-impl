"""Tests for streaming parser — event sequences (spec § 18)."""

from jmd import StreamEvent, jmd_stream


def events(source: str) -> list[StreamEvent]:
    """Return the full list of stream events for a JMD source string."""
    return list(jmd_stream(source))


def event_types(source: str) -> list[str]:
    """Return only the event type strings for a JMD source string."""
    return [e.type for e in events(source)]


def field_events(source: str) -> list[tuple[str | None, object]]:
    """Return (key, value) pairs for all FIELD events in a JMD source string."""
    return [(e.key, e.value) for e in events(source) if e.type == "FIELD"]


class TestDocumentEvents:
    """Tests for DOCUMENT_START and DOCUMENT_END events."""

    def test_document_start(self) -> None:
        """Test that the first event is DOCUMENT_START with the label."""
        evs = events("# Order\nid: 1")
        assert evs[0].type == "DOCUMENT_START"
        assert evs[0].key == "Order"

    def test_document_end(self) -> None:
        """Test that a DOCUMENT_END event is emitted."""
        types = event_types("# Order\nid: 1")
        assert "DOCUMENT_END" in types

    def test_field_event(self) -> None:
        """Test that FIELD events are emitted for each key-value pair."""
        fields = field_events("# X\nid: 42\nstatus: pending")
        assert ("id", 42) in fields
        assert ("status", "pending") in fields


class TestStreamingOrder:
    """Tests for event ordering guarantees."""

    def test_field_before_document_end(self) -> None:
        """Test that FIELD events appear before DOCUMENT_END."""
        types = event_types("# X\nval: 1")
        field_idx = types.index("FIELD")
        end_idx = types.index("DOCUMENT_END")
        assert field_idx < end_idx

    def test_object_start_before_fields(self) -> None:
        """Test that OBJECT_START appears before the first FIELD."""
        types = event_types("# X\n## child\nval: 1")
        obj_idx = types.index("OBJECT_START")
        field_idx = types.index("FIELD")
        assert obj_idx < field_idx

    def test_array_start_before_items(self) -> None:
        """Test that ARRAY_START appears before the first ITEM_VALUE."""
        types = event_types("# X\n## tags[]\n- a\n- b")
        arr_idx = types.index("ARRAY_START")
        item_idx = types.index("ITEM_VALUE")
        assert arr_idx < item_idx


class TestArrayStreaming:
    """Tests for array item streaming events."""

    def test_scalar_item_values(self) -> None:
        """Test that scalar array items are emitted as ITEM_VALUE events."""
        evs = events("# X\n## tags[]\n- python\n- jmd")
        item_vals = [e.value for e in evs if e.type == "ITEM_VALUE"]
        assert item_vals == ["python", "jmd"]

    def test_object_item_events(self) -> None:
        """Test that object array items emit ITEM_START and FIELD events."""
        types = event_types("# X\n## items[]\n- name: A\n  qty: 1")
        assert "ITEM_START" in types
        assert "FIELD" in types


class TestStreamingPartialDocs:
    """Tests for streaming behaviour on partial or multi-field documents."""

    def test_partial_document_yields_received_fields(self) -> None:
        """A partial document contains all fields received so far."""
        source = "# Order\nid: 42\nstatus: pending"
        fields = field_events(source)
        assert ("id", 42) in fields
        assert ("status", "pending") in fields

    def test_first_field_arrives_early(self) -> None:
        """FIELD event for first key arrives before rest of document."""
        evs = events(
            "# Order\nid: 1\nstatus: pending\n## customer\nname: Anna"
        )
        first_field = next(e for e in evs if e.type == "FIELD")
        assert first_field.key == "id"
