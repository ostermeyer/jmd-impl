"""JMD QBE — Query by Example (v0.3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from ._tokenizer import tokenize, Line
from ._scalars import parse_key, parse_scalar
from ._parser import _is_object_item_content, _is_indent_field


@dataclass
class Condition:
    """A single parsed field condition from a QBE query."""

    op: str       # '=', '!=', '>', '>=', '<', '<=', '~', '?', '?:', '|'
    values: list[Any]

    def __repr__(self) -> str:
        if self.op == "?":
            return "PROJECT"
        if self.op == "?:":
            return "PROJECT_ALL"
        if len(self.values) == 1:
            return f"{self.op}{self.values[0]!r}"
        return f'{self.op}({"|".join(repr(v) for v in self.values)})'


@dataclass
class QueryField:
    """A scalar field condition in a QBE query."""

    key: str
    condition: Condition


@dataclass
class QueryObject:
    """A nested object scope in a QBE query."""

    key: str
    fields: list[Any]  # list of QueryField | QueryObject | QueryArray


@dataclass
class QueryArray:
    """An array condition (EXISTS predicate) in a QBE query."""

    key: str
    item_fields: list[Any]  # list of QueryField | QueryObject


@dataclass
class JMDQuery:
    """A parsed QBE query document."""

    label: str
    fields: list[Any]  # list of QueryField | QueryObject | QueryArray

    def __repr__(self) -> str:
        lines = [f"JMDQuery({self.label!r})"]
        for f in self.fields:
            lines.append(f"  {f}")
        return "\n".join(lines)


def _parse_condition(raw: str) -> Condition:
    """Parse a condition string into a Condition object."""
    raw = raw.strip()
    if raw == "?":
        return Condition(op="?", values=[])
    if raw == "?: ?":
        return Condition(op="?:", values=[])

    for op in (">=", "<=", ">", "<", "!", "~"):
        if raw.startswith(op):
            rest = raw[len(op):]
            parts = [parse_scalar(p.strip()) for p in rest.split("|")]
            return Condition(op=op, values=parts)

    parts = [parse_scalar(p.strip()) for p in raw.split("|")]
    op = "=" if len(parts) == 1 else "|"
    return Condition(op=op, values=parts)


class JMDQueryParser:
    """Parses JMD QBE query documents (#?) using the v0.3 heading-scope model."""

    def __init__(self) -> None:
        self._lines: list[Line] = []
        self._pos: int = 0

    def parse(self, source: str) -> JMDQuery:
        """Parse a JMD query document."""
        self._lines = tokenize(source)
        self._pos = 0

        if not self._lines:
            raise ValueError("Empty query document")

        first = self._lines[0]
        if first.heading_depth != 1 or not first.content.startswith("? "):
            raise ValueError(
                f"Line {first.number}: query document must start with "
                f"'#? <label>'"
            )

        label = first.content[2:].strip()
        self._pos = 1
        fields = self._parse_query_body(depth=1)
        return JMDQuery(label=label, fields=fields)

    def _cur(self) -> Line | None:
        if self._pos < len(self._lines):
            return self._lines[self._pos]
        return None

    def _advance(self) -> None:
        self._pos += 1

    def _parse_query_body(self, depth: int) -> list[Any]:
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

                if content.endswith("[]"):
                    key = parse_key(content[:-2])
                    item_fields = self._parse_query_array_items(depth + 1)
                    fields.append(
                        QueryArray(key=key, item_fields=item_fields)
                    )
                elif ": " in content:
                    key_part, _, cond_part = content.partition(": ")
                    fields.append(QueryField(
                        key=parse_key(key_part),
                        condition=_parse_condition(cond_part),
                    ))
                else:
                    key = parse_key(content)
                    sub = self._parse_query_body(depth + 1)
                    fields.append(QueryObject(key=key, fields=sub))
                continue

            if line.heading_depth == 0:
                self._advance()
                if line.content == "?: ?":
                    fields.append(QueryField(
                        key="?",
                        condition=Condition(op="?:", values=[]),
                    ))
                elif ": " in line.content:
                    key_part, _, cond_part = line.content.partition(": ")
                    fields.append(QueryField(
                        key=parse_key(key_part),
                        condition=_parse_condition(cond_part),
                    ))
                continue

            break

        return fields

    def _parse_query_array_items(self, depth: int) -> list[Any]:
        """Parse array items in a QBE query.

        Supports:
        - Scalar items: - value
        - Object items with indentation continuation:
            - key: cond
              key: cond
        """
        line = self._cur()
        if line is None:
            return []

        # Check if first item is scalar or object
        if line.heading_depth == 0 and line.content.startswith("- "):
            content_after = line.content[2:]
            if not _is_object_item_content(content_after):
                # Scalar array items: - value
                scalars: list[Any] = []
                while True:
                    line = self._cur()
                    if line is None:
                        break
                    if line.heading_depth != 0:
                        break
                    if not line.content.startswith("- "):
                        break
                    scalars.append(parse_scalar(line.content[2:]))
                    self._advance()
                return [QueryField(
                    key="__scalar__",
                    condition=Condition(op="in", values=scalars),
                )]

        # Object array items
        items: list[Any] = []
        while True:
            line = self._cur()
            if line is None:
                break

            # Bare - (object item with no first-line field)
            if line.heading_depth == 0 and line.content == "-":
                self._advance()
                q_item_fields = self._parse_query_body(depth)
                items.append(q_item_fields)
                continue

            # - key: cond (object item with first field + indented continuation)
            if line.heading_depth == 0 and line.content.startswith("- "):
                content_after = line.content[2:]
                if _is_object_item_content(content_after):
                    self._advance()
                    q_item_fields_list: list[Any] = []
                    # First field
                    kp, _, cp = content_after.partition(": ")
                    q_item_fields_list.append(QueryField(
                        key=parse_key(kp),
                        condition=_parse_condition(cp),
                    ))
                    # Indented continuation fields
                    while self._pos < len(self._lines):
                        nxt = self._lines[self._pos]
                        indent_result = _is_indent_field(nxt.raw_text)
                        if indent_result is not None:
                            _, ikp, icp = indent_result
                            q_item_fields_list.append(QueryField(
                                key=parse_key(ikp),
                                condition=_parse_condition(icp),
                            ))
                            self._advance()
                        else:
                            break
                    items.append(q_item_fields_list)
                    continue

            break
        return items


class JMDQueryExecutor:
    """Executes a JMDQuery against a list of Python dicts."""

    def execute(self, query: JMDQuery, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            self._project(r, query.fields)
            for r in records
            if self._match(r, query.fields)
        ]

    def _match(self, record: dict[str, Any], fields: list[Any]) -> bool:
        for f in fields:
            if isinstance(f, QueryField):
                if f.condition.op in ("?", "?:"):
                    continue
                val = record.get(f.key)
                if not self._eval(val, f.condition):
                    return False
            elif isinstance(f, QueryObject):
                sub = record.get(f.key, {})
                if not isinstance(sub, dict):
                    return False
                if not self._match(cast(dict[str, Any], sub), f.fields):
                    return False
            elif isinstance(f, QueryArray):
                arr = record.get(f.key, [])
                if not isinstance(arr, list) or not f.item_fields:
                    continue
                first = f.item_fields[0]
                if (isinstance(first, QueryField)
                        and first.key == "__scalar__"):
                    if not set(first.condition.values).issubset(
                            set(cast(list[Any], arr))):
                        return False
                else:
                    template = f.item_fields[0]
                    if not any(
                        self._match(cast(dict[str, Any], item), template)
                        for item in cast(list[Any], arr)
                        if isinstance(item, dict)
                    ):
                        return False
        return True

    def _eval(self, val: Any, cond: Condition) -> bool:
        op, values = cond.op, cond.values
        if op in ("=", "in"):
            return val == values[0]
        if op == "!=":
            return val != values[0]
        if op == "|":
            return val in values
        if op == "~":
            return str(values[0]).lower() in str(val).lower()
        if val is None:
            return False
        try:
            if op == ">":
                return float(val) > float(values[0])
            if op == ">=":
                return float(val) >= float(values[0])
            if op == "<":
                return float(val) < float(values[0])
            if op == "<=":
                return float(val) <= float(values[0])
        except (TypeError, ValueError):
            pass
        return False

    def _project(self, record: dict[str, Any], fields: list[Any]) -> dict[str, Any]:
        project_keys = {
            f.key for f in fields
            if isinstance(f, QueryField) and f.condition.op == "?"
        }
        has_wildcard = any(
            isinstance(f, QueryField) and f.condition.op == "?:"
            for f in fields
        )
        has_obj_proj = any(
            isinstance(f, (QueryObject, QueryArray)) for f in fields
        )
        has_any_proj = bool(project_keys) or has_wildcard or has_obj_proj

        if not has_any_proj:
            return dict(record)

        result: dict[str, Any] = {}
        for f in fields:
            if isinstance(f, QueryField):
                if f.condition.op == "?":
                    if f.key in record:
                        result[f.key] = record[f.key]
                elif f.condition.op == "?:":
                    explicit = {
                        g.key for g in fields
                        if isinstance(g, QueryField)
                        and g.key not in ("?", "?:")
                    }
                    for k, v in record.items():
                        if k not in explicit and k not in result:
                            result[k] = v
            elif isinstance(f, QueryObject):
                sub: Any = record.get(f.key, {})
                if isinstance(sub, dict):
                    result[f.key] = self._project(
                        cast(dict[str, Any], sub), f.fields)
            elif isinstance(f, QueryArray):
                arr = record.get(f.key, [])
                if not isinstance(arr, list) or not f.item_fields:
                    continue
                first = f.item_fields[0]
                if (isinstance(first, QueryField)
                        and first.key == "__scalar__"):
                    result[f.key] = arr
                else:
                    template: Any = f.item_fields[0]
                    typed_arr = cast(list[Any], arr)
                    result[f.key] = [
                        self._project(cast(dict[str, Any], item), template)
                        for item in typed_arr
                        if isinstance(item, dict)
                        and self._match(cast(dict[str, Any], item), template)
                    ]

        return result
