/*
 * _cparser_multiline.c — multiline and inline field values.
 *
 * Copyright (c) 2026 Andreas Ostermeyer <andreas@ostermeyer.de>
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0.
 * See LICENSE at the repository root for the full license text.
 */

#include "_cparser_internal.h"

/* ------------------------------------------------------------------ */
/* Shared scratch storage for multiline values (§5.2 / §9.1).          */
/* Ported from the Python parser's unbounded list of text parts.        */
/* ------------------------------------------------------------------ */

/* Parts borrow source bytes; the vector owns only its allocation. */
typedef struct {
    const char *text;
    Py_ssize_t len;
} TextPart;

typedef struct {
    TextPart *items;
    Py_ssize_t len;
    Py_ssize_t cap;
    Py_ssize_t output_bound;
} TextParts;

/*
 * Append a borrowed slice to an owned scratch vector. The source must outlive
 * rendering. Return 1 on success, 0 with MemoryError on allocation/size failure.
 * Commit neither the slice nor its size until growth succeeds; callers advance
 * their input cursor only after this function succeeds.
 */
static int
text_parts_append(TextParts *parts, const char *text, Py_ssize_t len)
{
    /* Every part needs at most one separator byte in either rendering mode. */
    if (len >= PY_SSIZE_T_MAX - parts->output_bound) {
        PyErr_NoMemory();
        return 0;
    }
    if (parts->len == parts->cap) {
        const Py_ssize_t maxcap = PY_SSIZE_T_MAX / sizeof(TextPart);
        Py_ssize_t newcap;
        TextPart *items;

        if (parts->cap == maxcap) {
            PyErr_NoMemory();
            return 0;
        }
        newcap = parts->cap == 0 ? 32 :
                 parts->cap > maxcap / 2 ? maxcap : parts->cap * 2;
        items = (TextPart *)PyMem_Realloc(
            parts->items, (size_t)newcap * sizeof(TextPart));
        if (!items) {
            PyErr_NoMemory();
            return 0;
        }
        parts->items = items;
        parts->cap = newcap;
    }
    parts->items[parts->len].text = text;
    parts->items[parts->len].len = len;
    parts->len++;
    parts->output_bound += len + 1;
    return 1;
}

/*
 * Render borrowed parts as a new Unicode reference, or NULL with an exception.
 * The vector and its source remain caller-owned. Only trailing empty parts are
 * removed: leading empty parts encode leading newlines (D13). Folded mode joins
 * consecutive nonempty lines with spaces and emits one newline per empty part.
 * Both modes fit the checked sum of (part length + one separator).
 */
static PyObject *
text_parts_render(const TextParts *parts, int folded)
{
    Py_ssize_t end = parts->len;
    Py_ssize_t off = 0;
    int para_open = 0;
    char *buf;
    PyObject *result;

    while (end > 0 && parts->items[end - 1].len == 0)
        end--;
    if (end == 0)
        return PyUnicode_FromStringAndSize("", 0);

    buf = (char *)PyMem_Malloc((size_t)parts->output_bound);
    if (!buf)
        return PyErr_NoMemory();
    for (Py_ssize_t i = 0; i < end; i++) {
        const TextPart *part = &parts->items[i];

        if (folded && part->len == 0) {
            para_open = 0;
            buf[off++] = '\n';
            continue;
        }
        if (folded ? para_open : i > 0)
            buf[off++] = folded ? ' ' : '\n';
        if (part->len > 0) {
            memcpy(buf + off, part->text, (size_t)part->len);
            off += part->len;
        }
        para_open = 1;
    }
    result = PyUnicode_FromStringAndSize(buf, off);
    PyMem_Free(buf);
    return result;
}

/* ------------------------------------------------------------------ */
/* Blockquote parser (§9.1).                                           */
/* ------------------------------------------------------------------ */

/*
 * Consume consecutive blockquote lines from the borrowed state. Return a new
 * Unicode reference or NULL with an exception. Scratch storage is released on
 * every exit, including append/render failure; no partial value is returned.
 */
PyObject *
parse_blockquote(ParserState *st)
{
    TextParts parts = {0};
    PyObject *result = NULL;

    while (st->pos < st->lines->len) {
        JMDLine *line = &st->lines->items[st->pos];
        if (line->heading_depth != 0)
            break;

        /* Preserve the parser's established whitespace normalization. */
        const char *raw = line->raw;
        Py_ssize_t raw_len = line->raw_len;
        while (raw_len > 0 && (*raw == ' ' || *raw == '\t')) {
            raw++;
            raw_len--;
        }
        while (raw_len > 0 &&
               (raw[raw_len - 1] == ' ' || raw[raw_len - 1] == '\t'))
            raw_len--;

        if (raw_len == 1 && raw[0] == '>') {
            if (!text_parts_append(&parts, "", 0))
                goto done;
        } else if (raw_len >= 2 && raw[0] == '>' && raw[1] == ' ') {
            if (!text_parts_append(&parts, raw + 2, raw_len - 2))
                goto done;
        } else {
            break;
        }
        st->pos++;
    }
    result = text_parts_render(&parts, 0);

done:
    PyMem_Free(parts.items);
    return result;
}

/* ------------------------------------------------------------------ */
/* Tolerated literal/folded block scalars (§5.2).                       */
/* ------------------------------------------------------------------ */

/*
 * Consume indented lines from the borrowed state, stripping the first content
 * line's indentation (at least two spaces). Blank lines remain paragraph parts.
 * Return a new Unicode reference or NULL with an exception; free scratch
 * storage on all paths. This mirrors Python's parse_block_scalar_from().
 */
PyObject *
parse_block_scalar(ParserState *st, int folded)
{
    TextParts parts = {0};
    Py_ssize_t indent_strip = -1;
    PyObject *result = NULL;

    while (st->pos < st->lines->len) {
        JMDLine *line = &st->lines->items[st->pos];
        if (line->heading_depth == -1) {
            if (!text_parts_append(&parts, "", 0))
                goto done;
            st->pos++;
            continue;
        }
        const char *raw = line->raw;
        Py_ssize_t raw_len = line->raw_len;
        if (raw_len == 0 || raw[0] != ' ')
            break;
        Py_ssize_t actual_indent = 0;
        while (actual_indent < raw_len && raw[actual_indent] == ' ')
            actual_indent++;
        if (actual_indent < 2)
            break;
        if (indent_strip < 0)
            indent_strip = actual_indent;
        if (actual_indent < indent_strip)
            break;
        if (!text_parts_append(&parts, raw + indent_strip,
                               raw_len - indent_strip))
            goto done;
        st->pos++;
    }
    result = text_parts_render(&parts, folded);

done:
    PyMem_Free(parts.items);
    return result;
}

/* ------------------------------------------------------------------ */
/* Check if next non-blank line starts with '>'                        */
/* ------------------------------------------------------------------ */

int
next_is_blockquote(ParserState *st)
{
    if (st->pos >= st->lines->len) return 0;
    JMDLine *nxt = &st->lines->items[st->pos];
    if (nxt->heading_depth != 0) return 0;

    /* Check stripped raw starts with '>' */
    const char *raw = nxt->raw;
    Py_ssize_t raw_len = nxt->raw_len;
    while (raw_len > 0 && (*raw == ' ' || *raw == '\t')) {
        raw++;
        raw_len--;
    }
    return (raw_len > 0 && raw[0] == '>');
}

/* ------------------------------------------------------------------ */
/* Parse a scalar or multiline field value at the current cursor.      */
/*                                                                      */
/* Input value_text is borrowed from a tokenized line. The returned     */
/* Python object is a new reference. Block parsers advance st->pos; a   */
/* scalar or empty value leaves it unchanged.                            */
/* ------------------------------------------------------------------ */

PyObject *
parse_field_value(ParserState *st, const char *value_text,
                  Py_ssize_t value_len)
{
    if (value_len == 0) {
        if (next_is_blockquote(st))
            return parse_blockquote(st);
        return PyUnicode_FromStringAndSize("", 0);
    }
    if (value_len == 1 && (value_text[0] == '|' || value_text[0] == '>'))
        return parse_block_scalar(st, value_text[0] == '>');
    return parse_scalar(value_text, value_len);
}

/* ------------------------------------------------------------------ */
/* Inline helper: find ": " using memchr (for non-kv contexts)         */
/* ------------------------------------------------------------------ */

Py_ssize_t
find_colon_space(const char *s, Py_ssize_t len)
{
    /* Find first occurrence of ": " in s */
    const char *p = s;
    Py_ssize_t remaining = len;
    while (remaining > 1) {
        const char *colon = (const char *)memchr(p, ':', (size_t)(remaining - 1));
        if (!colon) return -1;
        if (colon[1] == ' ') return (Py_ssize_t)(colon - s);
        Py_ssize_t skip = (colon - p) + 1;
        p += skip;
        remaining -= skip;
    }
    return -1;
}
