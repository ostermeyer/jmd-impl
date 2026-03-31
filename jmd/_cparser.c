/*
 * _cparser.c — C-accelerated JMD parser (CPython extension).
 *
 * Copyright (c) 2026 Andreas Ostermeyer <andreas@ostermeyer.de>
 * Licensed under AGPL-3.0 — see LICENSE-CODE for details.
 *
 * Provides a drop-in replacement for JMDParser().parse() that operates
 * directly on Python objects with no intermediate data structure.
 *
 * Public API:
 *     from jmd._cparser import parse
 *     result = parse(jmd_string)             # returns dict or list
 *
 * See also: _cserializer.c for the C-accelerated serializer.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <string.h>
#include <stdlib.h>
#include <ctype.h>
#include <errno.h>
#include <limits.h>

/* ------------------------------------------------------------------ */
/* Key interning cache                                                  */
/* ------------------------------------------------------------------ */

#define KEY_CACHE_SIZE 128   /* must be power of 2 */
#define KEY_CACHE_MASK (KEY_CACHE_SIZE - 1)

typedef struct {
    const char *raw;
    Py_ssize_t  len;
    PyObject   *pyobj;        /* borrowed ref held alive by the cache */
} KeyCacheEntry;

static KeyCacheEntry key_cache[KEY_CACHE_SIZE];

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

static PyObject *
intern_key(const char *raw, Py_ssize_t len)
{
    unsigned int idx = key_hash(raw, len) & KEY_CACHE_MASK;
    KeyCacheEntry *e = &key_cache[idx];
    if (e->pyobj && e->len == len && memcmp(e->raw, raw, (size_t)len) == 0) {
        Py_INCREF(e->pyobj);
        return e->pyobj;
    }
    PyObject *obj = PyUnicode_FromStringAndSize(raw, len);
    if (!obj) return NULL;
    /* Evict old entry */
    Py_XDECREF(e->pyobj);
    e->raw = raw;
    e->len = len;
    e->pyobj = obj;
    Py_INCREF(obj);   /* one ref for cache, one for caller */
    return obj;
}

/* ------------------------------------------------------------------ */
/* Line token                                                          */
/* ------------------------------------------------------------------ */

typedef struct {
    const char *raw;        /* pointer into source (not owned)         */
    Py_ssize_t  raw_len;    /* length of raw line                      */
    const char *content;    /* pointer to stripped/content portion      */
    Py_ssize_t  content_len;
    int         heading_depth; /* -1 = blank, 0 = normal, 1..6 = heading */
    int         number;        /* 1-based line number                     */
} JMDLine;

/* ------------------------------------------------------------------ */
/* Dynamic array of lines                                              */
/* ------------------------------------------------------------------ */

typedef struct {
    JMDLine    *items;
    Py_ssize_t  len;
    Py_ssize_t  cap;
} LineArray;

static int
linearray_init(LineArray *la)
{
    la->cap = 256;
    la->len = 0;
    la->items = (JMDLine *)PyMem_Malloc(la->cap * sizeof(JMDLine));
    return la->items != NULL;
}

static void
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

static int
tokenize(const char *source, Py_ssize_t source_len, LineArray *lines)
{
    Py_ssize_t pos = 0;
    int lineno = 0;

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

static PyObject *
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
static PyObject *
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

static Py_ssize_t
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
static int
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

/* Check if items[-1] is a dict with any value that is a dict or list */
static int
last_item_has_nested(PyObject *items)
{
    Py_ssize_t n = PyList_GET_SIZE(items);
    if (n == 0) return 0;
    PyObject *last = PyList_GET_ITEM(items, n - 1);
    if (!PyDict_Check(last)) return 0;

    PyObject *key, *value;
    Py_ssize_t pos = 0;
    while (PyDict_Next(last, &pos, &key, &value)) {
        if (PyDict_Check(value) || PyList_Check(value))
            return 1;
    }
    return 0;
}

/* ------------------------------------------------------------------ */
/* Parser state                                                        */
/* ------------------------------------------------------------------ */

typedef struct {
    LineArray  *lines;
    Py_ssize_t  pos;
} ParserState;

/* Forward declarations */
static PyObject *parse_object_body(ParserState *st, int depth);
static PyObject *parse_array_body(ParserState *st, int depth);
static int parse_heading_into(ParserState *st, PyObject *obj, int depth);
static PyObject *parse_item_object(ParserState *st, int array_depth,
                                    PyObject *initial);

/* ------------------------------------------------------------------ */
/* Blockquote parser                                                   */
/* ------------------------------------------------------------------ */

static PyObject *
parse_blockquote(ParserState *st)
{
    /* Collect raw C strings, then build a single joined result.
       Avoid PyList + join + strip overhead from the previous version. */
    const char *parts[256];
    Py_ssize_t  part_lens[256];
    Py_ssize_t  nparts = 0;
    Py_ssize_t  total_len = 0;

    while (st->pos < st->lines->len) {
        JMDLine *line = &st->lines->items[st->pos];
        if (line->heading_depth != 0)
            break;

        /* Strip whitespace from raw */
        const char *raw = line->raw;
        Py_ssize_t raw_len = line->raw_len;
        while (raw_len > 0 && (*raw == ' ' || *raw == '\t')) {
            raw++;
            raw_len--;
        }
        while (raw_len > 0 && (raw[raw_len - 1] == ' ' || raw[raw_len - 1] == '\t'))
            raw_len--;

        if (raw_len == 1 && raw[0] == '>') {
            /* Paragraph break → empty string part */
            if (nparts < 256) {
                parts[nparts] = "";
                part_lens[nparts] = 0;
                nparts++;
            }
            st->pos++;
        } else if (raw_len >= 2 && raw[0] == '>' && raw[1] == ' ') {
            if (nparts < 256) {
                parts[nparts] = raw + 2;
                part_lens[nparts] = raw_len - 2;
                total_len += raw_len - 2;
                nparts++;
            }
            st->pos++;
        } else {
            break;
        }
    }

    if (nparts == 0) {
        return PyUnicode_FromStringAndSize("", 0);
    }

    /* Strip leading/trailing empty parts (equivalent to .strip("\n")) */
    Py_ssize_t start = 0, end = nparts;
    while (start < end && part_lens[start] == 0) start++;
    while (end > start && part_lens[end - 1] == 0) end--;

    if (start >= end) {
        return PyUnicode_FromStringAndSize("", 0);
    }

    /* Calculate total output size (parts + newlines between) */
    total_len = 0;
    for (Py_ssize_t i = start; i < end; i++) {
        total_len += part_lens[i];
        if (i < end - 1) total_len++;  /* newline separator */
    }

    /* Build result string directly */
    char *buf = (char *)PyMem_Malloc((size_t)total_len);
    if (!buf) { PyErr_NoMemory(); return NULL; }

    Py_ssize_t off = 0;
    for (Py_ssize_t i = start; i < end; i++) {
        if (part_lens[i] > 0) {
            memcpy(buf + off, parts[i], (size_t)part_lens[i]);
            off += part_lens[i];
        }
        if (i < end - 1) {
            buf[off++] = '\n';
        }
    }

    PyObject *result = PyUnicode_FromStringAndSize(buf, total_len);
    PyMem_Free(buf);
    return result;
}

/* ------------------------------------------------------------------ */
/* Check if next non-blank line starts with '>'                        */
/* ------------------------------------------------------------------ */

static int
next_is_blockquote(ParserState *st)
{
    if (st->pos >= st->lines->len) return 0;
    JMDLine *nxt = &st->lines->items[st->pos];
    if (nxt->heading_depth != 0) return 0;

    /* Check stripped raw starts with '>' */
    const char *raw = nxt->raw;
    Py_ssize_t raw_len = nxt->raw_len;
    while (raw_len > 0 && (*raw == ' ' || *raw == '\t')) {
        raw++;
        raw_len--;
    }
    return (raw_len > 0 && raw[0] == '>');
}

/* ------------------------------------------------------------------ */
/* Inline helper: find ": " using memchr (for non-kv contexts)         */
/* ------------------------------------------------------------------ */

static Py_ssize_t
find_colon_space(const char *s, Py_ssize_t len)
{
    /* Find first occurrence of ": " in s */
    const char *p = s;
    Py_ssize_t remaining = len;
    while (remaining > 1) {
        const char *colon = (const char *)memchr(p, ':', (size_t)(remaining - 1));
        if (!colon) return -1;
        if (colon[1] == ' ') return (Py_ssize_t)(colon - s);
        Py_ssize_t skip = (colon - p) + 1;
        p += skip;
        remaining -= skip;
    }
    return -1;
}

/* ------------------------------------------------------------------ */
/* Object body parser                                                  */
/* ------------------------------------------------------------------ */

static PyObject *
parse_object_body(ParserState *st, int depth)
{
    PyObject *obj = PyDict_New();
    if (!obj) return NULL;

    LineArray *lines = st->lines;
    Py_ssize_t lines_len = lines->len;
    Py_ssize_t pos = st->pos;
    int depth_plus_1 = depth + 1;

    while (pos < lines_len) {
        JMDLine *line = &lines->items[pos];
        int hd = line->heading_depth;

        /* Blank line handling */
        if (hd == -1) {
            Py_ssize_t peek = pos + 1;
            while (peek < lines_len && lines->items[peek].heading_depth == -1)
                peek++;
            if (peek < lines_len) {
                JMDLine *nxt = &lines->items[peek];
                if (nxt->heading_depth > 0) {
                    pos++;
                    continue;
                }
            }
            if (depth == 1) {
                pos++;
                continue;
            } else {
                break;
            }
        }

        /* Heading at depth or shallower: scope ends */
        if (hd > 0 && hd <= depth)
            break;

        /* Heading at depth+1: child scope */
        if (hd == depth_plus_1) {
            st->pos = pos;
            if (parse_heading_into(st, obj, depth_plus_1) < 0) {
                Py_DECREF(obj);
                return NULL;
            }
            pos = st->pos;
            continue;
        }

        /* Non-heading line (hd == 0) */
        const char *content = line->content;
        Py_ssize_t content_len = line->content_len;

        /* Find ": " using memchr */
        Py_ssize_t colon_pos = find_colon_space(content, content_len);

        if (colon_pos >= 0) {
            PyObject *key = parse_key(content, colon_pos);
            if (!key) { Py_DECREF(obj); return NULL; }

            const char *val_str = content + colon_pos + 2;
            Py_ssize_t val_len = content_len - colon_pos - 2;

            if (val_len == 0) {
                /* Empty value — check for blockquote */
                pos++;
                st->pos = pos;
                if (next_is_blockquote(st)) {
                    PyObject *val = parse_blockquote(st);
                    if (!val) { Py_DECREF(key); Py_DECREF(obj); return NULL; }
                    if (PyDict_SetItem(obj, key, val) < 0) {
                        Py_DECREF(val);
                        Py_DECREF(key);
                        Py_DECREF(obj);
                        return NULL;
                    }
                    Py_DECREF(val);
                    pos = st->pos;
                } else {
                    PyObject *val = PyUnicode_FromStringAndSize("", 0);
                    if (!val) { Py_DECREF(key); Py_DECREF(obj); return NULL; }
                    if (PyDict_SetItem(obj, key, val) < 0) {
                        Py_DECREF(val);
                        Py_DECREF(key);
                        Py_DECREF(obj);
                        return NULL;
                    }
                    Py_DECREF(val);
                }
            } else {
                PyObject *val = parse_scalar(val_str, val_len);
                if (!val) { Py_DECREF(key); Py_DECREF(obj); return NULL; }
                if (PyDict_SetItem(obj, key, val) < 0) {
                    Py_DECREF(val);
                    Py_DECREF(key);
                    Py_DECREF(obj);
                    return NULL;
                }
                Py_DECREF(val);
                pos++;
            }
            Py_DECREF(key);
            continue;
        }

        /* key: (colon at end of line, no space after) — check for blockquote */
        if (content_len > 0 && content[content_len - 1] == ':') {
            PyObject *key = parse_key(content, content_len - 1);
            if (!key) { Py_DECREF(obj); return NULL; }
            pos++;
            st->pos = pos;
            if (next_is_blockquote(st)) {
                PyObject *val = parse_blockquote(st);
                if (!val) { Py_DECREF(key); Py_DECREF(obj); return NULL; }
                if (PyDict_SetItem(obj, key, val) < 0) {
                    Py_DECREF(val);
                    Py_DECREF(key);
                    Py_DECREF(obj);
                    return NULL;
                }
                Py_DECREF(val);
                pos = st->pos;
            } else {
                PyObject *val = PyUnicode_FromStringAndSize("", 0);
                if (!val) { Py_DECREF(key); Py_DECREF(obj); return NULL; }
                if (PyDict_SetItem(obj, key, val) < 0) {
                    Py_DECREF(val);
                    Py_DECREF(key);
                    Py_DECREF(obj);
                    return NULL;
                }
                Py_DECREF(val);
            }
            Py_DECREF(key);
            continue;
        }

        /* Unrecognized line: break */
        break;
    }

    st->pos = pos;
    return obj;
}

/* ------------------------------------------------------------------ */
/* Heading into object                                                 */
/* ------------------------------------------------------------------ */

static int
parse_heading_into(ParserState *st, PyObject *obj, int depth)
{
    if (st->pos >= st->lines->len)
        return 0;

    JMDLine *line = &st->lines->items[st->pos];
    const char *content = line->content;
    Py_ssize_t content_len = line->content_len;

    /* Depth-qualified array item: ## - */
    if (content_len == 1 && content[0] == '-')
        return 0;

    /* Anonymous sub-array: ## [] */
    if (content_len == 2 && content[0] == '[' && content[1] == ']')
        return 0;

    st->pos++;

    /* Array heading: ## key[] */
    if (content_len >= 3 && content[content_len - 2] == '['
        && content[content_len - 1] == ']')
    {
        PyObject *key = parse_key(content, content_len - 2);
        if (!key) return -1;
        PyObject *arr = parse_array_body(st, depth);
        if (!arr) { Py_DECREF(key); return -1; }
        int rc = PyDict_SetItem(obj, key, arr);
        Py_DECREF(key);
        Py_DECREF(arr);
        return rc < 0 ? -1 : 0;
    }

    /* Scalar heading: ## key: value — use memchr */
    Py_ssize_t colon_pos = find_colon_space(content, content_len);

    if (colon_pos >= 0) {
        PyObject *key = parse_key(content, colon_pos);
        if (!key) return -1;
        Py_ssize_t val_len = content_len - colon_pos - 2;
        if (val_len == 0) {
            /* Check for blockquote */
            if (next_is_blockquote(st)) {
                PyObject *val = parse_blockquote(st);
                if (!val) { Py_DECREF(key); return -1; }
                int rc = PyDict_SetItem(obj, key, val);
                Py_DECREF(key);
                Py_DECREF(val);
                return rc < 0 ? -1 : 0;
            } else {
                PyObject *val = PyUnicode_FromStringAndSize("", 0);
                if (!val) { Py_DECREF(key); return -1; }
                int rc = PyDict_SetItem(obj, key, val);
                Py_DECREF(key);
                Py_DECREF(val);
                return rc < 0 ? -1 : 0;
            }
        } else {
            PyObject *val = parse_scalar(content + colon_pos + 2, val_len);
            if (!val) { Py_DECREF(key); return -1; }
            int rc = PyDict_SetItem(obj, key, val);
            Py_DECREF(key);
            Py_DECREF(val);
            return rc < 0 ? -1 : 0;
        }
    }

    /* Scalar heading with trailing colon: ## key: */
    if (content_len > 0 && content[content_len - 1] == ':') {
        /* Make sure there's no ": " in content (already checked above) */
        PyObject *key = parse_key(content, content_len - 1);
        if (!key) return -1;
        if (next_is_blockquote(st)) {
            PyObject *val = parse_blockquote(st);
            if (!val) { Py_DECREF(key); return -1; }
            int rc = PyDict_SetItem(obj, key, val);
            Py_DECREF(key);
            Py_DECREF(val);
            return rc < 0 ? -1 : 0;
        } else {
            PyObject *val = PyUnicode_FromStringAndSize("", 0);
            if (!val) { Py_DECREF(key); return -1; }
            int rc = PyDict_SetItem(obj, key, val);
            Py_DECREF(key);
            Py_DECREF(val);
            return rc < 0 ? -1 : 0;
        }
    }

    /* Object heading: ## key */
    PyObject *key = parse_key(content, content_len);
    if (!key) return -1;
    PyObject *child = parse_object_body(st, depth);
    if (!child) { Py_DECREF(key); return -1; }
    int rc = PyDict_SetItem(obj, key, child);
    Py_DECREF(key);
    Py_DECREF(child);
    return rc < 0 ? -1 : 0;
}

/* ------------------------------------------------------------------ */
/* Check if content is a dash item ("- " prefix or bare "-")           */
/* ------------------------------------------------------------------ */

static int
is_dash_item(const char *content, Py_ssize_t len)
{
    if (len == 1 && content[0] == '-') return 1;
    if (len > 1 && content[0] == '-' && content[1] == ' ') return 1;
    return 0;
}

/* ------------------------------------------------------------------ */
/* Helper: parse "- key: value" item inline (avoids double find_kv)    */
/* Returns new dict with initial k:v, or NULL on error.                */
/* ------------------------------------------------------------------ */

static PyObject *
parse_dash_kv_item(const char *after, Py_ssize_t after_len,
                   Py_ssize_t split)
{
    PyObject *initial = PyDict_New();
    if (!initial) return NULL;
    PyObject *k = parse_key(after, split);
    PyObject *v = parse_scalar(after + split + 2, after_len - split - 2);
    if (!k || !v) {
        Py_XDECREF(k);
        Py_XDECREF(v);
        Py_DECREF(initial);
        return NULL;
    }
    if (PyDict_SetItem(initial, k, v) < 0) {
        Py_DECREF(k);
        Py_DECREF(v);
        Py_DECREF(initial);
        return NULL;
    }
    Py_DECREF(k);
    Py_DECREF(v);
    return initial;
}

/* ------------------------------------------------------------------ */
/* Array body parser                                                   */
/* ------------------------------------------------------------------ */

static PyObject *
parse_array_body(ParserState *st, int depth)
{
    PyObject *items = PyList_New(0);
    if (!items) return NULL;

    LineArray *lines = st->lines;
    Py_ssize_t lines_len = lines->len;
    Py_ssize_t pos = st->pos;
    int depth_plus_1 = depth + 1;

    while (pos < lines_len) {
        JMDLine *line = &lines->items[pos];
        int hd = line->heading_depth;

        /* Blank line */
        if (hd == -1) {
            Py_ssize_t peek = pos + 1;
            while (peek < lines_len && lines->items[peek].heading_depth == -1)
                peek++;
            if (peek < lines_len) {
                JMDLine *nxt = &lines->items[peek];
                int nhd = nxt->heading_depth;

                int is_item = 0;
                /* Non-heading dash item */
                if (nhd == 0 && is_dash_item(nxt->content, nxt->content_len))
                    is_item = 1;
                /* Same-depth heading dash item */
                if (nhd == depth && is_dash_item(nxt->content, nxt->content_len))
                    is_item = 1;
                /* Child-depth heading: [], -, or - ... */
                if (nhd == depth_plus_1) {
                    if ((nxt->content_len == 2 && nxt->content[0] == '['
                         && nxt->content[1] == ']')
                        || is_dash_item(nxt->content, nxt->content_len))
                        is_item = 1;
                }

                if (is_item) {
                    pos++;
                    continue;
                }

                /* Thematic break check */
                if (is_thematic_break(nxt) && last_item_has_nested(items)) {
                    pos++;
                    continue;
                }
            }
            break;
        }

        const char *content = line->content;
        Py_ssize_t content_len = line->content_len;

        /* Heading at same depth or shallower */
        if (hd > 0 && hd <= depth) {
            /* Depth-qualified item at same depth: ## - */
            if (hd == depth && content_len == 1 && content[0] == '-') {
                pos++;
                st->pos = pos;
                PyObject *item_obj = parse_item_object(st, depth, NULL);
                if (!item_obj) { Py_DECREF(items); return NULL; }
                if (PyList_Append(items, item_obj) < 0) {
                    Py_DECREF(item_obj);
                    Py_DECREF(items);
                    return NULL;
                }
                Py_DECREF(item_obj);
                pos = st->pos;
                continue;
            }
            if (hd == depth && content_len > 1 && content[0] == '-'
                && content[1] == ' ')
            {
                const char *after = content + 2;
                Py_ssize_t after_len = content_len - 2;
                /* Single call to find_kv_split (eliminates double call) */
                Py_ssize_t split = find_kv_split(after, after_len);
                if (split >= 0) {
                    pos++;
                    st->pos = pos;
                    PyObject *initial = parse_dash_kv_item(after, after_len, split);
                    if (!initial) { Py_DECREF(items); return NULL; }
                    PyObject *item_obj = parse_item_object(st, depth, initial);
                    Py_DECREF(initial);
                    if (!item_obj) { Py_DECREF(items); return NULL; }
                    if (PyList_Append(items, item_obj) < 0) {
                        Py_DECREF(item_obj);
                        Py_DECREF(items);
                        return NULL;
                    }
                    Py_DECREF(item_obj);
                    pos = st->pos;
                    continue;
                }
            }
            break;
        }

        /* Sub-array heading at depth+1: ### [] */
        if (hd == depth_plus_1 && content_len == 2
            && content[0] == '[' && content[1] == ']')
        {
            pos++;
            st->pos = pos;
            PyObject *sub = parse_array_body(st, depth_plus_1);
            if (!sub) { Py_DECREF(items); return NULL; }
            if (PyList_Append(items, sub) < 0) {
                Py_DECREF(sub);
                Py_DECREF(items);
                return NULL;
            }
            Py_DECREF(sub);
            pos = st->pos;
            continue;
        }

        /* Depth-qualified item at depth+1 */
        if (hd == depth_plus_1 && content_len == 1 && content[0] == '-') {
            pos++;
            st->pos = pos;
            PyObject *item_obj = parse_item_object(st, depth, NULL);
            if (!item_obj) { Py_DECREF(items); return NULL; }
            if (PyList_Append(items, item_obj) < 0) {
                Py_DECREF(item_obj);
                Py_DECREF(items);
                return NULL;
            }
            Py_DECREF(item_obj);
            pos = st->pos;
            continue;
        }
        if (hd == depth_plus_1 && content_len > 1 && content[0] == '-'
            && content[1] == ' ')
        {
            const char *after = content + 2;
            Py_ssize_t after_len = content_len - 2;
            Py_ssize_t split = find_kv_split(after, after_len);
            if (split >= 0) {
                pos++;
                st->pos = pos;
                PyObject *initial = parse_dash_kv_item(after, after_len, split);
                if (!initial) { Py_DECREF(items); return NULL; }
                PyObject *item_obj = parse_item_object(st, depth, initial);
                Py_DECREF(initial);
                if (!item_obj) { Py_DECREF(items); return NULL; }
                if (PyList_Append(items, item_obj) < 0) {
                    Py_DECREF(item_obj);
                    Py_DECREF(items);
                    return NULL;
                }
                Py_DECREF(item_obj);
                pos = st->pos;
                continue;
            }
        }

        /* Heading at depth+1 that is not [], -, or - ... : stop */
        if (hd == depth_plus_1)
            break;

        /* Deeper heading: stop */
        if (hd > depth_plus_1)
            break;

        /* Non-heading lines (hd == 0) */

        /* Bare `-` */
        if (content_len == 1 && content[0] == '-') {
            pos++;
            st->pos = pos;
            PyObject *item_obj = parse_item_object(st, depth, NULL);
            if (!item_obj) { Py_DECREF(items); return NULL; }
            if (PyList_Append(items, item_obj) < 0) {
                Py_DECREF(item_obj);
                Py_DECREF(items);
                return NULL;
            }
            Py_DECREF(item_obj);
            pos = st->pos;
            continue;
        }

        /* `- ...`: object item or scalar item */
        if (content_len > 1 && content[0] == '-' && content[1] == ' ') {
            const char *after = content + 2;
            Py_ssize_t after_len = content_len - 2;

            /* Single find_kv_split call — no separate is_kv_content */
            Py_ssize_t split = find_kv_split(after, after_len);
            if (split >= 0) {
                /* Object item with first field */
                pos++;
                st->pos = pos;
                PyObject *initial = parse_dash_kv_item(after, after_len, split);
                if (!initial) { Py_DECREF(items); return NULL; }
                PyObject *item_obj = parse_item_object(st, depth, initial);
                Py_DECREF(initial);
                if (!item_obj) { Py_DECREF(items); return NULL; }
                if (PyList_Append(items, item_obj) < 0) {
                    Py_DECREF(item_obj);
                    Py_DECREF(items);
                    return NULL;
                }
                Py_DECREF(item_obj);
                pos = st->pos;
            } else {
                /* Scalar item */
                PyObject *val = parse_scalar(after, after_len);
                if (!val) { Py_DECREF(items); return NULL; }
                if (PyList_Append(items, val) < 0) {
                    Py_DECREF(val);
                    Py_DECREF(items);
                    return NULL;
                }
                Py_DECREF(val);
                pos++;
            }
            continue;
        }

        /* Thematic break */
        if (is_thematic_break(line)) {
            if (last_item_has_nested(items)) {
                pos++;
                continue;
            }
            break;
        }

        break;
    }

    st->pos = pos;
    return items;
}

/* ------------------------------------------------------------------ */
/* Item object parser                                                  */
/* ------------------------------------------------------------------ */

static PyObject *
parse_item_object(ParserState *st, int array_depth, PyObject *initial)
{
    PyObject *obj;
    if (initial) {
        obj = PyDict_Copy(initial);
    } else {
        obj = PyDict_New();
    }
    if (!obj) return NULL;

    int child_depth = array_depth + 1;
    LineArray *lines = st->lines;
    Py_ssize_t lines_len = lines->len;
    Py_ssize_t pos = st->pos;

    /* Phase 1: consume indented continuation fields (2+ spaces + key: value) */
    if (pos < lines_len && lines->items[pos].raw_len > 0
        && lines->items[pos].raw[0] == ' ')
    {
        while (pos < lines_len) {
            JMDLine *line = &lines->items[pos];

            /* Check for indented continuation field */
            if (line->raw_len >= 3 && line->raw[0] == ' ' && line->raw[1] == ' ') {
                /* lstrip spaces */
                const char *stripped = line->raw;
                Py_ssize_t stripped_len = line->raw_len;
                while (stripped_len > 0 && *stripped == ' ') {
                    stripped++;
                    stripped_len--;
                }
                /* Also strip trailing whitespace */
                while (stripped_len > 0 &&
                       (stripped[stripped_len - 1] == ' ' ||
                        stripped[stripped_len - 1] == '\t' ||
                        stripped[stripped_len - 1] == '\r'))
                    stripped_len--;

                /* Single find_kv_split call */
                Py_ssize_t split = find_kv_split(stripped, stripped_len);
                if (split >= 0) {
                    PyObject *k = parse_key(stripped, split);
                    PyObject *v = parse_scalar(stripped + split + 2,
                                                stripped_len - split - 2);
                    if (!k || !v) {
                        Py_XDECREF(k);
                        Py_XDECREF(v);
                        Py_DECREF(obj);
                        return NULL;
                    }
                    if (PyDict_SetItem(obj, k, v) < 0) {
                        Py_DECREF(k);
                        Py_DECREF(v);
                        Py_DECREF(obj);
                        return NULL;
                    }
                    Py_DECREF(k);
                    Py_DECREF(v);
                    pos++;
                    continue;
                }
            }

            /* Blank line between indented fields: peek ahead */
            if (line->heading_depth == -1) {
                Py_ssize_t peek = pos + 1;
                while (peek < lines_len && lines->items[peek].heading_depth == -1)
                    peek++;
                if (peek < lines_len) {
                    JMDLine *nxt_line = &lines->items[peek];
                    /* If next non-blank is indented, skip blank */
                    if (nxt_line->raw_len >= 3
                        && nxt_line->raw[0] == ' ' && nxt_line->raw[1] == ' ')
                    {
                        /* lstrip spaces from nxt */
                        const char *ns = nxt_line->raw;
                        Py_ssize_t ns_len = nxt_line->raw_len;
                        while (ns_len > 0 && *ns == ' ') { ns++; ns_len--; }
                        while (ns_len > 0 &&
                               (ns[ns_len - 1] == ' ' || ns[ns_len - 1] == '\t'))
                            ns_len--;
                        if (find_kv_split(ns, ns_len) >= 0) {
                            pos++;
                            continue;
                        }
                    }
                    /* If next is a child heading, skip blank */
                    if (nxt_line->heading_depth == child_depth) {
                        pos++;
                        continue;
                    }
                }
                break;
            }

            /* Thematic break ends the current item */
            if (is_thematic_break(line))
                break;

            /* After indented fields, also accept bare fields and headings */
            break;
        }
    }

    st->pos = pos;

    /* Phase 2: consume bare fields and child headings */
    while (pos < lines_len) {
        JMDLine *line = &lines->items[pos];

        /* Blank line: peek ahead */
        if (line->heading_depth == -1) {
            Py_ssize_t peek = pos + 1;
            while (peek < lines_len && lines->items[peek].heading_depth == -1)
                peek++;
            if (peek < lines_len) {
                JMDLine *nxt = &lines->items[peek];
                if (nxt->heading_depth == child_depth) {
                    pos++;
                    st->pos = pos;
                    continue;
                }
            }
            break;
        }

        /* Heading at array_depth or shallower */
        if (line->heading_depth > 0 && line->heading_depth <= array_depth)
            break;

        /* Heading at child_depth */
        if (line->heading_depth == child_depth) {
            /* Item-start headings: stop */
            if ((line->content_len == 1 && line->content[0] == '-')
                || (line->content_len == 2 && line->content[0] == '['
                    && line->content[1] == ']')
                || (line->content_len > 1 && line->content[0] == '-'
                    && line->content[1] == ' '))
            {
                break;
            }
            st->pos = pos;
            if (parse_heading_into(st, obj, child_depth) < 0) {
                Py_DECREF(obj);
                return NULL;
            }
            pos = st->pos;
            continue;
        }

        /* Heading deeper than child: stop */
        if (line->heading_depth > child_depth)
            break;

        /* Thematic break: stop */
        if (is_thematic_break(line))
            break;

        /* Non-heading (hd == 0) */
        if (line->heading_depth == 0) {
            const char *content = line->content;
            Py_ssize_t content_len = line->content_len;

            /* Next item marker: stop */
            if (is_dash_item(content, content_len))
                break;

            /* Bare field: key: value — use memchr */
            Py_ssize_t colon_pos = find_colon_space(content, content_len);
            if (colon_pos >= 0) {
                PyObject *k = parse_key(content, colon_pos);
                PyObject *v = parse_scalar(content + colon_pos + 2,
                                            content_len - colon_pos - 2);
                if (!k || !v) {
                    Py_XDECREF(k);
                    Py_XDECREF(v);
                    Py_DECREF(obj);
                    return NULL;
                }
                if (PyDict_SetItem(obj, k, v) < 0) {
                    Py_DECREF(k);
                    Py_DECREF(v);
                    Py_DECREF(obj);
                    return NULL;
                }
                Py_DECREF(k);
                Py_DECREF(v);
                pos++;
                continue;
            }
        }

        break;
    }

    st->pos = pos;
    return obj;
}

/* ------------------------------------------------------------------ */
/* Top-level parse function                                            */
/* ------------------------------------------------------------------ */

static PyObject *
jmd_parse(PyObject *self, PyObject *args)
{
    (void)self;
    const char *source;
    Py_ssize_t source_len;

    if (!PyArg_ParseTuple(args, "s#", &source, &source_len))
        return NULL;

    LineArray lines;
    if (!linearray_init(&lines)) {
        PyErr_NoMemory();
        return NULL;
    }

    if (!tokenize(source, source_len, &lines)) {
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
        linearray_free(&lines);
        PyErr_SetString(PyExc_ValueError, "No root heading found");
        return NULL;
    }

    JMDLine *first = &lines.items[st.pos];

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

    linearray_free(&lines);
    return result;
}

/* ------------------------------------------------------------------ */
/* Module definition                                                   */
/* ------------------------------------------------------------------ */

static PyMethodDef cparser_methods[] = {
    {"parse", jmd_parse, METH_VARARGS,
     "Parse a JMD document string into a Python dict or list."},
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
    /* Clear key cache */
    memset(key_cache, 0, sizeof(key_cache));
    return PyModule_Create(&cparser_module);
}
