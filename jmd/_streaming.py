# SPDX-License-Identifier: Apache-2.0
"""Public adapters for incremental JMD event parsing (v0.3.5)."""

from __future__ import annotations

import codecs
from collections.abc import AsyncIterable, AsyncIterator, Generator

from ._stream_events import StreamEvent as StreamEvent
from ._stream_parser import JMDStreamParser as JMDStreamParser


def jmd_stream(source: str) -> Generator[StreamEvent, None, None]:
    """Generate events incrementally from a complete JMD source string.

    Args:
        source: Complete JMD document text.

    Yields:
        Events in the order in which completed source lines make them
        semantically available.
    """
    parser = JMDStreamParser()
    parts = source.split("\n")
    for index, raw in enumerate(parts):
        terminated = index < len(parts) - 1
        if not terminated and raw == "":
            break
        if terminated and raw.endswith("\r"):
            raw = raw[:-1]
        yield from parser.process_line(raw)
    yield from parser.finish()


async def to_lines(
    source: AsyncIterable[str | bytes | bytearray],
) -> AsyncIterator[str]:
    r"""Adapt asynchronous text or UTF-8 byte chunks to source lines.

    UTF-8 decoding is incremental, so one code point may span byte chunks.
    Lines are split on ``\n`` and the CR half of CRLF is removed. A final
    unterminated non-empty line is yielded.

    Args:
        source: Asynchronous iterable of text or UTF-8 byte chunks.

    Yields:
        Completed source lines without line endings.
    """
    decoder = codecs.getincrementaldecoder("utf-8")()
    decoding_bytes = False
    buffer = ""

    async for chunk in source:
        if isinstance(chunk, str):
            if decoding_bytes:
                buffer += decoder.decode(b"", final=True)
                decoder = codecs.getincrementaldecoder("utf-8")()
                decoding_bytes = False
            buffer += chunk
        else:
            buffer += decoder.decode(bytes(chunk), final=False)
            decoding_bytes = True

        while True:
            newline = buffer.find("\n")
            if newline < 0:
                break
            line = buffer[:newline]
            if line.endswith("\r"):
                line = line[:-1]
            yield line
            buffer = buffer[newline + 1 :]

    if decoding_bytes:
        buffer += decoder.decode(b"", final=True)
    if buffer:
        if buffer.endswith("\r"):
            buffer = buffer[:-1]
        yield buffer


async def events(
    source: AsyncIterable[str],
) -> AsyncIterator[StreamEvent]:
    """Generate events incrementally from asynchronous source lines.

    Args:
        source: Asynchronous iterable of lines without line endings.

    Yields:
        Each event before the next source line is requested.
    """
    parser = JMDStreamParser()
    async for line in source:
        for event in parser.process_line(line):
            yield event
    for event in parser.finish():
        yield event
