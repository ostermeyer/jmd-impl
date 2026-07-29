/*
 * _cparser_array.c — array bodies and record continuation.
 *
 * Copyright (c) 2026 Andreas Ostermeyer <andreas@ostermeyer.de>
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0.
 * See LICENSE at the repository root for the full license text.
 */

#include "_cparser_internal.h"

/* ------------------------------------------------------------------ */
/* Array body parser                                                   */
/* ------------------------------------------------------------------ */

PyObject *
parse_array_body(ParserState *st, int depth)
{
    PyObject *items = PyList_New(0);
    if (!items) return NULL;

    LineArray *lines = st->lines;
    Py_ssize_t lines_len = lines->len;
    Py_ssize_t pos = st->pos;
    int depth_plus_1 = depth + 1;

    while (pos < lines_len) {
        JMDLine *line = &lines->items[pos];
        int hd = line->heading_depth;

        /* Blank line */
        if (hd == -1) {
            Py_ssize_t peek = pos + 1;
            while (peek < lines_len && lines->items[peek].heading_depth == -1)
                peek++;
            if (peek < lines_len) {
                JMDLine *nxt = &lines->items[peek];
                int nhd = nxt->heading_depth;

                int is_item = 0;
                /* Non-heading dash item */
                if (nhd == 0 && is_dash_item(nxt->content, nxt->content_len))
                    is_item = 1;
                /* Same-depth heading dash item */
                if (nhd == depth && is_dash_item(nxt->content, nxt->content_len))
                    is_item = 1;
                /* Child-depth heading: [], -, or - ... */
                if (nhd == depth_plus_1) {
                    if ((nxt->content_len == 2 && nxt->content[0] == '['
                         && nxt->content[1] == ']')
                        || is_dash_item(nxt->content, nxt->content_len))
                        is_item = 1;
                }

                if (is_item) {
                    pos++;
                    continue;
                }

                /* Thematic break (---) after a blank line: skip the blank
                 * so the break is handled by the in-body separator rule
                 * below. Unconditional per the v0.3.4 §8.6 clarification —
                 * in a mixed array the qualifying item may follow the
                 * break, so we must not gate on the preceding item. */
                if (is_thematic_break(nxt)) {
                    pos++;
                    continue;
                }
            }
            break;
        }

        const char *content = line->content;
        Py_ssize_t content_len = line->content_len;

        /* Heading at same depth or shallower */
        if (hd > 0 && hd <= depth) {
            /* Anonymous heading at this array's depth = level-pop: a
             * deeper scope of the previous item (sub-array / sub-object)
             * was closed, and we are back at this array. Consume the
             * marker and continue with the next item. A labelled heading
             * at this depth still ends the array (§8.6). */
            if (hd == depth && content_len == 0) {
                pos++;
                continue;
            }
            /* Depth-qualified item at same depth: ## - */
            if (hd == depth && content_len == 1 && content[0] == '-') {
                pos++;
                st->pos = pos;
                PyObject *item_obj = parse_item_object(st, depth, NULL);
                if (!item_obj) { Py_DECREF(items); return NULL; }
                if (PyList_Append(items, item_obj) < 0) {
                    Py_DECREF(item_obj);
                    Py_DECREF(items);
                    return NULL;
                }
                Py_DECREF(item_obj);
                pos = st->pos;
                continue;
            }
            if (hd == depth && content_len > 1 && content[0] == '-'
                && content[1] == ' ')
            {
                const char *after = content + 2;
                Py_ssize_t after_len = content_len - 2;
                /* Single call to find_kv_split (eliminates double call) */
                Py_ssize_t split = find_item_field_split(after, after_len);
                if (split >= 0) {
                    pos++;
                    st->pos = pos;
                    PyObject *initial = parse_dash_kv_item(st, after, after_len, split);
                    if (!initial) { Py_DECREF(items); return NULL; }
                    PyObject *item_obj = parse_item_object(st, depth, initial);
                    Py_DECREF(initial);
                    if (!item_obj) { Py_DECREF(items); return NULL; }
                    if (PyList_Append(items, item_obj) < 0) {
                        Py_DECREF(item_obj);
                        Py_DECREF(items);
                        return NULL;
                    }
                    Py_DECREF(item_obj);
                    pos = st->pos;
                    continue;
                }
                /* Depth-qualified scalar item: ## - value */
                PyObject *val = parse_scalar(after, after_len);
                if (!val) { Py_DECREF(items); return NULL; }
                if (PyList_Append(items, val) < 0) {
                    Py_DECREF(val);
                    Py_DECREF(items);
                    return NULL;
                }
                Py_DECREF(val);
                pos++;
                continue;
            }
            break;
        }

        /* Sub-array heading at depth+1: ### [] */
        if (hd == depth_plus_1 && content_len == 2
            && content[0] == '[' && content[1] == ']')
        {
            pos++;
            st->pos = pos;
            PyObject *sub = parse_array_body(st, depth_plus_1);
            if (!sub) { Py_DECREF(items); return NULL; }
            if (PyList_Append(items, sub) < 0) {
                Py_DECREF(sub);
                Py_DECREF(items);
                return NULL;
            }
            Py_DECREF(sub);
            pos = st->pos;
            continue;
        }

        /* Depth-qualified item at depth+1 */
        if (hd == depth_plus_1 && content_len == 1 && content[0] == '-') {
            pos++;
            st->pos = pos;
            PyObject *item_obj = parse_item_object(st, depth, NULL);
            if (!item_obj) { Py_DECREF(items); return NULL; }
            if (PyList_Append(items, item_obj) < 0) {
                Py_DECREF(item_obj);
                Py_DECREF(items);
                return NULL;
            }
            Py_DECREF(item_obj);
            pos = st->pos;
            continue;
        }
        if (hd == depth_plus_1 && content_len > 1 && content[0] == '-'
            && content[1] == ' ')
        {
            const char *after = content + 2;
            Py_ssize_t after_len = content_len - 2;
            Py_ssize_t split = find_item_field_split(after, after_len);
            if (split >= 0) {
                pos++;
                st->pos = pos;
                PyObject *initial = parse_dash_kv_item(st, after, after_len, split);
                if (!initial) { Py_DECREF(items); return NULL; }
                PyObject *item_obj = parse_item_object(st, depth, initial);
                Py_DECREF(initial);
                if (!item_obj) { Py_DECREF(items); return NULL; }
                if (PyList_Append(items, item_obj) < 0) {
                    Py_DECREF(item_obj);
                    Py_DECREF(items);
                    return NULL;
                }
                Py_DECREF(item_obj);
                pos = st->pos;
                continue;
            }
            /* Depth+1 qualified scalar item: ### - value (§8.6b form) */
            PyObject *val = parse_scalar(after, after_len);
            if (!val) { Py_DECREF(items); return NULL; }
            if (PyList_Append(items, val) < 0) {
                Py_DECREF(val);
                Py_DECREF(items);
                return NULL;
            }
            Py_DECREF(val);
            pos++;
            continue;
        }

        /* Heading at depth+1 that is not [], -, or - ... : stop */
        if (hd == depth_plus_1)
            break;

        /* Deeper heading: stop */
        if (hd > depth_plus_1)
            break;

        /* Non-heading lines (hd == 0) */

        /* Bare `-` */
        if (content_len == 1 && content[0] == '-') {
            pos++;
            st->pos = pos;
            PyObject *item_obj = parse_item_object(st, depth, NULL);
            if (!item_obj) { Py_DECREF(items); return NULL; }
            if (PyList_Append(items, item_obj) < 0) {
                Py_DECREF(item_obj);
                Py_DECREF(items);
                return NULL;
            }
            Py_DECREF(item_obj);
            pos = st->pos;
            continue;
        }

        /* `- ...`: object item or scalar item */
        if (content_len > 1 && content[0] == '-' && content[1] == ' ') {
            const char *after = content + 2;
            Py_ssize_t after_len = content_len - 2;

            /* Single find_kv_split call — no separate is_kv_content */
            Py_ssize_t split = find_item_field_split(after, after_len);
            if (split >= 0) {
                /* Object item with first field */
                pos++;
                st->pos = pos;
                PyObject *initial = parse_dash_kv_item(st, after, after_len, split);
                if (!initial) { Py_DECREF(items); return NULL; }
                PyObject *item_obj = parse_item_object(st, depth, initial);
                Py_DECREF(initial);
                if (!item_obj) { Py_DECREF(items); return NULL; }
                if (PyList_Append(items, item_obj) < 0) {
                    Py_DECREF(item_obj);
                    Py_DECREF(items);
                    return NULL;
                }
                Py_DECREF(item_obj);
                pos = st->pos;
            } else {
                /* Scalar item */
                PyObject *val = parse_scalar(after, after_len);
                if (!val) { Py_DECREF(items); return NULL; }
                if (PyList_Append(items, val) < 0) {
                    Py_DECREF(val);
                    Py_DECREF(items);
                    return NULL;
                }
                Py_DECREF(val);
                pos++;
            }
            continue;
        }

        /* Thematic break (---): item separator within an array body
         * (§8.6). Unconditional per the v0.3.4 clarification — the array
         * as a whole qualifies for separators, so a `---` after a flat
         * item still starts the next item. Gating on the preceding item's
         * shape would drop items in mixed arrays. */
        if (is_thematic_break(line)) {
            pos++;
            continue;
        }

        break;
    }

    st->pos = pos;
    return items;
}

/* ------------------------------------------------------------------ */
/* Item object parser                                                  */
/* ------------------------------------------------------------------ */

PyObject *
parse_item_object(ParserState *st, int array_depth, PyObject *initial)
{
    PyObject *obj;
    if (initial) {
        obj = PyDict_Copy(initial);
    } else {
        obj = PyDict_New();
    }
    if (!obj) return NULL;
    /* §7.4 per-item kinds tracker; pre-populate from initial so repeated
       keys (across initial + subsequent bare fields) are detected. */
    PyObject *kinds = PyDict_New();
    if (!kinds) { Py_DECREF(obj); return NULL; }
    if (initial) {
        PyObject *k, *v;
        Py_ssize_t i = 0;
        while (PyDict_Next(initial, &i, &k, &v)) {
            if (record_bare_scalar_kind(kinds, k) < 0) {
                Py_DECREF(kinds); Py_DECREF(obj); return NULL;
            }
        }
    }

    int child_depth = array_depth + 1;
    LineArray *lines = st->lines;
    Py_ssize_t lines_len = lines->len;
    Py_ssize_t pos = st->pos;

    /* Phase 1: consume indented continuation fields (2+ spaces + key: value) */
    if (pos < lines_len && lines->items[pos].raw_len > 0
        && lines->items[pos].raw[0] == ' ')
    {
        while (pos < lines_len) {
            JMDLine *line = &lines->items[pos];

            /* Check for indented continuation field */
            if (line->raw_len >= 3 && line->raw[0] == ' ' && line->raw[1] == ' ') {
                /* lstrip spaces */
                const char *stripped = line->raw;
                Py_ssize_t stripped_len = line->raw_len;
                while (stripped_len > 0 && *stripped == ' ') {
                    stripped++;
                    stripped_len--;
                }
                /* Also strip trailing whitespace */
                while (stripped_len > 0 &&
                       (stripped[stripped_len - 1] == ' ' ||
                        stripped[stripped_len - 1] == '\t' ||
                        stripped[stripped_len - 1] == '\r'))
                    stripped_len--;

                Py_ssize_t split = find_item_field_split(stripped,
                                                          stripped_len);
                if (split >= 0) {
                    PyObject *k = parse_key(stripped, split);
                    const char *value_text = stripped + split + 1;
                    Py_ssize_t value_len = 0;
                    if (split + 1 < stripped_len
                        && stripped[split + 1] == ' ')
                    {
                        value_text++;
                        value_len = stripped_len - split - 2;
                    }
                    pos++;
                    st->pos = pos;
                    PyObject *v = parse_field_value(st, value_text,
                                                    value_len);
                    if (!k || !v) {
                        Py_XDECREF(k);
                        Py_XDECREF(v);
                        Py_DECREF(kinds);
                        Py_DECREF(obj);
                        return NULL;
                    }
                    if (set_scalar_field(obj, kinds, k, v,
                                         line->number, 0) < 0) {
                        Py_DECREF(k);
                        Py_DECREF(v);
                        Py_DECREF(kinds);
                        Py_DECREF(obj);
                        return NULL;
                    }
                    Py_DECREF(k);
                    Py_DECREF(v);
                    pos = st->pos;
                    continue;
                }
            }

            /* Blank line between indented fields: peek ahead */
            if (line->heading_depth == -1) {
                Py_ssize_t peek = pos + 1;
                while (peek < lines_len && lines->items[peek].heading_depth == -1)
                    peek++;
                if (peek < lines_len) {
                    JMDLine *nxt_line = &lines->items[peek];
                    /* If next non-blank is indented, skip blank */
                    if (nxt_line->raw_len >= 3
                        && nxt_line->raw[0] == ' ' && nxt_line->raw[1] == ' ')
                    {
                        /* lstrip spaces from nxt */
                        const char *ns = nxt_line->raw;
                        Py_ssize_t ns_len = nxt_line->raw_len;
                        while (ns_len > 0 && *ns == ' ') { ns++; ns_len--; }
                        while (ns_len > 0 &&
                               (ns[ns_len - 1] == ' ' || ns[ns_len - 1] == '\t'))
                            ns_len--;
                        if (find_item_field_split(ns, ns_len) >= 0) {
                            pos++;
                            continue;
                        }
                    }
                    /* If next is a child heading, skip blank */
                    if (nxt_line->heading_depth == child_depth) {
                        pos++;
                        continue;
                    }
                }
                break;
            }

            /* Thematic breaks are inert decoration inside an array item. */
            if (is_thematic_break(line)) {
                pos++;
                continue;
            }

            /* After indented fields, also accept bare fields and headings */
            break;
        }
    }

    st->pos = pos;

    /* Phase 2: consume bare fields and child headings */
    while (pos < lines_len) {
        JMDLine *line = &lines->items[pos];

        /* Blank line: peek ahead */
        if (line->heading_depth == -1) {
            Py_ssize_t peek = pos + 1;
            while (peek < lines_len && lines->items[peek].heading_depth == -1)
                peek++;
            if (peek < lines_len) {
                JMDLine *nxt = &lines->items[peek];
                if (nxt->heading_depth == child_depth) {
                    pos++;
                    st->pos = pos;
                    continue;
                }
            }
            break;
        }

        /* Heading at array_depth or shallower */
        if (line->heading_depth > 0 && line->heading_depth <= array_depth)
            break;

        /* Heading at child_depth */
        if (line->heading_depth == child_depth) {
            /* Item-start headings: stop */
            if ((line->content_len == 1 && line->content[0] == '-')
                || (line->content_len == 2 && line->content[0] == '['
                    && line->content[1] == ']')
                || (line->content_len > 1 && line->content[0] == '-'
                    && line->content[1] == ' '))
            {
                break;
            }
            st->pos = pos;
            if (parse_heading_into(st, obj, kinds, child_depth) < 0) {
                Py_DECREF(kinds);
                Py_DECREF(obj);
                return NULL;
            }
            pos = st->pos;
            continue;
        }

        /* Heading deeper than child: stop */
        if (line->heading_depth > child_depth)
            break;

        /* Thematic breaks are inert decoration inside an array item. */
        if (is_thematic_break(line)) {
            pos++;
            st->pos = pos;
            continue;
        }

        /* Non-heading (hd == 0) */
        if (line->heading_depth == 0) {
            const char *content = line->content;
            Py_ssize_t content_len = line->content_len;

            /* Next item marker: stop */
            if (is_dash_item(content, content_len))
                break;

            /* Bare field: key: value — use memchr */
            Py_ssize_t colon_pos = find_colon_space(content, content_len);
            if (colon_pos >= 0) {
                PyObject *k = parse_key(content, colon_pos);
                PyObject *v = parse_scalar(content + colon_pos + 2,
                                            content_len - colon_pos - 2);
                if (!k || !v) {
                    Py_XDECREF(k);
                    Py_XDECREF(v);
                    Py_DECREF(kinds);
                    Py_DECREF(obj);
                    return NULL;
                }
                if (set_scalar_field(obj, kinds, k, v, line->number, 0) < 0) {
                    Py_DECREF(k);
                    Py_DECREF(v);
                    Py_DECREF(kinds);
                    Py_DECREF(obj);
                    return NULL;
                }
                Py_DECREF(k);
                Py_DECREF(v);
                pos++;
                continue;
            }
        }

        break;
    }

    st->pos = pos;
    Py_DECREF(kinds);
    return obj;
}
