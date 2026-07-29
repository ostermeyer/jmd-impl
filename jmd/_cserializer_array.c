/*
 * _cserializer_array.c — array and record serialization.
 *
 * Copyright (c) 2026 Andreas Ostermeyer <andreas@ostermeyer.de>
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0.
 * See LICENSE at the repository root for the full license text.
 */

#include "_cserializer_internal.h"

/* ------------------------------------------------------------------ */
/* Array serialization                                                 */
/* ------------------------------------------------------------------ */

/* Classify array: 0=mixed, 1=all scalars, 2=all dicts, 3=all lists */
static int
classify_array(PyObject *list)
{
    Py_ssize_t n = PyList_GET_SIZE(list);
    if (n == 0) return 1;  /* empty -> treat as scalars */

    int all_dicts = 1, all_lists = 1, all_scalars = 1;
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *item = PyList_GET_ITEM(list, i);
        if (PyDict_Check(item)) { all_lists = 0; all_scalars = 0; }
        else if (PyList_Check(item)) { all_dicts = 0; all_scalars = 0; }
        else { all_dicts = 0; all_lists = 0; }
    }
    if (all_scalars) return 1;
    if (all_dicts) return 2;
    if (all_lists) return 3;
    return 0;
}

int
ser_write_array_items(OutBuf *ob, PyObject *list, int depth)
{
    Py_ssize_t n = PyList_GET_SIZE(list);
    if (n == 0) return 1;

    int kind = classify_array(list);

    if (kind == 3) {
        /* All lists -> sub-headings */
        for (Py_ssize_t i = 0; i < n; i++) {
            if (!outbuf_putc(ob, '\n')) return 0;
            if (!outbuf_heading(ob, depth + 1)) return 0;
            if (!outbuf_append(ob, "[]", 2)) return 0;
            if (!ser_write_array_items(ob, PyList_GET_ITEM(list, i), depth + 1))
                return 0;
        }
        return 1;
    }

    if (kind == 1) {
        /* All scalars -> "- value" lines */
        for (Py_ssize_t i = 0; i < n; i++) {
            if (!outbuf_append(ob, "\n- ", 3)) return 0;
            if (!ser_write_array_scalar(ob, PyList_GET_ITEM(list, i))) return 0;
        }
        return 1;
    }

    if (kind == 2) {
        /* All dicts -> records. Each record is bare `- field` (+ indented
         * continuation), nested fields as deeper headings. After a record
         * that opened a sub-structure, if more records follow, emit a
         * level-pop (`#`x depth, an anonymous heading at the array's own
         * depth) so the next bare `-` is read into THIS array (sec 8.6). */
        for (Py_ssize_t i = 0; i < n; i++) {
            PyObject *item = PyList_GET_ITEM(list, i);
            PyObject *ikey, *ivalue;
            Py_ssize_t ipos = 0;
            int first_scalar = 1;
            int wrote_nested = 0;

            /* First pass: scalar fields (inline continuation) */
            ipos = 0;
            while (PyDict_Next(item, &ipos, &ikey, &ivalue)) {
                if (PyDict_Check(ivalue) || PyList_Check(ivalue))
                    continue;

                Py_ssize_t klen;
                const char *ks = PyUnicode_AsUTF8AndSize(ikey, &klen);
                if (!ks) return 0;

                if (first_scalar) {
                    if (!outbuf_append(ob, "\n- ", 3)) return 0;
                    if (!ser_write_key(ob, ks, klen)) return 0;
                    if (!outbuf_append(ob, ": ", 2)) return 0;
                    if (!ser_write_scalar(ob, ivalue)) return 0;
                    first_scalar = 0;
                } else {
                    if (!outbuf_append(ob, "\n  ", 3)) return 0;
                    if (!ser_write_key(ob, ks, klen)) return 0;
                    if (!outbuf_append(ob, ": ", 2)) return 0;
                    if (!ser_write_scalar(ob, ivalue)) return 0;
                }
            }

            if (first_scalar) {
                /* No scalar fields at all -> bare "-" */
                if (!outbuf_append(ob, "\n-", 2)) return 0;
            }

            /* Second pass: nested fields */
            ipos = 0;
            while (PyDict_Next(item, &ipos, &ikey, &ivalue)) {
                if (!PyDict_Check(ivalue) && !PyList_Check(ivalue))
                    continue;

                Py_ssize_t klen;
                const char *ks = PyUnicode_AsUTF8AndSize(ikey, &klen);
                if (!ks) return 0;

                if (PyDict_Check(ivalue)) {
                    if (!outbuf_putc(ob, '\n')) return 0;
                    if (!outbuf_putc(ob, '\n')) return 0;
                    if (!outbuf_heading(ob, depth + 1)) return 0;
                    if (!ser_write_key(ob, ks, klen)) return 0;
                    if (!ser_write_object_fields(ob, ivalue, depth + 1))
                        return 0;
                } else {
                    if (!outbuf_putc(ob, '\n')) return 0;
                    if (!outbuf_putc(ob, '\n')) return 0;
                    if (!outbuf_heading(ob, depth + 1)) return 0;
                    if (!ser_write_key(ob, ks, klen)) return 0;
                    if (!outbuf_append(ob, "[]", 2)) return 0;
                    if (!ser_write_array_items(ob, ivalue, depth + 1))
                        return 0;
                }
                wrote_nested = 1;
            }

            /* Level-pop: this record opened a sub-structure and more
             * records follow -> return to the array's depth. The last
             * record needs no pop (end-of-scope closes it). */
            if (wrote_nested && i < n - 1) {
                if (!outbuf_putc(ob, '\n')) return 0;
                for (int d = 0; d < depth; d++) {
                    if (!outbuf_putc(ob, '#')) return 0;
                }
            }
        }
        return 1;
    }

    /* kind == 0: heterogeneous array.
     *
     * After any item that opens a sub-scope (a sub-array, or a dict with
     * nested fields), the NEXT item needs an explicit depth-qualified
     * heading `## - ...` (§8.6a same-depth form) so the parser pops out
     * of the inner scope and attaches the item to *this* array. The
     * qualifier uses the array's own scope depth — for items[] opened
     * by `## items[]` at depth 2, items take a `## -` prefix.
     */
    int needs_qualifier = 0;
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *item = PyList_GET_ITEM(list, i);
        if (PyDict_Check(item)) {
            PyObject *ikey, *ivalue;
            Py_ssize_t ipos = 0;
            int first_scalar = 1;
            int has_nested = 0;

            /* Emit scalar fields (with optional qualifier prefix on first) */
            ipos = 0;
            while (PyDict_Next(item, &ipos, &ikey, &ivalue)) {
                if (PyDict_Check(ivalue) || PyList_Check(ivalue)) {
                    has_nested = 1;
                    continue;
                }
                Py_ssize_t klen;
                const char *ks = PyUnicode_AsUTF8AndSize(ikey, &klen);
                if (!ks) return 0;
                if (first_scalar) {
                    if (!outbuf_putc(ob, '\n')) return 0;
                    if (needs_qualifier) {
                        if (!outbuf_heading(ob, depth)) return 0;
                    }
                    if (!outbuf_append(ob, "- ", 2)) return 0;
                    first_scalar = 0;
                } else {
                    if (!outbuf_append(ob, "\n  ", 3)) return 0;
                }
                if (!ser_write_key(ob, ks, klen)) return 0;
                if (!outbuf_append(ob, ": ", 2)) return 0;
                if (!ser_write_scalar(ob, ivalue)) return 0;
            }
            if (first_scalar) {
                /* No scalar fields — emit bare bullet */
                if (!outbuf_putc(ob, '\n')) return 0;
                if (needs_qualifier) {
                    if (!outbuf_heading(ob, depth)) return 0;
                }
                if (!outbuf_putc(ob, '-')) return 0;
            }
            /* Nested fields */
            ipos = 0;
            while (PyDict_Next(item, &ipos, &ikey, &ivalue)) {
                if (!PyDict_Check(ivalue) && !PyList_Check(ivalue))
                    continue;
                Py_ssize_t klen;
                const char *ks = PyUnicode_AsUTF8AndSize(ikey, &klen);
                if (!ks) return 0;
                if (PyDict_Check(ivalue)) {
                    if (!outbuf_putc(ob, '\n')) return 0;
                    if (!outbuf_putc(ob, '\n')) return 0;
                    if (!outbuf_heading(ob, depth + 1)) return 0;
                    if (!ser_write_key(ob, ks, klen)) return 0;
                    if (!ser_write_object_fields(ob, ivalue, depth + 1))
                        return 0;
                } else {
                    if (!outbuf_putc(ob, '\n')) return 0;
                    if (!outbuf_putc(ob, '\n')) return 0;
                    if (!outbuf_heading(ob, depth + 1)) return 0;
                    if (!ser_write_key(ob, ks, klen)) return 0;
                    if (!outbuf_append(ob, "[]", 2)) return 0;
                    if (!ser_write_array_items(ob, ivalue, depth + 1))
                        return 0;
                }
            }
            needs_qualifier = has_nested;
        }
        else if (PyList_Check(item)) {
            if (!outbuf_putc(ob, '\n')) return 0;
            if (!outbuf_heading(ob, depth + 1)) return 0;
            if (!outbuf_append(ob, "[]", 2)) return 0;
            if (!ser_write_array_items(ob, item, depth + 1)) return 0;
            needs_qualifier = 1;
        }
        else {
            if (!outbuf_putc(ob, '\n')) return 0;
            if (needs_qualifier) {
                if (!outbuf_heading(ob, depth)) return 0;
            }
            if (!outbuf_append(ob, "- ", 2)) return 0;
            if (!ser_write_array_scalar(ob, item)) return 0;
            needs_qualifier = 0;
        }
    }
    return 1;
}
