# SPDX-License-Identifier: Apache-2.0
"""Fault-inject the production C multiline parser in an isolated test module."""

import importlib.util
import shlex
import subprocess
import sys
import sysconfig
from pathlib import Path
from typing import Protocol, cast

import pytest

import jmd


class MultilineProbe(Protocol):
    """Narrow typed interface of the test-only native module."""

    def parse(self, mode: int, fail_at: int) -> str:
        """Parse 300 lines with the selected allocation failure."""
        ...

    def overflow(self) -> None:
        """Verify that impossible sizes fail before allocating."""
        ...


@pytest.fixture(scope="module")
def allocator_probe(tmp_path_factory: pytest.TempPathFactory) -> MultilineProbe:
    """Compile production multiline code with local allocation wrappers."""
    if not jmd._HAS_CPARSER:
        pytest.skip("Native parser is unavailable")
    if sys.platform == "win32":
        pytest.skip("Probe compiler command currently uses POSIX LDSHARED")
    destination = tmp_path_factory.mktemp("multiline-probe")
    extension = destination / (
        "_multiline_probe" + str(sysconfig.get_config_var("EXT_SUFFIX"))
    )
    source = Path(__file__).with_name("multiline_allocator_probe.c")
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
    spec = importlib.util.spec_from_file_location("_multiline_probe", extension)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(MultilineProbe, module)


@pytest.mark.parametrize("mode", [0, 1, 2])
@pytest.mark.parametrize("fail_at", [1, 2, 3, 4, 5, 6, 7])
def test_allocation_failure_is_atomic(
    allocator_probe: MultilineProbe, mode: int, fail_at: int,
) -> None:
    """Each growth/render failure raises, frees scratch, and permits retry."""
    with pytest.raises(MemoryError):
        allocator_probe.parse(mode, fail_at)
    separator = " " if mode == 2 else "\n"
    assert allocator_probe.parse(mode, 0) == separator.join(["text"] * 300)


def test_size_overflow_raises_before_allocation(
    allocator_probe: MultilineProbe,
) -> None:
    """Vector and output sizing reject overflow without touching dummy input."""
    allocator_probe.overflow()
