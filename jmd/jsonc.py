# SPDX-License-Identifier: Apache-2.0
"""JSONC ↔ JMD conversion — JMD over JSONC companion specification.

Implements the lossless mapping defined in the companion document
``jmd-over-jsonc.md`` (Draft 0.1) in the jmd-spec repository: JSONC
(JSON with comments and trailing commas) round-trips through a JMD
intermediate that carries comments
via two extension markers (``#/`` line, ``#*`` block) and frames its
frontmatter explicitly with mandatory ``---`` delimiters.

Public API:

* :func:`parse_jsonc` — JSONC source → :class:`JsoncDoc`.
* :func:`serialize_jsonc` — :class:`JsoncDoc` → JSONC source.
* :func:`to_jmd` — :class:`JsoncDoc` → JMD-text.
* :func:`from_jmd` — JMD-text → :class:`JsoncDoc`.

The :class:`JsoncDoc` AST preserves three things JSON itself cannot
carry: comment positions in their parent's content sequence, comment
text, and the root-section label that the JMD form needs and the
JSONC form does not. The label is supplied on parse_jsonc by the
caller (typically the file's type — ``Settings``, ``ColorScheme``,
…).

This module is the public face of the conversion subsystem. The
actual work is split across leaf modules so each stays focused and
independently reviewable:

* :mod:`._jsonc_ast` — dataclasses (Field, ScalarArray, …).
* :mod:`._jsonc_parse_jsonc` — JSONC source → AST.
* :mod:`._jsonc_emit_jmd` — AST → JMD-text.
* :mod:`._jsonc_parse_jmd` — JMD-text → AST.
* :mod:`._jsonc_emit_jsonc` — AST → JSONC source.

Pure Python, stdlib only — unlike :mod:`jmd.xml`, this companion needs
no optional extra.
"""

from __future__ import annotations

from ._jsonc_ast import (
    BlockComment,
    Element,
    Field,
    JsoncDoc,
    LineComment,
    ObjectArray,
    ScalarArray,
    Section,
)
from ._jsonc_emit_jmd import to_jmd
from ._jsonc_emit_jsonc import serialize_jsonc
from ._jsonc_parse_jmd import JsoncJmdParseError, from_jmd
from ._jsonc_parse_jsonc import JsoncParseError, parse_jsonc

__all__ = [
    "BlockComment",
    "Element",
    "Field",
    "JsoncJmdParseError",
    "JsoncDoc",
    "JsoncParseError",
    "LineComment",
    "ObjectArray",
    "ScalarArray",
    "Section",
    "from_jmd",
    "parse_jsonc",
    "serialize_jsonc",
    "to_jmd",
]
