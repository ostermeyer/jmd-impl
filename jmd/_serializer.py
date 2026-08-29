# SPDX-License-Identifier: Apache-2.0
"""JMD Serializer (v0.3.5 — indentation continuation, blockquote multiline)."""

from __future__ import annotations

import json
from collections.abc import Collection
from typing import Any, cast

from ._scalars import quote_key, serialize_scalar


def validate_label(label: str, *, root_is_array: bool) -> str:
    r"""Validate and normalize a root-heading label (D11).

    Args:
        label: The raw label string, possibly mode-prefixed.
        root_is_array: Whether the serialized root value is an array.

    Returns:
        The label with leading/trailing whitespace removed.

    Raises:
        ValueError: If the label contains ``\\n`` or ``\\r``, an object root
            has no label or an array sigil, or an array label already carries
            a non-empty ``[]`` sigil.
    """
    if "\n" in label or "\r" in label:
        raise ValueError(
            "JMD root labels must not contain newline characters; "
            f"got {label!r}"
        )
    label = label.lstrip()
    # Preserve a leading mode prefix (``- ``, ``? ``, ``! ``) together
    # with its trailing space — stripping it would erase the mode marker.
    if len(label) >= 2 and label[0] in "-?!" and label[1] == " ":
        label = label[:2] + label[2:].rstrip()
    else:
        label = label.rstrip()

    _, rest = _split_label(label)
    if not root_is_array and (not rest or rest.endswith("[]")):
        raise ValueError(
            "JMD object roots require a non-empty label without an [] sigil"
        )
    if root_is_array and rest not in ("", "[]") and rest.endswith("[]"):
        raise ValueError("JMD array labels must omit the [] sigil")
    return label


def _split_label(label: str) -> tuple[str, str]:
    """Split an optional mode-prefix off a root-heading label.

    Mode markers (``-``, ``?``, ``!``) attach directly to ``#`` in the
    root heading: ``#- Order``, ``#? Order``, ``#! Order``. Callers
    pass the mark as a ``- ``, ``? `` or ``! `` prefix on ``label``;
    the serializer attaches it to ``#`` without a space between them.
    Plain data documents (no prefix) emit ``# Label``.

    Args:
        label: Label string, optionally carrying a mode prefix.

    Returns:
        Tuple ``(mark, rest)`` where ``mark`` is ``""`` for data mode
        or one of ``"-"``, ``"?"``, ``"!"`` and ``rest`` is the label
        without the prefix.
    """
    if len(label) >= 2 and label[0] in "-?!" and label[1] == " ":
        return label[0], label[2:]
    return "", label

def _serialize_array_scalar(value: Any) -> str:
    """Serialize a scalar unambiguously in array-item position.

    Args:
        value: Scalar value to serialize.

    Returns:
        Canonical scalar text. Strings containing ``": "`` are quoted so
        the parser cannot mistake them for object items.
    """
    if isinstance(value, str) and ": " in value:
        return json.dumps(value, ensure_ascii=False)
    return serialize_scalar(value)


class _BlockquoteString(str):
    """Internal marker selecting JMD's blockquote rendering form."""

    _jmd_blockquote = True


PathSegment = tuple[str, bool]
BlockquotePath = tuple[str, ...]


def _unescape_pointer_segment(segment: str, path: str) -> str:
    """Return one JSON-Pointer segment after validating its escapes."""
    result: list[str] = []
    index = 0
    while index < len(segment):
        character = segment[index]
        if character != "~":
            result.append(character)
            index += 1
            continue
        if index + 1 == len(segment) or segment[index + 1] not in "01":
            raise ValueError(f"invalid JSON Pointer escape in {path!r}")
        result.append("~" if segment[index + 1] == "0" else "/")
        index += 2
    return "".join(result)


def normalize_blockquote_paths(
    paths: Collection[str] | None,
) -> frozenset[BlockquotePath]:
    """Validate caller-selected, JSON-Pointer-like render paths.

    Args:
        paths: Paths rooted at the data value. Object-key segments use JSON
            Pointer escaping. A star segment matches exactly one array item.

    Returns:
        Immutable decoded path segments.

    Raises:
        TypeError: If a path is not a string.
        ValueError: If a path is root-relative or has an invalid escape.
    """
    if paths is None:
        return frozenset()

    normalized: set[BlockquotePath] = set()
    for path in paths:
        if not isinstance(path, str):
            raise TypeError("blockquote paths must be strings")
        if not path.startswith("/"):
            raise ValueError(
                "blockquote paths must be non-root JSON Pointer paths"
            )
        normalized.add(
            tuple(
                _unescape_pointer_segment(segment, path)
                for segment in path[1:].split("/")
            )
        )
    return frozenset(normalized)


def _format_blockquote_path(path: BlockquotePath) -> str:
    """Return an escaped path for an actionable error message."""
    return "/" + "/".join(
        segment.replace("~", "~0").replace("/", "~1")
        for segment in path
    )


def select_blockquote_paths(
    data: Any,
    paths: Collection[str] | None,
) -> Any:
    """Mark selected object string fields for blockquote rendering.

    The markers exist only during generation. They are never part of the JMD
    value model and are not observable through parsing.

    Args:
        data: JMD value to prepare for generation.
        paths: Output-only paths of object string fields.

    Returns:
        A structurally equivalent value with internal string markers.

    Raises:
        ValueError: If a path does not identify an object string field.
    """
    selected_paths = normalize_blockquote_paths(paths)
    if not selected_paths:
        return data

    matched: set[BlockquotePath] = set()

    def path_matches(
        selected: BlockquotePath, current: tuple[PathSegment, ...]
    ) -> bool:
        """Return whether a control path matches the current data value."""
        return len(selected) == len(current) and all(
            expected == actual
            or (expected == "*" and is_array_index)
            for expected, (actual, is_array_index) in zip(
                selected, current, strict=True
            )
        )

    def transform(value: Any, current: tuple[PathSegment, ...]) -> Any:
        """Copy containers and mark a selected leaf."""
        matching = next(
            (
                selected
                for selected in selected_paths
                if path_matches(selected, current)
            ),
            None,
        )
        if matching is not None:
            if not isinstance(value, str) or current[-1][1]:
                raise ValueError(
                    "blockquote path must identify an object string field: "
                    f"{_format_blockquote_path(matching)}"
                )
            matched.add(matching)
            return _BlockquoteString(value)
        if isinstance(value, dict):
            return {
                key: transform(child, current + ((key, False),))
                for key, child in value.items()
            }
        if isinstance(value, list):
            return [
                transform(child, current + ((str(index), True),))
                for index, child in enumerate(value)
            ]
        return value

    prepared = transform(data, ())
    unmatched = selected_paths - matched
    if unmatched:
        raise ValueError(
            "blockquote path does not identify an object string field: "
            f"{_format_blockquote_path(next(iter(unmatched)))}"
        )
    return prepared


class JMDSerializer:
    r"""Serializes Python dicts/lists to JMD v0.3.5 format.

    Uses indentation continuation for array object items and
    blockquotes for multiline string values.

    Example:
        >>> JMDSerializer().serialize({"id": 42}, label="Order")
        '# Order\nid: 42'
    """

    def serialize(self, data: Any, label: str = "Document") -> str:
        """Serialize a Python value to a JMD document string.

        Raises:
            ValueError: If the label contains newline or carriage-return
                characters (D11). Leading/trailing whitespace is stripped
                silently.
        """
        label = validate_label(label, root_is_array=isinstance(data, list))
        mark, rest = _split_label(label)
        prefix = f"#{mark} "
        lines: list[str] = []
        if isinstance(data, list):
            root = f"{prefix}[]" if rest == "[]" else f"{prefix}{rest}[]"
            lines.append(root)
            self._write_array_items(data, lines, depth=1)
        else:
            lines.append(f"{prefix}{rest}")
            self._write_object_fields(
                cast(dict[str, Any], data), lines, depth=1
            )
        return "\n".join(lines)

    def _heading(self, depth: int) -> str:
        return "#" * depth + " "

    def _write_multiline(self, value: str, lines: list[str]) -> None:
        """Write a multiline string as blockquote lines."""
        for part in value.split("\n"):
            if part == "":
                lines.append(">")
            else:
                lines.append(f"> {part}")

    def _write_object_fields(
        self,
        obj: dict[str, Any],
        lines: list[str],
        depth: int,
    ) -> None:
        # JMD's nested headings remain open until another root-level
        # field is emitted. Group scalar fields before nested structures so
        # insertion order cannot turn a valid scalar into a heading.
        for nested in (False, True):
            for key, value in obj.items():
                is_nested = isinstance(value, (dict, list))
                if is_nested != nested:
                    continue
                k = quote_key(key)
                if isinstance(value, dict):
                    lines.append("")
                    lines.append(f"{self._heading(depth + 1)}{k}")
                    self._write_object_fields(
                        cast(dict[str, Any], value), lines, depth + 1
                    )
                elif isinstance(value, list):
                    lines.append("")
                    lines.append(f"{self._heading(depth + 1)}{k}[]")
                    self._write_array_items(value, lines, depth + 1)
                elif isinstance(value, str) and (
                    "\n" in value or isinstance(value, _BlockquoteString)
                ):
                    lines.append(f"{k}:")
                    self._write_multiline(value, lines)
                else:
                    lines.append(f"{k}: {serialize_scalar(value)}")

    def _write_array_items(
        self,
        lst: list[Any],
        lines: list[str],
        depth: int,
    ) -> None:
        if not lst:
            return

        all_lists = all(isinstance(item, list) for item in lst)
        all_dicts = all(isinstance(item, dict) for item in lst)
        all_scalars = all(
            not isinstance(item, (dict, list)) for item in lst
        )

        if all_lists:
            for item in lst:
                lines.append(f"{self._heading(depth + 1)}[]")
                self._write_array_items(cast(list[Any], item), lines, depth + 1)
        elif all_dicts:
            n = len(lst)
            for i, item in enumerate(lst):
                scalar_fields: dict[str, Any] = {
                    k: v for k, v in item.items()
                    if not isinstance(v, (dict, list))
                }
                nested_fields: dict[str, Any] = {
                    k: v for k, v in item.items()
                    if isinstance(v, (dict, list))
                }
                if scalar_fields:
                    first = True
                    for k, v in scalar_fields.items():
                        qk = quote_key(k)
                        is_blockquote = isinstance(v, _BlockquoteString) or (
                            isinstance(v, str) and "\n" in v
                        )
                        if is_blockquote:
                            if first:
                                lines.append("-")
                            lines.append(f"  {qk}:")
                            self._write_multiline(v, lines)
                        elif first:
                            lines.append(
                                f"- {qk}: {serialize_scalar(v)}"
                            )
                        else:
                            lines.append(
                                f"  {qk}: {serialize_scalar(v)}"
                            )
                        first = False
                else:
                    lines.append("-")
                if nested_fields:
                    self._write_object_fields(nested_fields, lines, depth)
                    # Level-pop (§8.6): this record opened a sub-structure
                    # (heading at depth+1). If more records follow, emit an
                    # anonymous heading at the array's own depth to pop back,
                    # so the next bare `-` item is read into THIS array. The
                    # last record needs no pop — end-of-scope closes it.
                    if i < n - 1:
                        lines.append("#" * depth)
        elif all_scalars:
            for item in lst:
                lines.append(f"- {_serialize_array_scalar(item)}")
        else:
            # Heterogeneous array — items mixing scalars, dicts, sub-arrays.
            #
            # After any item that opens a sub-scope (a nested list, or a
            # dict with nested fields), the NEXT item needs an explicit
            # depth-qualified heading `## - ...` (§8.6a/b) so the parser
            # pops out of the sub-scope and attaches the item to *this*
            # array.  A bare `- ...` would otherwise be consumed by the
            # innermost array or fail inside the opened object.
            # Depth-qualifier uses the array's own scope depth (§8.6a
            # same-depth form): `## items[]` lives at scope depth 2, so
            # its items take a `## - ...` prefix. The §8.6b parent-depth
            # form (`### - ...`) would be ambiguous if the previous item
            # was a sub-array also at depth 3.
            qualifier = self._heading(depth)
            needs_qualifier = False
            for item in lst:
                if isinstance(item, dict):
                    d_item = cast(dict[str, Any], item)
                    het_scalar_fields: dict[str, Any] = {
                        k: v for k, v in d_item.items()
                        if not isinstance(v, (dict, list))
                    }
                    het_nested_fields: dict[str, Any] = {
                        k: v for k, v in d_item.items()
                        if isinstance(v, (dict, list))
                    }
                    pfx = qualifier if needs_qualifier else ""
                    if het_scalar_fields:
                        first = True
                        for k, v in het_scalar_fields.items():
                            qk = quote_key(k)
                            is_blockquote = (
                                isinstance(v, _BlockquoteString)
                                or (isinstance(v, str) and "\n" in v)
                            )
                            if is_blockquote:
                                if first:
                                    lines.append(f"{pfx}-")
                                lines.append(f"  {qk}:")
                                self._write_multiline(v, lines)
                            elif first:
                                lines.append(
                                    f"{pfx}- {qk}: {serialize_scalar(v)}"
                                )
                            else:
                                lines.append(
                                    f"  {qk}: {serialize_scalar(v)}"
                                )
                            first = False
                    else:
                        lines.append(f"{pfx}-")
                    if het_nested_fields:
                        self._write_object_fields(
                            het_nested_fields, lines, depth)
                    needs_qualifier = bool(het_nested_fields)
                elif isinstance(item, list):
                    # Anonymous sub-array still opens at depth+1; only
                    # the item-qualifier shrinks to same-depth.
                    lines.append(f"{self._heading(depth + 1)}[]")
                    self._write_array_items(
                        item, lines, depth + 1
                    )
                    needs_qualifier = True
                else:
                    pfx = qualifier if needs_qualifier else ""
                    lines.append(f"{pfx}- {_serialize_array_scalar(item)}")
                    needs_qualifier = False
