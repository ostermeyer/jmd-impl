# SPDX-License-Identifier: Apache-2.0
"""Conformance tests against the canonical JMD v0.3.5 fixture suite.

The suite deliberately keeps parser and serializer evidence independent:

* parser tests receive their body oracle from ``.json`` fixtures;
* serializer tests receive their body value from ``.json`` rather than from a
  preceding body parse;
* parser and serializer backends are selected independently;
* round-trip tests exercise every parser/serializer combination; and
* the direct :class:`jmd.JMDParser` contract is separate from the public
  pure-Python fallback.

Fixture discovery is fail-loud. The explicit ``JMD_FIXTURES`` override wins,
followed by the vendored specification and then a sibling checkout. A missing
root, missing required directory, or orphaned fixture aborts collection rather
than turning qualification into a passing suite of skips.
"""

from __future__ import annotations

import json
import os
import pathlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, cast

import pytest

import jmd
from jmd._parser import JMDParseError

ParserName = Literal["c", "py", "direct-py"]
SerializerName = Literal["c", "py"]
DocumentMode = Literal["data", "schema", "query", "delete"]
ParseCallable = Callable[[str], jmd.Envelope]
SerializeCallable = Callable[[jmd.Envelope], str]

_REQUIRED_DIRECTORIES = frozenset(
    {"data", "delete", "tolerance", "must-fail"}
)
_C_PARSER_AVAILABLE = jmd._HAS_CPARSER
_C_SERIALIZER_AVAILABLE = jmd._HAS_CSERIALIZER


@dataclass(frozen=True)
class ParserSurface:
    """One independently selected parser surface."""

    name: ParserName
    parse: ParseCallable


@dataclass(frozen=True)
class SerializerSurface:
    """One independently selected serializer surface."""

    name: SerializerName
    serialize: SerializeCallable


def _fixtures_root() -> pathlib.Path:
    """Return the required conformance root and fail loudly when absent."""
    env = os.environ.get("JMD_FIXTURES")
    if env:
        root = pathlib.Path(env).expanduser().resolve()
        if not root.is_dir():
            raise FileNotFoundError(
                f"JMD_FIXTURES does not name a directory: {root}"
            )
        return root

    here = pathlib.Path(__file__).resolve().parent
    repo_root = here.parent
    for candidate in (
        repo_root / "vendor" / "jmd-spec" / "conformance",
        repo_root.parent / "jmd-spec" / "conformance",
    ):
        if candidate.is_dir():
            return candidate.resolve()

    raise FileNotFoundError(
        "JMD conformance fixtures are required; initialize vendor/jmd-spec, "
        "provide a sibling jmd-spec checkout, or set JMD_FIXTURES"
    )


def _validate_fixture_layout(root: pathlib.Path) -> None:
    """Reject incomplete fixture trees and orphaned fixture files."""
    missing_directories = sorted(
        name for name in _REQUIRED_DIRECTORIES if not (root / name).is_dir()
    )
    if missing_directories:
        raise FileNotFoundError(
            "missing conformance directories: "
            + ", ".join(missing_directories)
        )

    for mode_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        jmd_stems = {path.stem for path in mode_dir.glob("*.jmd")}
        if mode_dir.name == "must-fail":
            oracle_stems = {
                path.name.removesuffix(".error.json")
                for path in mode_dir.glob("*.error.json")
            }
        else:
            oracle_stems = {path.stem for path in mode_dir.glob("*.json")}

        missing_oracles = sorted(jmd_stems - oracle_stems)
        missing_inputs = sorted(oracle_stems - jmd_stems)
        if missing_oracles or missing_inputs:
            details: list[str] = []
            if missing_oracles:
                details.append(f"missing oracle for {missing_oracles!r}")
            if missing_inputs:
                details.append(f"missing JMD for {missing_inputs!r}")
            raise FileNotFoundError(
                f"incomplete fixtures in {mode_dir}: " + "; ".join(details)
            )


def _collect_pairs(
    root: pathlib.Path,
) -> list[tuple[str, pathlib.Path, pathlib.Path]]:
    """Return ``(mode, jmd_path, json_path)`` for every fixture pair."""
    pairs: list[tuple[str, pathlib.Path, pathlib.Path]] = []
    for mode_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        if mode_dir.name == "must-fail":
            continue
        for jmd_path in sorted(mode_dir.glob("*.jmd")):
            pairs.append(
                (mode_dir.name, jmd_path, jmd_path.with_suffix(".json"))
            )
    if not pairs:
        raise FileNotFoundError(f"no paired conformance fixtures in {root}")
    return pairs


def _collect_must_fail(
    root: pathlib.Path,
) -> list[tuple[pathlib.Path, pathlib.Path]]:
    """Return every must-fail input and structured-error oracle pair."""
    pairs = [
        (jmd_path, jmd_path.with_suffix(".error.json"))
        for jmd_path in sorted((root / "must-fail").glob("*.jmd"))
    ]
    if not pairs:
        raise FileNotFoundError(f"no must-fail fixtures in {root}")
    return pairs


def _read_jmd_fixture(path: pathlib.Path) -> str:
    """Read fixture bytes as text without universal-newline translation."""
    with path.open(encoding="utf-8", newline="") as fixture:
        return fixture.read()


def _load_value_oracle(
    jmd_path: pathlib.Path,
    json_path: pathlib.Path,
) -> tuple[str, object]:
    """Load fixture text and its independent JSON body oracle."""
    jmd_text = _read_jmd_fixture(jmd_path)
    expected = json.loads(json_path.read_text(encoding="utf-8"))
    return jmd_text, expected


def _load_envelope_oracle(
    mode: str,
    jmd_path: pathlib.Path,
    json_path: pathlib.Path,
) -> tuple[str, jmd.Envelope]:
    """Build a serializer oracle without parsing the fixture body."""
    jmd_text, expected = _load_value_oracle(jmd_path, json_path)
    parsed_mode, label, frontmatter, _ = jmd.JMDParser().parse_header(jmd_text)
    assert parsed_mode == mode
    return jmd_text, jmd.Envelope(
        mode=parsed_mode,
        label=label,
        value=expected,
        frontmatter=frontmatter,
    )


_FIXTURES_ROOT = _fixtures_root()
_validate_fixture_layout(_FIXTURES_ROOT)
_PAIRS = _collect_pairs(_FIXTURES_ROOT)
_CANONICAL = [pair for pair in _PAIRS if pair[0] != "tolerance"]
_CANONICAL_IDS = [
    f"{mode}/{jmd_path.stem}" for mode, jmd_path, _ in _CANONICAL
]
_MUST_FAIL = _collect_must_fail(_FIXTURES_ROOT)
_MUST_FAIL_IDS = [
    f"must-fail/{jmd_path.stem}" for jmd_path, _ in _MUST_FAIL
]
_PARSER_BACKENDS: tuple[ParserName, ...] = ("c", "py", "direct-py")
_SERIALIZER_BACKENDS: tuple[SerializerName, ...] = ("c", "py")


@pytest.fixture(params=_PARSER_BACKENDS)
def parser_surface(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> ParserSurface:
    """Select one parser surface without changing serializer dispatch."""
    name = cast(ParserName, request.param)
    if name == "c":
        if not _C_PARSER_AVAILABLE:
            pytest.skip("C parser unavailable; c-labelled case not executed")
        monkeypatch.setattr(jmd, "_HAS_CPARSER", True)
        return ParserSurface(name=name, parse=jmd.parse)
    if name == "py":
        monkeypatch.setattr(jmd, "_HAS_CPARSER", False)
        return ParserSurface(name=name, parse=jmd.parse)
    return ParserSurface(name=name, parse=jmd.JMDParser().parse)


@pytest.fixture(params=_SERIALIZER_BACKENDS)
def serializer_surface(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> SerializerSurface:
    """Select one serializer backend without changing parser dispatch."""
    name = cast(SerializerName, request.param)
    if name == "c":
        if not _C_SERIALIZER_AVAILABLE:
            pytest.skip(
                "C serializer unavailable; c-labelled case not executed"
            )
        monkeypatch.setattr(jmd, "_HAS_CSERIALIZER", True)
    else:
        monkeypatch.setattr(jmd, "_HAS_CSERIALIZER", False)
    return SerializerSurface(name=name, serialize=jmd.serialize)


@pytest.mark.parametrize(
    "fixtures_root",
    [pytest.param(_FIXTURES_ROOT, id=f"source={_FIXTURES_ROOT}")],
)
def test_fixture_source(fixtures_root: pathlib.Path) -> None:
    """Expose the exact fixture source in the qualification test IDs."""
    assert fixtures_root == _FIXTURES_ROOT
    assert all(
        (fixtures_root / name).is_dir() for name in _REQUIRED_DIRECTORIES
    )


@pytest.mark.parametrize(
    ("mode", "jmd_path", "json_path"),
    _PAIRS,
    ids=[f"{mode}/{path.stem}" for mode, path, _ in _PAIRS],
)
def test_parse(
    parser_surface: ParserSurface,
    mode: str,
    jmd_path: pathlib.Path,
    json_path: pathlib.Path,
) -> None:
    """Parse a fixture and compare its body with the JSON oracle."""
    jmd_text, expected = _load_value_oracle(jmd_path, json_path)
    envelope = parser_surface.parse(jmd_text)
    assert envelope.value == expected
    if mode in ("data", "schema", "query", "delete"):
        assert envelope.mode == mode


@pytest.mark.parametrize(
    ("mode", "jmd_path", "json_path"),
    _CANONICAL,
    ids=_CANONICAL_IDS,
)
def test_serialize(
    serializer_surface: SerializerSurface,
    mode: str,
    jmd_path: pathlib.Path,
    json_path: pathlib.Path,
) -> None:
    """Serialize an independent body oracle to canonical bytes."""
    jmd_text, oracle = _load_envelope_oracle(mode, jmd_path, json_path)
    output = serializer_surface.serialize(oracle)
    assert output + "\n" == jmd_text


@pytest.mark.parametrize(
    ("mode", "jmd_path", "json_path"),
    _CANONICAL,
    ids=_CANONICAL_IDS,
)
def test_roundtrip(
    parser_surface: ParserSurface,
    serializer_surface: SerializerSurface,
    mode: str,
    jmd_path: pathlib.Path,
    json_path: pathlib.Path,
) -> None:
    """Exercise every independent parser/serializer combination."""
    jmd_text, oracle = _load_envelope_oracle(mode, jmd_path, json_path)
    serialized = serializer_surface.serialize(oracle)
    reparsed = parser_surface.parse(serialized)
    assert serialized + "\n" == jmd_text
    assert reparsed == oracle


@pytest.mark.parametrize(
    ("jmd_path", "err_path"),
    _MUST_FAIL,
    ids=_MUST_FAIL_IDS,
)
def test_must_fail(
    parser_surface: ParserSurface,
    jmd_path: pathlib.Path,
    err_path: pathlib.Path,
) -> None:
    """Reject invalid input with the expected error kind and source line."""
    expected = json.loads(err_path.read_text(encoding="utf-8"))
    jmd_text = _read_jmd_fixture(jmd_path)
    with pytest.raises(JMDParseError) as exc:
        parser_surface.parse(jmd_text)
    assert exc.value.kind == expected["kind"]
    assert exc.value.line == expected["line"]


# ---------------------------------------------------------------------------
# Q3 — multiline values in array records (spec §§5.2, 8.3, and 9)
# ---------------------------------------------------------------------------

_Q3_ARRAY_MULTILINE_CASES = [
    pytest.param(
        "# Records[]\n"
        "- note:\n"
        "  > alpha\n"
        "  > beta\n"
        "  tail: after\n"
        "- id: 2\n"
        "  tail: later\n",
        [
            {"note": "alpha\nbeta", "tail": "after"},
            {"id": 2, "tail": "later"},
        ],
        id="first-field-blockquote-root-array",
    ),
    pytest.param(
        "# Records[]\n"
        "- id: 1\n"
        "  note:\n"
        "  > alpha\n"
        "  > beta\n"
        "  tail: after\n"
        "- id: 2\n",
        [
            {"id": 1, "note": "alpha\nbeta", "tail": "after"},
            {"id": 2},
        ],
        id="continuation-blockquote-root-array",
    ),
    pytest.param(
        "# Root\n"
        "## records[]\n"
        "- id: 1\n"
        "  note: |\n"
        "    alpha\n"
        "    beta\n"
        "  tail: after\n"
        "- id: 2\n",
        {
            "records": [
                {"id": 1, "note": "alpha\nbeta", "tail": "after"},
                {"id": 2},
            ]
        },
        id="literal-block-scalar-depth-two",
    ),
    pytest.param(
        "# Root\n"
        "## container\n"
        "### records[]\n"
        "- id: 1\n"
        "  note: >\n"
        "    alpha\n"
        "    beta\n"
        "  tail: after\n"
        "- id: 2\n",
        {
            "container": {
                "records": [
                    {"id": 1, "note": "alpha beta", "tail": "after"},
                    {"id": 2},
                ]
            }
        },
        id="folded-block-scalar-depth-three",
    ),
    pytest.param(
        "# Records[]\n"
        "- id: 1\n"
        "## before\n"
        "note:\n"
        "> alpha\n"
        "> beta\n"
        "## after\n"
        "value: end\n"
        "#\n"
        "- id: 2\n",
        [
            {
                "id": 1,
                "before": {"note": "alpha\nbeta"},
                "after": {"value": "end"},
            },
            {"id": 2},
        ],
        id="nested-sections-around-blockquote",
    ),
]


@pytest.mark.parametrize(
    ("jmd_text", "expected"),
    _Q3_ARRAY_MULTILINE_CASES,
)
def test_q3_array_record_multiline_parse(
    parser_surface: ParserSurface,
    jmd_text: str,
    expected: object,
) -> None:
    """Preserve multiline fields, later fields, and later array records."""
    assert parser_surface.parse(jmd_text).value == expected


_Q3_ROUNDTRIP_ENVELOPE = jmd.Envelope(
    mode="data",
    label="Records",
    frontmatter={},
    value=[
        {"id": 1, "note": "alpha\nbeta", "tail": "after"},
        {"id": 2, "tail": "later"},
    ],
)


def test_q3_array_record_multiline_roundtrip(
    parser_surface: ParserSurface,
    serializer_surface: SerializerSurface,
) -> None:
    """Round-trip multiline array records across every backend pairing."""
    serialized = serializer_surface.serialize(_Q3_ROUNDTRIP_ENVELOPE)
    assert parser_surface.parse(serialized) == _Q3_ROUNDTRIP_ENVELOPE


# ---------------------------------------------------------------------------
# Q6 — strict generator requirements (spec §§6.1, 11.2, and 22.1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ("data", "schema", "query", "delete"))
def test_q6_generator_rejects_unlabelled_object_root(
    serializer_surface: SerializerSurface,
    mode: DocumentMode,
) -> None:
    """Never emit an anonymous object root for any document mode."""
    envelope = jmd.Envelope(mode=mode, label="", value={})
    with pytest.raises(ValueError, match="non-empty label"):
        serializer_surface.serialize(envelope)


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        pytest.param(" leading", '# X\nv: " leading"', id="leading-space"),
        pytest.param("trailing  ", '# X\nv: "trailing  "', id="trailing-space"),
        pytest.param(
            "line\rbreak",
            '# X\nv: "line\\rbreak"',
            id="carriage-return",
        ),
    ),
)
def test_q6_generator_quotes_significant_whitespace(
    parser_surface: ParserSurface,
    serializer_surface: SerializerSurface,
    value: str,
    expected: str,
) -> None:
    """Quote whitespace that bare-value normalization would destroy."""
    envelope = jmd.Envelope(
        mode="data",
        label="X",
        value={"v": value},
    )
    serialized = serializer_surface.serialize(envelope)
    assert serialized == expected
    assert parser_surface.parse(serialized) == envelope


@pytest.mark.parametrize(
    "envelope",
    (
        pytest.param(
            jmd.Envelope(
                mode="data",
                label="X",
                value={"v": "before\x00after"},
            ),
            id="object-value",
        ),
        pytest.param(
            jmd.Envelope(
                mode="data",
                label="X",
                value=["before\x00after"],
            ),
            id="array-value",
        ),
        pytest.param(
            jmd.Envelope(
                mode="data",
                label="X",
                value={"before\x00after": "value"},
            ),
            id="object-key",
        ),
    ),
)
def test_q6_generator_preserves_embedded_nul(
    parser_surface: ParserSurface,
    serializer_surface: SerializerSurface,
    envelope: jmd.Envelope,
) -> None:
    """Preserve embedded NUL code points across every backend pairing."""
    serialized = serializer_surface.serialize(envelope)
    assert parser_surface.parse(serialized) == envelope


# ---------------------------------------------------------------------------
# Streaming backend — fixture coverage
#
# The batch parsers are qualified against every fixture above; the streaming
# parser was not exercised by this module at all. That gap let a canonical
# data/ fixture (array-level-pop) raise invalid_structure in the streaming
# backend while the suite stayed green.
#
# These tests deliberately do not reconstruct a value from the event stream.
# A nested container belonging to an array item is emitted after that item's
# ITEM_END, so folding events back into a document requires an assumption
# about event ordering that §18 does not state. Asserting acceptance and
# rejection is what can be checked without encoding that assumption; exact
# sequences for specific constructs are asserted in tests/test_streaming.py.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "jmd_path", "json_path"),
    _PAIRS,
    ids=[f"{mode}/{path.stem}" for mode, path, _ in _PAIRS],
)
def test_stream_accepts_every_fixture(
    mode: str,
    jmd_path: pathlib.Path,
    json_path: pathlib.Path,
) -> None:
    """Stream every fixture the batch parsers accept, without error."""
    del mode, json_path
    jmd_text = _read_jmd_fixture(jmd_path)
    events = list(jmd.jmd_stream(jmd_text))
    assert events, "streaming produced no events"
    assert events[0].type == "DOCUMENT_START"
    assert events[-1].type == "DOCUMENT_END"


@pytest.mark.parametrize(
    ("jmd_path", "err_path"),
    _MUST_FAIL,
    ids=_MUST_FAIL_IDS,
)
def test_stream_rejects_must_fail_fixtures(
    jmd_path: pathlib.Path,
    err_path: pathlib.Path,
) -> None:
    """Reject in the streaming backend whatever the batch parsers reject."""
    expected = json.loads(err_path.read_text(encoding="utf-8"))
    jmd_text = _read_jmd_fixture(jmd_path)
    with pytest.raises(JMDParseError) as exc:
        list(jmd.jmd_stream(jmd_text))
    assert exc.value.kind == expected["kind"]


# Fixtures the fold check cannot decide, by open specification question.
# Both are recorded as open in §18.2 / §22.2; neither is a defect in this
# implementation, and both should shrink this list when they are settled.
_FOLD_UNDECIDABLE = {
    # The root scope's representation is unsettled: a root array arrives as
    # an ARRAY_START keyed by the document label, indistinguishable from a
    # child array sharing that label, so a consumer cannot attribute it.
    "data/root-array",
    "delete/bulk-composite",
    "delete/bulk-scalar",
    "tolerance/depth-plus-one-root-array",
    "tolerance/indent-mixed",
    "tolerance/indent-single-space",
    "tolerance/indent-tab",
    "tolerance/thematic-break-continuation",
    # §7.4 promotes repeated headings to an implicit array. A streaming
    # parser cannot know about the promotion until the second heading
    # arrives, by which point the first scope has been emitted as an
    # object, so the stream and the batch value legitimately differ.
    "tolerance/repeated-headings-nested",
    "tolerance/repeated-headings-promote",
    "tolerance/repeated-headings-three",
}


def _fold_events(events: list[jmd.StreamEvent]) -> object:
    """Rebuild a document value from a well-formed event stream.

    §18.2 requires the stream to be a well-formed traversal: matched opens
    and closes, in reverse order, with an item's sub-structures nested
    inside that item's pair. A plain stack therefore suffices — a stream
    this cannot fold is not well-formed, which is the property under test.
    """
    root: object = None
    stack: list[object] = []
    pending_key: str | None = None
    pending: list[str] = []

    def innermost() -> object:
        nonlocal root
        if stack:
            return stack[-1]
        if root is None:
            root = {}
        return root

    def flush() -> None:
        nonlocal pending_key
        if pending_key is not None:
            obj = cast(dict[str, object], innermost())
            obj[pending_key] = "\n".join(pending)
            pending_key = None
            pending.clear()

    def attach(key: str | None, child: object) -> None:
        # Deliberately never promotes a container to the root: with the
        # root's representation unsettled (§18.2), a root-level ARRAY_START
        # cannot be told apart from a child array, and guessing would bake
        # one reading of an open question into the test. Documents whose
        # root is an array are skipped instead — see _FOLD_UNDECIDABLE.
        top = innermost()
        if isinstance(top, list):
            top.append(child)
        else:
            assert key is not None, "container in an object needs a key"
            cast(dict[str, object], top)[key] = child

    for event in events:
        kind = event.type
        if kind == "FIELD_CONTENT":
            pending.append(cast(str, event.value))
            continue
        flush()
        if kind in ("DOCUMENT_START", "DOCUMENT_END"):
            continue
        if kind in ("OBJECT_START", "ITEM_START"):
            child: object = {}
            attach(event.key, child)
            stack.append(child)
        elif kind == "ARRAY_START":
            child = []
            attach(event.key, child)
            stack.append(child)
        elif kind in ("OBJECT_END", "ARRAY_END", "ITEM_END"):
            assert stack, f"{kind} with no open scope"
            stack.pop()
        elif kind == "ITEM_VALUE":
            cast(list[object], innermost()).append(event.value)
        elif kind == "FIELD":
            target = cast(dict[str, object], innermost())
            target[cast(str, event.key)] = event.value
        elif kind == "FIELD_START":
            pending_key = cast(str, event.key)
        else:  # pragma: no cover - guards against a new event type
            raise AssertionError(f"unhandled event type {kind}")
    flush()

    assert not stack, f"{len(stack)} scope(s) left open at DOCUMENT_END"
    return {} if root is None else root


@pytest.mark.parametrize(
    ("mode", "jmd_path", "json_path"),
    _PAIRS,
    ids=[f"{mode}/{path.stem}" for mode, path, _ in _PAIRS],
)
def test_stream_events_fold_to_the_json_oracle(
    mode: str,
    jmd_path: pathlib.Path,
    json_path: pathlib.Path,
) -> None:
    """Fold each fixture's event stream and compare with the oracle."""
    if f"{mode}/{jmd_path.stem}" in _FOLD_UNDECIDABLE:
        pytest.skip("open specification question — see _FOLD_UNDECIDABLE")
    jmd_text, expected = _load_value_oracle(jmd_path, json_path)
    assert _fold_events(list(jmd.jmd_stream(jmd_text))) == expected
