# Changelog

All notable changes to `jmd-format` are documented here. The project
follows [Semantic Versioning](https://semver.org/); while on `0.x`, minor
releases may carry behavioral (breaking) changes.

## [Unreleased]

### Fixed

Level-pops (§8.6) into **object** scopes were unimplemented across all
three parser backends. §3.2a extends the level-pop to every depth,
including `#` at depth 1, but §8.6 documented only the array case, and
all three backends read it that way. No serializer emits an object-scope
level-pop — a field after a nested object is written as a scalar heading
(§7.2) — so round-trip testing never reached the path. The inputs that
do reach it are hand-written and model-generated documents.

- **Pure-Python batch parser** dropped fields silently after `#` returned
  to the root, and raised a bare `IndexError` from `parse_key("")` for
  every other object-scope pop — an unstructured crash, not a
  `JMDParseError`. Over-deep pops raised `prose_in_body`.
- **C batch parser** had the identical gap in `parse_object_body`; the
  fix mirrors the existing array-scope level-pop in `_cparser_array.c`.
  **Not yet compiled or executed** — no C toolchain was available in the
  environment where this was written.
- **Streaming parser** closed scopes at *and deeper than* the pop depth
  rather than deeper only, so the pop target itself was closed. This also
  broke the *array* case: `conformance/data/array-level-pop.jmd`, a
  canonical fixture, raised `invalid_structure`. `tests/test_conformance.py`
  never runs fixtures through the streaming backend, so it went unnoticed.

Degenerate pops are now no-ops per §8.6: a pop at the current depth, and
a pop deeper than any established scope, close nothing and are accepted.

Two further streaming defects, surfaced by the new fixture coverage below:

- A thematic break inside an array body closed the open item scope, so an
  indented continuation line after `---` was rejected. §8.6 makes `---`
  pure decoration within an array body, with no structural effect. 0.7.0
  made this change for the batch parsers ("`---` inside an array body is
  now decoration", below); the streaming backend never received it.
- An INDENT outside an array item raised `invalid_indentation`, which is
  not a spec error kind. The batch parsers and the must-fail fixtures use
  `prose_in_body` (§3.6.2, §11.2).

### Added

- `tests/test_conformance.py` now runs every fixture through the streaming
  backend — acceptance and `DOCUMENT_START`/`DOCUMENT_END` framing for the
  corpus, error-kind equality for `must-fail`. The module previously did
  not reference the streaming parser at all. These tests do not rebuild a
  value from the event stream: a nested container belonging to an array
  item is emitted after that item's `ITEM_END`, so folding events back
  into a document needs an ordering assumption §18 does not state.
- `tests/test_streaming.py::TestLevelPop` asserts full event sequences for
  object- and array-scope level-pops.
- `tests/test_spec_v035.py` gains five §8.6 parse cases, run across all
  three parser backends, so the C fix is covered once the extension builds.

## [0.8.0] — 2026-07-29

Full cross-backend qualification against **JMD Specification v0.3.5**,
including the current errata at `ef07178a4f16`. The C accelerator and the
pure-Python implementation now satisfy the same expanded conformance,
robustness, serializer, and streaming evidence.

### Changed (breaking)

- `JMDStreamParser.process_line()` now returns every event completed by the
  current source line instead of buffering the document until `finish()`.
  Direct push consumers must consume each returned list; `finish()` emits
  only pending scope and document-end events and remains idempotent.
- Canonical blockquote fields emit `FIELD_START` followed by one
  `FIELD_CONTENT` event per source line. Tolerated `key: |` and `key: >`
  blocks emit `FIELD_START` and one aggregate `FIELD` on closure or EOF.
- The synchronous and asynchronous adapters now share the incremental state
  machine. UTF-8 byte chunks may split a code point without data loss.

### Fixed

- Multiline fields in array records preserve their complete content and every
  field or item that follows them across both parser backends.
- Malformed anonymous sub-arrays raise `invalid_structure` instead of making
  either batch parser loop without progress.
- Serializers quote strings whose boundary whitespace, carriage returns, or
  array-item colon-space would otherwise change during parsing.
- The C serializer preserves embedded NUL characters in object values, array
  values, and keys.
- Batch and incremental parsers reject an in-band second root, including an
  apparent `# Error` document after partial data, rather than treating it as
  transport-level failure signalling.

### Performance and internals

- The one-pass C fast path is retained in the modular parser: Python scans
  only the lenient frontmatter prefix and C tokenizes the untouched body once
  while preserving document-absolute diagnostics.
- Python parser responsibilities, both C accelerators, streaming events,
  parser state, and JSON Schema conversion have bounded module owners with
  explicit ownership and compatibility re-exports where required.
- The vendored conformance corpus is pinned to the qualified specification
  commit and supplemented by whole-spec parser and serializer axes.

## [0.7.0] — 2026-07-07

Conformance completeness for **JMD Specification v0.3.5** and a rewritten,
much faster C parse path. Both the C accelerator and the pure-Python
fallback are validated against the canonical `jmd-spec` conformance suite,
with identical error kinds and line numbers across backends.

### Changed (breaking)

Parsing is now strict where it previously laundered malformed input into
valid structure with silent data loss. Documents that used to parse may
now be rejected with a structured `JMDParseError`:

- **Prose in the body** — an indented line that is not a continuation
  field, a bullet, a blockquote, a heading, or a thematic break →
  `prose_in_body` (§3.6.2, §11.2). Leading whitespace is a significant
  INDENT, valid only for array-item continuation.
- **A second root heading**, or a **mid-document mode marker**
  (`#?` / `#!` / `#-`) → `second_root_heading` /
  `mode_marker_mid_document` (§18.0).
- **A lone carriage return** (a `\r` not part of a `\r\n`) →
  `lone_carriage_return` (§11.2).
- **An indented or otherwise missing column-0 root heading** →
  `no_root_heading` (§3.1, §11.2).
- **`---` inside an array body** is now decoration (§8.6): an indented
  continuation field after it stays with the open item instead of being
  dropped.
- The serializer now **quotes scalar array items** that would otherwise
  reparse as an object item / structural marker, or that carry
  significant edge whitespace (§6.1/§6.2) — fixing a round-trip break.

### Added

- **BOM tolerance** — a leading U+FEFF is consumed and ignored (§11.2).
- Structured errors carry a machine-readable `.kind` and a
  document-absolute `.line`, reported identically by both backends.

### Performance

- The C fast path is substantially faster: header extraction now consumes
  only the frontmatter prefix and hands the raw body to a self-sufficient
  C parser in a single pass, instead of tokenizing the whole document in
  Python beforehand.

### Internal

- Line-ending handling in the conformance harness is byte-exact and
  portable across all supported Python versions (3.10+); CRLF-tolerance
  fixtures are read raw so they exercise the real code path.
