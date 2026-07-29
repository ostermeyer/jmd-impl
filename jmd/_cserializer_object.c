/*
 * _cserializer_object.c — object-field serialization.
 *
 * Copyright (c) 2026 Andreas Ostermeyer <andreas@ostermeyer.de>
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0.
 * See LICENSE at the repository root for the full license text.
 */

#include "_cserializer_internal.h"

/* ------------------------------------------------------------------ */
/* Object serialization                                                */
/* ------------------------------------------------------------------ */

int
ser_write_object_fields(OutBuf *ob, PyObject *dict, int depth)
{
    PyObject *key, *value;
    Py_ssize_t pos = 0;
    int needs_heading = 0;

    while (PyDict_Next(dict, &pos, &key, &value)) {
        const char *kstr = PyUnicode_AsUTF8(key);
        if (!kstr) return 0;
        Py_ssize_t klen = (Py_ssize_t)strlen(kstr);

        if (PyDict_Check(value)) {
            /* Nested object -> heading */
            if (!outbuf_putc(ob, '\n')) return 0;
            if (!outbuf_putc(ob, '\n')) return 0;
            if (!outbuf_heading(ob, depth + 1)) return 0;
            if (!ser_write_key(ob, kstr, klen)) return 0;
            if (!ser_write_object_fields(ob, value, depth + 1)) return 0;
            needs_heading = 1;
        }
        else if (PyList_Check(value)) {
            /* Nested array -> heading with [] */
            if (!outbuf_putc(ob, '\n')) return 0;
            if (!outbuf_putc(ob, '\n')) return 0;
            if (!outbuf_heading(ob, depth + 1)) return 0;
            if (!ser_write_key(ob, kstr, klen)) return 0;
            if (!outbuf_append(ob, "[]", 2)) return 0;
            if (!ser_write_array_items(ob, value, depth + 1)) return 0;
            needs_heading = 1;
        }
        else if (PyUnicode_Check(value)) {
            const char *vs = PyUnicode_AsUTF8(value);
            if (!vs) return 0;
            Py_ssize_t vlen = (Py_ssize_t)strlen(vs);
            if (memchr(vs, '\n', (size_t)vlen)) {
                /* Multiline -> blockquote */
                if (!outbuf_putc(ob, '\n')) return 0;
                if (needs_heading) {
                    if (!outbuf_heading(ob, depth + 1)) return 0;
                    if (!ser_write_key(ob, kstr, klen)) return 0;
                    if (!outbuf_putc(ob, ':')) return 0;
                } else {
                    if (!ser_write_key(ob, kstr, klen)) return 0;
                    if (!outbuf_putc(ob, ':')) return 0;
                }
                if (!ser_write_multiline(ob, vs, vlen)) return 0;
                needs_heading = 1;
            } else {
                /* Single-line string */
                if (!outbuf_putc(ob, '\n')) return 0;
                if (needs_heading) {
                    if (!outbuf_heading(ob, depth + 1)) return 0;
                }
                if (!ser_write_key(ob, kstr, klen)) return 0;
                if (!outbuf_append(ob, ": ", 2)) return 0;
                if (ser_needs_quote(vs, vlen)) {
                    if (!ser_write_quoted(ob, vs, vlen)) return 0;
                } else {
                    if (!outbuf_append(ob, vs, vlen)) return 0;
                }
            }
        }
        else {
            /* Scalar (non-string) */
            if (!outbuf_putc(ob, '\n')) return 0;
            if (needs_heading) {
                if (!outbuf_heading(ob, depth + 1)) return 0;
            }
            if (!ser_write_key(ob, kstr, klen)) return 0;
            if (!outbuf_append(ob, ": ", 2)) return 0;
            if (!ser_write_scalar(ob, value)) return 0;
        }
    }
    return 1;
}
