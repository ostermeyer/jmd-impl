/*
 * _cserializer_scalar.c — keys, scalar values, and multiline strings.
 *
 * Copyright (c) 2026 Andreas Ostermeyer <andreas@ostermeyer.de>
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0.
 * See LICENSE at the repository root for the full license text.
 */

#include "_cserializer_internal.h"

/* ------------------------------------------------------------------ */
/* Scalar serialization                                                */
/* ------------------------------------------------------------------ */

/* Check if a bare string needs quoting. */
int
ser_needs_quote(const char *s, Py_ssize_t len)
{
    if (len == 0) return 1;
    /* Bare-value normalization strips significant boundary whitespace. */
    if (s[0] == ' ' || s[len - 1] == ' ')
        return 1;
    /* Structural literals */
    if (len == 4 && (memcmp(s, "null", 4) == 0 || memcmp(s, "true", 4) == 0))
        return 1;
    if (len == 5 && memcmp(s, "false", 5) == 0)
        return 1;
    /* Lone dash */
    if (len == 1 && s[0] == '-')
        return 1;
    /* Starts with structural prefix: "# " or "- " */
    if (len >= 2 && ((s[0] == '#' && s[1] == ' ') || (s[0] == '-' && s[1] == ' ')))
        return 1;
    /* Starts with '"' */
    if (s[0] == '"')
        return 1;
    /* Contains newline, carriage return, or tab */
    if (memchr(s, '\n', (size_t)len)
        || memchr(s, '\r', (size_t)len)
        || memchr(s, '\t', (size_t)len))
        return 1;
    /* Looks like a number? Try parsing as double. */
    {
        char tmp[64];
        if (len < 63) {
            memcpy(tmp, s, (size_t)len);
            tmp[len] = '\0';
            char *end;
            errno = 0;
            strtod(tmp, &end);
            if (end == tmp + len && errno == 0)
                return 1;
        }
    }
    return 0;
}

/* Check if a key can be bare (only [a-zA-Z0-9_-]). */
static int
ser_key_is_bare(const char *s, Py_ssize_t len)
{
    for (Py_ssize_t i = 0; i < len; i++) {
        char c = s[i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '_' || c == '-'))
            return 0;
    }
    return len > 0;
}

/* Write a JSON-quoted string (for keys or values that need quoting).
 * Uses a fast path for ASCII with no special chars, falls back to
 * PyObject json.dumps for complex cases. */
int
ser_write_quoted(OutBuf *ob, const char *s, Py_ssize_t len)
{
    /* Fast path: check if simple ASCII (no escapes needed) */
    int simple = 1;
    for (Py_ssize_t i = 0; i < len; i++) {
        unsigned char c = (unsigned char)s[i];
        if (c < 0x20 || c == '"' || c == '\\' || c >= 0x80) {
            simple = 0;
            break;
        }
    }
    if (simple) {
        if (!outbuf_putc(ob, '"')) return 0;
        if (!outbuf_append(ob, s, len)) return 0;
        return outbuf_putc(ob, '"');
    }
    /* Fallback: use json.dumps */
    PyObject *pystr = PyUnicode_FromStringAndSize(s, len);
    if (!pystr) return 0;
    PyObject *json_mod = PyImport_ImportModule("json");
    if (!json_mod) { Py_DECREF(pystr); return 0; }
    PyObject *result = PyObject_CallMethod(json_mod, "dumps", "O", pystr);
    Py_DECREF(json_mod);
    Py_DECREF(pystr);
    if (!result) return 0;
    Py_ssize_t rlen;
    const char *rstr = PyUnicode_AsUTF8AndSize(result, &rlen);
    if (!rstr) { Py_DECREF(result); return 0; }
    int ok = outbuf_append(ob, rstr, rlen);
    Py_DECREF(result);
    return ok;
}

/* Write a key (bare or quoted). */
int
ser_write_key(OutBuf *ob, const char *s, Py_ssize_t len)
{
    if (ser_key_is_bare(s, len))
        return outbuf_append(ob, s, len);
    return ser_write_quoted(ob, s, len);
}

/* Write a scalar value. Returns 1 on success, 0 on error. */
int
ser_write_scalar(OutBuf *ob, PyObject *value)
{
    if (value == Py_None)
        return outbuf_append(ob, "null", 4);

    if (value == Py_True)
        return outbuf_append(ob, "true", 4);

    if (value == Py_False)
        return outbuf_append(ob, "false", 5);

    if (PyLong_Check(value)) {
        /* Fast integer formatting */
        long v = PyLong_AsLong(value);
        if (v == -1 && PyErr_Occurred()) {
            /* Big int — fall back to str() */
            PyErr_Clear();
            PyObject *s = PyObject_Str(value);
            if (!s) return 0;
            Py_ssize_t slen;
            const char *cs = PyUnicode_AsUTF8AndSize(s, &slen);
            int ok = cs ? outbuf_append(ob, cs, slen) : 0;
            Py_DECREF(s);
            return ok;
        }
        char tmp[24];
        int n = snprintf(tmp, sizeof(tmp), "%ld", v);
        return outbuf_append(ob, tmp, n);
    }

    if (PyFloat_Check(value)) {
        /* Use Python's repr() for floats — it produces the shortest
         * representation that roundtrips exactly (e.g. 249.95 not
         * 249.94999999999999).  This matches Python's str(float). */
        PyObject *s = PyObject_Repr(value);
        if (!s) return 0;
        Py_ssize_t slen;
        const char *cs = PyUnicode_AsUTF8AndSize(s, &slen);
        int ok = cs ? outbuf_append(ob, cs, slen) : 0;
        Py_DECREF(s);
        return ok;
    }

    if (PyUnicode_Check(value)) {
        Py_ssize_t slen;
        const char *s = PyUnicode_AsUTF8AndSize(value, &slen);
        if (!s) return 0;
        /* Multiline strings are handled by caller (blockquote mode).
         * Here we only handle single-line strings. */
        if (ser_needs_quote(s, slen))
            return ser_write_quoted(ob, s, slen);
        return outbuf_append(ob, s, slen);
    }

    if (PyBytes_Check(value) || PyByteArray_Check(value)) {
        /* binary → sha256:<hex> via Python hashlib */
        PyObject *hashlib = PyImport_ImportModule("hashlib");
        if (!hashlib) return 0;
        PyObject *bytes_obj = PyBytes_Check(value)
            ? (Py_INCREF(value), value)
            : PyBytes_FromStringAndSize(PyByteArray_AS_STRING(value),
                                        PyByteArray_GET_SIZE(value));
        if (!bytes_obj) { Py_DECREF(hashlib); return 0; }
        PyObject *digest = PyObject_CallMethod(hashlib, "sha256", "O", bytes_obj);
        Py_DECREF(hashlib);
        Py_DECREF(bytes_obj);
        if (!digest) return 0;
        PyObject *hexdig = PyObject_CallMethod(digest, "hexdigest", NULL);
        Py_DECREF(digest);
        if (!hexdig) return 0;
        Py_ssize_t hlen;
        const char *hstr = PyUnicode_AsUTF8AndSize(hexdig, &hlen);
        if (!hstr) { Py_DECREF(hexdig); return 0; }
        int ok = outbuf_append(ob, "sha256:", 7);
        if (ok) ok = outbuf_append(ob, hstr, hlen);
        Py_DECREF(hexdig);
        return ok;
    }

    /* Fallback: str(value) */
    PyObject *s = PyObject_Str(value);
    if (!s) return 0;
    Py_ssize_t cslen;
    const char *cs = PyUnicode_AsUTF8AndSize(s, &cslen);
    int ok = cs ? outbuf_append(ob, cs, cslen) : 0;
    Py_DECREF(s);
    return ok;
}

/*
 * Write a scalar in array-item position without the object-item colon trap.
 *
 * Inputs: ob is a borrowed output buffer; value is a borrowed Python object.
 * Output: 1 on success, 0 with a Python exception on failure.  No reference
 * is retained after return.
 */
int
ser_write_array_scalar(OutBuf *ob, PyObject *value)
{
    if (PyUnicode_Check(value)) {
        Py_ssize_t len;
        const char *s = PyUnicode_AsUTF8AndSize(value, &len);
        if (!s)
            return 0;

        for (Py_ssize_t i = 0; i + 1 < len; i++) {
            if (s[i] == ':' && s[i + 1] == ' ')
                return ser_write_quoted(ob, s, len);
        }
    }

    return ser_write_scalar(ob, value);
}

/* ------------------------------------------------------------------ */
/* Multiline strings -> blockquote                                     */
/* ------------------------------------------------------------------ */

int
ser_write_multiline(OutBuf *ob, const char *s, Py_ssize_t len)
{
    const char *end = s + len;
    while (s < end) {
        const char *nl = (const char *)memchr(s, '\n', (size_t)(end - s));
        Py_ssize_t line_len = nl ? (nl - s) : (end - s);
        if (!outbuf_putc(ob, '\n')) return 0;
        if (line_len == 0) {
            if (!outbuf_putc(ob, '>')) return 0;
        } else {
            if (!outbuf_append(ob, "> ", 2)) return 0;
            if (!outbuf_append(ob, s, line_len)) return 0;
        }
        s = nl ? nl + 1 : end;
    }
    return 1;
}
