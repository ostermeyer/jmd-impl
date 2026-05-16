# SPDX-License-Identifier: Apache-2.0
"""Property-Tests for JMD spec v0.3.3 §5.2 (Block Scalars).

Property 1: Literal Block Mapping (|)
    A literal block scalar `key: |\n  line1\n  line2` parsed via jmd_to_dict
    MUST yield a string with lines separated by `\n` (`"line1\\nline2"`),
    matching the exact content of the lines when stripped of the common indentation.
    A trailing blank line at the end of the block is dropped.

Property 2: Folded Block Mapping (>) ohne Blanks
    A folded block scalar `key: >\n  word1\n  word2` parsed via jmd_to_dict
    MUST yield a single-line string with non-empty lines joined by a single
    space (`"word1 word2"`).

Property 3: Folded Blank Line Mapping (>)
    Eine Leerzeile zwischen Gruppen in einem folded block scalar wird zu einem
    Newline; N+1 Leerzeilen zu N Newlines.

Property 4: Cross-Position-Konsistenz
    Block-scalar in bare Position (`key: |`) und in Heading-Position
    (`## key: |`) muss exakt denselben geparsten Value ergeben.
"""

from __future__ import annotations

import hypothesis
import hypothesis.strategies as st
import pytest

import jmd


class TestPropertiesBlockScalars:
    @hypothesis.given(
        key=st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=10),
        lines=st.lists(
            st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=10),
            min_size=1,
            max_size=10
        ),
        indent=st.integers(min_value=2, max_value=8)
    )
    def test_property_literal_block_scalar(self, key: str, lines: list[str], indent: int) -> None:
        """Property 1: Literal block scalar (|) joins lines with newline."""
        padding = " " * indent
        body = "\n".join(f"{padding}{line}" for line in lines)
        # We add \n\n at the end to simulate document end and check trailing blank line dropping.
        src = f"# Root\n{key}: |\n{body}\n\n"
        
        expected_val = "\n".join(lines)
        
        result = jmd.jmd_to_dict(src)
        assert key in result
        assert result[key] == expected_val

    @hypothesis.given(
        key=st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=10),
        lines=st.lists(
            st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=10),
            min_size=1,
            max_size=10
        ),
        indent=st.integers(min_value=2, max_value=8)
    )
    def test_property_folded_block_scalar(self, key: str, lines: list[str], indent: int) -> None:
        """Property 2: Folded block scalar (>) joins non-empty lines with space."""
        padding = " " * indent
        body = "\n".join(f"{padding}{line}" for line in lines)
        src = f"# Root\n{key}: >\n{body}\n\n"
        
        expected_val = " ".join(lines)
        
        result = jmd.jmd_to_dict(src)
        assert key in result
        assert result[key] == expected_val

    @hypothesis.given(
        lines1=st.lists(st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1), min_size=1, max_size=3),
        lines2=st.lists(st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1), min_size=1, max_size=3),
        blank_lines=st.integers(min_value=1, max_value=4),
        indent=st.integers(min_value=2, max_value=4)
    )
    def test_property_folded_block_scalar_with_blanks(
        self, lines1: list[str], lines2: list[str], blank_lines: int, indent: int
    ) -> None:
        """Property 3: Folded Blank Line Mapping."""
        padding = " " * indent
        
        p1 = "\n".join(f"{padding}{line}" for line in lines1)
        p2 = "\n".join(f"{padding}{line}" for line in lines2)
        
        # blank_lines determines how many empty lines we put between the paragraphs.
        # Spec says: "a blank line within the block preserves a newline"
        # N blank lines -> N newlines in output? Wait, N+1 newlines?
        # N blank lines between paragraphs = N+1 newlines in source text
        # e.g., line\n\nline -> 1 blank line in text -> 1 newline in output -> line\nline
        
        # If blank_lines is 1, we join them with \n\n in JMD source.
        # This becomes 1 newline in the parsed string.
        separator = "\n" * (blank_lines + 1)
        body = f"{p1}{separator}{p2}"
        
        src = f"# Root\ntext: >\n{body}\n\n"
        
        expect_p1 = " ".join(lines1)
        expect_p2 = " ".join(lines2)
        
        # N blank lines = N newlines
        expected_sep = "\n" * blank_lines
        expected_val = f"{expect_p1}{expected_sep}{expect_p2}"
        
        result = jmd.jmd_to_dict(src)
        assert "text" in result
        assert result["text"] == expected_val

    @hypothesis.given(
        lines=st.lists(
            st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=10),
            min_size=1,
            max_size=10
        ),
        indent=st.integers(min_value=2, max_value=8)
    )
    def test_property_cross_position_consistency(self, lines: list[str], indent: int) -> None:
        """Property 4: Cross-Position-Konsistenz."""
        padding = " " * indent
        body = "\n".join(f"{padding}{line}" for line in lines)
        
        # Case A: Bare Field
        src_bare = f"# Root\ntest: |\n{body}\n\n"
        # Case B: Heading Position
        src_heading = f"# Root\n## test: |\n{body}\n\n"
        
        result_bare = jmd.jmd_to_dict(src_bare)
        result_heading = jmd.jmd_to_dict(src_heading)
        
        assert "test" in result_bare
        assert "test" in result_heading
        
        assert result_bare["test"] == result_heading["test"]

