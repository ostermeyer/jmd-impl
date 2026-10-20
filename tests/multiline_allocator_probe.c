/*
 * Multiline allocator fault probe: compile the production translation unit
 * with local allocation wrappers. No hooks enter the shipped extension.
 * Exercises §5.2 / §9.1 scratch ownership, failure and overflow invariants.
 *
 * Copyright (c) 2026 Andreas Ostermeyer <andreas@ostermeyer.de>
 * SPDX-License-Identifier: Apache-2.0
 */

#include "../jmd/_cparser_internal.h"

static int allocation_index;
static int fail_at;
static int live_allocations;

/* Return owned storage, or inject failure at the requested allocation. */
static void *
probe_malloc(size_t size)
{
    void *result;
    if (++allocation_index == fail_at)
        return NULL;
    result = PyMem_Malloc(size);
    if (result)
        live_allocations++;
    return result;
}

/* Preserve the old allocation on injected failure, as PyMem_Realloc does. */
static void *
probe_realloc(void *old, size_t size)
{
    int was_null = old == NULL;
    void *result;
    if (++allocation_index == fail_at)
        return NULL;
    result = PyMem_Realloc(old, size);
    if (result && was_null)
        live_allocations++;
    return result;
}

/* Release owned probe storage and count balanced ownership, including NULL. */
static void
probe_free(void *ptr)
{
    if (ptr)
        live_allocations--;
    PyMem_Free(ptr);
}

/* Fail Unicode construction too, after the output buffer has been allocated. */
static PyObject *
probe_unicode(const char *text, Py_ssize_t len)
{
    if (++allocation_index == fail_at)
        return PyErr_NoMemory();
    return PyUnicode_FromStringAndSize(text, len);
}

#define PyUnicode_FromStringAndSize probe_unicode
#define PyMem_Malloc probe_malloc
#define PyMem_Realloc probe_realloc
#define PyMem_Free probe_free
#include "../jmd/_cparser_multiline.c"
#undef PyMem_Malloc
#undef PyMem_Realloc
#undef PyMem_Free
#undef PyUnicode_FromStringAndSize

/* Inline scalars are outside this probe; satisfy the translation unit's link. */
PyObject *
parse_scalar(const char *text, Py_ssize_t len)
{
    (void)text;
    (void)len;
    PyErr_SetString(PyExc_AssertionError, "unexpected inline scalar");
    return NULL;
}

/*
 * Run one real multiline parse over borrowed static source slices. Arguments
 * are mode (quote/literal/folded) and the allocation to fail (zero = success).
 * Return its new Unicode reference or the original MemoryError. Every outcome
 * must release all scratch allocations; failure must not consume unstored data.
 */
static PyObject *
probe_parse(PyObject *self, PyObject *args)
{
    JMDLine lines[300] = {{0}};
    LineArray array = {lines, 300, 300};
    ParserState state = {&array, 0};
    int mode;
    PyObject *result;
    (void)self;
    if (!PyArg_ParseTuple(args, "ii", &mode, &fail_at))
        return NULL;
    allocation_index = live_allocations = 0;
    for (int i = 0; i < 300; i++) {
        lines[i].raw = mode == 0 ? "> text" : "  text";
        lines[i].raw_len = 6;
        lines[i].heading_depth = 0;
    }
    result = mode == 0 ? parse_blockquote(&state) :
                        parse_block_scalar(&state, mode == 2);
    if (live_allocations != 0) {
        Py_XDECREF(result);
        PyErr_SetString(PyExc_AssertionError, "scratch storage leaked");
        return NULL;
    }
    if (fail_at > 0 && fail_at <= allocation_index) {
        /* Growth happens before accepting lines 0, 32, 64, 128 and 256.
         * The sixth allocates the output, the seventh constructs Unicode. */
        Py_ssize_t expected = fail_at == 1 ? 0 :
                              fail_at < 6 ? 32 << (fail_at - 2) : 300;
        if (result || !PyErr_ExceptionMatches(PyExc_MemoryError) ||
            state.pos != expected) {
            Py_XDECREF(result);
            PyErr_SetString(PyExc_AssertionError,
                            "failure lost its exception or advanced input");
            return NULL;
        }
    }
    return result;
}

/*
 * Exercise impossible sizes without huge allocations. The borrowed dummy text
 * is never dereferenced: both overflow guards must reject before allocation.
 * Return None on success, NULL on a failed invariant.
 */
static PyObject *
probe_overflow(PyObject *self, PyObject *unused)
{
    TextParts parts = {0};
    int accepted;
    (void)self;
    (void)unused;
    fail_at = allocation_index = live_allocations = 0;

    parts.output_bound = 10;
    accepted = text_parts_append(&parts, "", PY_SSIZE_T_MAX - 10);
    if (accepted || !PyErr_ExceptionMatches(PyExc_MemoryError) ||
        allocation_index != 0) {
        PyErr_SetString(PyExc_AssertionError, "output size overflow accepted");
        return NULL;
    }
    PyErr_Clear();

    parts.output_bound = 0;
    parts.len = parts.cap = PY_SSIZE_T_MAX / sizeof(TextPart);
    accepted = text_parts_append(&parts, "", 0);
    if (accepted || !PyErr_ExceptionMatches(PyExc_MemoryError) ||
        allocation_index != 0) {
        PyErr_SetString(PyExc_AssertionError, "vector size overflow accepted");
        return NULL;
    }
    PyErr_Clear();
    Py_RETURN_NONE;
}

static PyMethodDef methods[] = {
    {"parse", probe_parse, METH_VARARGS, "Parse with one injected allocation failure."},
    {"overflow", probe_overflow, METH_NOARGS, "Check size guards without allocation."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "_multiline_probe", NULL, -1, methods,
    NULL, NULL, NULL, NULL
};

/* CPython module entry: return an owned module reference or NULL. */
PyMODINIT_FUNC
PyInit__multiline_probe(void)
{
    return PyModule_Create(&module);
}
