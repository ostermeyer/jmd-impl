# SPDX-License-Identifier: Apache-2.0
"""Verify lexer failures using the real C source and test-only wrappers."""

import importlib.util
import shlex
import subprocess
import sys
import sysconfig
from pathlib import Path
from typing import Protocol, cast

import pytest

import jmd


class LexerProbe(Protocol):
    """Typed interface of the native lexer fault probe."""

    def parse(self, text: str, fail_at: int, error_kind: int) -> object:
        """Convert text with the chosen failure injected."""
        ...

    def lines(self, fail_at: int) -> None:
        """Check vector state and ownership during allocation failure."""
        ...

    def overflow(self, mode: int) -> None:
        """Exercise a size bound without allocating huge buffers."""
        ...


@pytest.fixture(scope="module")
def lexer_probe(tmp_path_factory: pytest.TempPathFactory) -> LexerProbe:
    """Compile the production lexer with local fault-injection wrappers."""
    if not jmd._HAS_CPARSER:
        pytest.skip("Native parser is unavailable")
    if sys.platform == "win32":
        pytest.skip("Probe compiler command currently uses POSIX LDSHARED")
    destination = tmp_path_factory.mktemp("lexer-probe")
    extension = destination / (
        "_lexer_probe" + str(sysconfig.get_config_var("EXT_SUFFIX"))
    )
    source = Path(__file__).with_name("lexer_allocator_probe.c")
    command = shlex.split(str(sysconfig.get_config_var("LDSHARED")))
    command += [
        "-fPIC", "-Wall", "-Wextra", "-Werror",
        "-I" + sysconfig.get_path("include"),
        str(source), "-o", str(extension),
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, check=False, timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    spec = importlib.util.spec_from_file_location("_lexer_probe", extension)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(LexerProbe, module)


@pytest.mark.parametrize("raw, fail_at", [
    ("5", 1), ("9" * 25, 1),
    ("9" * 300, 1), ("9" * 300, 2),
    ("0.5", 1), ("0.5" + "0" * 300, 1),
    ("0.5" + "0" * 300, 2),
])
def test_numeric_memory_error_propagates(
    lexer_probe: LexerProbe, raw: str, fail_at: int,
) -> None:
    """Scratch and result allocation failures raise and leave no leak."""
    with pytest.raises(MemoryError):
        lexer_probe.parse(raw, fail_at, 1)
    expected = float(raw) if "." in raw else int(raw)
    assert lexer_probe.parse(raw, 0, 1) == expected


@pytest.mark.parametrize("raw, fail_at", [
    ("9" * 25, 1), ("9" * 300, 2),
])
def test_only_value_error_allows_string_fallback(
    lexer_probe: LexerProbe, raw: str, fail_at: int,
) -> None:
    """Neither short nor long integer paths swallow unrelated exceptions."""
    with pytest.raises(RuntimeError, match="injected"):
        lexer_probe.parse(raw, fail_at, 2)
    assert lexer_probe.parse(raw, fail_at, 3) == raw


@pytest.mark.parametrize("fail_at", [1, 2])
def test_line_vector_allocation_failure(
    lexer_probe: LexerProbe, fail_at: int,
) -> None:
    """Initialization/growth failures are explicit and preserve prior data."""
    with pytest.raises(MemoryError):
        lexer_probe.lines(fail_at)
    lexer_probe.lines(0)


@pytest.mark.parametrize("mode", [0, 1, 2])
def test_lexer_size_bounds(lexer_probe: LexerProbe, mode: int) -> None:
    """Vector growth and NUL termination cannot overflow their byte counts."""
    with pytest.raises(MemoryError):
        lexer_probe.overflow(mode)
