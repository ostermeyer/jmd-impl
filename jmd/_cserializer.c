/*
 * _cserializer.c — C-accelerated JMD serializer (CPython extension).
 *
 * Copyright (c) 2026 Andreas Ostermeyer <andreas@ostermeyer.de>
 * Licensed under AGPL-3.0 — see LICENSE-CODE for details.
 *
 * Provides a drop-in replacement for JMDSerializer().serialize() that
 * operates directly on Python objects with no intermediate data structure.
 *
 * Public API:
 *     from jmd._cserializer import serialize
 *     text = serialize(data, "Label")      # returns JMD string
 *
 * See also: _cparser.c for the C-accelerated parser.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <string.h>
#include <stdlib.h>
#include <errno.h>

/* ------------------------------------------------------------------ */
/* Output buffer                                                       */
/* ------------------------------------------------------------------ */

typedef struct {
    char       *buf;
    Py_ssize_t  len;
    Py_ssize_t  cap;
} OutBuf;

static int
outbuf_init(OutBuf *ob)
{
    ob->cap = 4096;
    ob->len = 0;
    ob->buf = (char *)PyMem_Malloc(ob->cap);
    return ob->buf != NULL;
}

static void
outbuf_free(OutBuf *ob)
{
    if (ob->buf) PyMem_Free(ob->buf);
    ob->buf = NULL;
}

static int
outbuf_grow(OutBuf *ob, Py_ssize_t need)
{
    Py_ssize_t new_cap = ob->cap;
    while (new_cap - ob->len < need)
        new_cap *= 2;
    if (new_cap != ob->cap) {
        char *tmp = (char *)PyMem_Realloc(ob->buf, new_cap);
        if (!tmp) { PyErr_NoMemory(); return 0; }
        ob->buf = tmp;
        ob->cap = new_cap;
    }
    return 1;
}

static int
outbuf_append(OutBuf *ob, const char *s, Py_ssize_t slen)
{
    if (!outbuf_grow(ob, slen)) return 0;
    memcpy(ob->buf + ob->len, s, (size_t)slen);
    ob->len += slen;
    return 1;
}

static int
outbuf_putc(OutBuf *ob, char c)
{
    if (!outbuf_grow(ob, 1)) return 0;
    ob->buf[ob->len++] = c;
    return 1;
}

/* Append '#' repeated `depth` times followed by a space. */
static int
outbuf_heading(OutBuf *ob, int depth)
{
    if (!outbuf_grow(ob, depth + 1)) return 0;
    memset(ob->buf + ob->len, '#', (size_t)depth);
    ob->len += depth;
    ob->buf[ob->len++] = ' ';
    return 1;
}

/* ------------------------------------------------------------------ */
/* Scalar serialization                                                */
/* ------------------------------------------------------------------ */

/* Check if a bare string needs quoting. */
static int
ser_needs_quote(const char *s, Py_ssize_t len)
{
    if (len == 0) return 1;
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
    /* Contains newline or tab */
    if (memchr(s, '\n', (size_t)len) || memchr(s, '\t', (size_t)len))
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
static int
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
    const char *rstr = PyUnicode_AsUTF8(result);
    if (!rstr) { Py_DECREF(result); return 0; }
    Py_ssize_t rlen = (Py_ssize_t)strlen(rstr);
    int ok = outbuf_append(ob, rstr, rlen);
    Py_DECREF(result);
    return ok;
}

/* Write a key (bare or quoted). */
static int
ser_write_key(OutBuf *ob, const char *s, Py_ssize_t len)
{
    if (ser_key_is_bare(s, len))
        return outbuf_append(ob, s, len);
    return ser_write_quoted(ob, s, len);
}

/* Write a scalar value. Returns 1 on success, 0 on error. */
static int
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
            const char *cs = PyUnicode_AsUTF8(s);
            Py_ssize_t slen = cs ? (Py_ssize_t)strlen(cs) : 0;
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
        const char *cs = PyUnicode_AsUTF8(s);
        Py_ssize_t slen = cs ? (Py_ssize_t)strlen(cs) : 0;
        int ok = cs ? outbuf_append(ob, cs, slen) : 0;
        Py_DECREF(s);
        return ok;
    }

    if (PyUnicode_Check(value)) {
        const char *s = PyUnicode_AsUTF8(value);
        if (!s) return 0;
        Py_ssize_t slen = (Py_ssize_t)strlen(s);
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
        const char *hstr = PyUnicode_AsUTF8(hexdig);
        if (!hstr) { Py_DECREF(hexdig); return 0; }
        int ok = outbuf_append(ob, "sha256:", 7);
        if (ok) ok = outbuf_append(ob, hstr, (Py_ssize_t)strlen(hstr));
        Py_DECREF(hexdig);
        return ok;
    }

    /* Fallback: str(value) */
    PyObject *s = PyObject_Str(value);
    if (!s) return 0;
    const char *cs = PyUnicode_AsUTF8(s);
    Py_ssize_t cslen = cs ? (Py_ssize_t)strlen(cs) : 0;
    int ok = cs ? outbuf_append(ob, cs, cslen) : 0;
    Py_DECREF(s);
    return ok;
}

/* ------------------------------------------------------------------ */
/* Multiline strings -> blockquote                                     */
/* ------------------------------------------------------------------ */

static int
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

/* ------------------------------------------------------------------ */
/* Forward declarations for recursive serialization                    */
/* ------------------------------------------------------------------ */

static int ser_write_object_fields(OutBuf *ob, PyObject *dict, int depth);
static int ser_write_array_items(OutBuf *ob, PyObject *list, int depth);

/* ------------------------------------------------------------------ */
/* Object serialization                                                */
/* ------------------------------------------------------------------ */

static int
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

/* ------------------------------------------------------------------ */
/* Array serialization                                                 */
/* ------------------------------------------------------------------ */

/* Check if any value in a dict is a dict or list. */
static int
has_nested_values(PyObject *dict)
{
    PyObject *key, *value;
    Py_ssize_t pos = 0;
    while (PyDict_Next(dict, &pos, &key, &value)) {
        if (PyDict_Check(value) || PyList_Check(value))
            return 1;
    }
    return 0;
}

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

static int
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
            if (!ser_write_scalar(ob, PyList_GET_ITEM(list, i))) return 0;
        }
        return 1;
    }

    if (kind == 2) {
        /* All dicts -> indentation continuation items */
        /* Check if any item has nested fields (for thematic breaks) */
        int any_nested = 0;
        for (Py_ssize_t i = 0; i < n; i++) {
            if (has_nested_values(PyList_GET_ITEM(list, i))) {
                any_nested = 1;
                break;
            }
        }

        for (Py_ssize_t i = 0; i < n; i++) {
            PyObject *item = PyList_GET_ITEM(list, i);
            PyObject *ikey, *ivalue;
            Py_ssize_t ipos = 0;
            int first_scalar = 1;
            int wrote_thematic = 0;

            /* Thematic break between items if any has nested fields */
            if (i > 0 && any_nested) {
                if (!outbuf_append(ob, "\n\n---\n", 5)) return 0;
                wrote_thematic = 1;
            }

            /* First pass: scalar fields (inline continuation) */
            ipos = 0;
            while (PyDict_Next(item, &ipos, &ikey, &ivalue)) {
                if (PyDict_Check(ivalue) || PyList_Check(ivalue))
                    continue;

                const char *ks = PyUnicode_AsUTF8(ikey);
                if (!ks) return 0;
                Py_ssize_t klen = (Py_ssize_t)strlen(ks);

                if (first_scalar) {
                    if (!wrote_thematic && i > 0) {
                        /* No thematic break needed, just newline */
                    }
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

                const char *ks = PyUnicode_AsUTF8(ikey);
                if (!ks) return 0;
                Py_ssize_t klen = (Py_ssize_t)strlen(ks);

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
        }
        return 1;
    }

    /* kind == 0: heterogeneous array -- treat like all-dicts but handle scalars */
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *item = PyList_GET_ITEM(list, i);
        if (PyDict_Check(item)) {
            /* Emit as indentation continuation item */
            PyObject *ikey, *ivalue;
            Py_ssize_t ipos = 0;
            int first_scalar = 1;

            ipos = 0;
            while (PyDict_Next(item, &ipos, &ikey, &ivalue)) {
                if (PyDict_Check(ivalue) || PyList_Check(ivalue))
                    continue;
                const char *ks = PyUnicode_AsUTF8(ikey);
                if (!ks) return 0;
                Py_ssize_t klen = (Py_ssize_t)strlen(ks);
                if (first_scalar) {
                    if (!outbuf_append(ob, "\n- ", 3)) return 0;
                    first_scalar = 0;
                } else {
                    if (!outbuf_append(ob, "\n  ", 3)) return 0;
                }
                if (!ser_write_key(ob, ks, klen)) return 0;
                if (!outbuf_append(ob, ": ", 2)) return 0;
                if (!ser_write_scalar(ob, ivalue)) return 0;
            }
            if (first_scalar) {
                if (!outbuf_append(ob, "\n-", 2)) return 0;
            }
            /* Nested fields */
            ipos = 0;
            while (PyDict_Next(item, &ipos, &ikey, &ivalue)) {
                if (!PyDict_Check(ivalue) && !PyList_Check(ivalue))
                    continue;
                const char *ks = PyUnicode_AsUTF8(ikey);
                if (!ks) return 0;
                Py_ssize_t klen = (Py_ssize_t)strlen(ks);
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
        }
        else if (PyList_Check(item)) {
            if (!outbuf_putc(ob, '\n')) return 0;
            if (!outbuf_heading(ob, depth + 1)) return 0;
            if (!outbuf_append(ob, "[]", 2)) return 0;
            if (!ser_write_array_items(ob, item, depth + 1)) return 0;
        }
        else {
            if (!outbuf_append(ob, "\n- ", 3)) return 0;
            if (!ser_write_scalar(ob, item)) return 0;
        }
    }
    return 1;
}

/* ------------------------------------------------------------------ */
/* Top-level serialize function                                        */
/* ------------------------------------------------------------------ */

static PyObject *
jmd_serialize(PyObject *self, PyObject *args)
{
    (void)self;
    PyObject *data;
    const char *label = "Document";

    if (!PyArg_ParseTuple(args, "O|s", &data, &label))
        return NULL;

    OutBuf ob;
    if (!outbuf_init(&ob)) {
        PyErr_NoMemory();
        return NULL;
    }

    if (PyList_Check(data)) {
        if (!outbuf_append(&ob, "# []", 4)) { outbuf_free(&ob); return NULL; }
        if (!ser_write_array_items(&ob, data, 1)) { outbuf_free(&ob); return NULL; }
    }
    else if (PyDict_Check(data)) {
        if (!outbuf_append(&ob, "# ", 2)) { outbuf_free(&ob); return NULL; }
        if (!outbuf_append(&ob, label, (Py_ssize_t)strlen(label)))
            { outbuf_free(&ob); return NULL; }
        if (!ser_write_object_fields(&ob, data, 1))
            { outbuf_free(&ob); return NULL; }
    }
    else {
        outbuf_free(&ob);
        PyErr_SetString(PyExc_TypeError, "serialize() expects a dict or list");
        return NULL;
    }

    PyObject *result = PyUnicode_FromStringAndSize(ob.buf, ob.len);
    outbuf_free(&ob);
    return result;
}

/* ------------------------------------------------------------------ */
/* Module definition                                                   */
/* ------------------------------------------------------------------ */

static PyMethodDef cserializer_methods[] = {
    {"serialize", jmd_serialize, METH_VARARGS,
     "Serialize a Python dict or list to a JMD document string."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef cserializer_module = {
    PyModuleDef_HEAD_INIT,
    "jmd._cserializer",
    "C-accelerated JMD serializer.",
    -1,
    cserializer_methods,
    NULL, NULL, NULL, NULL
};

PyMODINIT_FUNC
PyInit__cserializer(void)
{
    return PyModule_Create(&cserializer_module);
}
