"""Tests for JMD Schema Documents (spec § 14)."""

import pytest
from jmd import JMDSchemaParser, SchemaField, SchemaObject, SchemaArray, SchemaRef, JMDSchema


def parse_schema(source: str) -> JMDSchema:
    return JMDSchemaParser().parse(source)


class TestSchemaParsing:
    def test_label(self):
        s = parse_schema("#! Order\nid: integer\nstatus: string")
        assert s.label == "Order"

    def test_scalar_fields(self):
        s = parse_schema("#! X\nid: integer\nname: string\nactive: boolean")
        keys = {f.key for f in s.fields}
        assert keys == {"id", "name", "active"}

    def test_field_types(self):
        s = parse_schema("#! X\nid: integer\nprice: number\nname: string")
        types = {f.key: f.base_type for f in s.fields}
        assert types["id"] == "integer"
        assert types["price"] == "number"
        assert types["name"] == "string"

    def test_optional_modifier(self):
        s = parse_schema("#! X\nid: integer\nnote: string optional")
        note = next(f for f in s.fields if f.key == "note")
        assert note.optional is True
        id_field = next(f for f in s.fields if f.key == "id")
        assert id_field.optional is False

    def test_readonly_modifier(self):
        s = parse_schema("#! X\nid: integer readonly\nname: string")
        id_field = next(f for f in s.fields if f.key == "id")
        assert id_field.readonly is True
        name_field = next(f for f in s.fields if f.key == "name")
        assert name_field.readonly is False

    def test_optional_and_readonly(self):
        s = parse_schema("#! X\ntoken: string optional readonly")
        field = s.fields[0]
        assert field.optional is True
        assert field.readonly is True

    def test_enum_type(self):
        s = parse_schema("#! X\nstatus: string(active|inactive|pending)")
        field = s.fields[0]
        assert field.base_type == "string"
        assert "active" in field.enum_values

    def test_reference_type(self):
        s = parse_schema("#! X\ncategory: -> Category")
        ref = s.fields[0]
        assert isinstance(ref, SchemaRef)
        assert ref.ref == "Category"
        assert ref.optional is False

    def test_optional_reference(self):
        s = parse_schema("#! X\nwarehouse: -> Warehouse optional")
        ref = s.fields[0]
        assert isinstance(ref, SchemaRef)
        assert ref.optional is True

    def test_array_reference(self):
        s = parse_schema("#! X\nitems: []-> OrderItem")
        arr = s.fields[0]
        assert isinstance(arr, SchemaArray)
        assert arr.item_type == "ref"
        assert arr.item_ref == "OrderItem"
        assert arr.optional is False

    def test_array_reference_optional(self):
        s = parse_schema("#! X\ntags: []-> Tag optional")
        arr = s.fields[0]
        assert isinstance(arr, SchemaArray)
        assert arr.item_ref == "Tag"
        assert arr.optional is True

    def test_nested_object(self):
        s = parse_schema("#! X\n## address\ncity: string\nzip: string")
        obj = next(f for f in s.fields if f.key == "address")
        assert isinstance(obj, SchemaObject)
        assert any(f.key == "city" for f in obj.fields)

    def test_array_scalar(self):
        s = parse_schema("#! X\ntags[]: string")
        arr = s.fields[0]
        assert isinstance(arr, SchemaArray)
        assert arr.item_type == "string"

    def test_array_object(self):
        s = parse_schema("#! X\n## items[]: object\n- id: integer\n  name: string")
        arr = next(f for f in s.fields if f.key == "items")
        assert isinstance(arr, SchemaArray)
        assert arr.item_type == "object"
        assert any(f.key == "id" for f in arr.item_fields)


class TestJsonSchemaExport:
    def test_basic_export(self):
        s = parse_schema("#! Order\nid: integer\nstatus: string")
        js = s.to_json_schema()
        assert js["title"] == "Order"
        assert js["type"] == "object"
        assert "id" in js["properties"]
        assert "status" in js["properties"]

    def test_required_fields(self):
        s = parse_schema("#! X\nid: integer\nnote: string optional")
        js = s.to_json_schema()
        assert "id" in js["required"]
        assert "note" not in js["required"]

    def test_bare_pipe_enum(self):
        s = parse_schema("#! X\nstatus: pending|active|shipped")
        field = s.fields[0]
        assert field.base_type == "string"
        assert field.enum_values == ["pending", "active", "shipped"]

    def test_bare_pipe_enum_with_optional(self):
        s = parse_schema("#! X\nstatus: pending|active|shipped optional")
        field = s.fields[0]
        assert field.enum_values == ["pending", "active", "shipped"]
        assert field.optional is True

    def test_format_hint_email(self):
        s = parse_schema("#! X\nemail: string email")
        field = s.fields[0]
        assert field.base_type == "string"
        assert field.format_hint == "email"

    def test_format_hint_datetime(self):
        s = parse_schema("#! X\ncreated_at: string datetime readonly")
        field = s.fields[0]
        assert field.format_hint == "datetime"
        assert field.readonly is True

    def test_format_hint_date(self):
        s = parse_schema("#! X\ndue_date: string date optional")
        field = s.fields[0]
        assert field.format_hint == "date"
        assert field.optional is True

    def test_format_hint_uri(self):
        s = parse_schema("#! X\nwebsite: string uri optional")
        field = s.fields[0]
        assert field.format_hint == "uri"

    def test_default_value_string(self):
        s = parse_schema("#! X\nstatus: pending|active|shipped = pending")
        field = s.fields[0]
        assert field.default == "pending"

    def test_default_value_integer(self):
        s = parse_schema("#! X\nretries: integer = 3")
        field = s.fields[0]
        assert field.default == 3

    def test_default_value_boolean(self):
        s = parse_schema("#! X\nactive: boolean = true")
        field = s.fields[0]
        assert field.default is True

    def test_frontmatter_skipped(self):
        s = parse_schema("version: 1\n\n#! Order\nid: integer")
        assert s.label == "Order"
        assert s.fields[0].key == "id"

    def test_invalid_marker_raises(self):
        with pytest.raises(ValueError):
            parse_schema("# Order\nid: integer")

    def test_json_schema_format_hint(self):
        s = parse_schema("#! X\nemail: string email")
        js = s.to_json_schema()
        assert js["properties"]["email"]["format"] == "email"

    def test_json_schema_default(self):
        s = parse_schema("#! X\nretries: integer = 3")
        js = s.to_json_schema()
        assert js["properties"]["retries"]["default"] == 3

    def test_json_schema_enum(self):
        s = parse_schema("#! X\nstatus: pending|active|shipped")
        js = s.to_json_schema()
        assert js["properties"]["status"]["enum"] == ["pending", "active", "shipped"]

    def test_json_schema_array_ref(self):
        s = parse_schema("#! X\nitems: []-> OrderItem")
        js = s.to_json_schema()
        assert js["properties"]["items"]["type"] == "array"
        assert js["properties"]["items"]["items"]["$ref"] == "OrderItem"
