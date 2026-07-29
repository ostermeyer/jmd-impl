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
/* Blockquote parser                                                   */
/* ------------------------------------------------------------------ */

PyObject *
parse_blockquote(ParserState *st)
{
    /* Collect raw C strings, then build a single joined result.
       Avoid PyList + join + strip overhead from the previous version. */
    const char *parts[256];
    Py_ssize_t  part_lens[256];
    Py_ssize_t  nparts = 0;
    Py_ssize_t  total_len = 0;

    while (st->pos < st->lines->len) {
        JMDLine *line = &st->lines->items[st->pos];
        if (line->heading_depth != 0)
            break;

        /* Strip whitespace from raw */
        const char *raw = line->raw;
        Py_ssize_t raw_len = line->raw_len;
        while (raw_len > 0 && (*raw == ' ' || *raw == '\t')) {
            raw++;
            raw_len--;
        }
        while (raw_len > 0 && (raw[raw_len - 1] == ' ' || raw[raw_len - 1] == '\t'))
            raw_len--;

        if (raw_len == 1 && raw[0] == '>') {
            /* Paragraph break → empty string part */
            if (nparts < 256) {
                parts[nparts] = "";
                part_lens[nparts] = 0;
                nparts++;
            }
            st->pos++;
        } else if (raw_len >= 2 && raw[0] == '>' && raw[1] == ' ') {
            if (nparts < 256) {
                parts[nparts] = raw + 2;
                part_lens[nparts] = raw_len - 2;
                total_len += raw_len - 2;
                nparts++;
            }
            st->pos++;
        } else {
            break;
        }
    }

    if (nparts == 0) {
        return PyUnicode_FromStringAndSize("", 0);
    }

    /* D13: only trim trailing empty parts (rstrip "\n"). Leading empty
       parts are part of the value — they encode a leading newline, which
       must round-trip losslessly. */
    Py_ssize_t start = 0, end = nparts;
    while (end > start && part_lens[end - 1] == 0) end--;

    if (start >= end) {
        return PyUnicode_FromStringAndSize("", 0);
    }

    /* Calculate total output size (parts + newlines between) */
    total_len = 0;
    for (Py_ssize_t i = start; i < end; i++) {
        total_len += part_lens[i];
        if (i < end - 1) total_len++;  /* newline separator */
    }

    /* Build result string directly */
    char *buf = (char *)PyMem_Malloc((size_t)total_len);
    if (!buf) { PyErr_NoMemory(); return NULL; }

    Py_ssize_t off = 0;
    for (Py_ssize_t i = start; i < end; i++) {
        if (part_lens[i] > 0) {
            memcpy(buf + off, parts[i], (size_t)part_lens[i]);
            off += part_lens[i];
        }
        if (i < end - 1) {
            buf[off++] = '\n';
        }
    }

    PyObject *result = PyUnicode_FromStringAndSize(buf, total_len);
    PyMem_Free(buf);
    return result;
}

/* ------------------------------------------------------------------ */
/* YAML-style block scalar (§5.2): ``key: |`` (literal) and             */
/* ``key: >`` (folded). Mirrors the pure-Python parse_block_scalar_from */
/* in jmd/_parser.py — parser-tolerant alternative to the blockquote    */
/* form. Consumes consecutive lines indented by ≥2 spaces from the      */
/* current position; the first indented line's width sets the strip.    */
/* ------------------------------------------------------------------ */

PyObject *
parse_block_scalar(ParserState *st, int folded)
{
    /* Collect stripped line parts (-1 sentinel for in-block blank). */
    const char *parts[256];
    Py_ssize_t  part_lens[256];
    Py_ssize_t  nparts = 0;
    Py_ssize_t  indent_strip = -1;

    while (st->pos < st->lines->len) {
        JMDLine *line = &st->lines->items[st->pos];
        if (line->heading_depth == -1) {
            /* Blank line within block: paragraph separator marker. */
            if (nparts < 256) {
                parts[nparts] = "";
                part_lens[nparts] = 0;
                nparts++;
            }
            st->pos++;
            continue;
        }
        const char *raw = line->raw;
        Py_ssize_t raw_len = line->raw_len;
        if (raw_len == 0 || raw[0] != ' ')
            break;
        /* Count leading spaces. */
        Py_ssize_t actual_indent = 0;
        while (actual_indent < raw_len && raw[actual_indent] == ' ')
            actual_indent++;
        if (actual_indent < 2)
            break;
        if (indent_strip < 0)
            indent_strip = actual_indent;
        if (actual_indent < indent_strip)
            break;
        if (nparts < 256) {
            parts[nparts] = raw + indent_strip;
            part_lens[nparts] = raw_len - indent_strip;
            nparts++;
        }
        st->pos++;
    }

    /* Drop trailing blank parts (§5.2 chomp). */
    while (nparts > 0 && part_lens[nparts - 1] == 0)
        nparts--;

    if (nparts == 0)
        return PyUnicode_FromStringAndSize("", 0);

    if (!folded) {
        /* Literal mode: join with newlines. */
        Py_ssize_t total = 0;
        for (Py_ssize_t i = 0; i < nparts; i++) {
            total += part_lens[i];
            if (i < nparts - 1) total++;
        }
        char *buf = (char *)PyMem_Malloc((size_t)total);
        if (!buf) { PyErr_NoMemory(); return NULL; }
        Py_ssize_t off = 0;
        for (Py_ssize_t i = 0; i < nparts; i++) {
            if (part_lens[i] > 0) {
                memcpy(buf + off, parts[i], (size_t)part_lens[i]);
                off += part_lens[i];
            }
            if (i < nparts - 1) buf[off++] = '\n';
        }
        PyObject *result = PyUnicode_FromStringAndSize(buf, total);
        PyMem_Free(buf);
        return result;
    }

    /* Folded mode: non-blank consecutive lines folded with spaces;
       each in-block blank line becomes one '\n'.  Worst-case output
       size is bounded by sum(part_lens) + nparts (separators). */
    Py_ssize_t cap = 0;
    for (Py_ssize_t i = 0; i < nparts; i++)
        cap += part_lens[i] + 1;
    char *buf = (char *)PyMem_Malloc((size_t)cap > 0 ? (size_t)cap : 1);
    if (!buf) { PyErr_NoMemory(); return NULL; }
    Py_ssize_t off = 0;
    int para_open = 0;  /* a non-blank line is currently being collected */
    for (Py_ssize_t i = 0; i < nparts; i++) {
        if (part_lens[i] == 0) {
            /* Blank line: close paragraph, emit \n separator. */
            para_open = 0;
            buf[off++] = '\n';
            continue;
        }
        if (para_open) {
            /* Continue paragraph: fold previous line break to a space. */
            buf[off++] = ' ';
        }
        memcpy(buf + off, parts[i], (size_t)part_lens[i]);
        off += part_lens[i];
        para_open = 1;
    }
    PyObject *result = PyUnicode_FromStringAndSize(buf, off);
    PyMem_Free(buf);
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
