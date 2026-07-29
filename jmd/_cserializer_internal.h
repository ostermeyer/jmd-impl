/*
 * _cserializer_internal.h — private interfaces for the C serializer.
 *
 * Copyright (c) 2026 Andreas Ostermeyer <andreas@ostermeyer.de>
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0.
 * See LICENSE at the repository root for the full license text.
 *
 * OutBuf owns its PyMem-allocated byte buffer between init and free.
 * Serializer functions borrow Python objects and retain no references.
 * Integer results use 1 for success and 0 with a Python exception set.
 */

#ifndef JMD_CSERIALIZER_INTERNAL_H
#define JMD_CSERIALIZER_INTERNAL_H

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Growable owned output storage; content need not be NUL-terminated. */
typedef struct {
    char *buf;
    Py_ssize_t len;
    Py_ssize_t cap;
} OutBuf;

/* Output-buffer lifetime and append operations. */
int outbuf_init(OutBuf *output);
void outbuf_free(OutBuf *output);
int outbuf_append(OutBuf *output, const char *text, Py_ssize_t len);
int outbuf_putc(OutBuf *output, char value);
int outbuf_heading(OutBuf *output, int depth);

/* Scalar, key, and multiline rendering. */
int ser_needs_quote(const char *text, Py_ssize_t len);
int ser_write_quoted(OutBuf *output, const char *text, Py_ssize_t len);
int ser_write_key(OutBuf *output, const char *text, Py_ssize_t len);
int ser_write_scalar(OutBuf *output, PyObject *value);
int ser_write_array_scalar(OutBuf *output, PyObject *value);
int ser_write_multiline(
    OutBuf *output, const char *text, Py_ssize_t len);

/* Mutually recursive collection renderers. */
int ser_write_object_fields(
    OutBuf *output, PyObject *object, int depth);
int ser_write_array_items(
    OutBuf *output, PyObject *array, int depth);

#endif /* JMD_CSERIALIZER_INTERNAL_H */
