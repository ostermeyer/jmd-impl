# SPDX-License-Identifier: Apache-2.0
"""Canonical item blockquotes and legacy input normalization (spec 8.3/9.1)."""

import pytest

import jmd
from jmd._stream_parser import JMDStreamParser


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("first", [False, True])
@pytest.mark.parametrize("separate_opener", [False, True])
@pytest.mark.parametrize("indent", ["", "  ", "\t", "    "])
def test_item_blockquote_normalizes_without_losing_fields(
    native: bool, first: bool, separate_opener: bool, indent: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both parsers accept old input; both generators emit canonical bytes."""
    if native and not (jmd._HAS_CPARSER and jmd._HAS_CSERIALIZER):
        pytest.skip("Native parser and serializer are unavailable")
    monkeypatch.setattr(jmd, "_HAS_CPARSER", native)
    monkeypatch.setattr(jmd, "_HAS_CSERIALIZER", native)
    parts = ["alpha", "", "> quoted", "- listed", "omega"]
    value = "\n".join(parts)
    item: dict[str, object] = {"message": value, "tail": False}
    if not first:
        item = {"id": 1, **item}
    prefix = "- message:" if first else "- id: 1\n  message:"
    if separate_opener:
        prefix = "-\n  " + prefix[2:]
    source = "# Comment[]\n" + prefix + "\n"
    source += "\n".join(
        indent + ("> " + part if part else ">") for part in parts
    )
    source += "\n  tail: false\n- id: 2\n"
    expected = [item, {"id": 2}]
    assert jmd.parse(source).value == expected
    assert jmd.JMDParser().parse(source).value == expected

    canonical = "# Comment[]\n"
    canonical += "- message:\n" if first else "- id: 1\n  message:\n"
    canonical += (
        "  > alpha\n  >\n  > > quoted\n  > - listed\n  > omega\n"
        "  tail: false\n- id: 2"
    )
    assert jmd.serialize(expected, label="Comment") == canonical
    assert jmd.serialize(jmd.parse(source)) == canonical


@pytest.mark.parametrize("first", [False, True])
@pytest.mark.parametrize("indent", ["", "  ", "\t", "    "])
def test_item_blockquote_streams_each_line(
    first: bool, indent: str,
) -> None:
    """Content arrives immediately and a later field closes the quote."""
    parser = JMDStreamParser()
    parser.process_line("# Comment[]")
    if not first:
        parser.process_line("- id: 1")
    opener = "- message:" if first else "  message:"
    assert any(
        event.type == "FIELD_START" and event.key == "message"
        for event in parser.process_line(opener)
    )
    for part in ("alpha", "", "> quoted", "- listed", "omega"):
        line = indent + ("> " + part if part else ">")
        assert [
            (event.type, event.value) for event in parser.process_line(line)
        ] == [("FIELD_CONTENT", part)]
    assert any(
        event.type == "FIELD" and event.key == "tail" and event.value is False
        for event in parser.process_line("  tail: false")
    )
    assert any(
        event.type == "FIELD" and event.key == "id" and event.value == 2
        for event in parser.process_line("- id: 2")
    )
    assert parser.finish()[-1].type == "DOCUMENT_END"
