# Changelog

## 0.7.0 — 2026-07-29

This release qualifies the Python reference implementation against JMD v0.3.5.

### Changed

- `JMDStreamParser.process_line()` now emits every event made complete by the
  current source line instead of buffering the complete document until
  `finish()`.
- Direct push consumers must consume the list returned by every
  `process_line()` call. `finish()` emits only pending scope and document-end
  events and remains idempotent.
- Canonical blockquote fields emit `FIELD_START` followed by one
  `FIELD_CONTENT` event per source line.
- Tolerated `key: |` and `key: >` blocks emit `FIELD_START`, retain only the
  current multiline value, and emit one aggregate `FIELD` on closure or EOF.
- The synchronous and asynchronous adapters now share the incremental state
  machine. UTF-8 byte chunks may split a code point without data loss.

### Fixed

- Python and C parsers consume one leading BOM, reject lone carriage returns,
  and enforce the single-root and document-mode boundaries from JMD v0.3.5.
- Multiline fields in array records preserve their content and every field or
  item that follows them.
- Thematic breaks inside array records remain decoration, and scalar array
  strings containing colon-space serialize without changing their type.
- The C serializer preserves embedded NUL characters in object values, array
  values, and keys.
- Streaming no longer retains the complete source document. Scope transitions
  and blank-line resets are emitted as soon as they become structurally
  determined.

### Qualification and internals

- The vendored conformance corpus is aligned with JMD v0.3.5 and supplemented
  by a spec-derived suite covering all applicable parser and serializer axes.
- Python parser responsibilities and both C accelerators are split into
  bounded modules or translation units with explicit ownership.
- Streaming event vocabulary, parser state machine, bounded multiline/scope
  state, and public adapters have separate module ownership.
- Repository-wide Ruff, strict Mypy, and all 765 tests pass.
