# SPDX-License-Identifier: Apache-2.0
"""JSON Schema to JMD schema conversion."""

from __future__ import annotations

import json
from typing import Any, cast

from ._scalars import quote_key


def _jmd_type_expr(
    prop: dict[str, Any], key: str, required_keys: set[str]
) -> str:
    """Return the JMD type expression for one JSON Schema property."""
    base = prop.get("type", "string")
    optional = "?" if key not in required_keys else ""
    enum_values = prop.get("enum", [])
    if enum_values:
        enum_str = "|".join(str(value) for value in enum_values)
        return f"{base}({enum_str}){optional}"
    return f"{base}{optional}"


def _json_schema_props_to_jmd(
    properties: dict[str, Any],
    required: set[str],
    lines: list[str],
    depth: int,
    indent: bool = False,
) -> None:
    """Append JSON Schema properties as JMD schema lines.

    Args:
        properties: Mapping of property names to JSON Schema property dicts.
        required: Set of property names that are required (non-optional).
        lines: Output list to which JMD schema lines are appended.
        depth: Current heading depth, where 1 denotes the root.
        indent: Whether to emit array-item continuation fields.
    """
    heading = "#" * (depth + 1) + " " if not indent else ""
    prefix = "  " if indent else ""

    for key, prop_raw in properties.items():
        prop = cast(dict[str, Any], prop_raw)
        quoted_key = quote_key(key)
        property_type: str = prop.get("type", "string")
        optional_mark = "" if key in required else "?"

        if property_type == "object":
            sub_properties = cast(
                dict[str, Any], prop.get("properties", {})
            )
            sub_required = set(cast(list[str], prop.get("required", [])))
            lines.append(f"{heading}{quoted_key}")
            _json_schema_props_to_jmd(
                sub_properties,
                sub_required,
                lines,
                depth + 1,
            )
        elif property_type == "array":
            items = cast(dict[str, Any], prop.get("items", {}))
            item_type: str = items.get("type", "string")
            if item_type == "object":
                sub_properties = cast(
                    dict[str, Any], items.get("properties", {})
                )
                sub_required = set(
                    cast(list[str], items.get("required", []))
                )
                lines.append(
                    f"{heading}{quoted_key}[]: object{optional_mark}"
                )
                if sub_properties:
                    first = True
                    for item_key, item_prop_raw in sub_properties.items():
                        item_prop = cast(dict[str, Any], item_prop_raw)
                        quoted_item_key = quote_key(item_key)
                        item_type_expr = _jmd_type_expr(
                            item_prop,
                            item_key,
                            sub_required,
                        )
                        if first:
                            lines.append(
                                f"- {quoted_item_key}: {item_type_expr}"
                            )
                            first = False
                        else:
                            lines.append(
                                f"  {quoted_item_key}: {item_type_expr}"
                            )
            else:
                lines.append(
                    f"{heading}{quoted_key}[]: "
                    f"{item_type}{optional_mark}"
                )
        else:
            type_expr = _jmd_type_expr(prop, key, required)
            lines.append(f"{prefix}{heading}{quoted_key}: {type_expr}")


def json_schema_to_jmd_schema(json_schema_source: str) -> str:
    """Convert a JSON Schema string to a JMD schema document."""
    json_schema: dict[str, Any] = json.loads(json_schema_source)
    label: str = json_schema.get("title", "Document")
    lines: list[str] = [f"#! {label}"]
    _json_schema_props_to_jmd(
        properties=cast(
            dict[str, Any], json_schema.get("properties", {})
        ),
        required=set(
            cast(list[str], json_schema.get("required", []))
        ),
        lines=lines,
        depth=1,
    )
    return "\n".join(lines)
