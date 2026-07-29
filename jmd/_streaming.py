# SPDX-License-Identifier: Apache-2.0
"""JMD Streaming Parser (v0.3.5)."""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Generator, Iterable
from dataclasses import dataclass
from typing import Any

from ._envelope import Mode, split_mode_label
from ._parser_common import (
    _K_ARRAY_PROMOTED,
    _K_ARRAY_SIGIL,
    _K_OBJECT,
    _K_SCALAR_BARE,
    _K_SCALAR_HEADING,
    JMDParseError,
    _is_indent_field,
    _is_object_item_content,
    parse_block_scalar_from,
)
from ._scalars import parse_key, parse_scalar
from ._tokenizer import is_thematic_break, tokenize

# §7.4 sigil-lock entry — one per object/item/doc scope on the stack.
# Streaming parser tracks kinds to detect repeated-key conflicts, but
# does NOT promote: each OBJECT_START is emitted as a separate event;
# the consumer composes the implicit array if it wants tree semantics.
_ScopeEntry = tuple[str, "str | None", int, dict[str, str]]


def _register_key(
    scope_stack: list[_ScopeEntry],
    key: str,
    new_kind: str,
    line_num: int,
) -> None:
    """Apply §7.4 conflict-detection on the innermost key-owning scope.

    Updates the kinds dict in place. Streaming-parser variant: does NOT
    rewrite the data (the stream consumer composes promoted arrays from
    the repeated OBJECT_START events). Raises :class:`JMDParseError` for
    the three §7.4.2 error classes.
    """
    for entry in reversed(scope_stack):
        stype, _, _, kinds = entry
        if stype not in ("object", "item", "doc"):
            continue
        existing = kinds.get(key)
        if existing is None:
            kinds[key] = new_kind
            return
        # Promotion case — kinds updates, no error.
        if existing == _K_OBJECT and new_kind == _K_OBJECT:
            kinds[key] = _K_ARRAY_PROMOTED
            return
        if existing == _K_ARRAY_PROMOTED and new_kind == _K_OBJECT:
            return
        if existing == _K_ARRAY_SIGIL and new_kind == _K_ARRAY_SIGIL:
            raise JMDParseError(
                kind="repeated_explicit_array", line=line_num, key=key,
                form={"existing": existing, "new": new_kind},
            )
        if _K_ARRAY_SIGIL in (existing, new_kind):
            raise JMDParseError(
                kind="sigil_conflict", line=line_num, key=key,
                form={"existing": existing, "new": new_kind},
            )
        raise JMDParseError(
            kind="repeated_scalar_key", line=line_num, key=key,
            form={"existing": existing, "new": new_kind},
        )


@dataclass
class StreamEvent:
    """A single event emitted by the JMD streaming parser.

    The ``mode`` and ``frontmatter`` fields are populated only on
    ``DOCUMENT_START`` events — they together with ``key`` (the root
    label) constitute the full envelope header per spec §18. All
    subsequent events carry body content only and leave both fields
    ``None``.
    """

    type: str   # DOCUMENT_START | DOCUMENT_END | FIELD | OBJECT_START |
    # OBJECT_END | ARRAY_START | ARRAY_END | ITEM_START | ITEM_END | ITEM_VALUE
    key: str | None = None
    value: Any = None
    # Envelope header — set only on DOCUMENT_START (§18, mirrors §3.6).
    mode: Mode | None = None
    frontmatter: dict[str, Any] | None = None

    def __repr__(self) -> str:
        if self.type == "DOCUMENT_START":
            return (
                f"StreamEvent(DOCUMENT_START, mode={self.mode!r}, "
                f"label={self.key!r}, frontmatter={self.frontmatter!r})"
            )
        if self.value is not None:
            return (f"StreamEvent({self.type}, key={self.key!r}, "
                    f"value={self.value!r})")
        if self.key:
            return f"StreamEvent({self.type}, key={self.key!r})"
        return f"StreamEvent({self.type})"


def jmd_stream(source: str) -> Generator[StreamEvent, None, None]:
    """Generate StreamEvents from a JMD v0.3.5 source string.

    Processes the document line by line using a scope stack driven by
    heading depth. Supports blockquote multiline strings and indentation
    continuation for array object items.

    Args:
        source: Complete JMD document text.

    Yields:
        StreamEvent instances representing parsing events.
    """
    lines = tokenize(source)
    if not lines:
        return

    scope_stack: list[_ScopeEntry] = []

    def close_scopes_to(
        target_depth: int,
    ) -> Generator[StreamEvent, None, None]:
        """Close scopes deeper than target_depth."""
        while scope_stack:
            stype, skey, sdepth, _ = scope_stack[-1]
            if sdepth < target_depth:
                break
            scope_stack.pop()
            if stype == "object":
                yield StreamEvent("OBJECT_END", key=skey)
            elif stype == "item":
                yield StreamEvent("ITEM_END")
            elif stype == "array":
                yield StreamEvent("ARRAY_END", key=skey)

    # Frontmatter (§3.5): buffered into a dict here. Per §18 the
    # envelope header — mode, label, frontmatter — is emitted as a
    # single DOCUMENT_START event when the first heading arrives;
    # subsequent events carry body content only and never re-transmit
    # the header. §3.5.1 tolerates stray ``---`` markers around the
    # block. Multi-line values (D12) follow as ``key:`` + blockquote.
    frontmatter: dict[str, Any] = {}
    fi = 0
    while fi < len(lines):
        fline = lines[fi]
        if fline.heading_depth > 0:
            break
        if fline.heading_depth == -1:
            fi += 1
            continue
        # §3.5.1: ``---`` (or more) is decorative noise around frontmatter.
        if is_thematic_break(fline):
            fi += 1
            continue
        content = fline.content
        if ": " in content:
            key_part, _, val_part = content.partition(": ")
            frontmatter[parse_key(key_part)] = parse_scalar(val_part)
            fi += 1
            continue
        if content.endswith(":") and ": " not in content:
            # D12: key: (no value) followed by blockquote multi-line value
            key = parse_key(content[:-1])
            fi += 1
            # Consume the blockquote lines
            parts: list[str] = []
            while fi < len(lines):
                nxt = lines[fi]
                if nxt.heading_depth != 0:
                    break
                raw = nxt.raw_text.strip()
                if raw == ">":
                    parts.append("")
                    fi += 1
                elif raw.startswith("> "):
                    parts.append(raw[2:])
                    fi += 1
                else:
                    break
            frontmatter[key] = "\n".join(parts).rstrip("\n")
            continue
        if (content and not content.startswith(">")
                and not content.startswith("- ")):
            frontmatter[parse_key(content)] = True
            fi += 1
            continue
        break
    if fi >= len(lines):
        return

    first = lines[fi]
    if first.heading_depth != 1:
        return

    mode, label = split_mode_label(first.content)
    # Emit the envelope header in a single event per §18.
    yield StreamEvent(
        "DOCUMENT_START",
        key=label,
        mode=mode,
        frontmatter=dict(frontmatter),
    )
    if first.content == "[]" or first.content == "- []":
        yield StreamEvent("ARRAY_START", key="[]")
        scope_stack.append(("array", "[]", 1, {}))
    elif first.content.endswith("[]"):
        # #? Label[] / #! Label[] / # Label[] — root array with a label
        yield StreamEvent("ARRAY_START", key=label or None)
        scope_stack.append(("array", label or "[]", 1, {}))
    else:
        scope_stack.append(("doc", label, 0, {}))

    li = fi + 1
    while li < len(lines):
        line = lines[li]
        li += 1

        # Blank line: scope reset (Section 7.2a).
        if line.heading_depth == -1:
            peek = li
            while peek < len(lines) and lines[peek].heading_depth == -1:
                peek += 1
            if peek < len(lines):
                nxt = lines[peek]
                if nxt.heading_depth > 0:
                    continue
                if (nxt.heading_depth == 0
                        and (nxt.content == "-"
                             or nxt.content.startswith("- "))):
                    if scope_stack and scope_stack[-1][0] in ("array", "item"):
                        continue
                # Thematic break after blank line within array: cosmetic
                if is_thematic_break(nxt):
                    if scope_stack and scope_stack[-1][0] in ("array", "item"):
                        continue
                # Blockquote after blank line — keep going
                if (nxt.heading_depth == 0
                        and nxt.raw_text.strip().startswith(">")):
                    continue
            if scope_stack and scope_stack[-1][0] == "item":
                scope_stack.pop()
                yield StreamEvent("ITEM_END")
            closed: list[tuple[str, str | None]] = []
            while scope_stack and scope_stack[-1][0] != "doc":
                stype, skey, _, _ = scope_stack.pop()
                closed.append((stype, skey))
            if closed:
                yield StreamEvent("SCOPE_RESET")
                for stype, skey in closed:
                    if stype == "object":
                        yield StreamEvent("OBJECT_END", key=skey)
                    elif stype == "array":
                        yield StreamEvent("ARRAY_END", key=skey)
            continue

        # Heading line: manage scope.
        if line.heading_depth > 0:
            depth = line.heading_depth
            content = line.content

            # Depth-qualified item: ## - or ## - key: val
            if content == "-" or content.startswith("- "):
                yield from close_scopes_to(depth + 1)

                if (scope_stack
                        and scope_stack[-1][0] == "item"
                        and scope_stack[-1][2] == depth):
                    scope_stack.pop()
                    yield StreamEvent("ITEM_END")

                if content.startswith("- "):
                    content_after = content[2:]
                    if _is_object_item_content(content_after):
                        yield StreamEvent("ITEM_START")
                        scope_stack.append(("item", None, depth, {}))
                        key_part, _, val_part = content_after.partition(": ")
                        _register_key(
                            scope_stack, parse_key(key_part),
                            _K_SCALAR_BARE, line.number,
                        )
                        yield StreamEvent("FIELD", key=parse_key(key_part),
                                          value=parse_scalar(val_part))
                        # Consume indented continuation fields
                        while li < len(lines):
                            indent_result = _is_indent_field(lines[li].raw_text)
                            if indent_result is not None:
                                _, ikp, ivp = indent_result
                                _register_key(
                                    scope_stack, parse_key(ikp),
                                    _K_SCALAR_BARE, lines[li].number,
                                )
                                yield StreamEvent("FIELD", key=parse_key(ikp),
                                                  value=parse_scalar(ivp))
                                li += 1
                            else:
                                break
                    else:
                        yield StreamEvent(
                            "ITEM_VALUE",
                            value=parse_scalar(content_after),
                        )
                else:
                    yield StreamEvent("ITEM_START")
                    scope_stack.append(("item", None, depth, {}))

            else:
                yield from close_scopes_to(depth)

                if content == "[]":
                    yield StreamEvent("ARRAY_START", key=None)
                    scope_stack.append(("array", None, depth, {}))
                elif content.endswith("[]"):
                    key = parse_key(content[:-2])
                    _register_key(
                        scope_stack, key, _K_ARRAY_SIGIL, line.number,
                    )
                    yield StreamEvent("ARRAY_START", key=key)
                    scope_stack.append(("array", key, depth, {}))
                elif ": " in content:
                    key_part, _, val_part = content.partition(": ")
                    key = parse_key(key_part)
                    _register_key(
                        scope_stack, key, _K_SCALAR_HEADING, line.number,
                    )
                    if val_part == "|" or val_part == ">":
                        # §5.2 block scalar from scalar heading.
                        bs_value, li = parse_block_scalar_from(
                            lines, li, folded=(val_part == ">"),
                        )
                        yield StreamEvent("FIELD", key=key, value=bs_value)
                        continue
                    if val_part == "":
                        # Check for blockquote
                        if (li < len(lines)
                                and lines[li].heading_depth == 0
                                and lines[li].raw_text.strip().startswith(">")):
                            bq_parts: list[str] = []
                            while li < len(lines):
                                raw = lines[li].raw_text.strip()
                                if raw == ">":
                                    bq_parts.append("")
                                    li += 1
                                elif raw.startswith("> "):
                                    bq_parts.append(raw[2:])
                                    li += 1
                                else:
                                    break
                            yield StreamEvent("FIELD", key=key,
                                              value="\n".join(bq_parts).strip("\n"))
                        else:
                            yield StreamEvent("FIELD", key=key, value="")
                    else:
                        yield StreamEvent(
                            "FIELD",
                            key=key,
                            value=parse_scalar(val_part),
                        )
                else:
                    key = parse_key(content)
                    _register_key(
                        scope_stack, key, _K_OBJECT, line.number,
                    )
                    yield StreamEvent("OBJECT_START", key=key)
                    scope_stack.append(("object", key, depth, {}))

        # Bare object item: -
        elif line.content == "-":
            if (scope_stack
                    and scope_stack[-1][0] == "item"):
                scope_stack.pop()
                yield StreamEvent("ITEM_END")
            yield StreamEvent("ITEM_START")
            item_depth = (scope_stack[-1][2] + 1) if scope_stack else 1
            scope_stack.append(("item", None, item_depth, {}))

        # Inline object item or scalar array item: - value
        elif line.content.startswith("- "):
            content_after = line.content[2:]
            if _is_object_item_content(content_after):
                if (scope_stack
                        and scope_stack[-1][0] == "item"):
                    scope_stack.pop()
                    yield StreamEvent("ITEM_END")
                yield StreamEvent("ITEM_START")
                item_depth = (
                    (scope_stack[-1][2] + 1) if scope_stack else 1
                )
                scope_stack.append(("item", None, item_depth, {}))
                key_part, _, val_part = content_after.partition(": ")
                _register_key(
                    scope_stack, parse_key(key_part),
                    _K_SCALAR_BARE, line.number,
                )
                yield StreamEvent("FIELD", key=parse_key(key_part),
                                  value=parse_scalar(val_part))
                # Consume indented continuation fields
                while li < len(lines):
                    indent_result = _is_indent_field(lines[li].raw_text)
                    if indent_result is not None:
                        _, ikp, ivp = indent_result
                        _register_key(
                            scope_stack, parse_key(ikp),
                            _K_SCALAR_BARE, lines[li].number,
                        )
                        yield StreamEvent("FIELD", key=parse_key(ikp),
                                          value=parse_scalar(ivp))
                        li += 1
                    else:
                        break
            else:
                if (scope_stack
                        and scope_stack[-1][0] == "item"):
                    scope_stack.pop()
                    yield StreamEvent("ITEM_END")
                yield StreamEvent(
                    "ITEM_VALUE",
                    value=parse_scalar(content_after),
                )

        # Thematic break (---): array item separator.
        # Close all child scopes above the outermost array so the
        # next bare `- ` line starts a fresh item in that array.
        elif is_thematic_break(line):
            target_idx: int | None = None
            for _si, (_st, _sk, _sd, _) in enumerate(scope_stack):
                if _st == "array":
                    target_idx = _si
                    break
            if target_idx is not None:
                while len(scope_stack) > target_idx + 1:
                    stype, skey, _, _ = scope_stack.pop()
                    if stype == "object":
                        yield StreamEvent("OBJECT_END", key=skey)
                    elif stype == "array":
                        yield StreamEvent("ARRAY_END", key=skey)
                    elif stype == "item":
                        yield StreamEvent("ITEM_END")

        # Orphan blockquote line: a '>' not preceded by a key: handler.
        # This should not occur in valid JMD documents. Skip all consecutive
        # blockquote lines without emitting any event — intentional no-op.
        elif line.raw_text.strip().startswith(">"):
            # Back up — we already consumed this line via li += 1
            li -= 1
            while li < len(lines):
                raw = lines[li].raw_text.strip()
                if raw == ">" or raw.startswith("> "):
                    li += 1
                else:
                    break

        # Bare field: key: value or key: (with blockquote)
        elif ": " in line.content:
            key_part, _, val_part = line.content.partition(": ")
            key = parse_key(key_part)
            _register_key(scope_stack, key, _K_SCALAR_BARE, line.number)
            if val_part == "|" or val_part == ">":
                # §5.2 block scalar from bare field.
                bs_value, li = parse_block_scalar_from(
                    lines, li, folded=(val_part == ">"),
                )
                yield StreamEvent("FIELD", key=key, value=bs_value)
                continue
            if val_part == "":
                # Check for blockquote
                if (li < len(lines)
                        and lines[li].heading_depth == 0
                        and lines[li].raw_text.strip().startswith(">")):
                    bq_parts_bare: list[str] = []
                    while li < len(lines):
                        raw = lines[li].raw_text.strip()
                        if raw == ">":
                            bq_parts_bare.append("")
                            li += 1
                        elif raw.startswith("> "):
                            bq_parts_bare.append(raw[2:])
                            li += 1
                        else:
                            break
                    yield StreamEvent("FIELD", key=key,
                                      value="\n".join(bq_parts_bare).strip("\n"))
                else:
                    yield StreamEvent("FIELD", key=key, value="")
            else:
                yield StreamEvent(
                    "FIELD",
                    key=key,
                    value=parse_scalar(val_part),
                )

        # key: (colon at end, no space) — check for blockquote
        elif line.content.endswith(":") and ": " not in line.content:
            key = parse_key(line.content[:-1])
            _register_key(scope_stack, key, _K_SCALAR_BARE, line.number)
            if (li < len(lines)
                    and lines[li].heading_depth == 0
                    and lines[li].raw_text.strip().startswith(">")):
                bq_parts_key: list[str] = []
                while li < len(lines):
                    raw = lines[li].raw_text.strip()
                    if raw == ">":
                        bq_parts_key.append("")
                        li += 1
                    elif raw.startswith("> "):
                        bq_parts_key.append(raw[2:])
                        li += 1
                    else:
                        break
                yield StreamEvent("FIELD", key=key,
                                  value="\n".join(bq_parts_key).strip("\n"))
            else:
                yield StreamEvent("FIELD", key=key, value="")

    # Close all remaining scopes.
    while scope_stack:
        stype, skey, _, _ = scope_stack.pop()
        if stype == "object":
            yield StreamEvent("OBJECT_END", key=skey)
        elif stype == "item":
            yield StreamEvent("ITEM_END")
        elif stype == "array":
            yield StreamEvent("ARRAY_END", key=skey)

    yield StreamEvent("DOCUMENT_END")


# ---------------------------------------------------------------------------
# Push-style streaming API (matches jmd-js createParser/events/toLines)
# ---------------------------------------------------------------------------


class JMDStreamParser:
    """Streaming JMD parser with a push API.

    Designed for symmetry with the JavaScript reference (``jmd-js``):
    callers push lines via :meth:`process_line` and finalize with
    :meth:`finish` to drain any remaining events. The convenience
    :meth:`events` accepts an iterable of lines.

    Current implementation buffers all lines and yields events from
    :meth:`finish`; ``process_line`` returns an empty list. The API
    is forward-compatible with a future truly-incremental parser
    (the consumer code does not change). True incremental emission
    is tracked as a follow-up — Memory ``4d156451`` discusses the
    architectural tipping points.

    Example::

        parser = JMDStreamParser()
        parser.process_line("# Order")
        parser.process_line("id: 42")
        events = parser.finish()
    """

    def __init__(self) -> None:
        self._lines: list[str] = []
        self._finished: bool = False

    def process_line(self, line: str) -> list[StreamEvent]:
        """Push one source line (without trailing newline).

        Returns the events that can be emitted now. In the current
        buffered implementation this is always ``[]``; events are
        emitted by :meth:`finish`.

        Raises:
            RuntimeError: If called after :meth:`finish`.
        """
        if self._finished:
            raise RuntimeError("process_line called after finish()")
        self._lines.append(line)
        return []

    def finish(self) -> list[StreamEvent]:
        """Signal end of input and return all remaining events.

        Idempotent: a second call returns ``[]``.
        """
        if self._finished:
            return []
        self._finished = True
        return list(jmd_stream("\n".join(self._lines)))

    @staticmethod
    def events(source: Iterable[str]) -> Generator[StreamEvent, None, None]:
        """Convenience: iterate events for an iterable of lines.

        Equivalent to feeding each line via :meth:`process_line` and
        draining :meth:`finish`. Yields events in order.
        """
        parser = JMDStreamParser()
        for line in source:
            yield from parser.process_line(line)
        yield from parser.finish()


async def to_lines(
    source: AsyncIterable[str | bytes],
) -> AsyncIterator[str]:
    r"""Adapter: async-iterable of str/bytes chunks → async-iterable of lines.

    Splits on ``\n``, strips trailing ``\r``. The final unterminated
    line is yielded if non-empty. Matches the contract of the jmd-js
    ``toLines`` helper.
    """
    buffer = ""
    async for chunk in source:
        if isinstance(chunk, (bytes, bytearray)):
            chunk = chunk.decode("utf-8")
        buffer += chunk
        while True:
            idx = buffer.find("\n")
            if idx < 0:
                break
            line = buffer[:idx]
            if line.endswith("\r"):
                line = line[:-1]
            yield line
            buffer = buffer[idx + 1:]
    if buffer:
        if buffer.endswith("\r"):
            buffer = buffer[:-1]
        yield buffer


async def events(
    source: AsyncIterable[str],
) -> AsyncIterator[StreamEvent]:
    """Async events generator over an async-iterable of lines.

    Mirrors jmd-js's ``events(source)``. Buffers internally per
    :class:`JMDStreamParser` semantics; ``finish`` is implied by
    end-of-iteration. Returns a sync stream of :class:`StreamEvent`
    instances, ready for ``async for`` consumption alongside the
    line source.
    """
    parser = JMDStreamParser()
    async for line in source:
        for ev in parser.process_line(line):
            yield ev
    for ev in parser.finish():
        yield ev
