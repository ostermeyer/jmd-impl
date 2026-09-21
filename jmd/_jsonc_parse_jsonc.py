# SPDX-License-Identifier: Apache-2.0
"""JSONC source → :mod:`._jsonc_ast` AST.

Tokenizer + recursive-descent parser for JSONC (JSON with line
and block comments + trailing commas), converting source text to
the AST defined in :mod:`._jsonc_ast`. Public entry point is
:func:`parse_jsonc`; everything else is implementation.

Pure Python, stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._jsonc_ast import (
    BlockComment,
    Element,
    Field,
    JsoncDoc,
    LineComment,
    ObjectArray,
    ScalarArray,
    Section,
)

# --- JSONC tokenizer -----------------------------------------------------
#
# Converts JSONC source text into a flat token stream. Comments and
# trailing commas come through as their own tokens; the parser then
# decides where to attach them in the AST.
#
# We tokenize char-by-char rather than via regex — JSONC strings
# require escape-aware parsing, comments require lookahead, and a
# single state machine keeps all the cases obvious.


# Token kinds — string constants used as discriminators. A simple
# sentinel pattern; no enum so the token tuples stay JSON-friendly
# for any future debugging / introspection.
TOK_LBRACE = "{"
TOK_RBRACE = "}"
TOK_LBRACKET = "["
TOK_RBRACKET = "]"
TOK_COMMA = ","
TOK_COLON = ":"
TOK_STRING = "string"
TOK_NUMBER = "number"
TOK_TRUE = "true"
TOK_FALSE = "false"
TOK_NULL = "null"
TOK_LINE_COMMENT = "line-comment"
TOK_BLOCK_COMMENT = "block-comment"


@dataclass
class Token:
    """One JSONC lexical element.

    ``value`` carries content for string/number/comment tokens.
    """

    kind: str
    value: Any = None


class JsoncParseError(ValueError):
    """Raised for malformed JSONC input.

    Carries an ``offset`` attribute pointing at the offending byte
    so callers can surface a position to the user.
    """

    def __init__(self, message: str, offset: int) -> None:
        super().__init__(f"{message} (at offset {offset})")
        self.offset = offset


def _tokenize_jsonc(text: str) -> list[Token]:
    r"""Tokenize *text* into a flat token list.

    Whitespace is consumed silently — its only role is to separate
    tokens. Comments are emitted as their own tokens so the parser
    can preserve their sequence-positions in the AST. String escapes
    follow the JSON spec (``\n``, ``\t``, ``é``, etc.); the
    escape-decoding step lives in :func:`_read_string`.
    """
    tokens: list[Token] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c == "{":
            tokens.append(Token(TOK_LBRACE))
            i += 1
            continue
        if c == "}":
            tokens.append(Token(TOK_RBRACE))
            i += 1
            continue
        if c == "[":
            tokens.append(Token(TOK_LBRACKET))
            i += 1
            continue
        if c == "]":
            tokens.append(Token(TOK_RBRACKET))
            i += 1
            continue
        if c == ",":
            tokens.append(Token(TOK_COMMA))
            i += 1
            continue
        if c == ":":
            tokens.append(Token(TOK_COLON))
            i += 1
            continue
        if c == "/":
            if i + 1 < n and text[i + 1] == "/":
                end = text.find("\n", i + 2)
                if end == -1:
                    end = n
                tokens.append(
                    Token(
                        TOK_LINE_COMMENT,
                        _normalize_line_comment(text[i + 2 : end]),
                    ),
                )
                i = end
                continue
            if i + 1 < n and text[i + 1] == "*":
                end = text.find("*/", i + 2)
                if end == -1:
                    raise JsoncParseError(
                        "unterminated block comment",
                        i,
                    )
                tokens.append(
                    Token(TOK_BLOCK_COMMENT, text[i + 2 : end]),
                )
                i = end + 2
                continue
            raise JsoncParseError(
                f"unexpected character {c!r}",
                i,
            )
        if c == '"':
            value, i = _read_string(text, i)
            tokens.append(Token(TOK_STRING, value))
            continue
        if c == "-" or c.isdigit():
            value, i = _read_number(text, i)
            tokens.append(Token(TOK_NUMBER, value))
            continue
        if text.startswith("true", i):
            tokens.append(Token(TOK_TRUE, True))
            i += 4
            continue
        if text.startswith("false", i):
            tokens.append(Token(TOK_FALSE, False))
            i += 5
            continue
        if text.startswith("null", i):
            tokens.append(Token(TOK_NULL, None))
            i += 4
            continue
        raise JsoncParseError(
            f"unexpected character {c!r}",
            i,
        )
    return tokens


def _read_string(text: str, start: int) -> tuple[str, int]:
    r"""Read a JSON-quoted string starting at ``text[start] == '\"'``.

    Returns ``(decoded_value, next_index)`` where ``next_index`` is
    one past the closing quote. Raises :class:`JsoncParseError` for
    unterminated strings, invalid escapes, or invalid ``\u`` units.
    """
    assert text[start] == '"'
    n = len(text)
    i = start + 1
    out: list[str] = []
    while i < n:
        c = text[i]
        if c == '"':
            return "".join(out), i + 1
        if c == "\\":
            if i + 1 >= n:
                raise JsoncParseError("dangling escape in string", i)
            esc = text[i + 1]
            if esc == '"':
                out.append('"')
                i += 2
                continue
            if esc == "\\":
                out.append("\\")
                i += 2
                continue
            if esc == "/":
                out.append("/")
                i += 2
                continue
            if esc == "b":
                out.append("\b")
                i += 2
                continue
            if esc == "f":
                out.append("\f")
                i += 2
                continue
            if esc == "n":
                out.append("\n")
                i += 2
                continue
            if esc == "r":
                out.append("\r")
                i += 2
                continue
            if esc == "t":
                out.append("\t")
                i += 2
                continue
            if esc == "u":
                if i + 6 > n:
                    raise JsoncParseError(
                        "truncated \\u escape",
                        i,
                    )
                hex_part = text[i + 2 : i + 6]
                try:
                    out.append(chr(int(hex_part, 16)))
                except ValueError as exc:
                    raise JsoncParseError(
                        f"invalid \\u escape: {hex_part!r}",
                        i,
                    ) from exc
                i += 6
                continue
            raise JsoncParseError(
                f"invalid escape \\{esc}",
                i,
            )
        out.append(c)
        i += 1
    raise JsoncParseError("unterminated string", start)


def _read_number(text: str, start: int) -> tuple[Any, int]:
    """Read a JSON number; returns ``(int|float, next_index)``.

    JSON's numeric grammar is reused as-is; we additionally accept
    Python's ``int`` and ``float`` parsing semantics, which is a
    strict superset for the JSON-permitted inputs.
    """
    n = len(text)
    i = start
    if text[i] == "-":
        i += 1
    while i < n and text[i].isdigit():
        i += 1
    is_float = False
    if i < n and text[i] == ".":
        is_float = True
        i += 1
        while i < n and text[i].isdigit():
            i += 1
    if i < n and text[i] in "eE":
        is_float = True
        i += 1
        if i < n and text[i] in "+-":
            i += 1
        while i < n and text[i].isdigit():
            i += 1
    raw = text[start:i]
    try:
        if is_float:
            return float(raw), i
        return int(raw), i
    except ValueError as exc:
        raise JsoncParseError(
            f"invalid number {raw!r}",
            start,
        ) from exc


def _normalize_line_comment(raw: str) -> str:
    """Strip the one conventional leading space from ``// X``.

    JSONC line comments are typically authored as ``// text``
    with a single space after the ``//``. We absorb that space
    on tokenize so the AST text is the bare comment payload, and
    the JSONC and JMD serializers both prepend their own single
    space delimiter on emit. Comments without a leading space
    (``//text``) round-trip unchanged.
    """
    if raw.startswith(" "):
        return raw[1:]
    return raw


def _strip_block_comment_line(line: str) -> str:
    """Strip leading whitespace and an optional ``*`` border.

    Applies to one line of a block comment's body.
    """
    stripped = line.lstrip()
    if stripped.startswith("* "):
        return stripped[2:]
    if stripped == "*":
        return ""
    return stripped


def _block_comment_from_token(raw_body: str) -> BlockComment:
    r"""Build a :class:`BlockComment` from a tokenizer's raw body.

    The raw body is the exact text between ``/*`` and ``*/``. We
    split it into lines; leading and trailing single-newline
    boundaries are stripped (a comment of the form
    ``/*\n…\n*/`` produces lines ``[…]`` without empty wrappers),
    matching how authors typically space-pad block comments.
    Trailing whitespace per line is also stripped (cosmetic).
    """
    body = raw_body
    if body.startswith("\n"):
        body = body[1:]
    if body.endswith("\n"):
        body = body[:-1]
    lines = body.split("\n") if body else []
    cleaned = [_strip_block_comment_line(ln).rstrip() for ln in lines]
    # Strip leading / trailing empty lines: the JSONC serializer
    # pads the line before ``*/`` with the indent of the surrounding
    # block, which appears as an empty trailing line after
    # whitespace-strip. Symmetric for a leading blank.
    while cleaned and cleaned[0] == "":
        cleaned.pop(0)
    while cleaned and cleaned[-1] == "":
        cleaned.pop()
    return BlockComment(body_lines=cleaned)


# --- JSONC parser --------------------------------------------------------
#
# Recursive-descent over the token stream from :func:`_tokenize_jsonc`.
# The parser builds the AST top-down: the root must be an object
# (every ST configuration file we target starts with one), nested
# objects become :class:`Section`, arrays of scalars become
# :class:`ScalarArray`, arrays of objects become :class:`ObjectArray`,
# and comment tokens get woven into the surrounding object/array
# content sequence at the position they appear.
#
# Comments inside arrays attach as siblings of the array items rather
# than to a specific item — we keep the model uniform.


class _TokenStream:
    """Tiny cursor over a token list with peek/expect helpers."""

    __slots__ = ("tokens", "i")

    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.i = 0

    def peek(self) -> Token | None:
        if self.i < len(self.tokens):
            return self.tokens[self.i]
        return None

    def next(self) -> Token:
        if self.i >= len(self.tokens):
            raise JsoncParseError("unexpected end of input", -1)
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def expect(self, kind: str) -> Token:
        tok = self.next()
        if tok.kind != kind:
            raise JsoncParseError(
                f"expected {kind!r}, got {tok.kind!r}",
                -1,
            )
        return tok


def parse_jsonc(
    text: str,
    *,
    root_label: str = "Root",
) -> JsoncDoc:
    """Parse JSONC source into a :class:`JsoncDoc`.

    ``root_label`` becomes the root section's label in the AST and,
    on JMD output, the document's root heading. JSONC itself carries
    no equivalent slot — the caller knows whether they're parsing a
    ``Settings`` file, a ``Keymap``, etc.

    Args:
        text: JSONC source text.
        root_label: Caller-supplied label for the root section.

    Returns:
        A :class:`JsoncDoc` whose root section reflects the JSONC
        content with comments preserved at their sequence-positions.

    Raises:
        JsoncParseError: On malformed JSONC input. The error's
            ``offset`` attribute pinpoints the offending byte.
    """
    tokens = _tokenize_jsonc(text)
    stream = _TokenStream(tokens)
    # Skip any leading file-level comments before the root object.
    # ST configuration files commonly carry a copyright / usage
    # header above the ``{`` — dropping these is a documented loss
    # (spec §6 open-question), preferable to an error that breaks
    # legitimate input.
    while True:
        head = stream.peek()
        if head is None or head.kind not in (
            TOK_LINE_COMMENT,
            TOK_BLOCK_COMMENT,
        ):
            break
        stream.next()
    if stream.peek() is None:
        return JsoncDoc(root=Section(label=root_label))
    head = stream.peek()
    if head is None or head.kind != TOK_LBRACE:
        raise JsoncParseError(
            "JSONC root must be an object",
            -1,
        )
    root_children = _parse_object_body(stream)
    leftover = stream.peek()
    if leftover is not None:
        raise JsoncParseError(
            f"unexpected token after root object: {leftover.kind!r}",
            -1,
        )
    return JsoncDoc(
        root=Section(label=root_label, children=root_children),
    )


def _parse_object_body(stream: _TokenStream) -> list[Element]:
    """Parse ``{ … }`` content; consume both braces.

    Returns the object's content sequence as a list of
    :class:`Element` instances. Comments collected between/around
    members get woven in at the position they appear.
    """
    stream.expect(TOK_LBRACE)
    children: list[Element] = []
    while True:
        tok = stream.peek()
        if tok is None:
            raise JsoncParseError("unterminated object", -1)
        if tok.kind == TOK_RBRACE:
            stream.next()
            return children
        if tok.kind == TOK_LINE_COMMENT:
            stream.next()
            children.append(LineComment(text=tok.value))
            continue
        if tok.kind == TOK_BLOCK_COMMENT:
            stream.next()
            children.append(_block_comment_from_token(tok.value))
            continue
        if tok.kind == TOK_COMMA:
            stream.next()
            continue
        if tok.kind != TOK_STRING:
            raise JsoncParseError(
                f"expected string key, got {tok.kind!r}",
                -1,
            )
        key_tok = stream.next()
        stream.expect(TOK_COLON)
        value_tok = stream.peek()
        if value_tok is None:
            raise JsoncParseError("expected value", -1)
        if value_tok.kind == TOK_LBRACE:
            sub_children = _parse_object_body(stream)
            children.append(
                Section(
                    label=key_tok.value,
                    children=sub_children,
                ),
            )
            continue
        if value_tok.kind == TOK_LBRACKET:
            children.append(
                _parse_array(stream, key_tok.value),
            )
            continue
        scalar = _parse_scalar(stream)
        children.append(Field(key=key_tok.value, value=scalar))


def _parse_array(stream: _TokenStream, key: str) -> Element:
    """Parse ``[ … ]``; classify as scalar-array or object-array.

    Per-item classification: if every item is an object, we emit
    :class:`ObjectArray`; otherwise :class:`ScalarArray`. Mixed
    arrays — objects interspersed with scalars — fall back to
    :class:`ScalarArray`. Spec §6 documents the lossy edge cases.
    """
    stream.expect(TOK_LBRACKET)
    items: list[Any] = []
    has_object = False
    has_non_object = False
    while True:
        tok = stream.peek()
        if tok is None:
            raise JsoncParseError("unterminated array", -1)
        if tok.kind == TOK_RBRACKET:
            stream.next()
            break
        if tok.kind in (TOK_LINE_COMMENT, TOK_BLOCK_COMMENT):
            # Array-internal comments have no clean JMD position;
            # spec §6 open-question. We drop them silently here.
            stream.next()
            continue
        if tok.kind == TOK_COMMA:
            stream.next()
            continue
        if tok.kind == TOK_LBRACE:
            has_object = True
            sub_children = _parse_object_body(stream)
            items.append(
                Section(label="", children=sub_children),
            )
            continue
        if tok.kind == TOK_LBRACKET:
            has_non_object = True
            inner = _parse_array(stream, key="")
            if isinstance(inner, ScalarArray):
                items.append(inner.items)
            else:
                items.append(inner)
            continue
        has_non_object = True
        items.append(_parse_scalar(stream))
    if has_object and not has_non_object:
        result_obj: ObjectArray = ObjectArray(key=key, items=[])
        for it in items:
            if isinstance(it, Section):
                result_obj.items.append(it)
        return result_obj
    return ScalarArray(key=key, items=items)


def _parse_scalar(stream: _TokenStream) -> Any:
    """Consume one scalar token; return its Python value."""
    tok = stream.next()
    if tok.kind == TOK_STRING:
        return tok.value
    if tok.kind == TOK_NUMBER:
        return tok.value
    if tok.kind == TOK_TRUE:
        return True
    if tok.kind == TOK_FALSE:
        return False
    if tok.kind == TOK_NULL:
        return None
    raise JsoncParseError(
        f"expected scalar, got {tok.kind!r}",
        -1,
    )


__all__ = [
    "JsoncParseError",
    "parse_jsonc",
]
