# SPDX-License-Identifier: Apache-2.0
"""Persistent state machine for incremental JMD event parsing."""

from __future__ import annotations

from collections.abc import Generator, Iterable

from ._envelope import split_mode_label
from ._parser_common import (
    _K_ARRAY_SIGIL,
    _K_OBJECT,
    _K_SCALAR_BARE,
    _K_SCALAR_HEADING,
    JMDParseError,
)
from ._scalars import parse_key, parse_scalar
from ._stream_events import StreamEvent
from ._stream_state import (
    Blockquote,
    BlockScalar,
    Scope,
    close_current_item,
    close_scopes_to,
    close_top_scope,
    fold_block_lines,
    has_open_array,
    register_key,
    reset_scopes,
    split_field,
)
from ._tokenizer import Line, is_thematic_break, tokenize_line


class JMDStreamParser:
    """Incremental JMD parser with one-line push semantics.

    Each call consumes one source line without its line ending and returns every
    semantic event made available by that completed line. Only open structural
    scopes, frontmatter, one pending blank, and the current multiline value are
    retained.
    """

    def __init__(self) -> None:
        """Initialize empty incremental parser state."""
        self._line_number = 0
        self._finished = False
        self._seen_root = False
        self._root_is_array = False
        self._frontmatter: dict[str, object] = {}
        self._scopes: list[Scope] = []
        self._blockquote: Blockquote | None = None
        self._block_scalar: BlockScalar | None = None
        self._pending_blank = False

    def process_line(self, line: str) -> list[StreamEvent]:
        """Consume one completed source line and return available events.

        Args:
            line: Source line without a trailing LF or CRLF delimiter.

        Returns:
            Events made semantically complete by this line.

        Raises:
            RuntimeError: If called after :meth:`finish`.
            JMDParseError: If the line violates the JMD structure.
        """
        if self._finished:
            raise RuntimeError("process_line called after finish()")

        self._line_number += 1
        if "\r" in line:
            raise JMDParseError(
                kind="lone_carriage_return",
                line=self._line_number,
                key="",
            )
        if self._line_number == 1:
            line = line.removeprefix("\ufeff")

        events: list[StreamEvent] = []
        while True:
            if self._consume_multiline(line, events):
                return events

            token = tokenize_line(self._line_number, line)
            if not self._seen_root:
                self._handle_header_line(token, events)
                return events

            if token.heading_depth == -1:
                self._pending_blank = True
                return events

            if self._pending_blank:
                self._resolve_pending_blank(token, events)

            if token.heading_depth > 0:
                self._validate_document_boundary(token)
                self._handle_heading(token, events)
            else:
                self._handle_body_line(token, events)
            return events

    def finish(self) -> list[StreamEvent]:
        """Close pending state and return terminal events exactly once."""
        if self._finished:
            return []
        self._finished = True

        events: list[StreamEvent] = []
        if self._block_scalar is not None:
            self._commit_block_scalar(events)
        if self._blockquote is not None:
            self._commit_blockquote()
        if not self._seen_root:
            if self._line_number == 0:
                return []
            raise ValueError("No root heading found")

        if self._pending_blank:
            self._pending_blank = False
            reset_scopes(self._scopes, events)
        while self._scopes:
            close_top_scope(self._scopes, events)
        events.append(StreamEvent("DOCUMENT_END"))
        return events

    @staticmethod
    def events(source: Iterable[str]) -> Generator[StreamEvent, None, None]:
        """Yield events from an iterable of already separated source lines."""
        parser = JMDStreamParser()
        for line in source:
            yield from parser.process_line(line)
        yield from parser.finish()

    def _consume_multiline(
        self,
        raw: str,
        events: list[StreamEvent],
    ) -> bool:
        """Consume an active multiline value or close it and redispatch."""
        block = self._block_scalar
        if block is not None:
            if raw.strip() == "":
                block.parts.append("")
                return True
            indent = len(raw) - len(raw.lstrip(" "))
            if indent < 2:
                self._commit_block_scalar(events)
                return False
            if block.indent is None:
                block.indent = indent
            if indent < block.indent:
                self._commit_block_scalar(events)
                return False
            block.parts.append(raw[block.indent :])
            return True

        quote = self._blockquote
        if quote is None:
            return False
        stripped = raw.strip()
        if stripped == ">" or stripped.startswith("> "):
            value = "" if stripped == ">" else stripped[2:]
            if quote.frontmatter:
                quote.parts.append(value)
            else:
                events.append(StreamEvent("FIELD_CONTENT", value=value))
            return True
        self._commit_blockquote()
        return False

    def _commit_blockquote(self) -> None:
        """Commit frontmatter quote state; body quotes are event-complete."""
        quote = self._blockquote
        if quote is None:
            return
        if quote.frontmatter:
            self._frontmatter[quote.key] = "\n".join(quote.parts).rstrip("\n")
        self._blockquote = None

    def _commit_block_scalar(self, events: list[StreamEvent]) -> None:
        """Emit the aggregate value of the current tolerated block scalar."""
        block = self._block_scalar
        if block is None:
            return
        parts = list(block.parts)
        while parts and parts[-1] == "":
            parts.pop()
        value = fold_block_lines(parts) if block.folded else "\n".join(parts)
        events.append(StreamEvent("FIELD", key=block.key, value=value))
        self._block_scalar = None

    def _handle_header_line(
        self,
        line: Line,
        events: list[StreamEvent],
    ) -> None:
        """Accumulate frontmatter or emit the document header at the root."""
        if line.heading_depth > 0:
            if line.heading_depth != 1:
                raise ValueError(
                    f"Line {line.number}: expected '# <label>' or '# []'"
                )
            if line.raw_text.startswith((" ", "\t")) or line.content == "":
                raise JMDParseError(
                    kind="no_root_heading",
                    line=line.number,
                    key="",
                )
            self._start_document(line, events)
            return

        if line.heading_depth == -1 or is_thematic_break(line):
            return
        field_parts = split_field(line.content)
        if field_parts is not None:
            key_raw, value_raw = field_parts
            key = parse_key(key_raw)
            if value_raw == "":
                self._blockquote = Blockquote(key, frontmatter=True)
            else:
                self._frontmatter[key] = parse_scalar(value_raw)
            return
        if (
            line.content
            and not line.content.startswith(">")
            and not line.content.startswith("- ")
        ):
            self._frontmatter[parse_key(line.content)] = True

    def _start_document(
        self,
        line: Line,
        events: list[StreamEvent],
    ) -> None:
        """Emit DOCUMENT_START and initialize the root scope."""
        mode, label = split_mode_label(line.content)
        self._seen_root = True
        self._root_is_array = (
            line.content == "[]"
            or line.content == "- []"
            or line.content.endswith("[]")
        )
        events.append(
            StreamEvent(
                "DOCUMENT_START",
                key=label,
                mode=mode,
                frontmatter=dict(self._frontmatter),
            )
        )
        self._frontmatter.clear()
        # §18.2: the root is opened and closed like any other scope, and
        # carries no key — a key names the slot a scope occupies in its
        # parent, and the root has no parent. The label rides on
        # DOCUMENT_START. This is what lets a consumer learn the root's
        # kind even for an empty document, where the label cannot say
        # (DOCUMENT_START strips any `[]` sigil, §3.6).
        if self._root_is_array:
            events.append(StreamEvent("ARRAY_START"))
            self._scopes.append(Scope("array", None, 1))
        else:
            events.append(StreamEvent("OBJECT_START"))
            self._scopes.append(Scope("doc", None, 0))

    def _resolve_pending_blank(
        self,
        line: Line,
        events: list[StreamEvent],
    ) -> None:
        """Resolve the one permitted blank-line lookahead."""
        self._pending_blank = False
        if line.heading_depth > 0:
            return
        if (
            (line.content == "-" or line.content.startswith("- "))
            and has_open_array(self._scopes)
        ):
            # A cosmetic blank line between array items also returns from
            # any child scopes opened by the preceding item.  Otherwise the
            # next bare ``-`` sees the child object/array rather than its
            # enclosing array and is rejected as invalid_structure.
            for index in range(len(self._scopes) - 1, -1, -1):
                if self._scopes[index].kind == "array":
                    while len(self._scopes) > index + 1:
                        close_top_scope(self._scopes, events)
                    break
            return
        if is_thematic_break(line) and has_open_array(self._scopes):
            return
        reset_scopes(self._scopes, events)

    def _validate_document_boundary(self, line: Line) -> None:
        """Reject a second root heading or a mid-document mode marker."""
        if line.heading_depth != 1 or line.content == "":
            return
        raw = line.raw_text.lstrip(" \t")
        if raw.startswith(("#? ", "#! ", "#- ")):
            raise JMDParseError(
                kind="mode_marker_mid_document",
                line=line.number,
                key="",
            )
        if self._root_is_array and (
            line.content == "-" or line.content.startswith("- ")
        ):
            return
        raise JMDParseError(
            kind="second_root_heading",
            line=line.number,
            key="",
        )

    def _handle_heading(
        self,
        line: Line,
        events: list[StreamEvent],
    ) -> None:
        """Process a structural or scalar heading."""
        if line.raw_text.startswith((" ", "\t")):
            raise JMDParseError(
                kind="invalid_indentation",
                line=line.number,
                key="",
            )

        depth = line.heading_depth
        content = line.content
        if content == "-" or content.startswith("- "):
            close_scopes_to(self._scopes, depth + 1, events)
            close_current_item(self._scopes, depth, events)
            if not has_open_array(self._scopes):
                self._raise_structure(line)
            if content == "-":
                self._start_item(depth, events)
                return
            self._emit_item_content(content[2:], depth, line.number, events)
            return

        if content == "":
            # §8.6 level-pop: close scopes *deeper than* D and continue in
            # the scope at depth D. A labelled heading replaces the scope
            # at its own depth (close_scopes_to(depth), below), but an
            # anonymous one returns to it, so the target must survive the
            # close. Applies to object and array scopes alike; if nothing
            # is open at depth D the loop closes nothing and the pop is a
            # no-op, which is what the spec's degenerate cases require.
            close_scopes_to(self._scopes, depth + 1, events)
            # Returning to an array at depth D also ends the item that was
            # open in it: the pop says "back at the array", so the next
            # `- ` line is a sibling, not a continuation (§8.6).
            close_current_item(self._scopes, depth, events)
            return

        close_scopes_to(self._scopes, depth, events)
        if content == "[]":
            events.append(StreamEvent("ARRAY_START"))
            self._scopes.append(Scope("array", None, depth))
            return
        if content.endswith("[]"):
            key = parse_key(content[:-2])
            register_key(self._scopes, key, _K_ARRAY_SIGIL, line.number)
            events.append(StreamEvent("ARRAY_START", key=key))
            self._scopes.append(Scope("array", key, depth))
            return

        field_parts = split_field(content)
        if field_parts is not None:
            key_raw, value_raw = field_parts
            self._emit_field(
                parse_key(key_raw),
                value_raw,
                _K_SCALAR_HEADING,
                line.number,
                events,
            )
            return

        key = parse_key(content)
        register_key(self._scopes, key, _K_OBJECT, line.number)
        events.append(StreamEvent("OBJECT_START", key=key))
        self._scopes.append(Scope("object", key, depth))

    def _handle_body_line(
        self,
        line: Line,
        events: list[StreamEvent],
    ) -> None:
        """Process a non-heading body line."""
        if is_thematic_break(line):
            # §8.6: within an array body a thematic break is pure
            # decoration with no structural effect, and explicitly not an
            # item separator. Closing the open item here severed the
            # `- key: val` / indented-continuation pair, so a continuation
            # line after `---` was rejected. Outside an array the helper
            # was already a no-op, so skipping outright changes nothing
            # there.
            return
        if line.raw_text.strip().startswith(">"):
            raise JMDParseError(
                kind="prose_in_body",
                line=line.number,
                key="",
            )
        if line.content == "-" or line.content.startswith("- "):
            self._handle_bare_item(line, events)
            return

        field_parts = split_field(line.content)
        if line.raw_text.startswith((" ", "\t")):
            if not self._scopes or self._scopes[-1].kind != "item":
                # §3.6.2/§11.2: an INDENT outside an array item is prose,
                # not an indentation defect. `invalid_indentation` is not
                # a spec error kind; the batch parsers and the must-fail
                # fixtures both use `prose_in_body`.
                raise JMDParseError(
                    kind="prose_in_body",
                    line=line.number,
                    key="",
                )
            if field_parts is None:
                self._raise_structure(line)
        if field_parts is not None:
            # A bare field belongs only to an object or the current object
            # array item. A root/child array has no key-owning scope, so
            # accepting it would diverge from batch parsing and lose input.
            if not self._scopes or self._scopes[-1].kind == "array":
                raise JMDParseError(
                    kind="prose_in_body",
                    line=line.number,
                    key="",
                )
            key_raw, value_raw = field_parts
            self._emit_field(
                parse_key(key_raw),
                value_raw,
                _K_SCALAR_BARE,
                line.number,
                events,
            )
            return
        raise JMDParseError(
            kind="prose_in_body",
            line=line.number,
            key="",
        )

    def _handle_bare_item(
        self,
        line: Line,
        events: list[StreamEvent],
    ) -> None:
        """Emit a bare array item at the current array depth."""
        if self._scopes and self._scopes[-1].kind == "item":
            close_top_scope(self._scopes, events)
        if not self._scopes or self._scopes[-1].kind != "array":
            self._raise_structure(line)
        # §18.2: an item scope carries its *array's* depth, not the depth of
        # the headings it may contain. At array_depth + 1 it collided with
        # its own sub-structure, so `### sub[]` under a `## arr[]` item ran
        # close_scopes_to(3) and closed the item before opening the child —
        # emitting ITEM_END outside the pair that should enclose the child.
        # The depth-qualified path (`## -`) already passes the array depth.
        array_depth = self._scopes[-1].depth
        if line.content == "-":
            self._start_item(array_depth, events)
            return
        self._emit_item_content(
            line.content[2:],
            array_depth,
            line.number,
            events,
        )

    def _emit_item_content(
        self,
        content: str,
        depth: int,
        line_number: int,
        events: list[StreamEvent],
    ) -> None:
        """Emit either a scalar item or an object item's first field."""
        field_parts = split_field(content)
        if field_parts is None:
            events.append(
                StreamEvent("ITEM_VALUE", value=parse_scalar(content))
            )
            return
        self._start_item(depth, events)
        key_raw, value_raw = field_parts
        self._emit_field(
            parse_key(key_raw),
            value_raw,
            _K_SCALAR_BARE,
            line_number,
            events,
        )

    def _start_item(self, depth: int, events: list[StreamEvent]) -> None:
        """Open one object-array item."""
        events.append(StreamEvent("ITEM_START"))
        self._scopes.append(Scope("item", None, depth))

    def _emit_field(
        self,
        key: str,
        raw_value: str,
        kind: str,
        line_number: int,
        events: list[StreamEvent],
    ) -> None:
        """Register a field and emit its scalar or multiline opener event."""
        register_key(self._scopes, key, kind, line_number)
        if raw_value in ("|", ">"):
            events.append(StreamEvent("FIELD_START", key=key))
            self._block_scalar = BlockScalar(
                key=key,
                folded=raw_value == ">",
            )
        elif raw_value == "":
            events.append(StreamEvent("FIELD_START", key=key))
            self._blockquote = Blockquote(key)
        else:
            events.append(
                StreamEvent("FIELD", key=key, value=parse_scalar(raw_value))
            )

    @staticmethod
    def _raise_structure(line: Line) -> None:
        """Raise a structured error for a body construct in no valid scope."""
        raise JMDParseError(
            kind="invalid_structure",
            line=line.number,
            key="",
        )
