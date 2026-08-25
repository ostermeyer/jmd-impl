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
    int nested;

    /*
     * Nested headings remain open in JMD. Emit all scalar siblings before
     * nested objects and arrays, so mapping insertion order never changes
     * a scalar field into a heading.
     */
    for (nested = 0; nested < 2; nested++) {
        PyObject *key, *value;
        Py_ssize_t pos = 0;

        while (PyDict_Next(dict, &pos, &key, &value)) {
            int is_nested = PyDict_Check(value) || PyList_Check(value);
            Py_ssize_t klen;
            const char *kstr;

            if (is_nested != nested) continue;
            kstr = PyUnicode_AsUTF8AndSize(key, &klen);
            if (!kstr) return 0;

            if (PyDict_Check(value)) {
                if (!outbuf_putc(ob, '\n')) return 0;
                if (!outbuf_putc(ob, '\n')) return 0;
                if (!outbuf_heading(ob, depth + 1)) return 0;
                if (!ser_write_key(ob, kstr, klen)) return 0;
                if (!ser_write_object_fields(ob, value, depth + 1)) return 0;
            }
            else if (PyList_Check(value)) {
                if (!outbuf_putc(ob, '\n')) return 0;
                if (!outbuf_putc(ob, '\n')) return 0;
                if (!outbuf_heading(ob, depth + 1)) return 0;
                if (!ser_write_key(ob, kstr, klen)) return 0;
                if (!outbuf_append(ob, "[]", 2)) return 0;
                if (!ser_write_array_items(ob, value, depth + 1)) return 0;
            }
            else if (PyUnicode_Check(value)) {
                Py_ssize_t vlen;
                const char *vs = PyUnicode_AsUTF8AndSize(value, &vlen);

                if (!vs) return 0;
                if (!outbuf_putc(ob, '\n')) return 0;
                if (!ser_write_key(ob, kstr, klen)) return 0;
                if (memchr(vs, '\n', (size_t)vlen)) {
                    if (!outbuf_putc(ob, ':')) return 0;
                    if (!ser_write_multiline(ob, vs, vlen)) return 0;
                } else {
                    if (!outbuf_append(ob, ": ", 2)) return 0;
                    if (ser_needs_quote(vs, vlen)) {
                        if (!ser_write_quoted(ob, vs, vlen)) return 0;
                    } else if (!outbuf_append(ob, vs, vlen)) {
                        return 0;
                    }
                }
            }
            else {
                if (!outbuf_putc(ob, '\n')) return 0;
                if (!ser_write_key(ob, kstr, klen)) return 0;
                if (!outbuf_append(ob, ": ", 2)) return 0;
                if (!ser_write_scalar(ob, value)) return 0;
            }
        }
    }
    return 1;
}
