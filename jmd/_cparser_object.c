/*
 * _cparser_object.c — object bodies and shared item helpers.
 *
 * Copyright (c) 2026 Andreas Ostermeyer <andreas@ostermeyer.de>
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0.
 * See LICENSE at the repository root for the full license text.
 */

#include "_cparser_internal.h"

/* ------------------------------------------------------------------ */
/* Object body parser                                                  */
/* ------------------------------------------------------------------ */

PyObject *
parse_object_body(ParserState *st, int depth)
{
    PyObject *obj = PyDict_New();
    if (!obj) return NULL;
    /* §7.4 per-scope kinds tracker: maps key → kind string singleton.
       Detached lifetime — released at function exit. */
    PyObject *kinds = PyDict_New();
    if (!kinds) { Py_DECREF(obj); return NULL; }

    LineArray *lines = st->lines;
    Py_ssize_t lines_len = lines->len;
    Py_ssize_t pos = st->pos;
    int depth_plus_1 = depth + 1;

    while (pos < lines_len) {
        JMDLine *line = &lines->items[pos];
        int hd = line->heading_depth;

        /* Blank line handling */
        if (hd == -1) {
            Py_ssize_t peek = pos + 1;
            while (peek < lines_len && lines->items[peek].heading_depth == -1)
                peek++;
            if (peek < lines_len) {
                JMDLine *nxt = &lines->items[peek];
                if (nxt->heading_depth > 0) {
                    pos++;
                    continue;
                }
            }
            if (depth == 1) {
                pos++;
                continue;
            } else {
                break;
            }
        }

        /* §8.6 level-pop into an object scope. Recursive descent means
         * deeper scopes have already returned by the time we see this
         * line, so we are the innermost open scope:
         *   hd < depth   the pop targets an enclosing scope — return and
         *                let it consume the line.
         *   hd == depth  the pop targets us; nothing left to close.
         *   hd > depth   no scope was ever established that deep; the pop
         *                clamps to us. Both are no-ops.
         * A level-pop never opens a scope, so this must not reach
         * parse_heading_into (an anonymous `[]` sub-array heading has
         * content_len 2 and is unaffected). Mirrors the array-scope
         * level-pop in _cparser_array.c. */
        if (hd > 0 && line->content_len == 0) {
            if (hd < depth)
                break;
            pos++;
            continue;
        }

        /* Heading at depth or shallower: scope ends */
        if (hd > 0 && hd <= depth)
            break;

        /* Heading at depth+1: child scope */
        if (hd == depth_plus_1) {
            st->pos = pos;
            if (parse_heading_into(st, obj, kinds, depth_plus_1) < 0) {
                Py_DECREF(kinds);
                Py_DECREF(obj);
                return NULL;
            }
            pos = st->pos;
            continue;
        }

        /* Indentation denotes array-item continuation only (§11.2). */
        if (line->raw_len > 0
            && (line->raw[0] == ' ' || line->raw[0] == '\t'))
        {
            raise_structural_parse_error("prose_in_body", line->number);
            Py_DECREF(kinds);
            Py_DECREF(obj);
            return NULL;
        }

        /* Non-heading line (hd == 0) */
        const char *content = line->content;
        Py_ssize_t content_len = line->content_len;

        /* Find ": " using memchr */
        Py_ssize_t colon_pos = find_colon_space(content, content_len);

        if (colon_pos >= 0) {
            PyObject *key = parse_key(content, colon_pos);
            if (!key) { Py_DECREF(kinds); Py_DECREF(obj); return NULL; }

            const char *val_str = content + colon_pos + 2;
            Py_ssize_t val_len = content_len - colon_pos - 2;

            if (val_len == 0) {
                /* Empty value — check for blockquote */
                pos++;
                st->pos = pos;
                if (next_is_blockquote(st)) {
                    PyObject *val = parse_blockquote(st);
                    if (!val) { Py_DECREF(key); Py_DECREF(kinds); Py_DECREF(obj); return NULL; }
                    if (set_scalar_field(obj, kinds, key, val, line->number, 0) < 0) {
                        Py_DECREF(val);
                        Py_DECREF(key);
                        Py_DECREF(kinds);
                        Py_DECREF(obj);
                        return NULL;
                    }
                    Py_DECREF(val);
                    pos = st->pos;
                } else {
                    PyObject *val = PyUnicode_FromStringAndSize("", 0);
                    if (!val) { Py_DECREF(key); Py_DECREF(kinds); Py_DECREF(obj); return NULL; }
                    if (set_scalar_field(obj, kinds, key, val, line->number, 0) < 0) {
                        Py_DECREF(val);
                        Py_DECREF(key);
                        Py_DECREF(kinds);
                        Py_DECREF(obj);
                        return NULL;
                    }
                    Py_DECREF(val);
                }
            } else {
                /* §5.2: ``key: |`` (literal) and ``key: >`` (folded)
                   open a YAML-style block scalar at the next line. */
                PyObject *val;
                if (val_len == 1 && (val_str[0] == '|' || val_str[0] == '>')) {
                    int folded = (val_str[0] == '>');
                    pos++;
                    st->pos = pos;
                    val = parse_block_scalar(st, folded);
                    if (!val) { Py_DECREF(key); Py_DECREF(kinds); Py_DECREF(obj); return NULL; }
                    pos = st->pos;
                } else {
                    val = parse_scalar(val_str, val_len);
                    if (!val) { Py_DECREF(key); Py_DECREF(kinds); Py_DECREF(obj); return NULL; }
                    pos++;
                }
                if (set_scalar_field(obj, kinds, key, val, line->number, 0) < 0) {
                    Py_DECREF(val);
                    Py_DECREF(key);
                    Py_DECREF(kinds);
                    Py_DECREF(obj);
                    return NULL;
                }
                Py_DECREF(val);
            }
            Py_DECREF(key);
            continue;
        }

        /* key: (colon at end of line, no space after) — check for blockquote */
        if (content_len > 0 && content[content_len - 1] == ':') {
            PyObject *key = parse_key(content, content_len - 1);
            if (!key) { Py_DECREF(kinds); Py_DECREF(obj); return NULL; }
            pos++;
            st->pos = pos;
            if (next_is_blockquote(st)) {
                PyObject *val = parse_blockquote(st);
                if (!val) { Py_DECREF(key); Py_DECREF(kinds); Py_DECREF(obj); return NULL; }
                if (set_scalar_field(obj, kinds, key, val, line->number, 0) < 0) {
                    Py_DECREF(val);
                    Py_DECREF(key);
                    Py_DECREF(kinds);
                    Py_DECREF(obj);
                    return NULL;
                }
                Py_DECREF(val);
                pos = st->pos;
            } else {
                PyObject *val = PyUnicode_FromStringAndSize("", 0);
                if (!val) { Py_DECREF(key); Py_DECREF(kinds); Py_DECREF(obj); return NULL; }
                if (set_scalar_field(obj, kinds, key, val, line->number, 0) < 0) {
                    Py_DECREF(val);
                    Py_DECREF(key);
                    Py_DECREF(kinds);
                    Py_DECREF(obj);
                    return NULL;
                }
                Py_DECREF(val);
            }
            Py_DECREF(key);
            continue;
        }

        /* A nested object may end where its containing array resumes. */
        if (depth > 1
            && ((content_len == 1 && content[0] == '-')
                || (content_len > 1 && content[0] == '-'
                    && content[1] == ' ')
                || is_thematic_break(line)))
            break;

        raise_structural_parse_error("prose_in_body", line->number);
        Py_DECREF(kinds);
        Py_DECREF(obj);
        return NULL;
    }

    st->pos = pos;
    Py_DECREF(kinds);
    return obj;
}

/* ------------------------------------------------------------------ */
/* Heading into object                                                 */
/* ------------------------------------------------------------------ */

int
parse_heading_into(ParserState *st, PyObject *obj, PyObject *kinds, int depth)
{
    if (st->pos >= st->lines->len)
        return 0;

    JMDLine *line = &st->lines->items[st->pos];
    const char *content = line->content;
    Py_ssize_t content_len = line->content_len;
    int line_no = line->number;

    /* Array item markers are valid only when consumed by a parent array. */
    if ((content_len == 1 && content[0] == '-')
        || (content_len == 2 && content[0] == '[' && content[1] == ']'))
        return raise_structural_parse_error("invalid_structure", line_no);

    st->pos++;

    /* Array heading: ## key[] (§7.4: array-sigil — record kind) */
    if (content_len >= 3 && content[content_len - 2] == '['
        && content[content_len - 1] == ']')
    {
        PyObject *key = parse_key(content, content_len - 2);
        if (!key) return -1;
        PyObject *arr = parse_array_body(st, depth);
        if (!arr) { Py_DECREF(key); return -1; }
        int rc = set_array_sigil_heading(obj, kinds, key, arr, line_no);
        Py_DECREF(key);
        Py_DECREF(arr);
        return rc;
    }

    /* Scalar heading: ## key: value — use memchr */
    Py_ssize_t colon_pos = find_colon_space(content, content_len);

    if (colon_pos >= 0) {
        PyObject *key = parse_key(content, colon_pos);
        if (!key) return -1;
        Py_ssize_t val_len = content_len - colon_pos - 2;
        if (val_len == 0) {
            /* Check for blockquote */
            if (next_is_blockquote(st)) {
                PyObject *val = parse_blockquote(st);
                if (!val) { Py_DECREF(key); return -1; }
                int rc = set_scalar_field(obj, kinds, key, val, line_no, 1);
                Py_DECREF(key);
                Py_DECREF(val);
                return rc;
            } else {
                PyObject *val = PyUnicode_FromStringAndSize("", 0);
                if (!val) { Py_DECREF(key); return -1; }
                int rc = set_scalar_field(obj, kinds, key, val, line_no, 1);
                Py_DECREF(key);
                Py_DECREF(val);
                return rc;
            }
        } else {
            /* §5.2: ``## key: |`` and ``## key: >`` open a block scalar. */
            const char *val_str = content + colon_pos + 2;
            PyObject *val;
            if (val_len == 1 && (val_str[0] == '|' || val_str[0] == '>')) {
                int folded = (val_str[0] == '>');
                val = parse_block_scalar(st, folded);
            } else {
                val = parse_scalar(val_str, val_len);
            }
            if (!val) { Py_DECREF(key); return -1; }
            int rc = set_scalar_field(obj, kinds, key, val, line_no, 1);
            Py_DECREF(key);
            Py_DECREF(val);
            return rc;
        }
    }

    /* Scalar heading with trailing colon: ## key: */
    if (content_len > 0 && content[content_len - 1] == ':') {
        /* Make sure there's no ": " in content (already checked above) */
        PyObject *key = parse_key(content, content_len - 1);
        if (!key) return -1;
        if (next_is_blockquote(st)) {
            PyObject *val = parse_blockquote(st);
            if (!val) { Py_DECREF(key); return -1; }
            int rc = set_scalar_field(obj, kinds, key, val, line_no, 1);
            Py_DECREF(key);
            Py_DECREF(val);
            return rc;
        } else {
            PyObject *val = PyUnicode_FromStringAndSize("", 0);
            if (!val) { Py_DECREF(key); return -1; }
            int rc = set_scalar_field(obj, kinds, key, val, line_no, 1);
            Py_DECREF(key);
            Py_DECREF(val);
            return rc;
        }
    }

    /* Object heading: ## key (§7.4: object — record kind, promote on repeat) */
    PyObject *key = parse_key(content, content_len);
    if (!key) return -1;
    PyObject *child = parse_object_body(st, depth);
    if (!child) { Py_DECREF(key); return -1; }
    int rc = set_object_heading(obj, kinds, key, child, line_no);
    Py_DECREF(key);
    Py_DECREF(child);
    return rc;
}

/* ------------------------------------------------------------------ */
/* Check if content is a dash item ("- " prefix or bare "-")           */
/* ------------------------------------------------------------------ */

int
is_dash_item(const char *content, Py_ssize_t len)
{
    if (len == 1 && content[0] == '-') return 1;
    if (len > 1 && content[0] == '-' && content[1] == ' ') return 1;
    return 0;
}

/* Return the key/value separator for an item field. A trailing colon is
 * the empty-value form that may introduce a canonical blockquote. */
Py_ssize_t
find_item_field_split(const char *content, Py_ssize_t content_len)
{
    Py_ssize_t split = find_kv_split(content, content_len);
    if (split >= 0)
        return split;
    if (content_len > 1 && content[content_len - 1] == ':')
        return content_len - 1;
    return -1;
}

/* ------------------------------------------------------------------ */
/* Parse the first field carried by an object item's dash line.        */
/*                                                                      */
/* Input slices are borrowed from the token vector. The returned dict  */
/* is a new reference. Multiline content is consumed through st->pos.  */
/* ------------------------------------------------------------------ */

PyObject *
parse_dash_kv_item(ParserState *st, const char *after,
                   Py_ssize_t after_len, Py_ssize_t split)
{
    PyObject *initial = PyDict_New();
    if (!initial) return NULL;
    PyObject *k = parse_key(after, split);
    const char *value_text = after + split + 1;
    Py_ssize_t value_len = 0;
    if (split + 1 < after_len && after[split + 1] == ' ') {
        value_text++;
        value_len = after_len - split - 2;
    }
    PyObject *v = parse_field_value(st, value_text, value_len);
    if (!k || !v) {
        Py_XDECREF(k);
        Py_XDECREF(v);
        Py_DECREF(initial);
        return NULL;
    }
    if (PyDict_SetItem(initial, k, v) < 0) {
        Py_DECREF(k);
        Py_DECREF(v);
        Py_DECREF(initial);
        return NULL;
    }
    Py_DECREF(k);
    Py_DECREF(v);
    return initial;
}
