# SPDX-License-Identifier: Apache-2.0
"""JMD Streaming Parser (v0.3)."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

from ._parser import (
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
    """A single event emitted by the JMD streaming parser."""

    type: str   # DOCUMENT_START | DOCUMENT_END | FIELD | OBJECT_START |
    # OBJECT_END | ARRAY_START | ARRAY_END | ITEM_START | ITEM_END | ITEM_VALUE
    key: str | None = None
    value: Any = None

    def __repr__(self) -> str:
        if self.value is not None:
            return (f"StreamEvent({self.type}, key={self.key!r}, "
                    f"value={self.value!r})")
        if self.key:
            return f"StreamEvent({self.type}, key={self.key!r})"
        return f"StreamEvent({self.type})"


def jmd_stream(source: str) -> Generator[StreamEvent, None, None]:
    """Generate StreamEvents from a JMD v0.3 source string.

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

    first = lines[0]
    if first.heading_depth == 1 and (
        first.content == "[]" or first.content == "- []"
    ):
        yield StreamEvent("DOCUMENT_START", key="[]")
        yield StreamEvent("ARRAY_START", key="[]")
        scope_stack.append(("array", "[]", 1, {}))
    elif first.heading_depth == 1:
        label = first.content
        if label.startswith("? "):
            label = label[2:]
        elif label.startswith("! "):
            label = label[2:]
        elif label.startswith("- "):
            label = label[2:]
        yield StreamEvent("DOCUMENT_START", key=label)
        scope_stack.append(("doc", label, 0, {}))
    else:
        return

    li = 1
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
