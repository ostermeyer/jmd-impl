# Changelog

All notable changes to `jmd-format` are documented here. The project
follows [Semantic Versioning](https://semver.org/); while on `0.x`, minor
releases may carry behavioral (breaking) changes.

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
