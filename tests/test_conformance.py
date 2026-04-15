"""Conformance tests against the canonical JMD test suite.

Fixtures live in the sibling ``jmd-spec`` repository at
``conformance/``.  The search path can be overridden with the
``JMD_FIXTURES`` environment variable.

Each fixture is a pair ``<name>.jmd`` + ``<name>.json``.  Fixtures
are grouped by document mode:

* ``data/``, ``schema/``, ``query/``, ``delete/`` — canonical
  documents.  Three tests run for every pair:

  1. **Parse**     — ``jmd.parse(.jmd)`` deep-equals ``.json``
  2. **Serialize** — ``jmd.serialize(value, label)`` equals ``.jmd``
     byte-for-byte (label reconstructed from the root heading)
  3. **Round-trip** — ``parse(serialize(parse(.jmd), ...))`` yields
     the same value

* ``tolerance/`` — inputs exercising parser-tolerance rules where the
  canonical output diverges from the input.  Only the **Parse** test
  runs; Serialize would re-canonicalize and therefore not match.
"""

from __future__ import annotations

import json
import os
import pathlib
import re

import pytest

import jmd

_MODE_PREFIX = {
    "data": "",
    "schema": "! ",
    "query": "? ",
    "delete": "- ",
}

_HEADING_RE = re.compile(r"^#([!?-])?\s+(.*)$")


def _fixtures_root() -> pathlib.Path | None:
    """Return the conformance fixtures root or ``None`` if not found."""
    env = os.environ.get("JMD_FIXTURES")
    if env:
        return pathlib.Path(env)
    here = pathlib.Path(__file__).resolve().parent
    candidate = here.parent.parent / "jmd-spec" / "conformance"
    return candidate if candidate.exists() else None


def _collect_pairs() -> list[tuple[str, pathlib.Path, pathlib.Path]]:
    """Return (mode, jmd_path, json_path) for every fixture pair."""
    root = _fixtures_root()
    if root is None:
        return []
    pairs: list[tuple[str, pathlib.Path, pathlib.Path]] = []
    for mode_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for jmd_path in sorted(mode_dir.glob("*.jmd")):
            json_path = jmd_path.with_suffix(".json")
            if json_path.exists():
                pairs.append((mode_dir.name, jmd_path, json_path))
    return pairs


def _extract_label(jmd_text: str) -> str:
    """Extract the bare label from the first heading of a JMD document.

    Frontmatter and blank lines before the heading are skipped.  The
    trailing ``[]`` sigil for root arrays is stripped — the serializer
    re-adds it when the value is a list.
    """
    for line in jmd_text.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            text = m.group(2)
            return text[:-2] if text.endswith("[]") else text
    msg = "No root heading found"
    raise ValueError(msg)


def _label_arg(mode: str, label: str) -> str:
    """Build the mode-prefixed label to pass to ``jmd.serialize``."""
    return _MODE_PREFIX.get(mode, "") + label


_PAIRS = _collect_pairs()
_CANONICAL = [p for p in _PAIRS if p[0] != "tolerance"]
_CANONICAL_IDS = [f"{m}/{p.stem}" for m, p, _ in _CANONICAL]


@pytest.mark.skipif(
    not _PAIRS,
    reason="jmd-spec fixtures not found — clone ostermeyer/jmd-spec as a "
    "sibling or set JMD_FIXTURES",
)
@pytest.mark.parametrize(
    ("mode", "jmd_path", "json_path"),
    _PAIRS,
    ids=[f"{m}/{p.stem}" for m, p, _ in _PAIRS],
)
def test_parse(
    mode: str, jmd_path: pathlib.Path, json_path: pathlib.Path,
) -> None:
    """Parse the fixture and deep-compare against the expected JSON value."""
    del mode  # used only for the test id
    jmd_text = jmd_path.read_text(encoding="utf-8")
    expected = json.loads(json_path.read_text(encoding="utf-8"))
    assert jmd.parse(jmd_text) == expected


@pytest.mark.skipif(
    not _CANONICAL,
    reason="jmd-spec canonical fixtures not found",
)
@pytest.mark.parametrize(
    ("mode", "jmd_path", "json_path"), _CANONICAL, ids=_CANONICAL_IDS,
)
def test_serialize(
    mode: str, jmd_path: pathlib.Path, json_path: pathlib.Path,
) -> None:
    """Serialize the .json and byte-compare against the .jmd fixture."""
    jmd_text = jmd_path.read_text(encoding="utf-8")
    expected = json.loads(json_path.read_text(encoding="utf-8"))
    label = _label_arg(mode, _extract_label(jmd_text))
    # JMDParser exposes the frontmatter dict as a side-effect of parsing;
    # the top-level jmd.parse() returns only the value.
    parser = jmd.JMDParser()
    parser.parse(jmd_text)
    fm = parser.frontmatter or None
    out = jmd.serialize(expected, label=label, frontmatter=fm)
    # Fixture files end with a single trailing newline; the serializer
    # mirrors the byte form emitted by the C-accelerated reference (no
    # trailing newline — callers add it when writing a file).
    assert out + "\n" == jmd_text


@pytest.mark.skipif(
    not _CANONICAL,
    reason="jmd-spec canonical fixtures not found",
)
@pytest.mark.parametrize(
    ("mode", "jmd_path", "json_path"), _CANONICAL, ids=_CANONICAL_IDS,
)
def test_roundtrip(
    mode: str, jmd_path: pathlib.Path, json_path: pathlib.Path,
) -> None:
    """Parse, serialize, parse again — must yield the original value."""
    jmd_text = jmd_path.read_text(encoding="utf-8")
    expected = json.loads(json_path.read_text(encoding="utf-8"))
    label = _label_arg(mode, _extract_label(jmd_text))
    parser = jmd.JMDParser()
    value = parser.parse(jmd_text)
    fm = parser.frontmatter or None
    out = jmd.serialize(value, label=label, frontmatter=fm)
    assert jmd.parse(out) == expected
