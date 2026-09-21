# SPDX-License-Identifier: Apache-2.0
"""Tests for the ``jmd`` command-line interface."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from jmd._cli import _USAGE, main

ORDER_JMD = "# Order\nid: 42\nstatus: pending\n"


class _Terminal(io.StringIO):
    """Stdin stand-in for an interactive terminal."""

    def isatty(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("argv", [[], ["-h"], ["--help"], ["help"]])
def test_help_prints_usage_only(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(argv) == 0
    captured = capsys.readouterr()
    assert captured.out == _USAGE
    assert captured.err == ""


def test_usage_points_to_spec() -> None:
    assert "https://github.com/ostermeyer/jmd-spec" in _USAGE


def test_bare_invocation_writes_nothing(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "jmd"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == _USAGE
    assert list(tmp_path.iterdir()) == []


def test_unknown_command_fails_with_usage_on_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["frobnicate"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Unknown command: frobnicate" in captured.err
    assert _USAGE in captured.err


# ---------------------------------------------------------------------------
# Input: file, stdin, none
# ---------------------------------------------------------------------------


def test_to_json_reads_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "order.jmd"
    src.write_text(ORDER_JMD, encoding="utf-8")
    assert main(["to-json", str(src)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "id": 42,
        "status": "pending",
    }


@pytest.mark.parametrize("argv", [["to-json"], ["to-json", "-"]])
def test_to_json_reads_piped_stdin(
    argv: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(ORDER_JMD))
    assert main(argv) == 0
    assert json.loads(capsys.readouterr().out) == {
        "id": 42,
        "status": "pending",
    }


def test_from_json_reads_piped_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"id": 42}'))
    assert main(["from-json", "--label", "Order"]) == 0
    assert capsys.readouterr().out == "# Order\nid: 42\n"


@pytest.mark.parametrize(
    "cmd", ["to-json", "from-json", "render", "roundtrip"]
)
def test_missing_input_on_terminal_fails_fast(
    cmd: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "stdin", _Terminal())
    assert main([cmd]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"jmd {cmd}: no input" in captured.err


def test_roundtrip_reports_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(ORDER_JMD))
    assert main(["roundtrip"]) == 0
    assert "Roundtrip OK" in capsys.readouterr().out
