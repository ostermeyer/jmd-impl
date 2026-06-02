# SPDX-License-Identifier: Apache-2.0
r"""Property-Tests for JMD spec v0.3.3 §5.2 (Block Scalars).

Property 1: Literal Block Mapping (|)
    A literal block scalar ``key: |`` parsed via jmd_to_dict
    MUST yield a string with lines separated by ``\n``,
    matching the line content stripped of the common
    indentation. A trailing blank line is dropped.

Property 2: Folded Block Mapping (>) without blanks
    A folded block scalar ``key: >`` parsed via jmd_to_dict
    MUST yield a single-line string with non-empty lines
    joined by a single space.

Property 3: Folded Blank Line Mapping (>)
    A blank line between groups in a folded block scalar maps
    to a newline; N+1 blank source lines yield N newlines.

Property 4: Cross-position consistency
    A block scalar in bare position (``key: |``) and in
    heading position (``## key: |``) must yield the exact
    same parsed value.
"""

from __future__ import annotations

import hypothesis
import hypothesis.strategies as st

import jmd

_LOWER = st.characters(min_codepoint=97, max_codepoint=122)


def _words(min_size: int = 1, max_size: int = 10) -> st.SearchStrategy[str]:
    """Strategy for a lowercase word of bounded length."""
    return st.text(alphabet=_LOWER, min_size=min_size, max_size=max_size)


class TestPropertiesBlockScalars:
    """Property tests for §5.2 literal / folded block scalars."""

    @hypothesis.given(
        key=_words(),
        lines=st.lists(_words(), min_size=1, max_size=10),
        indent=st.integers(min_value=2, max_value=8),
    )
    def test_property_literal_block_scalar(
        self, key: str, lines: list[str], indent: int
    ) -> None:
        """Property 1: literal block scalar (|) joins with newline."""
        padding = " " * indent
        body = "\n".join(f"{padding}{line}" for line in lines)
        # Trailing \n\n simulates document end; checks that the
        # trailing blank line is dropped.
        src = f"# Root\n{key}: |\n{body}\n\n"

        expected_val = "\n".join(lines)

        result = jmd.jmd_to_dict(src)
        assert key in result
        assert result[key] == expected_val

    @hypothesis.given(
        key=_words(),
        lines=st.lists(_words(), min_size=1, max_size=10),
        indent=st.integers(min_value=2, max_value=8),
    )
    def test_property_folded_block_scalar(
        self, key: str, lines: list[str], indent: int
    ) -> None:
        """Property 2: folded block scalar (>) joins with space."""
        padding = " " * indent
        body = "\n".join(f"{padding}{line}" for line in lines)
        src = f"# Root\n{key}: >\n{body}\n\n"

        expected_val = " ".join(lines)

        result = jmd.jmd_to_dict(src)
        assert key in result
        assert result[key] == expected_val

    @hypothesis.given(
        lines1=st.lists(_words(min_size=1), min_size=1, max_size=3),
        lines2=st.lists(_words(min_size=1), min_size=1, max_size=3),
        blank_lines=st.integers(min_value=1, max_value=4),
        indent=st.integers(min_value=2, max_value=4),
    )
    def test_property_folded_block_scalar_with_blanks(
        self,
        lines1: list[str],
        lines2: list[str],
        blank_lines: int,
        indent: int,
    ) -> None:
        """Property 3: folded-block blank lines map to newlines."""
        padding = " " * indent

        p1 = "\n".join(f"{padding}{line}" for line in lines1)
        p2 = "\n".join(f"{padding}{line}" for line in lines2)

        # ``blank_lines`` empty lines between the paragraphs.
        # Spec: a blank line within the block preserves a
        # newline — N blank lines yield N newlines in output,
        # which means N+1 newline chars in the source text.
        separator = "\n" * (blank_lines + 1)
        body = f"{p1}{separator}{p2}"

        src = f"# Root\ntext: >\n{body}\n\n"

        expect_p1 = " ".join(lines1)
        expect_p2 = " ".join(lines2)
        expected_sep = "\n" * blank_lines
        expected_val = f"{expect_p1}{expected_sep}{expect_p2}"

        result = jmd.jmd_to_dict(src)
        assert "text" in result
        assert result["text"] == expected_val

    @hypothesis.given(
        lines=st.lists(_words(), min_size=1, max_size=10),
        indent=st.integers(min_value=2, max_value=8),
    )
    def test_property_cross_position_consistency(
        self, lines: list[str], indent: int
    ) -> None:
        """Property 4: bare and heading position parse identically."""
        padding = " " * indent
        body = "\n".join(f"{padding}{line}" for line in lines)

        src_bare = f"# Root\ntest: |\n{body}\n\n"
        src_heading = f"# Root\n## test: |\n{body}\n\n"

        result_bare = jmd.jmd_to_dict(src_bare)
        result_heading = jmd.jmd_to_dict(src_heading)

        assert "test" in result_bare
        assert "test" in result_heading
        assert result_bare["test"] == result_heading["test"]
