"""JMD Schema Documents (#! Label) — v0.3."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field as dc_field
from typing import Any, cast

from ._tokenizer import tokenize, Line
from ._scalars import parse_key, parse_scalar, quote_key
from ._parser import _is_object_item_content, _is_indent_field


@dataclass
class SchemaRef:
    """A reference type field in a JMD schema (``-> Label``)."""

    key: str
    ref: str          # Referenced label, e.g. ``'Category'``
    optional: bool = False
    readonly: bool = False


@dataclass
class SchemaField:
    """A scalar field type declaration in a JMD schema."""

    key: str
    base_type: str
    optional: bool = False
    readonly: bool = False
    enum_values: list[Any] = dc_field(default_factory=lambda: cast(list[Any], []))
    format_hint: str | None = None
    default: Any = None


@dataclass
class SchemaObject:
    """A nested object type declaration in a JMD schema."""

    key: str
    fields: list[Any]  # list of SchemaField | SchemaObject | SchemaArray
    optional: bool = False


@dataclass
class SchemaArray:
    """An array type declaration in a JMD schema."""

    key: str
    item_type: str
    item_fields: list[Any] = dc_field(default_factory=lambda: cast(list[Any], []))
    optional: bool = False
    item_ref: str | None = None  # set when item_type == "ref" ([]-> Label)


@dataclass
class JMDSchema:
    """A parsed JMD schema document."""

    label: str
    fields: list[Any]

    def to_json_schema(self) -> dict[str, Any]:
        """Convert to a JSON Schema dict (draft 2020-12)."""
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": self.label,
            "type": "object",
            "properties": self._fields_to_props(self.fields),
            "required": self._required(self.fields),
        }

    def _fields_to_props(self, fields: list[Any]) -> dict[str, Any]:
        props: dict[str, Any] = {}
        for f in fields:
            if isinstance(f, SchemaField):
                props[f.key] = self._field_schema(f)
            elif isinstance(f, SchemaObject):
                props[f.key] = {
                    "type": "object",
                    "properties": self._fields_to_props(f.fields),
                    "required": self._required(f.fields),
                }
            elif isinstance(f, SchemaArray):
                if f.item_type == "ref" and f.item_ref:
                    items: dict[str, Any] = {"$ref": f.item_ref}
                elif f.item_type == "object" and f.item_fields:
                    items = {
                        "type": "object",
                        "properties": self._fields_to_props(f.item_fields),
                        "required": self._required(f.item_fields),
                    }
                else:
                    items = {"type": f.item_type}
                props[f.key] = {"type": "array", "items": items}
        return props

    def _field_schema(self, f: SchemaField) -> dict[str, Any]:
        if f.base_type == "binary":
            return {"type": "string", "contentEncoding": "sha256"}
        s: dict[str, Any] = {"type": f.base_type}
        if f.enum_values:
            s["enum"] = f.enum_values
        if f.format_hint:
            s["format"] = f.format_hint
        if f.default is not None:
            s["default"] = f.default
        return s

    def _required(self, fields: list[Any]) -> list[str]:
        return [
            f.key for f in fields
            if not getattr(f, "optional", False)
        ]


_FORMAT_HINTS = {"email", "date", "datetime", "uri"}
_MODIFIERS = {"optional", "readonly"}


def _parse_type_expr(
    raw: str,
) -> tuple[str, bool, bool, list[Any], str | None, str | None, Any]:
    """Parse a JMD type expression.

    Returns:
        (base_type, optional, readonly, enum_values, ref, format_hint, default)
    """
    raw = raw.strip()

    # Array reference type: "[]-> Label [optional] [readonly]"
    arr_ref_m = re.match(r"^\[\]->\s+(\w+)(.*)", raw)
    if arr_ref_m:
        ref_label = arr_ref_m.group(1)
        rest = arr_ref_m.group(2).strip().split()
        return "ref[]", "optional" in rest, "readonly" in rest, [], ref_label, None, None

    # Reference type: "-> Label [optional] [readonly]"
    ref_m = re.match(r"^->\s+(\w+)(.*)", raw)
    if ref_m:
        ref_label = ref_m.group(1)
        rest = ref_m.group(2).strip().split()
        return "ref", "optional" in rest, "readonly" in rest, [], ref_label, None, None

    # Extract default value ("= value") before splitting on spaces
    default: Any = None
    default_m = re.search(r"\s*=\s*(\S+)", raw)
    if default_m:
        default = parse_scalar(default_m.group(1))
        raw = raw[:default_m.start()].strip()

    # Split remaining into tokens and separate modifiers
    tokens = raw.split()
    optional = "optional" in tokens
    readonly = "readonly" in tokens
    type_tokens = [t for t in tokens if t not in _MODIFIERS]

    # Legacy ?-suffix optional notation
    if type_tokens and type_tokens[-1].endswith("?"):
        optional = True
        type_tokens[-1] = type_tokens[-1][:-1]
        if not type_tokens[-1]:
            type_tokens.pop()

    # Legacy parenthesized enum: type(a|b|c)
    joined = " ".join(type_tokens)
    m = re.match(r"^(\w+)\(([^)]+)\)$", joined)
    if m:
        base_type = m.group(1)
        enum_values: list[Any] = [parse_scalar(v.strip()) for v in m.group(2).split("|")]
        return base_type, optional, readonly, enum_values, None, None, default

    # Bare pipe enum: pending|active|shipped
    if type_tokens and "|" in type_tokens[0]:
        enum_values = [parse_scalar(v.strip()) for v in type_tokens[0].split("|")]
        return "string", optional, readonly, enum_values, None, None, default

    # Base type with optional format hint: "string email", "string datetime"
    format_hint: str | None = None
    if len(type_tokens) >= 2 and type_tokens[1] in _FORMAT_HINTS:
        base_type = type_tokens[0]
        format_hint = type_tokens[1]
    else:
        base_type = type_tokens[0] if type_tokens else "string"

    return base_type, optional, readonly, [], None, format_hint, default


def _make_schema_field(key: str, type_expr: str) -> "SchemaField | SchemaRef | SchemaArray":
    """Parse *type_expr* and return the appropriate schema field object."""
    base_type, optional, readonly, enum_values, ref, format_hint, default = _parse_type_expr(type_expr)
    if base_type == "ref[]":
        return SchemaArray(key=key, item_type="ref", item_ref=ref, optional=optional)
    if ref is not None:
        return SchemaRef(key=key, ref=ref, optional=optional, readonly=readonly)
    return SchemaField(
        key=key, base_type=base_type,
        optional=optional, readonly=readonly,
        enum_values=enum_values,
        format_hint=format_hint,
        default=default,
    )


class JMDSchemaParser:
    """Parses JMD schema documents (#!) using the v0.3 heading-scope model."""

    def __init__(self) -> None:
        self._lines: list[Line] = []
        self._pos: int = 0

    def parse(self, source: str) -> JMDSchema:
        """Parse a JMD schema document."""
        self._lines = tokenize(source)
        self._pos = 0
        self.frontmatter: dict[str, Any] = {}

        if not self._lines:
            raise ValueError("Empty schema document")

        # Skip frontmatter (key: value lines before the first heading)
        while self._pos < len(self._lines):
            line = self._lines[self._pos]
            if line.heading_depth > 0:
                break
            if line.heading_depth == -1:
                self._pos += 1
                continue
            if ": " in line.content:
                key_part, _, val_part = line.content.partition(": ")
                self.frontmatter[parse_key(key_part)] = parse_scalar(val_part)
            elif line.content and not line.content.startswith(("- ", ">")):
                self.frontmatter[parse_key(line.content)] = True
            self._pos += 1

        if self._pos >= len(self._lines):
            raise ValueError("No root heading found in schema document")

        first = self._lines[self._pos]
        if first.heading_depth != 1 or not first.content.startswith("! "):
            raise ValueError(
                "Schema document must start with '#! <label>'"
            )

        label = first.content[2:].strip()
        self._pos += 1
        fields = self._parse_schema_body(depth=1)
        return JMDSchema(label=label, fields=fields)

    def _cur(self) -> Line | None:
        if self._pos < len(self._lines):
            return self._lines[self._pos]
        return None

    def _advance(self) -> None:
        self._pos += 1

    def _parse_schema_body(self, depth: int) -> list[Any]:
        fields: list[Any] = []
        while True:
            line = self._cur()
            if line is None:
                break

            # Skip blank lines
            if line.heading_depth == -1:
                self._advance()
                continue

            if line.heading_depth > 0 and line.heading_depth <= depth:
                break

            if line.heading_depth == depth + 1:
                self._advance()
                content = line.content

                # Array with type: key[]: type
                if "[]: " in content:
                    key_part, _, type_part = content.partition("[]: ")
                    key = parse_key(key_part)
                    base_type, optional, _ro, _ev, _ref, _fh, _dv = _parse_type_expr(type_part)
                    if base_type == "object":
                        item_fields = self._parse_schema_dash_item()
                        fields.append(SchemaArray(
                            key=key, item_type="object",
                            item_fields=item_fields, optional=optional,
                        ))
                    else:
                        fields.append(SchemaArray(
                            key=key, item_type=base_type,
                            optional=optional,
                        ))

                # Scalar or ref: key: type
                elif ": " in content:
                    key_part, _, type_part = content.partition(": ")
                    fields.append(_make_schema_field(parse_key(key_part), type_part))

                # Object: key
                else:
                    key = parse_key(content)
                    sub_fields = self._parse_schema_body(depth + 1)
                    fields.append(SchemaObject(
                        key=key, fields=sub_fields,
                    ))
                continue

            # Bare `-` or `- key: type` (item template for preceding array)
            if line.heading_depth == 0 and (
                line.content == "-"
                or line.content.startswith("- ")
            ):
                item_fields = self._parse_schema_dash_item()
                if fields and isinstance(fields[-1], SchemaArray):
                    fields[-1].item_fields = item_fields
                continue

            # Bare field: key: type
            if line.heading_depth == 0 and ": " in line.content:
                self._advance()
                if "[]: " in line.content:
                    key_part, _, type_part = line.content.partition("[]: ")
                    key = parse_key(key_part)
                    base_type, optional, _ro, _ev, _ref, _fh, _dv = _parse_type_expr(type_part)
                    fields.append(SchemaArray(
                        key=key, item_type=base_type,
                        optional=optional,
                    ))
                else:
                    key_part, _, type_part = line.content.partition(": ")
                    fields.append(_make_schema_field(parse_key(key_part), type_part))
                continue

            break

        return fields

    def _parse_schema_dash_item(self) -> list[Any]:
        """Parse a `- key: type` + indented continuation lines as item template."""
        line = self._cur()
        if line is None:
            return []

        item_fields: list[Any] = []

        if line.content == "-":
            # Bare dash: fields follow as bare fields
            self._advance()
            return self._parse_schema_body_bare(1)  # depth doesn't really matter here

        if line.content.startswith("- "):
            content_after = line.content[2:]
            if _is_object_item_content(content_after):
                self._advance()
                # First field
                kp, _, tp = content_after.partition(": ")
                item_fields.append(_make_schema_field(parse_key(kp), tp))
                # Indented continuation fields
                while self._pos < len(self._lines):
                    nxt = self._lines[self._pos]
                    indent_result = _is_indent_field(nxt.raw_text)
                    if indent_result is not None:
                        _, ikp, itp = indent_result
                        item_fields.append(_make_schema_field(parse_key(ikp), itp))
                        self._advance()
                    else:
                        break
                return item_fields

        self._advance()
        return item_fields

    def _parse_schema_body_bare(self, depth: int) -> list[Any]:
        """Parse bare fields (not under headings) — used after bare `-` in schema."""
        fields: list[Any] = []
        while True:
            line = self._cur()
            if line is None:
                break
            if line.heading_depth != 0:
                break
            if line.heading_depth == -1:
                self._advance()
                continue
            if ": " not in line.content:
                break
            self._advance()
            key_part, _, type_part = line.content.partition(": ")
            fields.append(_make_schema_field(parse_key(key_part), type_part))
        return fields


# ---------------------------------------------------------------------------
# JSON Schema <-> JMD Schema conversion
# ---------------------------------------------------------------------------

def _jmd_type_expr(prop: dict[str, Any], key: str, required_keys: set[str]) -> str:
    base = prop.get("type", "string")
    optional = "?" if key not in required_keys else ""
    enum_values = prop.get("enum", [])
    if enum_values:
        enum_str = "|".join(str(v) for v in enum_values)
        return f"{base}({enum_str}){optional}"
    return f"{base}{optional}"


def _json_schema_props_to_jmd(
    properties: dict[str, Any],
    required: set[str],
    lines: list[str],
    depth: int,
    indent: bool = False,
) -> None:
    """Convert JSON Schema properties to JMD schema lines.

    Args:
        indent: If True, write fields as indented continuation (for array item templates).
    """
    heading = "#" * (depth + 1) + " " if not indent else ""
    prefix = "  " if indent else ""

    for key, prop_raw in properties.items():
        prop: dict[str, Any] = cast(dict[str, Any], prop_raw)
        q_key = quote_key(key)
        ptype: str = prop.get("type", "string")
        optional_mark = "" if key in required else "?"

        if ptype == "object":
            sub_props = cast(dict[str, Any], prop.get("properties", {}))
            sub_req = set(cast(list[str], prop.get("required", [])))
            lines.append(f"{heading}{q_key}")
            _json_schema_props_to_jmd(sub_props, sub_req, lines, depth + 1)
        elif ptype == "array":
            items: dict[str, Any] = cast(dict[str, Any], prop.get("items", {}))
            item_type: str = items.get("type", "string")
            if item_type == "object":
                sub_props = cast(dict[str, Any], items.get("properties", {}))
                sub_req = set(cast(list[str], items.get("required", [])))
                lines.append(
                    f"{heading}{q_key}[]: object{optional_mark}"
                )
                if sub_props:
                    # Write item template with indentation continuation
                    first = True
                    for ikey, iprop_raw in sub_props.items():
                        iprop: dict[str, Any] = cast(dict[str, Any], iprop_raw)
                        iq_key = quote_key(ikey)
                        itype_expr = _jmd_type_expr(iprop, ikey, sub_req)
                        if first:
                            lines.append(f"- {iq_key}: {itype_expr}")
                            first = False
                        else:
                            lines.append(f"  {iq_key}: {itype_expr}")
            else:
                lines.append(
                    f"{heading}{q_key}[]: {item_type}{optional_mark}"
                )
        else:
            type_expr = _jmd_type_expr(prop, key, required)
            lines.append(f"{prefix}{heading}{q_key}: {type_expr}")


def json_schema_to_jmd_schema(json_schema_source: str) -> str:
    """Convert a JSON Schema string to a JMD Schema document."""
    js: dict[str, Any] = json.loads(json_schema_source)
    label: str = js.get("title", "Document")
    lines: list[str] = [f"#! {label}"]
    _json_schema_props_to_jmd(
        properties=cast(dict[str, Any], js.get("properties", {})),
        required=set(cast(list[str], js.get("required", []))),
        lines=lines,
        depth=1,
    )
    return "\n".join(lines)
