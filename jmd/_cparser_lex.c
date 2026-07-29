/*
 * _cparser_lex.c — line tokenization and scalar conversion.
 *
 * Copyright (c) 2026 Andreas Ostermeyer <andreas@ostermeyer.de>
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0.
 * See LICENSE at the repository root for the full license text.
 */

#include "_cparser_internal.h"

/* ------------------------------------------------------------------ */
/* Dynamic array of lines                                              */
/* ------------------------------------------------------------------ */

int
linearray_init(LineArray *la)
{
    la->cap = 256;
    la->len = 0;
    la->items = (JMDLine *)PyMem_Malloc(la->cap * sizeof(JMDLine));
    return la->items != NULL;
}

void
linearray_free(LineArray *la)
{
    PyMem_Free(la->items);
    la->items = NULL;
    la->len = la->cap = 0;
}

static int
linearray_push(LineArray *la, const JMDLine *line)
{
    if (la->len == la->cap) {
        Py_ssize_t newcap = la->cap * 2;
        JMDLine *tmp = (JMDLine *)PyMem_Realloc(la->items,
                                                  newcap * sizeof(JMDLine));
        if (!tmp) {
            PyErr_NoMemory();
            return 0;
        }
        la->items = tmp;
        la->cap = newcap;
    }
    la->items[la->len++] = *line;
    return 1;
}

/* ------------------------------------------------------------------ */
/* Tokenizer                                                           */
/* ------------------------------------------------------------------ */

/* Check if text starts with '#' repeated `n` times, then either space
   or end of string. Returns heading depth or 0.                       */
static int
detect_heading(const char *text, Py_ssize_t text_len,
               const char **out_content, Py_ssize_t *out_content_len)
{
    if (text_len == 0 || text[0] != '#')
        return 0;

    /* Root markers: #? #! #-  followed by space */
    if (text_len >= 3 && (text[1] == '?' || text[1] == '!' || text[1] == '-')
        && text[2] == ' ')
    {
        /* Content is "<marker> <rest>" */
        /* Build content as "? label" / "! label" / "- label" */
        *out_content = text + 1;          /* skip the '#' */
        *out_content_len = text_len - 1;
        return 1;
    }

    /* Count consecutive '#' */
    int depth = 0;
    while (depth < text_len && text[depth] == '#')
        depth++;

    /* Bare heading: just "##..." with nothing after */
    if (depth == text_len) {
        *out_content = text + text_len; /* empty */
        *out_content_len = 0;
        return depth;
    }

    /* Must be followed by space */
    if (text[depth] != ' ')
        return 0;

    *out_content = text + depth + 1;
    *out_content_len = text_len - depth - 1;
    return depth;
}

int
tokenize(
    const char *source,
    Py_ssize_t source_len,
    int line_offset,
    LineArray *lines)
{
    Py_ssize_t pos = 0;
    int lineno = line_offset;

    while (pos <= source_len) {
        lineno++;
        /* Find end of line */
        const char *line_start = source + pos;
        const char *nl = (const char *)memchr(line_start, '\n',
                                               source_len - pos);
        Py_ssize_t line_len;
        if (nl) {
            line_len = nl - line_start;
        } else {
            line_len = source_len - pos;
        }

        /* Handle \r\n */
        Py_ssize_t raw_len = line_len;
        if (raw_len > 0 && line_start[raw_len - 1] == '\r')
            raw_len--;

        /* Strip leading and trailing whitespace for 'text' */
        const char *text = line_start;
        Py_ssize_t text_len = raw_len;
        while (text_len > 0 && (*text == ' ' || *text == '\t')) {
            text++;
            text_len--;
        }
        while (text_len > 0 && (text[text_len - 1] == ' ' ||
                                 text[text_len - 1] == '\t'))
            text_len--;

        JMDLine ln;
        ln.raw = line_start;
        ln.raw_len = raw_len;
        ln.number = lineno;

        if (text_len == 0) {
            /* Blank line */
            ln.heading_depth = -1;
            ln.content = text;
            ln.content_len = 0;
        } else {
            const char *hcontent;
            Py_ssize_t hcontent_len;
            int hd = detect_heading(text, text_len, &hcontent, &hcontent_len);
            if (hd > 0) {
                ln.heading_depth = hd;
                ln.content = hcontent;
                ln.content_len = hcontent_len;
            } else {
                ln.heading_depth = 0;
                ln.content = text;
                ln.content_len = text_len;
            }
        }

        if (!linearray_push(lines, &ln))
            return 0;

        if (nl)
            pos = (nl - source) + 1;
        else
            break;
    }
    return 1;
}

/* ------------------------------------------------------------------ */
/* Scalar parsing                                                      */
/* ------------------------------------------------------------------ */

/* Fallback: use json.loads for strings with complex escapes. */
static PyObject *
parse_quoted_string_json(const char *s, Py_ssize_t len)
{
    PyObject *pystr = PyUnicode_FromStringAndSize(s, len);
    if (!pystr) return NULL;

    static PyObject *json_loads = NULL;
    if (!json_loads) {
        PyObject *json_mod = PyImport_ImportModule("json");
        if (!json_mod) { Py_DECREF(pystr); return NULL; }
        json_loads = PyObject_GetAttrString(json_mod, "loads");
        Py_DECREF(json_mod);
        if (!json_loads) { Py_DECREF(pystr); return NULL; }
    }

    PyObject *result = PyObject_CallOneArg(json_loads, pystr);
    Py_DECREF(pystr);
    return result;
}

/* Parse a JSON-style quoted string, handling escapes.
   Fast path: if no backslash, just return the inner content.
   Returns a new reference to a PyUnicode, or NULL on error. */
static PyObject *
parse_quoted_string(const char *s, Py_ssize_t len)
{
    /* s[0] = '"', s[len-1] = '"' */
    const char *inner = s + 1;
    Py_ssize_t inner_len = len - 2;

    /* Fast path: use memchr instead of byte loop */
    int has_backslash = (memchr(inner, '\\', (size_t)inner_len) != NULL);

    if (!has_backslash) {
        return PyUnicode_FromStringAndSize(inner, inner_len);
    }

    /* Handle common escapes in C for speed */
    /* Worst case: same length as input (escapes make it shorter) */
    char *buf = (char *)PyMem_Malloc((size_t)inner_len);
    if (!buf) {
        PyErr_NoMemory();
        return NULL;
    }

    Py_ssize_t out = 0;
    for (Py_ssize_t i = 0; i < inner_len; i++) {
        if (inner[i] == '\\' && i + 1 < inner_len) {
            char c = inner[i + 1];
            switch (c) {
                case '"':  buf[out++] = '"';  i++; break;
                case '\\': buf[out++] = '\\'; i++; break;
                case '/':  buf[out++] = '/';  i++; break;
                case 'n':  buf[out++] = '\n'; i++; break;
                case 't':  buf[out++] = '\t'; i++; break;
                case 'r':  buf[out++] = '\r'; i++; break;
                case 'b':  buf[out++] = '\b'; i++; break;
                case 'f':  buf[out++] = '\f'; i++; break;
                case 'u':
                    /* \uXXXX — fall back to json.loads for correctness */
                    PyMem_Free(buf);
                    return parse_quoted_string_json(s, len);
                default:
                    buf[out++] = inner[i];
                    break;
            }
        } else {
            buf[out++] = inner[i];
        }
    }

    PyObject *result = PyUnicode_FromStringAndSize(buf, out);
    PyMem_Free(buf);
    return result;
}

PyObject *
parse_scalar(const char *raw, Py_ssize_t len)
{
    if (len == 0) {
        return PyUnicode_FromStringAndSize("", 0);
    }

    char c0 = raw[0];

    /* Quoted string */
    if (c0 == '"' && len >= 2 && raw[len - 1] == '"') {
        return parse_quoted_string(raw, len);
    }
    if (c0 == '"') {
        /* Unterminated quote: return as bare string */
        return PyUnicode_FromStringAndSize(raw, len);
    }

    /* null */
    if (len == 4 && memcmp(raw, "null", 4) == 0) {
        Py_RETURN_NONE;
    }
    /* true */
    if (len == 4 && memcmp(raw, "true", 4) == 0) {
        Py_RETURN_TRUE;
    }
    /* false */
    if (len == 5 && memcmp(raw, "false", 5) == 0) {
        Py_RETURN_FALSE;
    }

    /* Number detection: starts with digit or '-' followed by digit */
    int is_num = 0;
    if (c0 >= '0' && c0 <= '9') {
        is_num = 1;
    } else if (c0 == '-' && len > 1 && raw[1] >= '0' && raw[1] <= '9') {
        is_num = 1;
    }

    if (is_num) {
        /* Check for float indicators using memchr — much faster than loop */
        int is_float = (memchr(raw, '.', (size_t)len) != NULL
                     || memchr(raw, 'e', (size_t)len) != NULL
                     || memchr(raw, 'E', (size_t)len) != NULL);

        if (is_float) {
            /* Parse as float */
            char buf[128];
            if (len < (Py_ssize_t)sizeof(buf)) {
                memcpy(buf, raw, (size_t)len);
                buf[len] = '\0';
                char *end;
                double val = strtod(buf, &end);
                if (end == buf + len) {
                    return PyFloat_FromDouble(val);
                }
            }
            /* Fall through to bare string */
        } else {
            /* Parse as int using strtol — avoid PyLong_FromString overhead */
            char buf[32];
            if (len < (Py_ssize_t)sizeof(buf)) {
                memcpy(buf, raw, (size_t)len);
                buf[len] = '\0';
                char *end;
                errno = 0;
                long val = strtol(buf, &end, 10);
                if (end == buf + len && errno == 0
                    && val >= LONG_MIN && val <= LONG_MAX)
                {
                    return PyLong_FromLong(val);
                }
                /* Overflow or parse error: try PyLong for big ints */
                if (end == buf + len) {
                    PyObject *result = PyLong_FromString(buf, NULL, 10);
                    if (result) return result;
                    PyErr_Clear();
                }
            } else {
                /* Very long number — use PyLong_FromString */
                char *tmp = (char *)PyMem_Malloc((size_t)(len + 1));
                if (tmp) {
                    memcpy(tmp, raw, (size_t)len);
                    tmp[len] = '\0';
                    PyObject *result = PyLong_FromString(tmp, NULL, 10);
                    PyMem_Free(tmp);
                    if (result) return result;
                    PyErr_Clear();
                }
            }
            /* Fall through to bare string */
        }
    }

    /* Bare string */
    return PyUnicode_FromStringAndSize(raw, len);
}

/* Parse a key: strip quotes if present, intern for cache hits */
PyObject *
parse_key(const char *raw, Py_ssize_t len)
{
    if (len >= 2 && raw[0] == '"' && raw[len - 1] == '"') {
        return parse_quoted_string(raw, len);
    }
    return intern_key(raw, len);
}

/* ------------------------------------------------------------------ */
/* Helper: find ": " split and return position or -1                   */
/* Matches: bare_key: value  or  "quoted key": value                   */
/* ------------------------------------------------------------------ */

Py_ssize_t
find_kv_split(const char *s, Py_ssize_t len)
{
    if (len < 3) return -1;  /* minimum: "k: v" */

    if (s[0] == '"') {
        /* Quoted key: find closing quote */
        Py_ssize_t i = 1;
        while (i < len) {
            if (s[i] == '\\') {
                i += 2;
                continue;
            }
            if (s[i] == '"') {
                /* Next must be ": " */
                if (i + 2 < len && s[i + 1] == ':' && s[i + 2] == ' ')
                    return i + 1;
                return -1;
            }
            i++;
        }
        return -1;
    }

    /* Bare key: use memchr to find ':' quickly, then verify */
    const char *p = s;
    Py_ssize_t remaining = len - 1;  /* need at least ': ' so -1 */

    while (remaining > 0) {
        const char *colon = (const char *)memchr(p, ':', (size_t)remaining);
        if (!colon) return -1;

        Py_ssize_t pos = colon - s;
        /* Check ": " and valid bare key chars before */
        if (pos + 1 < len && colon[1] == ' ') {
            /* Verify all chars before colon are valid key chars */
            int valid = 1;
            for (Py_ssize_t j = 0; j < pos; j++) {
                char c = s[j];
                if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                      (c >= '0' && c <= '9') || c == '_' || c == '-')) {
                    valid = 0;
                    break;
                }
            }
            if (valid && pos > 0)
                return pos;
        }
        /* Move past this colon */
        p = colon + 1;
        remaining = len - (p - s) - 1;
    }
    return -1;
}

/* Check if a line is a thematic break (--- or more hyphens) */
int
is_thematic_break(const JMDLine *line)
{
    if (line->heading_depth != 0) return 0;
    if (line->content_len < 3) return 0;
    const char *s = line->content;
    Py_ssize_t len = line->content_len;
    for (Py_ssize_t i = 0; i < len; i++) {
        if (s[i] != '-') return 0;
    }
    return 1;
}
