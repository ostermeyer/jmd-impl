/*
 * _cparser_internal.h — private interfaces for the C-accelerated parser.
 *
 * Copyright (c) 2026 Andreas Ostermeyer <andreas@ostermeyer.de>
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0.
 * See LICENSE at the repository root for the full license text.
 *
 * Token pointers borrow storage from the source object passed to parse();
 * the source outlives every parser state. LineArray owns only its vector.
 * Unless a declaration says otherwise, PyObject-returning functions return
 * a new reference and PyObject arguments are borrowed.
 */

#ifndef JMD_CPARSER_INTERNAL_H
#define JMD_CPARSER_INTERNAL_H

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <ctype.h>
#include <errno.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>

/* A non-owning view of one source line. */
typedef struct {
    const char *raw;
    Py_ssize_t raw_len;
    const char *content;
    Py_ssize_t content_len;
    int heading_depth;
    int number;
} JMDLine;

/* An owned vector of non-owning line views. */
typedef struct {
    JMDLine *items;
    Py_ssize_t len;
    Py_ssize_t cap;
} LineArray;

/* Mutable cursor over an initialized LineArray. */
typedef struct {
    LineArray *lines;
    Py_ssize_t pos;
} ParserState;

/* Runtime state and structured-error helpers. */
int cparser_runtime_init(void);
int raise_structural_parse_error(const char *kind, int line);
int set_object_heading(
    PyObject *obj, PyObject *kinds, PyObject *key, PyObject *value, int line);
int set_array_sigil_heading(
    PyObject *obj, PyObject *kinds, PyObject *key, PyObject *value, int line);
int set_scalar_field(
    PyObject *obj,
    PyObject *kinds,
    PyObject *key,
    PyObject *value,
    int line,
    int is_heading);
int record_bare_scalar_kind(PyObject *kinds, PyObject *key);
PyObject *intern_key(const char *raw, Py_ssize_t len);

/* Lexing and scalar conversion. */
int linearray_init(LineArray *lines);
void linearray_free(LineArray *lines);
int tokenize(
    const char *source,
    Py_ssize_t source_len,
    int line_offset,
    LineArray *lines);
PyObject *parse_scalar(const char *text, Py_ssize_t len);
PyObject *parse_key(const char *text, Py_ssize_t len);
Py_ssize_t find_kv_split(const char *text, Py_ssize_t len);
int is_thematic_break(const JMDLine *line);

/* Multiline and inline value handling. */
PyObject *parse_blockquote(ParserState *state);
PyObject *parse_block_scalar(ParserState *state, int folded);
int next_is_blockquote(ParserState *state);
PyObject *parse_field_value(
    ParserState *state, const char *value_text, Py_ssize_t value_len);
Py_ssize_t find_colon_space(const char *text, Py_ssize_t len);

/* Mutually recursive body parsers and shared item helpers. */
PyObject *parse_object_body(ParserState *state, int depth);
PyObject *parse_array_body(ParserState *state, int depth);
int parse_heading_into(
    ParserState *state, PyObject *obj, PyObject *kinds, int depth);
int is_dash_item(const char *content, Py_ssize_t len);
Py_ssize_t find_item_field_split(
    const char *content, Py_ssize_t content_len);
PyObject *parse_dash_kv_item(
    ParserState *state,
    const char *after,
    Py_ssize_t after_len,
    Py_ssize_t split);
PyObject *parse_item_object(
    ParserState *state, int array_depth, PyObject *initial);

#endif /* JMD_CPARSER_INTERNAL_H */
