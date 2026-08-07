# SPDX-License-Identifier: Apache-2.0
"""Tests for streaming parser — event sequences (spec § 18)."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

import jmd
from jmd import StreamEvent, jmd_stream
from jmd._parser import JMDParseError
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

    @staticmethod
    @pytest.mark.parametrize(
        ("opener", "content_indent", "expected"),
        [
            pytest.param(
                " |", "    alpha\n    beta", "alpha\nbeta", id="literal"
            ),
            pytest.param(
                " >", "    alpha\n    beta", "alpha beta", id="folded"
            ),
        ],
    )
    def test_multiline_item_preserves_following_data(
        opener: str,
        content_indent: str,
        expected: str,
    ) -> None:
        """Stream multiline item fields without losing later data."""
        source = (
            "# Records[]\n"
            "- id: 1\n"
            f"  note:{opener}\n"
            f"{content_indent}\n"
            "  tail: after\n"
            "- id: 2\n"
            "  tail: later\n"
        )
        fields = field_events(source)
        assert ("note", expected) in fields
        assert ("tail", "after") in fields
        assert ("tail", "later") in fields

    def test_blockquote_item_streams_content_and_later_fields(self) -> None:
        """Stream canonical multiline content without losing later data."""
        source = (
            "# Records[]\n"
            "- id: 1\n"
            "  note:\n"
            "  > alpha\n"
            "  > beta\n"
            "  tail: after\n"
            "- id: 2\n"
            "  tail: later\n"
        )
        streamed = events(source)
        selected = [
            (event.type, event.key, event.value)
            for event in streamed
            if event.type in {"FIELD", "FIELD_START", "FIELD_CONTENT"}
        ]
        assert ("FIELD_START", "note", None) in selected
        assert ("FIELD_CONTENT", None, "alpha") in selected
        assert ("FIELD_CONTENT", None, "beta") in selected
        assert ("FIELD", "tail", "after") in selected
        assert ("FIELD", "tail", "later") in selected


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
        evs = events("# Order\nid: 1\nstatus: pending\n## customer\nname: Anna")
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
        assert types == [
            "DOCUMENT_START",
            "OBJECT_START",
            "FIELD",
            "OBJECT_END",
            "DOCUMENT_END",
        ]

    def test_process_line_emits_completed_lines(self) -> None:
        """Emit each semantic event when its completed line arrives (§18.2)."""
        parser = JMDStreamParser()
        root_events = parser.process_line("# Doc")
        # §18.2: the root heading opens the root scope as well.
        assert [event.type for event in root_events] == [
            "DOCUMENT_START",
            "OBJECT_START",
        ]

        field_events = parser.process_line("x: 1")
        assert [event.type for event in field_events] == ["FIELD"]
        assert [event.type for event in parser.finish()] == [
            "OBJECT_END",
            "DOCUMENT_END",
        ]

    def test_process_line_streams_blockquote_content(self) -> None:
        """Emit FIELD_START and each FIELD_CONTENT line independently."""
        parser = JMDStreamParser()
        parser.process_line("# Doc")

        start_events = parser.process_line("note:")
        assert [
            (event.type, event.key, event.value) for event in start_events
        ] == [("FIELD_START", "note", None)]

        content_events = parser.process_line("> hello")
        assert [
            (event.type, event.key, event.value) for event in content_events
        ] == [("FIELD_CONTENT", None, "hello")]

    def test_process_line_emits_structural_events(self) -> None:
        """Close and open scopes as soon as a structural line completes."""
        parser = JMDStreamParser()
        parser.process_line("# Doc")

        assert [
            event.type for event in parser.process_line("## child")
        ] == ["OBJECT_START"]
        assert [event.type for event in parser.process_line("x: 1")] == [
            "FIELD"
        ]
        assert [
            event.type for event in parser.process_line("## tags[]")
        ] == ["OBJECT_END", "ARRAY_START"]
        assert [event.type for event in parser.process_line("- value")] == [
            "ITEM_VALUE"
        ]
        assert [event.type for event in parser.finish()] == [
            "ARRAY_END",
            "OBJECT_END",
            "DOCUMENT_END",
        ]

    def test_blank_line_uses_one_line_scope_reset_lookahead(self) -> None:
        """Resolve a pending blank from the next completed source line."""
        parser = JMDStreamParser()
        parser.process_line("# Doc")
        parser.process_line("## child")
        parser.process_line("x: 1")

        assert parser.process_line("") == []
        resumed = parser.process_line("total: 2")
        assert [event.type for event in resumed] == [
            "SCOPE_RESET",
            "OBJECT_END",
            "FIELD",
        ]

    def test_blank_line_before_array_item_is_cosmetic(self) -> None:
        """Keep an array open when a blank line precedes its next item."""
        parser = JMDStreamParser()
        parser.process_line("# Doc")
        parser.process_line("## tags[]")
        parser.process_line("- first")

        assert parser.process_line("") == []
        assert [event.type for event in parser.process_line("- second")] == [
            "ITEM_VALUE"
        ]

    @pytest.mark.parametrize(
        ("opener", "expected"),
        (("|", "alpha\nbeta"), (">", "alpha beta")),
    )
    def test_block_scalar_emits_aggregate_field_on_close(
        self,
        opener: str,
        expected: str,
    ) -> None:
        """Buffer only a tolerated block scalar and emit it on closure."""
        parser = JMDStreamParser()
        parser.process_line("# Doc")

        assert [
            event.type for event in parser.process_line(f"note: {opener}")
        ] == ["FIELD_START"]
        assert parser.process_line("  alpha") == []
        assert parser.process_line("  beta") == []

        resumed = parser.process_line("tail: after")
        assert [
            (event.type, event.key, event.value) for event in resumed
        ] == [
            ("FIELD", "note", expected),
            ("FIELD", "tail", "after"),
        ]

    def test_frontmatter_is_retained_only_until_document_start(self) -> None:
        """Emit complete frontmatter with the root and not as body events."""
        parser = JMDStreamParser()
        assert parser.process_line("confidence: high") == []
        assert parser.process_line("") == []

        root = parser.process_line("# Order")
        assert [event.type for event in root] == [
            "DOCUMENT_START",
            "OBJECT_START",
        ]
        assert root[0].frontmatter == {"confidence": "high"}

    def test_parser_does_not_retain_completed_source_lines(self) -> None:
        """Do not keep a whole-document source buffer after line processing."""
        parser = JMDStreamParser()
        parser.process_line("# Doc")
        for index in range(100):
            parser.process_line(f"k{index}: {index}")
        assert not hasattr(parser, "_lines")

    @pytest.mark.parametrize(
        ("line", "kind"),
        (
            ("# Other", "second_root_heading"),
            ("#- Other", "mode_marker_mid_document"),
        ),
    )
    def test_rejects_second_document_markers_incrementally(
        self,
        line: str,
        kind: str,
    ) -> None:
        """Reject a second document on the line where it arrives."""
        parser = JMDStreamParser()
        parser.process_line("# Doc")
        with pytest.raises(JMDParseError) as exc:
            parser.process_line(line)
        assert exc.value.kind == kind
        assert exc.value.line == 2

    def test_rejects_in_band_error_after_partial_data(self) -> None:
        """Reject corrected RT-068 after already emitting partial data."""
        parser = JMDStreamParser()
        parser.process_line("# []")
        partial = parser.process_line("- id: 1")
        assert [event.type for event in partial] == ["ITEM_START", "FIELD"]

        with pytest.raises(JMDParseError) as exc:
            parser.process_line("# Error")
        assert exc.value.kind == "second_root_heading"
        assert exc.value.line == 3

    def test_finish_without_root_fails(self) -> None:
        """Match the batch parser when EOF arrives before a root heading."""
        parser = JMDStreamParser()
        parser.process_line("confidence: high")
        with pytest.raises(ValueError, match="No root heading"):
            parser.finish()

    def test_leading_bom_is_consumed_incrementally(self) -> None:
        """Consume one tolerated BOM before tokenizing the first line."""
        parser = JMDStreamParser()
        assert [
            event.type for event in parser.process_line("\ufeff# Doc")
        ] == ["DOCUMENT_START", "OBJECT_START"]

    def test_lone_carriage_return_is_rejected_incrementally(self) -> None:
        r"""Reject a lone \r that is not the CR half of a line ending."""
        parser = JMDStreamParser()
        parser.process_line("# Doc")
        with pytest.raises(JMDParseError) as exc:
            parser.process_line("value: a\rb")
        assert exc.value.kind == "lone_carriage_return"
        assert exc.value.line == 2

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

    def test_to_lines_decodes_utf8_across_chunk_boundaries(self) -> None:
        """Preserve a UTF-8 code point split across byte chunks."""
        async def chunks() -> AsyncIterator[bytes]:
            yield b"# D\xc3"
            yield b"\xb6c\nname: JMD\n"

        async def go() -> list[str]:
            return [line async for line in to_lines(chunks())]

        assert asyncio.run(go()) == ["# Döc", "name: JMD"]

    def test_async_events_are_visible_before_next_input_line(self) -> None:
        """Yield each event before requesting the following source line."""
        observed: list[str] = []

        async def lines() -> AsyncIterator[str]:
            yield "# Doc"
            assert observed == ["DOCUMENT_START", "OBJECT_START"]
            yield "id: 7"

        async def go() -> None:
            async for event in async_stream_events(lines()):
                observed.append(event.type)

        asyncio.run(go())
        assert observed == [
            "DOCUMENT_START",
            "OBJECT_START",
            "FIELD",
            "OBJECT_END",
            "DOCUMENT_END",
        ]

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
            ("OBJECT_START", None, None),
            ("FIELD", "id", 7),
            ("OBJECT_END", None, None),
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


class TestLevelPop:
    """Tests for §8.6 level-pops in the streaming backend.

    A level-pop returns to the scope at depth *D*; a labelled heading
    replaces it. The distinction matters for scope closing: the pop target
    must survive. These sequences are asserted in full because the bug this
    guards against closed one scope too many, which a membership check on
    event types would not have caught.
    """

    def test_array_level_pop_keeps_the_outer_array_open(self) -> None:
        """Test that `#` after a sub-array resumes the outer array."""
        types = event_types(
            "# Registry\n## apis[]\n- name: clockodo\n"
            "### headers[]\n- name: X-Api-User\n##\n- name: public"
        )
        assert types == [
            "DOCUMENT_START",
            "OBJECT_START",  # the root scope (§18.2)
            "ARRAY_START",   # apis
            "ITEM_START",    # clockodo — stays open across its sub-array
            "FIELD",
            "ARRAY_START",   # headers — the item's own data (§18.2)
            "ITEM_START",
            "FIELD",
            "ITEM_END",
            "ARRAY_END",     # headers — apis must NOT close here
            "ITEM_END",      # clockodo, closed by the level-pop
            "ITEM_START",    # public
            "FIELD",
            "ITEM_END",
            "ARRAY_END",     # apis
            "OBJECT_END",    # the root scope
            "DOCUMENT_END",
        ]

    def test_object_level_pop_to_root(self) -> None:
        """Test that `#` closes a nested object and resumes at the root."""
        evs = events(
            "# Order\nid: 42\n## address\ncity: Berlin\n#\nnote: gift wrap"
        )
        assert [(e.type, e.key) for e in evs] == [
            ("DOCUMENT_START", "Order"),
            ("OBJECT_START", None),
            ("FIELD", "id"),
            ("OBJECT_START", "address"),
            ("FIELD", "city"),
            ("OBJECT_END", "address"),
            ("FIELD", "note"),
            ("OBJECT_END", None),
            ("DOCUMENT_END", None),
        ]

    def test_object_level_pop_to_intermediate_depth(self) -> None:
        """Test that `##` resumes the depth-2 object, not the root."""
        evs = events("# Doc\n## a\nx: 1\n### b\ny: 2\n##\nz: 3")
        assert [(e.type, e.key) for e in evs] == [
            ("DOCUMENT_START", "Doc"),
            ("OBJECT_START", None),
            ("OBJECT_START", "a"),
            ("FIELD", "x"),
            ("OBJECT_START", "b"),
            ("FIELD", "y"),
            ("OBJECT_END", "b"),
            ("FIELD", "z"),      # inside a — a is still open
            ("OBJECT_END", "a"),
            ("OBJECT_END", None),
            ("DOCUMENT_END", None),
        ]

    def test_level_pop_at_current_depth_is_a_noop(self) -> None:
        """Test that a pop targeting the open scope closes nothing."""
        evs = events("# Doc\n## a\nx: 1\n##\ny: 2")
        assert [(e.type, e.key) for e in evs] == [
            ("DOCUMENT_START", "Doc"),
            ("OBJECT_START", None),
            ("OBJECT_START", "a"),
            ("FIELD", "x"),
            ("FIELD", "y"),
            ("OBJECT_END", "a"),
            ("OBJECT_END", None),
            ("DOCUMENT_END", None),
        ]

    def test_over_deep_level_pop_is_a_noop(self) -> None:
        """Test that a pop deeper than any open scope closes nothing."""
        evs = events("# Doc\nx: 1\n###\ny: 2")
        assert [(e.type, e.key, e.value) for e in evs] == [
            ("DOCUMENT_START", "Doc", None),
            ("OBJECT_START", None, None),
            ("FIELD", "x", 1),
            ("FIELD", "y", 2),
            ("OBJECT_END", None, None),
            ("DOCUMENT_END", None, None),
        ]
