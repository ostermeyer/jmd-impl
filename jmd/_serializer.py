"""JMD Serializer (v0.3 — indentation continuation, blockquote multiline)."""

from __future__ import annotations

from typing import Any, cast

from ._scalars import quote_key, serialize_scalar


class JMDSerializer:
    r"""Serializes Python dicts/lists to JMD v0.3 format.

    Uses indentation continuation for array object items and
    blockquotes for multiline string values.

    Example:
        >>> JMDSerializer().serialize({"id": 42}, label="Order")
        '# Order\nid: 42'
    """

    def serialize(self, data: Any, label: str = "Document") -> str:
        """Serialize a Python value to a JMD document string."""
        lines: list[str] = []
        if isinstance(data, list):
            root = "# []" if label == "[]" else f"# {label}[]"
            lines.append(root)
            self._write_array_items(data, lines, depth=1)
        else:
            lines.append(f"# {label}")
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
            has_nested = any(
                any(isinstance(v, (dict, list)) for v in item.values())
                for item in lst
            )
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
                    # First field on the '- ' line, rest indented
                    first = True
                    for k, v in scalar_fields.items():
                        sv = serialize_scalar(v)
                        qk = quote_key(k)
                        if first:
                            if i > 0 and has_nested:
                                lines.append("")
                                lines.append("---")
                                lines.append("")
                            lines.append(f"- {qk}: {sv}")
                            first = False
                        else:
                            lines.append(f"  {qk}: {sv}")
                else:
                    if i > 0 and has_nested:
                        lines.append("")
                        lines.append("---")
                        lines.append("")
                    lines.append("-")
                if nested_fields:
                    self._write_object_fields(nested_fields, lines, depth)
        elif all_scalars:
            for item in lst:
                lines.append(f"- {serialize_scalar(item)}")
        else:
            # Heterogeneous array
            deeper_scope_opened = False
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
                    if het_scalar_fields:
                        first = True
                        for k, v in het_scalar_fields.items():
                            sv = serialize_scalar(v)
                            qk = quote_key(k)
                            if first:
                                if deeper_scope_opened:
                                    lines.append("")
                                    lines.append("---")
                                    lines.append("")
                                lines.append(f"- {qk}: {sv}")
                                first = False
                            else:
                                lines.append(f"  {qk}: {sv}")
                    else:
                        if deeper_scope_opened:
                            lines.append("")
                            lines.append("---")
                            lines.append("")
                        lines.append("-")
                    if het_nested_fields:
                        self._write_object_fields(
                            het_nested_fields, lines, depth)
                    if any(isinstance(v, (dict, list))
                           for v in d_item.values()):
                        deeper_scope_opened = True
                elif isinstance(item, list):
                    lines.append(f"{self._heading(depth + 1)}[]")
                    self._write_array_items(
                        item, lines, depth + 1
                    )
                    deeper_scope_opened = True
                else:
                    lines.append(f"- {serialize_scalar(item)}")
