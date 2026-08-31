/*
 * _cparser.c — CPython module entry point.
 *
 * Copyright (c) 2026 Andreas Ostermeyer <andreas@ostermeyer.de>
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0.
 * See LICENSE at the repository root for the full license text.
 */

#include "_cparser_internal.h"

/* ------------------------------------------------------------------ */
/* Top-level parse function                                            */
/* ------------------------------------------------------------------ */

static PyObject *
jmd_parse(PyObject *self, PyObject *args)
{
    (void)self;
    const char *source;
    Py_ssize_t source_len;
    int line_offset = 0;

    if (!PyArg_ParseTuple(
            args, "s#|i", &source, &source_len, &line_offset))
        return NULL;
    if (line_offset < 0) {
        PyErr_SetString(PyExc_ValueError, "line_offset must be non-negative");
        return NULL;
    }

    LineArray lines;
    if (!linearray_init(&lines)) {
        PyErr_NoMemory();
        return NULL;
    }

    if (!tokenize(source, source_len, line_offset, &lines)) {
        linearray_free(&lines);
        return NULL;
    }

    if (lines.len == 0) {
        linearray_free(&lines);
        PyErr_SetString(PyExc_ValueError, "Empty document");
        return NULL;
    }

    ParserState st;
    st.lines = &lines;
    st.pos = 0;

    /* Skip frontmatter (lines before first heading) */
    while (st.pos < lines.len) {
        JMDLine *line = &lines.items[st.pos];
        if (line->heading_depth > 0)
            break;
        if (line->heading_depth == -1) {
            st.pos++;
            continue;
        }
        /* Non-heading, non-blank: frontmatter field — skip */
        st.pos++;
    }

    if (st.pos >= lines.len) {
        raise_structural_parse_error("no_root_heading", line_offset + 1);
        linearray_free(&lines);
        return NULL;
    }

    JMDLine *first = &lines.items[st.pos];
    if (first->heading_depth == 1 && first->content_len == 0) {
        raise_structural_parse_error("no_root_heading", first->number);
        linearray_free(&lines);
        return NULL;
    }

    PyObject *result;

    /* Root array: # [] or # Label[] */
    if (first->heading_depth == 1
        && first->content_len >= 2
        && first->content[first->content_len - 2] == '['
        && first->content[first->content_len - 1] == ']')
    {
        st.pos++;
        result = parse_array_body(&st, 1);
    }
    /* Root object: # Label */
    else if (first->heading_depth == 1) {
        st.pos++;
        result = parse_object_body(&st, 1);
    }
    else {
        linearray_free(&lines);
        PyErr_SetString(PyExc_ValueError,
                        "Expected '# <label>' or '# []'");
        return NULL;
    }

    /* §18.0 and §3.6.2: a completed body may leave only decoration.
     * A labelled depth-one heading starts a forbidden second document;
     * a mode marker is the more specific error, and indented leftovers are
     * prose rather than data. */
    if (result != NULL) {
        Py_ssize_t pos = st.pos;
        while (pos < lines.len && lines.items[pos].heading_depth == -1)
            pos++;
        if (pos < lines.len) {
            JMDLine *leftover = &lines.items[pos];
            const char *error_kind = NULL;
            if (leftover->heading_depth == 1
                && leftover->content_len > 0)
            {
                const char *raw = leftover->raw;
                Py_ssize_t raw_len = leftover->raw_len;
                if (raw_len >= 3 && raw[0] == '#'
                    && (raw[1] == '?' || raw[1] == '!' || raw[1] == '-')
                    && (raw[2] == ' ' || raw[2] == '\t'))
                    error_kind = "mode_marker_mid_document";
                else
                    error_kind = "second_root_heading";
            }
            else if (leftover->raw_len > 0
                     && (leftover->raw[0] == ' '
                         || leftover->raw[0] == '\t'))
                error_kind = "prose_in_body";
            else
                /* The root has no parent scope that could consume this
                 * non-structural line.  Returning the parsed prefix would
                 * lose caller input, so fail with the shared prose error. */
                error_kind = "prose_in_body";

            if (error_kind != NULL) {
                raise_structural_parse_error(
                    error_kind, leftover->number);
                Py_DECREF(result);
                result = NULL;
            }
        }
    }

    linearray_free(&lines);
    return result;
}

/* ------------------------------------------------------------------ */
/* Module definition                                                   */
/* ------------------------------------------------------------------ */

static PyMethodDef cparser_methods[] = {
    {"parse", jmd_parse, METH_VARARGS,
     "Parse JMD body text, adding an optional source-line offset."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef cparser_module = {
    PyModuleDef_HEAD_INIT,
    "jmd._cparser",
    "C-accelerated JMD parser.",
    -1,
    cparser_methods,
    NULL, NULL, NULL, NULL
};

PyMODINIT_FUNC
PyInit__cparser(void)
{
    if (cparser_runtime_init() < 0)
        return NULL;

    return PyModule_Create(&cparser_module);
}
