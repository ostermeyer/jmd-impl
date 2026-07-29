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
