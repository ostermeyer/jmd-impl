# SPDX-License-Identifier: Apache-2.0
"""Lossless scalar generation across Python/C and collection positions."""

from __future__ import annotations

from typing import Literal

import pytest
from hypothesis import given
from hypothesis import strategies as st

import jmd

Backend = Literal["python", "c"]
_C_SERIALIZER = jmd._HAS_CSERIALIZER
_C_PARSER = jmd._HAS_CPARSER

_VALUES = [
    "|", ">", "0" * 63 + "1", "0" * 62, "0" * 63, "9" * 400,
    "1e9999", "1e-9999", "", " ", "\n", "\nstart", "end\n", "end\n\n",
    " a\nb ", "a \nb", "a\t\nb", "a\r\nb", "a\rb\nc",
    "a\n\nb", "a\n b", "ordinary\nmultiline", "true", "null",
]


def _serialize(
    value: object,
    backend: Backend,
    monkeypatch: pytest.MonkeyPatch,
    *,
    paths: tuple[str, ...] = (),
) -> str:
    """Exercise the chosen public serializer without hiding C absence."""
    if backend == "c" and not _C_SERIALIZER:
        pytest.skip("C serializer is unavailable")
    monkeypatch.setattr(jmd, "_HAS_CSERIALIZER", backend == "c")
    return jmd.serialize(value, label="Probe", blockquote_paths=paths)


def _assert_roundtrip(
    document: str, expected: object, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Check data preservation using both independent parser backends."""
    assert "\r" not in document
    assert all(line == line.rstrip(" \t") for line in document.split("\n"))
    assert jmd.JMDParser().parse(document).value == expected
    if _C_PARSER:
        monkeypatch.setattr(jmd, "_HAS_CPARSER", True)
        assert jmd.parse(document).value == expected


@pytest.mark.parametrize("backend", ["python", "c"])
@pytest.mark.parametrize("value", _VALUES)
@pytest.mark.parametrize("position", range(6))
def test_scalar_roundtrip_in_collections(
    backend: Backend, value: str, position: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contexts: list[object] = [
        {"value": value, "after": "tail"},
        {"child": {"value": value}},
        [value, "tail"],
        [{"value": value, "after": "tail"}, {"value": "next"}],
        [{"before": "head", "value": value}, {"value": "next"}],
        [0, {"value": value, "child": {"ok": True}}, "tail"],
    ]
    expected = contexts[position]
    document = _serialize(expected, backend, monkeypatch)
    _assert_roundtrip(document, expected, monkeypatch)


@pytest.mark.parametrize("backend", ["python", "c"])
@pytest.mark.parametrize(
    "value", ["", " ", " end", "end ", "\nstart", "end\n", "a \nb", "a\r\nb"],
)
@pytest.mark.parametrize("array", [False, True])
def test_explicit_blockquote_cannot_override_losslessness(
    backend: Backend, value: str, array: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected: object = [{"value": value}] if array else {"value": value}
    path = "/*/value" if array else "/value"
    document = _serialize(expected, backend, monkeypatch, paths=(path,))
    assert "> " not in document
    _assert_roundtrip(document, expected, monkeypatch)


@pytest.mark.parametrize("backend", ["python", "c"])
@pytest.mark.parametrize("value", ["one line", "first\nsecond", "a\n\nb"])
def test_safe_blockquote_rendering_stays_readable(
    backend: Backend, value: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _serialize(
        {"value": value}, backend, monkeypatch, paths=("/value",),
    )
    assert document.startswith("# Probe\nvalue:\n> ")
    _assert_roundtrip(document, {"value": value}, monkeypatch)


@pytest.mark.parametrize("backend", ["python", "c"])
@given(st.text(alphabet="abc019-|> \t\r\n", max_size=80))
def test_generated_whitespace_roundtrips(backend: Backend, value: str) -> None:
    with pytest.MonkeyPatch.context() as monkeypatch:
        expected = {"value": value}
        document = _serialize(expected, backend, monkeypatch)
        _assert_roundtrip(document, expected, monkeypatch)
