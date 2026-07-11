# SPDX-License-Identifier: Apache-2.0
"""§11.2 leading-whitespace significance (anti-laundering).

A leading indent is a significant INDENT, valid only for array-item
continuation. Indented headings and indented object-body fields must be
rejected, not silently laundered into valid structure. Runs against both
backends via the ``backend`` fixture.
"""
from __future__ import annotations

import pytest

import jmd
from jmd._parser import JMDParseError


@pytest.fixture(params=["c", "py"])
def backend(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> str:
    if request.param == "py":
        monkeypatch.setattr(jmd, "_HAS_CPARSER", False)
    return request.param


def test_indented_root_rejected(backend: str) -> None:
    del backend
    with pytest.raises(ValueError):
        jmd.parse("  # Doc\nid: 1\n")


def test_tab_indented_root_rejected(backend: str) -> None:
    del backend
    with pytest.raises(ValueError):
        jmd.parse("\t# Doc\nid: 1\n")


def test_indented_object_field_is_prose(backend: str) -> None:
    del backend
    with pytest.raises(JMDParseError) as exc:
        jmd.parse("# Doc\n  id: 1\n")
    assert exc.value.kind == "prose_in_body"
    assert exc.value.line == 2


def test_indented_field_after_frontmatter(backend: str) -> None:
    """Absolute line numbers survive the frontmatter offset."""
    del backend
    with pytest.raises(JMDParseError) as exc:
        jmd.parse("meta: 1\n\n# D\n  x: 2\n")
    assert exc.value.kind == "prose_in_body"
    assert exc.value.line == 4


def test_array_continuation_still_valid(backend: str) -> None:
    del backend
    assert jmd.parse("# R[]\n- a: 1\n  b: 2\n").value == [{"a": 1, "b": 2}]


def test_heading_depth_nesting_still_valid(backend: str) -> None:
    del backend
    assert jmd.parse("# R\n\n## sub\nx: 1\n").value == {"sub": {"x": 1}}
