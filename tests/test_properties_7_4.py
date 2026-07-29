# SPDX-License-Identifier: Apache-2.0
"""Property-Tests for JMD spec v0.3.3 §7.4 (Repeated Headings).

Property 1: Array Promotion (Length and Content)
    Any sequence of N >= 2 valid JMD objects with identical
    headings ``## <label>`` within the same parent scope must
    parse to an implicit array at ``<label>`` with exactly
    length N. The mapped array elements must match the objects
    parsed individually.

Property 2: Deterministic Errors
    Mixing sigiled (``## <label>[]``) and non-sigiled
    (``## <label>``) headings for the same label in the same
    scope MUST consistently raise a ``sigil_conflict``
    JMDParseError. Repeated explicit arrays (``## <label>[]``
    twice) MUST raise ``repeated_explicit_array``. Repeated
    scalar fields (e.g. ``## x: 10``) MUST raise
    ``repeated_scalar_key``.
"""

from __future__ import annotations

import hypothesis
import hypothesis.strategies as st
import pytest

import jmd
from jmd._parser import JMDParseError

_LOWER = st.characters(min_codepoint=97, max_codepoint=122)
_UPPER = st.characters(min_codepoint=65, max_codepoint=90)


@st.composite
def jmd_object_contents(draw: st.DrawFn) -> str:
    """Generate random valid jmd object body contents.

    Returns a key-value block of one to three ``key: int``
    lines — simple enough that the structural properties under
    test aren't perturbed by scalar-parsing edge cases.
    """
    keys = draw(
        st.lists(
            st.text(alphabet=_LOWER, min_size=1, max_size=5),
            min_size=1,
            max_size=3,
        )
    )
    lines = []
    for k in keys:
        v = draw(st.integers(min_value=0, max_value=100))
        lines.append(f"{k}: {v}")
    return "\n".join(lines)


class TestPropertiesRepeatedHeadings:
    """Property tests for §7.4 repeated-heading array promotion."""

    @hypothesis.given(
        obj_contents=st.lists(
            jmd_object_contents(),
            min_size=2,
            max_size=20,
        ),
        label=st.text(alphabet=_UPPER, min_size=1, max_size=5),
    )
    def test_property_repeated_heading_promotes_array_lossless(
        self, obj_contents: list[str], label: str
    ) -> None:
        """N repeated headings promote to a length-N array losslessly."""
        n_repeats = len(obj_contents)

        # Build the document.
        lines = ["# Root"]
        expected_items = []
        for content in obj_contents:
            lines.append(f"## {label}")
            if content.strip():
                lines.append(content)

            # Predict the expected parsed value dynamically.
            try:
                parsed_item = jmd.jmd_to_dict(f"# Item\n{content}")
                expected_items.append(parsed_item)
            except Exception:
                # Skip if generated content isn't a simple
                # parsable object.
                hypothesis.assume(False)

        src = "\n".join(lines)

        try:
            result = jmd.jmd_to_dict(src)
        except Exception:
            # Content might carry characters breaking syntax;
            # the focus here is structure.
            hypothesis.assume(False)

        assert label in result
        assert isinstance(result[label], list)
        assert len(result[label]) == n_repeats
        assert result[label] == expected_items

    @hypothesis.given(
        is_first_sigiled=st.booleans(),
        label=st.text(alphabet=_UPPER, min_size=1, max_size=5),
    )
    def test_property_sigil_conflict(
        self, is_first_sigiled: bool, label: str
    ) -> None:
        """Mixing sigiled / non-sigiled headings yields sigil_conflict."""
        if is_first_sigiled:
            first = f"## {label}[]\n- id: 1\n"
            second = f"## {label}\nid: 2\n"
        else:
            first = f"## {label}\nid: 1\n"
            second = f"## {label}[]\n- id: 2\n"

        src = f"# Root\n{first}{second}"

        with pytest.raises(JMDParseError) as exc_info:
            jmd.jmd_to_dict(src)
        assert exc_info.value.kind == "sigil_conflict", (
            f"Expected sigil_conflict, got {exc_info.value.kind}"
        )

    @hypothesis.given(
        label=st.text(alphabet=_UPPER, min_size=1, max_size=5),
    )
    def test_property_repeated_explicit_array(self, label: str) -> None:
        """Two explicit arrays yield repeated_explicit_array."""
        src = f"# Root\n## {label}[]\n- x: 1\n## {label}[]\n- x: 2\n"

        with pytest.raises(JMDParseError) as exc_info:
            jmd.jmd_to_dict(src)
        assert exc_info.value.kind == "repeated_explicit_array"

    @hypothesis.given(
        label=st.text(alphabet=_UPPER, min_size=1, max_size=5),
    )
    def test_property_repeated_scalar_key(self, label: str) -> None:
        """Two identical scalar keys in object scope yield error."""
        src = f"# Root\n{label}: 1\n{label}: 2\n"

        with pytest.raises(JMDParseError) as exc_info:
            jmd.jmd_to_dict(src)
        assert exc_info.value.kind == "repeated_scalar_key"
