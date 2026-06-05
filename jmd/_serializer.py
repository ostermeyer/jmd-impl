# SPDX-License-Identifier: Apache-2.0
"""JMD Serializer (v0.3.3 — indentation continuation, blockquote multiline)."""

from __future__ import annotations

from typing import Any, cast

from ._scalars import quote_key, serialize_scalar


def validate_label(label: str) -> str:
    r"""Validate and normalize a root-heading label (D11).

    Strips leading and trailing whitespace. Rejects labels containing
    newline or carriage-return characters, which would split the root
    heading into multiple lines and corrupt the document structure.

    Args:
        label: The raw label string, possibly mode-prefixed.

    Returns:
        The label with leading/trailing whitespace removed.

    Raises:
        ValueError: If the label contains ``\\n`` or ``\\r``.
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
        return label[:2] + label[2:].rstrip()
    return label.rstrip()


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


class JMDSerializer:
    r"""Serializes Python dicts/lists to JMD v0.3.3 format.

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
        label = validate_label(label)
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
        needs_heading = False
        for key, value in obj.items():
            k = quote_key(key)
            if isinstance(value, dict):
                lines.append("")
                lines.append(f"{self._heading(depth + 1)}{k}")
                self._write_object_fields(
                    cast(dict[str, Any], value), lines, depth + 1
                )
                needs_heading = True
            elif isinstance(value, list):
                lines.append("")
                lines.append(f"{self._heading(depth + 1)}{k}[]")
                self._write_array_items(
                    value, lines, depth + 1
                )
                needs_heading = True
            elif isinstance(value, str) and "\n" in value:
                # Multiline string → blockquote
                if needs_heading:
                    lines.append(f"{self._heading(depth + 1)}{k}:")
                else:
                    lines.append(f"{k}:")
                self._write_multiline(value, lines)
                needs_heading = True  # next scalar needs a heading
            elif needs_heading:
                lines.append(f"{self._heading(depth + 1)}{k}: "
                             f"{serialize_scalar(value)}")
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
                    # First field on the '- ' line, rest indented.
                    first = True
                    for k, v in scalar_fields.items():
                        sv = serialize_scalar(v)
                        qk = quote_key(k)
                        if first:
                            lines.append(f"- {qk}: {sv}")
                            first = False
                        else:
                            lines.append(f"  {qk}: {sv}")
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
                lines.append(f"- {serialize_scalar(item)}")
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
                            sv = serialize_scalar(v)
                            qk = quote_key(k)
                            if first:
                                lines.append(f"{pfx}- {qk}: {sv}")
                                first = False
                            else:
                                lines.append(f"  {qk}: {sv}")
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
                    lines.append(f"{pfx}- {serialize_scalar(item)}")
                    needs_qualifier = False
