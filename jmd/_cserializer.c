/*
 * _cserializer.c — CPython module entry point.
 *
 * Copyright (c) 2026 Andreas Ostermeyer <andreas@ostermeyer.de>
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0.
 * See LICENSE at the repository root for the full license text.
 */

#include "_cserializer_internal.h"

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

    /* Split off optional mode prefix: `- Label`, `? Label`, `! Label`.
       The mark attaches directly to `#` in the canonical root heading
       (`#- Label`), with no space between `#` and the mark. */
    const char *rest = label;
    char mark = 0;
    size_t label_len = strlen(label);
    if (label_len >= 2
        && (label[0] == '-' || label[0] == '?' || label[0] == '!')
        && label[1] == ' ') {
        mark = label[0];
        rest = label + 2;
    }

    if (PyList_Check(data)) {
        if (!outbuf_append(&ob, "#", 1)) { outbuf_free(&ob); return NULL; }
        if (mark && !outbuf_append(&ob, &mark, 1))
            { outbuf_free(&ob); return NULL; }
        if (!outbuf_append(&ob, " ", 1)) { outbuf_free(&ob); return NULL; }
        if (strcmp(rest, "[]") == 0) {
            if (!outbuf_append(&ob, "[]", 2)) { outbuf_free(&ob); return NULL; }
        } else {
            if (!outbuf_append(&ob, rest, (Py_ssize_t)strlen(rest)))
                { outbuf_free(&ob); return NULL; }
            if (!outbuf_append(&ob, "[]", 2)) { outbuf_free(&ob); return NULL; }
        }
        if (!ser_write_array_items(&ob, data, 1)) { outbuf_free(&ob); return NULL; }
    }
    else if (PyDict_Check(data)) {
        if (!outbuf_append(&ob, "#", 1)) { outbuf_free(&ob); return NULL; }
        if (mark && !outbuf_append(&ob, &mark, 1))
            { outbuf_free(&ob); return NULL; }
        if (!outbuf_append(&ob, " ", 1)) { outbuf_free(&ob); return NULL; }
        if (!outbuf_append(&ob, rest, (Py_ssize_t)strlen(rest)))
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
