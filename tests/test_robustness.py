# SPDX-License-Identifier: Apache-2.0
"""Termination and malformed-input robustness tests for JMD v0.3.5."""

from __future__ import annotations

import subprocess
import sys

import pytest

import jmd

_MALFORMED_SAME_DEPTH_SUBARRAY = (
    "# X\n"
    "## values[]\n"
    "- 1\n"
    "## - name: A\n"
    "## []\n"
    "- x\n"
)

_SUBPROCESS_PROBE = """
import sys
import jmd

backend = sys.argv[1]
source = sys.stdin.read()
try:
    if backend == "c":
        jmd._HAS_CPARSER = True
        jmd.parse(source)
    elif backend == "py":
        jmd._HAS_CPARSER = False
        jmd.parse(source)
    else:
        jmd.JMDParser().parse(source)
except Exception as exc:
    if getattr(exc, "kind", None) == "invalid_structure" and getattr(
        exc, "line", None
    ) == 5:
        raise SystemExit(0)
    raise SystemExit(2)
raise SystemExit(3)
"""


@pytest.mark.parametrize("backend", ("c", "py", "direct-py"))
def test_malformed_same_depth_subarray_fails_without_hanging(
    backend: str,
) -> None:
    """Reject an orphan same-depth sub-array with bounded progress."""
    if backend == "c" and not jmd._HAS_CPARSER:
        pytest.skip("C parser unavailable; c-labelled case not executed")

    try:
        completed = subprocess.run(
            [sys.executable, "-c", _SUBPROCESS_PROBE, backend],
            input=_MALFORMED_SAME_DEPTH_SUBARRAY,
            text=True,
            capture_output=True,
            check=False,
            timeout=1.0,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"{backend} parser did not make bounded progress")

    assert completed.returncode == 0, (
        f"{backend} parser returned {completed.returncode}: "
        f"stdout={completed.stdout!r}, stderr={completed.stderr!r}"
    )


@pytest.mark.skipif(not jmd._HAS_CPARSER, reason="C parser unavailable")
def test_c_key_cache_owns_cached_key_storage() -> None:
    """Keep cached keys valid after each short-lived source is released."""
    colliding_keys = ("aact", "aada")
    for value in range(1_000):
        for key in colliding_keys:
            source = f"# X\n{key}: {value}"
            assert jmd.parse(source).value == {key: value}
