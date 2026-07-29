/*
 * _cparser_runtime.c — runtime state and key ownership.
 *
 * Copyright (c) 2026 Andreas Ostermeyer <andreas@ostermeyer.de>
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0.
 * See LICENSE at the repository root for the full license text.
 */

#include "_cparser_internal.h"

/* ------------------------------------------------------------------ */
/* Key interning cache                                                  */
/* ------------------------------------------------------------------ */

#define KEY_CACHE_SIZE 128   /* must be power of 2 */
#define KEY_CACHE_MASK (KEY_CACHE_SIZE - 1)

typedef struct {
    Py_ssize_t  len;
    PyObject   *pyobj;        /* owned reference; also owns UTF-8 storage */
} KeyCacheEntry;

static KeyCacheEntry key_cache[KEY_CACHE_SIZE];

/* ------------------------------------------------------------------ */
/* §7.4 kinds singletons + structured-error helper                     */
/* ------------------------------------------------------------------ */

/* Kind constants — interned strings, mirror jmd._parser._K_* tags.
   Pointer identity is used for fast comparison.  Initialized in
   PyInit__cparser. */
static PyObject *K_OBJECT          = NULL;
static PyObject *K_ARRAY_SIGIL     = NULL;
static PyObject *K_ARRAY_PROMOTED  = NULL;
static PyObject *K_SCALAR_BARE     = NULL;
static PyObject *K_SCALAR_HEADING  = NULL;

/* JMDParseError class — imported lazily on first error-raise from
   jmd._parser, so this module need not depend on _parser at import
   time.  See raise_jmd_parse_error. */
static PyObject *JMDParseError_class = NULL;

/* Initialize process-lifetime parser state. The module owns the interned
 * kind references; cached key references are retained until eviction. */
int
cparser_runtime_init(void)
{
    memset(key_cache, 0, sizeof(key_cache));

    K_OBJECT = PyUnicode_InternFromString("object");
    K_ARRAY_SIGIL = PyUnicode_InternFromString("array_sigil");
    K_ARRAY_PROMOTED = PyUnicode_InternFromString("array_promoted");
    K_SCALAR_BARE = PyUnicode_InternFromString("scalar_bare");
    K_SCALAR_HEADING = PyUnicode_InternFromString("scalar_heading");
    if (!K_OBJECT || !K_ARRAY_SIGIL || !K_ARRAY_PROMOTED
        || !K_SCALAR_BARE || !K_SCALAR_HEADING)
    {
        return -1;
    }
    return 0;
}

static int
ensure_error_class(void)
{
    if (JMDParseError_class) return 0;
    PyObject *mod = PyImport_ImportModule("jmd._parser");
    if (!mod) return -1;
    JMDParseError_class = PyObject_GetAttrString(mod, "JMDParseError");
    Py_DECREF(mod);
    return JMDParseError_class ? 0 : -1;
}

/* Raise JMDParseError(kind=..., line=..., key=..., form={...}). The
   ``form`` dict carries existing+new kind strings (interned, so the
   pointers match those returned by PyDict_GetItem on the kinds dict).
   Returns -1 with the Python exception set. */
static int
raise_jmd_parse_error(const char *kind, int line, PyObject *key,
                      PyObject *existing_kind, PyObject *new_kind)
{
    if (ensure_error_class() < 0) return -1;
    PyObject *kw = PyDict_New();
    if (!kw) return -1;
    PyObject *kind_obj = PyUnicode_FromString(kind);
    PyObject *line_obj = PyLong_FromLong((long)line);
    PyObject *form = PyDict_New();
    if (!kind_obj || !line_obj || !form) {
        Py_XDECREF(kind_obj); Py_XDECREF(line_obj);
        Py_XDECREF(form); Py_DECREF(kw);
        return -1;
    }
    if (existing_kind)
        PyDict_SetItemString(form, "existing", existing_kind);
    if (new_kind)
        PyDict_SetItemString(form, "new", new_kind);
    PyDict_SetItemString(kw, "kind", kind_obj);
    PyDict_SetItemString(kw, "line", line_obj);
    PyDict_SetItemString(kw, "key", key);
    PyDict_SetItemString(kw, "form", form);
    Py_DECREF(kind_obj); Py_DECREF(line_obj); Py_DECREF(form);
    PyObject *args = PyTuple_New(0);
    PyObject *exc = PyObject_Call(JMDParseError_class, args, kw);
    Py_DECREF(args); Py_DECREF(kw);
    if (!exc) return -1;
    PyErr_SetObject(JMDParseError_class, exc);
    Py_DECREF(exc);
    return -1;
}

/* Raise a structural JMDParseError with an empty, newly owned key. */
int
raise_structural_parse_error(const char *kind, int line)
{
    PyObject *key = PyUnicode_FromStringAndSize("", 0);
    if (!key) return -1;
    int result = raise_jmd_parse_error(kind, line, key, NULL, NULL);
    Py_DECREF(key);
    return result;
}

/* §7.4.1 promote-to-array helper for an object-heading "## key".
   On first occurrence, sets obj[key] = value (a dict) and records
   K_OBJECT.  On second occurrence, promotes to [prev, value] and
   records K_ARRAY_PROMOTED.  Subsequent occurrences append.  Returns
   0 on success, -1 on error (with exception set). */
int
set_object_heading(PyObject *obj, PyObject *kinds, PyObject *key,
                   PyObject *value, int line)
{
    PyObject *existing_kind = PyDict_GetItem(kinds, key);  /* borrowed */
    if (!existing_kind) {
        if (PyDict_SetItem(obj, key, value) < 0) return -1;
        return PyDict_SetItem(kinds, key, K_OBJECT);
    }
    if (existing_kind == K_OBJECT) {
        PyObject *existing = PyDict_GetItem(obj, key);  /* borrowed */
        if (!existing) {
            PyErr_SetString(PyExc_RuntimeError,
                            "kinds tracker out of sync with obj");
            return -1;
        }
        Py_INCREF(existing);
        PyObject *arr = PyList_New(2);
        if (!arr) { Py_DECREF(existing); return -1; }
        PyList_SET_ITEM(arr, 0, existing);  /* steals */
        Py_INCREF(value);
        PyList_SET_ITEM(arr, 1, value);     /* steals */
        if (PyDict_SetItem(obj, key, arr) < 0) { Py_DECREF(arr); return -1; }
        Py_DECREF(arr);
        return PyDict_SetItem(kinds, key, K_ARRAY_PROMOTED);
    }
    if (existing_kind == K_ARRAY_PROMOTED) {
        PyObject *existing = PyDict_GetItem(obj, key);  /* borrowed */
        if (!existing || !PyList_Check(existing)) {
            PyErr_SetString(PyExc_RuntimeError,
                            "promoted-array slot is not a list");
            return -1;
        }
        return PyList_Append(existing, value);
    }
    if (existing_kind == K_ARRAY_SIGIL) {
        return raise_jmd_parse_error("sigil_conflict", line, key,
                                     K_ARRAY_SIGIL, K_OBJECT);
    }
    /* existing is scalar (bare or heading) */
    return raise_jmd_parse_error("repeated_scalar_key", line, key,
                                 existing_kind, K_OBJECT);
}

/* §7.4 helper for an array-sigil heading "## key[]". On first
   occurrence, sets obj[key] = value (a list) and records K_ARRAY_SIGIL.
   Any repetition is an error per §7.4.2(b) or (a). */
int
set_array_sigil_heading(PyObject *obj, PyObject *kinds, PyObject *key,
                        PyObject *value, int line)
{
    PyObject *existing_kind = PyDict_GetItem(kinds, key);  /* borrowed */
    if (!existing_kind) {
        if (PyDict_SetItem(obj, key, value) < 0) return -1;
        return PyDict_SetItem(kinds, key, K_ARRAY_SIGIL);
    }
    if (existing_kind == K_ARRAY_SIGIL) {
        return raise_jmd_parse_error("repeated_explicit_array", line, key,
                                     K_ARRAY_SIGIL, K_ARRAY_SIGIL);
    }
    if (existing_kind == K_OBJECT || existing_kind == K_ARRAY_PROMOTED) {
        return raise_jmd_parse_error("sigil_conflict", line, key,
                                     existing_kind, K_ARRAY_SIGIL);
    }
    /* existing is scalar */
    return raise_jmd_parse_error("repeated_scalar_key", line, key,
                                 existing_kind, K_ARRAY_SIGIL);
}

/* §7.4.2(c) helper for a bare field or scalar heading. Any repetition
   of the same key with a scalar in either form, or with a heading
   form, is a structured error. */
int
set_scalar_field(PyObject *obj, PyObject *kinds, PyObject *key,
                 PyObject *value, int line, int is_heading)
{
    PyObject *new_kind = is_heading ? K_SCALAR_HEADING : K_SCALAR_BARE;
    PyObject *existing_kind = PyDict_GetItem(kinds, key);  /* borrowed */
    if (!existing_kind) {
        if (PyDict_SetItem(obj, key, value) < 0) return -1;
        return PyDict_SetItem(kinds, key, new_kind);
    }
    if (existing_kind == K_ARRAY_SIGIL) {
        return raise_jmd_parse_error("sigil_conflict", line, key,
                                     existing_kind, new_kind);
    }
    /* object / promoted / scalar: all collapse to repeated_scalar_key. */
    return raise_jmd_parse_error("repeated_scalar_key", line, key,
                                 existing_kind, new_kind);
}

/* Record a pre-parsed bare scalar in an item-local kinds tracker. */
int
record_bare_scalar_kind(PyObject *kinds, PyObject *key)
{
    return PyDict_SetItem(kinds, key, K_SCALAR_BARE);
}

static unsigned int
key_hash(const char *s, Py_ssize_t len)
{
    /* FNV-1a 32 */
    unsigned int h = 2166136261u;
    for (Py_ssize_t i = 0; i < len; i++) {
        h ^= (unsigned char)s[i];
        h *= 16777619u;
    }
    return h;
}

PyObject *
intern_key(const char *raw, Py_ssize_t len)
{
    unsigned int idx = key_hash(raw, len) & KEY_CACHE_MASK;
    KeyCacheEntry *e = &key_cache[idx];
    if (e->pyobj && e->len == len) {
        Py_ssize_t cached_len;
        const char *cached_raw = PyUnicode_AsUTF8AndSize(
            e->pyobj, &cached_len);
        if (!cached_raw) return NULL;
        if (cached_len == len
            && memcmp(cached_raw, raw, (size_t)len) == 0)
        {
            Py_INCREF(e->pyobj);
            return e->pyobj;
        }
    }
    PyObject *obj = PyUnicode_FromStringAndSize(raw, len);
    if (!obj) return NULL;
    /* Evict the old owned key; never retain source-buffer pointers. */
    Py_XDECREF(e->pyobj);
    e->len = len;
    e->pyobj = obj;
    Py_INCREF(obj);   /* one ref for cache, one for caller */
    return obj;
}

