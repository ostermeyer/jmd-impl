/*
 * _cserializer_buffer.c — owned output-buffer operations.
 *
 * Copyright (c) 2026 Andreas Ostermeyer <andreas@ostermeyer.de>
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0.
 * See LICENSE at the repository root for the full license text.
 */

#include "_cserializer_internal.h"

/* ------------------------------------------------------------------ */
/* Output buffer                                                       */
/* ------------------------------------------------------------------ */

int
outbuf_init(OutBuf *ob)
{
    ob->cap = 4096;
    ob->len = 0;
    ob->buf = (char *)PyMem_Malloc(ob->cap);
    return ob->buf != NULL;
}

void
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

int
outbuf_append(OutBuf *ob, const char *s, Py_ssize_t slen)
{
    if (!outbuf_grow(ob, slen)) return 0;
    memcpy(ob->buf + ob->len, s, (size_t)slen);
    ob->len += slen;
    return 1;
}

int
outbuf_putc(OutBuf *ob, char c)
{
    if (!outbuf_grow(ob, 1)) return 0;
    ob->buf[ob->len++] = c;
    return 1;
}

/* Append '#' repeated `depth` times followed by a space. */
int
outbuf_heading(OutBuf *ob, int depth)
{
    if (!outbuf_grow(ob, depth + 1)) return 0;
    memset(ob->buf + ob->len, '#', (size_t)depth);
    ob->len += depth;
    ob->buf[ob->len++] = ' ';
    return 1;
}
