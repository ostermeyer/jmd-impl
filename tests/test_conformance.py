"""Conformance tests against the canonical JMD test suite.

Fixtures live in the sibling ``jmd-spec`` repository at
``conformance/``.  The search path can be overridden with the
``JMD_FIXTURES`` environment variable.

Each fixture is a pair ``<name>.jmd`` + ``<name>.json``.  The ``data/``
subdirectory contains canonical documents; the ``tolerance/``
subdirectory contains inputs that exercise parser-tolerance rules
(depth-qualified items, depth+1 items, etc.) where the input is
deliberately non-canonical.  For every pair we run a Parse test:
``jmd.parse(.jmd)`` must deep-equal the value in ``.json``.

Serializer parity with the JavaScript implementation is validated
separately by the cross-implementation stress harness in the jmd-js
repo; this module focuses on the parser contract.
"""

from __future__ import annotations

import json
import os
import pathlib

import pytest

import jmd


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


_PAIRS = _collect_pairs()


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
