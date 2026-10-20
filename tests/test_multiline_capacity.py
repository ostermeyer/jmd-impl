# SPDX-License-Identifier: Apache-2.0
"""Multiline values must survive scratch-buffer growth without data loss."""

import pytest

import jmd


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("marker", ["", "|", ">"])
@pytest.mark.parametrize("count", [255, 256, 257, 300, 1025, 8193])
def test_multiline_content_and_following_field(
    native: bool, marker: str, count: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All lines survive, including Unicode and the field after the block."""
    if native and not jmd._HAS_CPARSER:
        pytest.skip("Native parser is unavailable")
    monkeypatch.setattr(jmd, "_HAS_CPARSER", native)
    parts = [f"line {i}: Grüße 世界 🌍" for i in range(count)]
    prefix = "> " if not marker else "  "
    source = f"# Root\nmessage: {marker}\n"
    source += "\n".join(prefix + part for part in parts)
    source += "\ntail: false\n"
    value = (" " if marker == ">" else "\n").join(parts)
    assert jmd.parse(source).value == {"message": value, "tail": False}


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("marker", ["", "|", ">"])
@pytest.mark.parametrize("blank_count", [1, 257])
def test_multiline_blank_runs_cross_capacity(
    native: bool, marker: str, blank_count: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blank runs retain their established leading/interior/trailing meaning."""
    if native and not jmd._HAS_CPARSER:
        pytest.skip("Native parser is unavailable")
    monkeypatch.setattr(jmd, "_HAS_CPARSER", native)
    paragraph = [f"part {i}" for i in range(255)]
    parts = [""] * blank_count + paragraph + [""] * blank_count
    parts += ["last"] + [""] * 300
    prefix = "> " if not marker else "  "
    source = f"# Root\nmessage: {marker}\n"
    source += "\n".join(prefix + part if part else prefix.rstrip()
                        for part in parts)
    source += "\ntail: false\n"
    if marker == ">":
        value = "\n" * blank_count + " ".join(paragraph)
        value += "\n" * blank_count + "last"
    else:
        value = "\n".join(parts).rstrip("\n")
    assert jmd.parse(source).value == {"message": value, "tail": False}


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("first", [False, True])
@pytest.mark.parametrize("indent", ["", "  "])
def test_long_item_blockquote_keeps_record_boundaries(
    native: bool, first: bool, indent: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both legacy and canonical item quotes preserve later fields and items."""
    if native and not jmd._HAS_CPARSER:
        pytest.skip("Native parser is unavailable")
    monkeypatch.setattr(jmd, "_HAS_CPARSER", native)
    parts = [f"line {i}" for i in range(1025)]
    source = "# Item[]\n"
    source += "- message:\n" if first else "- id: 1\n  message:\n"
    source += "\n".join(indent + "> " + part for part in parts)
    source += "\n  tail: false\n- id: 2\n"
    item: dict[str, object] = {"message": "\n".join(parts), "tail": False}
    if not first:
        item = {"id": 1, **item}
    assert jmd.parse(source).value == [item, {"id": 2}]


@pytest.mark.parametrize("native", [False, True])
def test_large_key_set_is_not_limited_by_cache(
    native: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fixed key cache may evict entries, but cannot drop object fields."""
    if native and not jmd._HAS_CPARSER:
        pytest.skip("Native parser is unavailable")
    monkeypatch.setattr(jmd, "_HAS_CPARSER", native)
    expected = {f"field-{i}": i for i in range(1025)}
    source = "# Root\n" + "\n".join(
        f"{key}: {value}" for key, value in expected.items()
    )
    assert jmd.parse(source).value == expected
