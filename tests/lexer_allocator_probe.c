/*
 * Lexer fault probe: compile production code with test-only allocation and
 * numeric-conversion wrappers. Verify ownership, error propagation and checked
 * capacity arithmetic without requiring enormous real allocations.
 *
 * Copyright (c) 2026 Andreas Ostermeyer <andreas@ostermeyer.de>
 * SPDX-License-Identifier: Apache-2.0
 */

#include "../jmd/_cparser_internal.h"

static int calls;
static int fail_at;
static int error_kind;
static int live;
static size_t requested;

/* Fail the selected operation; wrappers own the corresponding exception. */
static int
fails(void)
{
    return ++calls == fail_at;
}

/* Return owned scratch storage; injected failure follows PyMem's contract. */
static void *
probe_malloc(size_t size)
{
    void *result;
    requested = size;
    if (fails())
        return NULL;
    result = PyMem_Malloc(size);
    if (result)
        live++;
    return result;
}

/* Preserve the old allocation when growth fails. */
static void *
probe_realloc(void *old, size_t size)
{
    int was_null = old == NULL;
    void *result;
    requested = size;
    if (fails())
        return NULL;
    result = PyMem_Realloc(old, size);
    if (result && was_null)
        live++;
    return result;
}

/* Release owned scratch storage, accepting NULL. */
static void
probe_free(void *ptr)
{
    if (ptr)
        live--;
    PyMem_Free(ptr);
}

/* Set a chosen conversion exception; only ValueError may permit fallback. */
static PyObject *
conversion_error(void)
{
    if (error_kind == 1)
        return PyErr_NoMemory();
    PyErr_SetString(error_kind == 2 ? PyExc_RuntimeError : PyExc_ValueError,
                    "injected numeric conversion error");
    return NULL;
}

/* Numeric constructors return new references or the injected exception. */
static PyObject *
probe_long_string(const char *text, char **end, int base)
{
    return fails() ? conversion_error() : PyLong_FromString(text, end, base);
}

static PyObject *
probe_long(long value)
{
    return fails() ? conversion_error() : PyLong_FromLong(value);
}

static PyObject *
probe_float(double value)
{
    return fails() ? conversion_error() : PyFloat_FromDouble(value);
}

#define PyMem_Malloc probe_malloc
#define PyMem_Realloc probe_realloc
#define PyMem_Free probe_free
#define PyLong_FromString probe_long_string
#define PyLong_FromLong probe_long
#define PyFloat_FromDouble probe_float
#include "../jmd/_cparser_lex.c"
#undef PyMem_Malloc
#undef PyMem_Realloc
#undef PyMem_Free
#undef PyLong_FromString
#undef PyLong_FromLong
#undef PyFloat_FromDouble

/* Unused runtime dependencies: keep unexpected calls visible in this probe. */
int
raise_structural_parse_error(const char *kind, int line)
{
    (void)kind;
    (void)line;
    PyErr_SetString(PyExc_AssertionError, "unexpected structural error");
    return -1;
}

PyObject *
intern_key(const char *raw, Py_ssize_t len)
{
    return PyUnicode_FromStringAndSize(raw, len);
}

/* Parse borrowed text and verify scratch cleanup on every result/error path. */
static PyObject *
probe_parse(PyObject *self, PyObject *args)
{
    const char *text;
    Py_ssize_t len;
    PyObject *result;
    (void)self;
    if (!PyArg_ParseTuple(args, "s#ii", &text, &len, &fail_at, &error_kind))
        return NULL;
    calls = live = 0;
    result = parse_scalar(text, len);
    if (live != 0) {
        Py_XDECREF(result);
        PyErr_SetString(PyExc_AssertionError, "numeric scratch leaked");
        return NULL;
    }
    return result;
}

/*
 * Exercise initialization and real vector growth. Failed growth must retain
 * pointer, length, capacity and existing content; all owned storage is released.
 */
static PyObject *
probe_lines(PyObject *self, PyObject *args)
{
    LineArray array;
    JMDLine line = {0};
    int ok;
    (void)self;
    if (!PyArg_ParseTuple(args, "i", &fail_at))
        return NULL;
    calls = live = 0;
    ok = linearray_init(&array);
    for (int i = 0; ok && i < 300; i++) {
        JMDLine *old = array.items;
        Py_ssize_t old_len = array.len;
        Py_ssize_t old_cap = array.cap;
        line.number = i;
        ok = linearray_push(&array, &line);
        if (!ok && (array.items != old || array.len != old_len ||
                    array.cap != old_cap)) {
            PyErr_SetString(PyExc_AssertionError, "growth failure mutated state");
            break;
        }
    }
    for (Py_ssize_t i = 0; i < array.len; i++) {
        if (array.items[i].number != i) {
            ok = 0;
            PyErr_SetString(PyExc_AssertionError, "growth corrupted content");
            break;
        }
    }
    linearray_free(&array);
    if (live != 0) {
        PyErr_SetString(PyExc_AssertionError, "line vector leaked");
        return NULL;
    }
    if (!ok)
        return NULL;
    Py_RETURN_NONE;
}

/*
 * Supply artificial sizes, never huge storage. Reject a full maximum vector
 * before allocation, clamp near-maximum doubling to a safe byte count, and
 * reject a numeric length whose terminating NUL cannot fit.
 */
static PyObject *
probe_overflow(PyObject *self, PyObject *args)
{
    int mode, ok;
    const Py_ssize_t maxcap = PY_SSIZE_T_MAX / sizeof(JMDLine);
    LineArray array = {NULL, maxcap, maxcap};
    JMDLine line = {0};
    char scratch[32];
    (void)self;
    if (!PyArg_ParseTuple(args, "i", &mode))
        return NULL;
    fail_at = 1;
    calls = live = 0;
    requested = 0;
    if (mode == 2) {
        char *result = copy_numeric_text("", PY_SSIZE_T_MAX,
                                         scratch, sizeof(scratch));
        ok = result == NULL && calls == 0;
    } else {
        if (mode == 1)
            array.len = array.cap = maxcap / 2 + 1;
        ok = !linearray_push(&array, &line);
        ok = ok && (mode == 0 ? calls == 0 :
                    calls == 1 && requested == (size_t)maxcap * sizeof(JMDLine));
    }
    if (!ok || !PyErr_ExceptionMatches(PyExc_MemoryError)) {
        PyErr_SetString(PyExc_AssertionError, "size guard failed");
        return NULL;
    }
    return NULL;  /* Preserve the expected MemoryError. */
}

static PyMethodDef methods[] = {
    {"parse", probe_parse, METH_VARARGS, "Parse with an injected failure."},
    {"lines", probe_lines, METH_VARARGS, "Check line-vector ownership."},
    {"overflow", probe_overflow, METH_VARARGS, "Check arithmetic limits."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "_lexer_probe", NULL, -1, methods,
    NULL, NULL, NULL, NULL
};

/* Return an owned module reference or NULL with the CPython exception. */
PyMODINIT_FUNC
PyInit__lexer_probe(void)
{
    return PyModule_Create(&module);
}
