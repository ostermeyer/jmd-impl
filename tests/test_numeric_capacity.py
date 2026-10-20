# SPDX-License-Identifier: Apache-2.0
"""Numeric type and value must not depend on the native scratch-buffer size."""

import json

import pytest

import jmd


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("size", [31, 32, 33, 127, 128, 129, 300, 1024])
@pytest.mark.parametrize("kind", ["integer", "fraction", "exponent"])
@pytest.mark.parametrize("negative", [False, True])
def test_numeric_buffer_boundaries(
    native: bool, size: int, kind: str, negative: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON supplies the independent value/type oracle for valid numbers."""
    if native and not jmd._HAS_CPARSER:
        pytest.skip("Native parser is unavailable")
    monkeypatch.setattr(jmd, "_HAS_CPARSER", native)
    if kind == "integer":
        raw = "1" * size
    elif kind == "fraction":
        raw = "0." + "1" * (size - 2)
    else:
        raw = "1e+" + "0" * (size - 4) + "2"
    if negative:
        raw = "-" + raw[1:] if kind == "integer" else "-" + raw
    expected = json.loads(raw)
    source = f"# Root\nvalue: {raw}\ntail: false"
    actual = jmd.parse(source).value
    assert actual == {"value": expected, "tail": False}
    assert type(actual["value"]) is type(expected)


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("raw", [
    "1e", "1.2x", "9" * 300 + "x", "1." + "2" * 300 + "x",
    "1e+" + "0" * 300 + "x", "1" * 300 + "\x00suffix",
])
def test_invalid_numbers_remain_complete_strings(
    native: bool, raw: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed numeric recognition must preserve all bytes, including NUL."""
    if native and not jmd._HAS_CPARSER:
        pytest.skip("Native parser is unavailable")
    monkeypatch.setattr(jmd, "_HAS_CPARSER", native)
    assert jmd.parse(f"# Root\nvalue: {raw}").value == {"value": raw}


@pytest.mark.parametrize("source, expected", [
    ("# Root\n## value: {raw}", {"value": 0.125}),
    ("# Values[]\n- {raw}\n- false", [0.125, False]),
    ("# Records[]\n- value: {raw}\n  tail: false",
     [{"value": 0.125, "tail": False}]),
])
def test_long_float_in_each_scalar_context(
    source: str, expected: object,
) -> None:
    """Heading and both array value paths use the repaired conversion."""
    if not jmd._HAS_CPARSER:
        pytest.skip("Native parser is unavailable")
    raw = "0.125" + "0" * 300
    assert jmd.parse(source.format(raw=raw)).value == expected
