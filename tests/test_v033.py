# SPDX-License-Identifier: Apache-2.0
"""Tests for JMD spec v0.3.3 additions.

Covers:
- §7.4 Repeated Headings as Implicit Arrays (promotion + three errors)
- §3.5.1 Frontmatter Marker Tolerance (--- around the block)
- §5.2 Multi-Line Block-Scalar Syntax Tolerance (| literal, > folded)
- D11 Serializer label validation (newline reject, whitespace strip)
- D13 Blockquote leading-newline lossless roundtrip
"""

from __future__ import annotations

import pytest

import jmd
from jmd import JMDParser, jmd_stream
from jmd._parser import JMDParseError

# ---------------------------------------------------------------------------
# §7.4 — Repeated Headings as Implicit Arrays
# ---------------------------------------------------------------------------


class TestRepeatedHeadingPromotion:
    """§7.4.1: repeated ``## key`` (no sigil) promotes to an array."""

    def test_two_occurrences_promote_to_array(self) -> None:
        """Test that two repeated ## Op headings produce a 2-element array."""
        src = "# Doc\n## Op\ntype: rect\n## Op\ntype: text\n"
        assert jmd.jmd_to_dict(src) == {
            "Op": [{"type": "rect"}, {"type": "text"}],
        }

    def test_three_occurrences_extend_array(self) -> None:
        """Test that a third repeated heading appends to the promoted array."""
        src = (
            "# Doc\n## Op\ntype: rect\n## Op\ntype: text\n## Op\ntype: path\n"
        )
        assert jmd.jmd_to_dict(src) == {
            "Op": [
                {"type": "rect"},
                {"type": "text"},
                {"type": "path"},
            ],
        }

    def test_single_occurrence_stays_object(self) -> None:
        """Test that a single ## Op without [] is parsed as a plain object."""
        src = "# Doc\n## Op\ntype: rect\n"
        assert jmd.jmd_to_dict(src) == {"Op": {"type": "rect"}}

    def test_nested_repeated_headings_promote(self) -> None:
        """Test that repeated ### row inside ## table promotes to an array."""
        src = (
            "# Doc\n"
            "## table\n"
            "### row\nh: 32\n"
            "### row\nh: 28\n"
        )
        assert jmd.jmd_to_dict(src) == {
            "table": {"row": [{"h": 32}, {"h": 28}]},
        }


class TestRepeatedHeadingErrors:
    """§7.4.2: three structured error conditions."""

    def test_sigil_conflict_without_then_with(self) -> None:
        """Test that ## Op then ## Op[] raises sigil_conflict."""
        src = "# Doc\n## Op\ntype: rect\n## Op[]\n- type: text\n"
        with pytest.raises(JMDParseError) as exc:
            jmd.jmd_to_dict(src)
        assert exc.value.kind == "sigil_conflict"

    def test_sigil_conflict_with_then_without(self) -> None:
        """Test that ## Op[] then ## Op also raises sigil_conflict."""
        src = "# Doc\n## Op[]\n- type: rect\n## Op\ntype: text\n"
        with pytest.raises(JMDParseError) as exc:
            jmd.jmd_to_dict(src)
        assert exc.value.kind == "sigil_conflict"

    def test_repeated_explicit_array(self) -> None:
        """Test that two ## Op[] sections raise repeated_explicit_array."""
        src = "# Doc\n## Op[]\n- type: rect\n## Op[]\n- type: text\n"
        with pytest.raises(JMDParseError) as exc:
            jmd.jmd_to_dict(src)
        assert exc.value.kind == "repeated_explicit_array"

    def test_repeated_bare_scalar(self) -> None:
        """Test that two bare ``x: ...`` lines raise repeated_scalar_key."""
        src = "# Doc\nx: 1\nx: 2\n"
        with pytest.raises(JMDParseError) as exc:
            jmd.jmd_to_dict(src)
        assert exc.value.kind == "repeated_scalar_key"

    def test_repeated_scalar_heading(self) -> None:
        """Test that two ## x: ... headings raise repeated_scalar_key."""
        src = "# Doc\n## x: 1\n## x: 2\n"
        with pytest.raises(JMDParseError) as exc:
            jmd.jmd_to_dict(src)
        assert exc.value.kind == "repeated_scalar_key"

    def test_mixed_scalar_forms(self) -> None:
        """Test that bare then heading form raises repeated_scalar_key."""
        src = "# Doc\nx: 1\n## x: 2\n"
        with pytest.raises(JMDParseError) as exc:
            jmd.jmd_to_dict(src)
        assert exc.value.kind == "repeated_scalar_key"

    def test_scalar_then_object_heading(self) -> None:
        """Test that bare ``x: 1`` then ``## x`` raises repeated_scalar_key."""
        src = "# Doc\nx: 1\n## x\ny: 5\n"
        with pytest.raises(JMDParseError) as exc:
            jmd.jmd_to_dict(src)
        assert exc.value.kind == "repeated_scalar_key"


class TestRepeatedHeadingStreaming:
    """§7.4: stream parser emits every occurrence as its own event pair."""

    def test_repeated_headings_yield_separate_events(self) -> None:
        """Test that three repeated ## Op produce three OBJECT_START events."""
        src = "# Doc\n## Op\ntype: a\n## Op\ntype: b\n## Op\ntype: c\n"
        events = list(jmd_stream(src))
        starts = [e for e in events
                  if e.type == "OBJECT_START" and e.key == "Op"]
        ends = [e for e in events
                if e.type == "OBJECT_END" and e.key == "Op"]
        assert len(starts) == 3
        assert len(ends) == 3


# ---------------------------------------------------------------------------
# §3.5.1 — Frontmatter Marker Tolerance
# ---------------------------------------------------------------------------


class TestFrontmatterMarkerTolerance:
    """§3.5.1: stray ``---`` lines around frontmatter are consumed."""

    def test_marker_before_frontmatter(self) -> None:
        """Test that --- before any field is consumed."""
        src = "---\nconfidence: high\n\n# Doc\nx: 1\n"
        p = JMDParser()
        body = p.parse(src)
        assert p.frontmatter == {"confidence": "high"}
        assert body == {"x": 1}

    def test_marker_after_frontmatter(self) -> None:
        """Test that --- between last field and root heading is consumed."""
        src = "confidence: high\n---\n# Doc\nx: 1\n"
        p = JMDParser()
        body = p.parse(src)
        assert p.frontmatter == {"confidence": "high"}
        assert body == {"x": 1}

    def test_markers_around_both_sides(self) -> None:
        """Test that --- markers on both sides parse identically."""
        wrapped = "---\nconfidence: high\nsource: db\n---\n\n# Doc\nx: 1\n"
        plain = "confidence: high\nsource: db\n\n# Doc\nx: 1\n"
        pw = JMDParser()
        pw.parse(wrapped)
        pp = JMDParser()
        pp.parse(plain)
        assert pw.frontmatter == pp.frontmatter
        assert pw.frontmatter == {"confidence": "high", "source": "db"}

    def test_four_or_more_hyphens_accepted(self) -> None:
        """Test that ---- and ----- are also tolerated as marker lines."""
        src = "----\nconfidence: high\n-----\n# Doc\nx: 1\n"
        p = JMDParser()
        body = p.parse(src)
        assert p.frontmatter == {"confidence": "high"}
        assert body == {"x": 1}


# ---------------------------------------------------------------------------
# §5.2 — Multi-Line Block-Scalar Syntax Tolerance
# ---------------------------------------------------------------------------


class TestBlockScalarLiteral:
    """§5.2: ``key: |`` opens a literal block scalar."""

    def test_bare_field_literal(self) -> None:
        """Test that a bare ``key: |`` block joins lines with newlines."""
        src = "# Doc\nbio: |\n  line one\n  line two\n"
        assert jmd.jmd_to_dict(src) == {"bio": "line one\nline two"}

    def test_scalar_heading_literal(self) -> None:
        """Test that ``## key: |`` opens a block scalar at heading position."""
        src = "# Doc\n## bio: |\n  alpha\n  beta\n"
        assert jmd.jmd_to_dict(src) == {"bio": "alpha\nbeta"}

    def test_trailing_blank_dropped(self) -> None:
        """Test that a trailing blank line at end of block is dropped."""
        src = "# Doc\nbio: |\n  one\n  \n"
        assert jmd.jmd_to_dict(src) == {"bio": "one"}


class TestBlockScalarFolded:
    """§5.2: ``key: >`` opens a folded block scalar (single-line value)."""

    def test_bare_field_folded(self) -> None:
        """Test that bare ``key: >`` folds lines with single spaces."""
        src = "# Doc\nbio: >\n  line one\n  line two\n"
        assert jmd.jmd_to_dict(src) == {"bio": "line one line two"}

    def test_scalar_heading_folded(self) -> None:
        """Test that ``## key: >`` folds at heading position."""
        src = "# Doc\n## bio: >\n  alpha\n  beta\n"
        assert jmd.jmd_to_dict(src) == {"bio": "alpha beta"}

    def test_folded_preserves_one_newline_per_blank(self) -> None:
        """Test that a blank line within folded block becomes one newline."""
        src = "# Doc\nbio: >\n  a\n\n  b\n"
        assert jmd.jmd_to_dict(src) == {"bio": "a\nb"}

    def test_folded_preserves_two_newlines_per_two_blanks(self) -> None:
        """Test that two blank lines preserve two newlines."""
        src = "# Doc\nbio: >\n  a\n\n\n  b\n"
        assert jmd.jmd_to_dict(src) == {"bio": "a\n\nb"}


class TestBlockScalarStreaming:
    """§5.2 in stream parser: block scalar yields a single FIELD event."""

    def test_literal_yields_field_with_newlines(self) -> None:
        """Test that streaming ``key: |`` emits one FIELD with joined value."""
        src = "# Doc\nkey: |\n  line one\n  line two\n"
        events = list(jmd_stream(src))
        fields = [e for e in events if e.type == "FIELD"]
        assert len(fields) == 1
        assert fields[0].key == "key"
        assert fields[0].value == "line one\nline two"

    def test_folded_yields_field_with_space_joined(self) -> None:
        """Test that streaming ``key: >`` emits one FIELD with folded value."""
        src = "# Doc\nbio: >\n  alpha\n  beta\n"
        events = list(jmd_stream(src))
        fields = [e for e in events if e.type == "FIELD"]
        assert len(fields) == 1
        assert fields[0].value == "alpha beta"


# ---------------------------------------------------------------------------
# D11 — Serializer label validation
# ---------------------------------------------------------------------------


class TestSerializerLabelValidation:
    """D11: serialize() validates and normalizes the root label."""

    def test_newline_in_label_rejected(self) -> None:
        r"""Test that a label containing \n raises ValueError."""
        with pytest.raises(ValueError, match="newline"):
            jmd.serialize({"x": 1}, label="foo\nbar")

    def test_carriage_return_in_label_rejected(self) -> None:
        r"""Test that a label containing \r raises ValueError."""
        with pytest.raises(ValueError, match="newline"):
            jmd.serialize({"x": 1}, label="foo\rbar")

    def test_surrounding_whitespace_stripped(self) -> None:
        """Test that leading and trailing whitespace is silently stripped."""
        out = jmd.serialize({"x": 1}, label="  Order  ")
        assert out == "# Order\nx: 1"

    def test_empty_label_allowed(self) -> None:
        """Test that an empty label produces an anonymous root heading."""
        out = jmd.serialize({"x": 1}, label="")
        assert out.startswith("# \n") or out.startswith("#\n")

    def test_mode_prefix_preserved_through_whitespace(self) -> None:
        """Test that mode prefix survives surrounding whitespace strip."""
        out = jmd.serialize({"x": 1}, label="  ? Tasks  ")
        assert out == "#? Tasks\nx: 1"

    def test_mode_prefix_with_empty_label(self) -> None:
        """Test that ``- `` produces an anonymous delete root array."""
        out = jmd.serialize([], label="- ")
        assert out == "#- []"


# ---------------------------------------------------------------------------
# D13 — Blockquote leading-newline lossless roundtrip
# ---------------------------------------------------------------------------


class TestBlockquoteLeadingNewline:
    r"""D13: a leading ``\n`` in a blockquote value must round-trip."""

    def test_leading_newline_roundtrip(self) -> None:
        r"""Test that ``\nfoo`` survives serialize → parse."""
        value = "\nfoo"
        s = jmd.serialize({"x": value}, label="T")
        assert jmd.jmd_to_dict(s) == {"x": value}

    def test_internal_blank_lines_preserved(self) -> None:
        """Test that internal blank lines in a blockquote value round-trip."""
        value = "a\n\nb"
        s = jmd.serialize({"x": value}, label="T")
        assert jmd.jmd_to_dict(s) == {"x": value}


# ---------------------------------------------------------------------------
# D12 — Multi-line frontmatter values via blockquote
# ---------------------------------------------------------------------------


class TestMultilineFrontmatter:
    """D12: multi-line frontmatter serializes as blockquote and round-trips."""

    def test_multiline_frontmatter_roundtrip(self) -> None:
        """Test that a multi-line frontmatter value survives roundtrip."""
        fm = {"summary": "line one\nline two", "page": 1}
        s = jmd.serialize({"id": 42}, label="Order", frontmatter=fm)
        p = JMDParser()
        body = p.parse(s)
        assert body == {"id": 42}
        assert p.frontmatter == fm

    def test_multiline_frontmatter_uses_blockquote(self) -> None:
        """Test that the serialized form uses key: + > line blockquote."""
        fm = {"note": "a\nb"}
        s = jmd.serialize({"x": 1}, label="T", frontmatter=fm)
        assert "note:\n> a\n> b" in s

    def test_leading_newline_in_frontmatter_lossless(self) -> None:
        r"""Test that leading \n in frontmatter survives roundtrip."""
        fm = {"note": "\nfollows"}
        s = jmd.serialize({"x": 1}, label="T", frontmatter=fm)
        p = JMDParser()
        p.parse(s)
        assert p.frontmatter == fm
