# SPDX-License-Identifier: Apache-2.0
"""Tests for streaming parser — event sequences (spec § 18)."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import jmd
from jmd import StreamEvent, jmd_stream
from jmd._streaming import (
    JMDStreamParser,
    to_lines,
)
from jmd._streaming import (
    events as async_stream_events,
)


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


# ---------------------------------------------------------------------------
# Slice B — push-style streaming API (createParser parity with jmd-js)
# ---------------------------------------------------------------------------


class TestStreamFrontmatter:
    """§18: frontmatter rides on the DOCUMENT_START envelope header."""

    def test_simple_frontmatter(self) -> None:
        """Test that key: value frontmatter rides on DOCUMENT_START."""
        src = "confidence: high\nsource: ledger\n\n# Order\nid: 42\n"
        evs = list(jmd.jmd_stream(src))
        assert evs[0].type == "DOCUMENT_START"
        assert evs[0].mode == "data"
        assert evs[0].key == "Order"
        assert evs[0].frontmatter == {
            "confidence": "high",
            "source": "ledger",
        }
        # Per §18, no separate FRONTMATTER events follow.
        assert not any(e.type == "FRONTMATTER" for e in evs)

    def test_dash_markers_tolerated(self) -> None:
        """Test §3.5.1: --- markers around frontmatter are consumed."""
        src = "---\nconfidence: high\n---\n\n# Order\nid: 42\n"
        evs = list(jmd.jmd_stream(src))
        assert evs[0].type == "DOCUMENT_START"
        assert evs[0].frontmatter == {"confidence": "high"}

    def test_multiline_frontmatter(self) -> None:
        """Test D12: multi-line frontmatter values via key: + blockquote."""
        src = "summary:\n> line one\n> line two\n\n# Doc\nx: 1\n"
        evs = list(jmd.jmd_stream(src))
        assert evs[0].type == "DOCUMENT_START"
        assert evs[0].frontmatter == {"summary": "line one\nline two"}

    def test_no_frontmatter_emits_empty_dict(self) -> None:
        """Test that absent frontmatter yields ``{}`` on DOCUMENT_START."""
        evs = list(jmd.jmd_stream("# Order\nid: 1\n"))
        assert evs[0].type == "DOCUMENT_START"
        assert evs[0].frontmatter == {}

    def test_document_start_carries_mode(self) -> None:
        """Test that the four modes surface on DOCUMENT_START.mode."""
        modes = {
            "# Order\nid: 1": "data",
            "#? Order\nstatus: active": "query",
            "#! Order\nid: integer": "schema",
            "#- Order\nid: 1": "delete",
        }
        for src, expected in modes.items():
            evs = list(jmd.jmd_stream(src))
            assert evs[0].mode == expected, f"for {src!r}"


class TestJMDStreamParser:
    """B.2: class-based push API mirrors jmd-js createParser()."""

    def test_class_events_helper(self) -> None:
        """Test JMDStreamParser.events over an iterable of lines."""
        evs = list(JMDStreamParser.events(["# Order", "id: 42"]))
        types = [e.type for e in evs]
        assert types == ["DOCUMENT_START", "FIELD", "DOCUMENT_END"]

    def test_process_line_finish(self) -> None:
        """Test push API: process_line accumulates, finish drains."""
        p = JMDStreamParser()
        assert p.process_line("# Doc") == []
        assert p.process_line("x: 1") == []
        evs = p.finish()
        types = [e.type for e in evs]
        assert types == ["DOCUMENT_START", "FIELD", "DOCUMENT_END"]

    def test_finish_idempotent(self) -> None:
        """Test that a second finish() call returns []."""
        p = JMDStreamParser()
        p.process_line("# Doc")
        p.finish()
        assert p.finish() == []

    def test_process_after_finish_raises(self) -> None:
        """Test that process_line after finish() raises RuntimeError."""
        p = JMDStreamParser()
        p.finish()
        try:
            p.process_line("# Late")
        except RuntimeError:
            return
        raise AssertionError("expected RuntimeError")


class TestAsyncStreamingAPI:
    """B.3: async events() + to_lines() match jmd-js's async pair."""

    def test_to_lines_splits_chunks(self) -> None:
        """Test that to_lines splits an async iterable of arbitrary chunks."""
        async def chunks() -> AsyncIterator[str]:
            yield "# As"
            yield "ync\nid: "
            yield "9\n"

        async def go() -> list[str]:
            return [line async for line in to_lines(chunks())]

        assert asyncio.run(go()) == ["# Async", "id: 9"]

    def test_to_lines_yields_trailing_unterminated(self) -> None:
        """Test that to_lines emits the final unterminated line."""
        async def chunks() -> AsyncIterator[str]:
            yield "a\nb"

        async def go() -> list[str]:
            return [line async for line in to_lines(chunks())]

        assert asyncio.run(go()) == ["a", "b"]

    def test_to_lines_strips_carriage_return(self) -> None:
        r"""Test that to_lines strips trailing \r from lines."""
        async def chunks() -> AsyncIterator[str]:
            yield "a\r\nb\r\n"

        async def go() -> list[str]:
            return [line async for line in to_lines(chunks())]

        assert asyncio.run(go()) == ["a", "b"]

    def test_async_events_pipeline(self) -> None:
        """Test that async events() reads from an async line source."""
        async def lines() -> AsyncIterator[str]:
            for ln in ["# Doc", "id: 7"]:
                yield ln

        async def go() -> list[tuple[Any, ...]]:
            return [
                (e.type, e.key, e.value)
                async for e in async_stream_events(lines())
            ]

        result = asyncio.run(go())
        # DOCUMENT_START now carries envelope header (mode + frontmatter)
        # in dedicated fields; the (type, key, value) tuple form keeps
        # value=None for DOCUMENT_START events.
        assert result == [
            ("DOCUMENT_START", "Doc", None),
            ("FIELD", "id", 7),
            ("DOCUMENT_END", None, None),
        ]

    def test_async_pipeline_end_to_end(self) -> None:
        """Test to_lines + async events composed over byte chunks."""
        async def chunks() -> AsyncIterator[bytes]:
            yield b"# Doc\nid: "
            yield b"42\n"

        async def go() -> list[tuple[Any, ...]]:
            return [
                (e.type, e.key, e.value)
                async for e in async_stream_events(to_lines(chunks()))
            ]

        result = asyncio.run(go())
        assert ("FIELD", "id", 42) in result
