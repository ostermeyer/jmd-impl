# SPDX-License-Identifier: Apache-2.0
"""Conformance tests against the canonical JMD test suite.

Fixtures live in ``jmd-spec`` at ``conformance/``.  They are located
via the first path that exists, in this order:

1. the ``JMD_FIXTURES`` environment variable (explicit override)
2. ``vendor/jmd-spec/conformance/`` (git submodule, preferred in CI)
3. ``../jmd-spec/conformance/`` (sibling checkout in a workspace)

Each fixture is a pair ``<name>.jmd`` + ``<name>.json``.  Fixtures
are grouped by document mode:

* ``data/``, ``schema/``, ``query/``, ``delete/`` — canonical
  documents.  Three tests run for every pair:

  1. **Parse**     — ``jmd.parse(.jmd).value`` deep-equals ``.json``
                     and the envelope ``.mode`` matches the directory
  2. **Serialize** — ``jmd.serialize(envelope)`` equals ``.jmd``
                     byte-for-byte (envelope from a prior parse)
  3. **Round-trip** — parse → serialize → parse again preserves
                      the envelope (§3.6.3)

* ``tolerance/`` — inputs exercising parser-tolerance rules where the
  canonical output diverges from the input.  Only the **Parse** test
  runs; Serialize would re-canonicalize and therefore not match.

Every test runs against both available backends — the C accelerator
(the production default when compiled) and the pure-Python fallback
(what users without a compiler get).  The ``backend`` fixture
monkeypatches ``_HAS_CPARSER`` / ``_HAS_CSERIALIZER`` to force the
fallback path; without this, a regression in the pure-Python code
would be invisible whenever a ``.so`` is present.
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
    repo_root = here.parent
    for candidate in (
        repo_root / "vendor" / "jmd-spec" / "conformance",
        repo_root.parent / "jmd-spec" / "conformance",
    ):
        if candidate.exists():
            return candidate
    return None


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


def _collect_must_fail() -> list[tuple[pathlib.Path, pathlib.Path]]:
    """Return (jmd_path, error_json_path) for every must-fail fixture pair."""
    root = _fixtures_root()
    if root is None:
        return []
    mf_dir = root / "must-fail"
    if not mf_dir.is_dir():
        return []
    pairs: list[tuple[pathlib.Path, pathlib.Path]] = []
    for jmd_path in sorted(mf_dir.glob("*.jmd")):
        err_path = jmd_path.with_suffix(".error.json")
        if err_path.exists():
            pairs.append((jmd_path, err_path))
    return pairs


_PAIRS = [p for p in _collect_pairs() if p[0] != "must-fail"]
_CANONICAL = [p for p in _PAIRS if p[0] != "tolerance"]
_CANONICAL_IDS = [f"{m}/{p.stem}" for m, p, _ in _CANONICAL]
_MUST_FAIL = _collect_must_fail()
_MUST_FAIL_IDS = [f"must-fail/{p.stem}" for p, _ in _MUST_FAIL]

# Which backends to exercise.  "c" uses the C-accelerated parser/
# serializer if compiled; "py" forces the pure-Python fallback by
# monkey-patching the availability flags.  Having both paths in the
# conformance suite guards against silent drift between the two — the
# original wrapper picked one at import time, leaving the other path
# untested.
_BACKENDS = ["c", "py"]


@pytest.fixture(params=_BACKENDS)
def backend(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> str:
    """Select the parser/serializer backend for a test run.

    The ``"c"`` param leaves the module flags alone (C accelerator
    wins if compiled).  The ``"py"`` param disables both accelerators
    at the public-API boundary; :class:`jmd.JMDParser` is already
    pure Python (no internal C dispatch), so flipping the two
    ``_HAS_*`` flags in :mod:`jmd` is sufficient to force the
    Python parse + serialize paths.
    """
    backend_name: str = request.param
    if backend_name == "py":
        monkeypatch.setattr(jmd, "_HAS_CPARSER", False)
        monkeypatch.setattr(jmd, "_HAS_CSERIALIZER", False)
    return backend_name


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
    backend: str,
    mode: str,
    jmd_path: pathlib.Path,
    json_path: pathlib.Path,
) -> None:
    """Parse the fixture; envelope.value matches the .json oracle.

    Also asserts that the envelope mode matches the fixture's
    directory name (``data/``, ``schema/``, ``query/``, ``delete/``)
    — the tolerance/ tree carries its own canonical mode in the fixture.
    """
    del backend
    if "crlf" in jmd_path.stem:
        # §11.2 CRLF-tolerance fixtures must reach the parser byte-exact,
        # not LF-normalized — else they only exercise the LF path.
        with jmd_path.open(encoding="utf-8", newline="") as fh:
            jmd_text = fh.read()
    else:
        jmd_text = jmd_path.read_text(encoding="utf-8")
    expected = json.loads(json_path.read_text(encoding="utf-8"))
    env = jmd.parse(jmd_text)
    assert env.value == expected
    if mode in ("data", "schema", "query", "delete"):
        assert env.mode == mode


@pytest.mark.skipif(
    not _CANONICAL,
    reason="jmd-spec canonical fixtures not found",
)
@pytest.mark.parametrize(
    ("mode", "jmd_path", "json_path"),
    _CANONICAL,
    ids=_CANONICAL_IDS,
)
def test_serialize(
    backend: str,
    mode: str,
    jmd_path: pathlib.Path,
    json_path: pathlib.Path,
) -> None:
    """Serialize the envelope and byte-compare against the .jmd fixture.

    Per §3.6.3 the canonical serializer entry takes an envelope
    directly. Parsing the fixture and serializing the envelope is
    the canonical round-trip path.
    """
    del backend, mode
    jmd_text = jmd_path.read_text(encoding="utf-8")
    expected = json.loads(json_path.read_text(encoding="utf-8"))
    env = jmd.parse(jmd_text)
    # Sanity check the envelope before serializing — guards against
    # the serializer compensating for a parse bug we'd rather see.
    assert env.value == expected
    out = jmd.serialize(env)
    # Fixture files end with a single trailing newline; the serializer
    # mirrors the byte form emitted by the C-accelerated reference (no
    # trailing newline — callers add it when writing a file).
    assert out + "\n" == jmd_text


@pytest.mark.skipif(
    not _CANONICAL,
    reason="jmd-spec canonical fixtures not found",
)
@pytest.mark.parametrize(
    ("mode", "jmd_path", "json_path"),
    _CANONICAL,
    ids=_CANONICAL_IDS,
)
def test_roundtrip(
    backend: str,
    mode: str,
    jmd_path: pathlib.Path,
    json_path: pathlib.Path,
) -> None:
    """Parse → serialize → parse preserves the envelope (§3.6.3)."""
    del backend, mode
    jmd_text = jmd_path.read_text(encoding="utf-8")
    expected = json.loads(json_path.read_text(encoding="utf-8"))
    env1 = jmd.parse(jmd_text)
    env2 = jmd.parse(jmd.serialize(env1))
    assert env2.value == expected
    assert env2.mode == env1.mode
    assert env2.label == env1.label
    assert env2.frontmatter == env1.frontmatter


@pytest.mark.skipif(
    not _MUST_FAIL,
    reason="jmd-spec must-fail fixtures not found",
)
@pytest.mark.parametrize(
    ("jmd_path", "err_path"),
    _MUST_FAIL,
    ids=_MUST_FAIL_IDS,
)
def test_must_fail(
    backend: str,
    jmd_path: pathlib.Path,
    err_path: pathlib.Path,
) -> None:
    """Parser MUST reject the fixture with the expected structured error.

    Asserts only kind and line — wording and exception subclass identity
    are implementation-specific. ``key`` and other advisory fields in
    the .error.json are not asserted.
    """
    del backend  # only used for the test id
    from jmd._parser import JMDParseError

    expected = json.loads(err_path.read_text(encoding="utf-8"))
    # Read raw (no newline translation) so §11.2 line-ending fixtures
    # (lone-CR) reach the parser; parse/serialize/roundtrip normalize CRLF.
    # py<3.13-kompatibel
    with jmd_path.open(encoding="utf-8", newline="") as fh:
            jmd_text = fh.read()
    with pytest.raises(JMDParseError) as exc:
        jmd.parse(jmd_text)
    assert exc.value.kind == expected["kind"]
    assert exc.value.line == expected["line"]
