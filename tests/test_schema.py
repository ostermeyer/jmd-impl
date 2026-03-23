"""Tests for JMD Schema Documents (spec § 14)."""

import pytest

from jmd import (
    JMDSchema,
    JMDSchemaParser,
    SchemaArray,
    SchemaObject,
    SchemaRef,
)


def parse_schema(source: str) -> JMDSchema:
    """Parse a schema document from source text."""
    return JMDSchemaParser().parse(source)


class TestSchemaParsing:
    """Tests for JMDSchemaParser field and modifier parsing."""

    def test_label(self) -> None:
        """Test that the schema label is parsed from the heading."""
        s = parse_schema("#! Order\nid: integer\nstatus: string")
        assert s.label == "Order"

    def test_scalar_fields(self) -> None:
        """Test that scalar field keys are all parsed."""
        s = parse_schema("#! X\nid: integer\nname: string\nactive: boolean")
        keys = {f.key for f in s.fields}
        assert keys == {"id", "name", "active"}

    def test_field_types(self) -> None:
        """Test that base types are parsed correctly for each field."""
        s = parse_schema("#! X\nid: integer\nprice: number\nname: string")
        types = {f.key: f.base_type for f in s.fields}
        assert types["id"] == "integer"
        assert types["price"] == "number"
        assert types["name"] == "string"

    def test_optional_modifier(self) -> None:
        """Test that the optional modifier is parsed and defaults to False."""
        s = parse_schema("#! X\nid: integer\nnote: string optional")
        note = next(f for f in s.fields if f.key == "note")
        assert note.optional is True
        id_field = next(f for f in s.fields if f.key == "id")
        assert id_field.optional is False

    def test_readonly_modifier(self) -> None:
        """Test that the readonly modifier is parsed and defaults to False."""
        s = parse_schema("#! X\nid: integer readonly\nname: string")
        id_field = next(f for f in s.fields if f.key == "id")
        assert id_field.readonly is True
        name_field = next(f for f in s.fields if f.key == "name")
        assert name_field.readonly is False

    def test_optional_and_readonly(self) -> None:
        """Test that optional and readonly modifiers can be combined."""
        s = parse_schema("#! X\ntoken: string optional readonly")
        field = s.fields[0]
        assert field.optional is True
        assert field.readonly is True

    def test_enum_type(self) -> None:
        """Test that a parenthesised pipe list is parsed as enum values."""
        s = parse_schema("#! X\nstatus: string(active|inactive|pending)")
        field = s.fields[0]
        assert field.base_type == "string"
        assert "active" in field.enum_values

    def test_reference_type(self) -> None:
        """Test that an arrow reference is parsed as a SchemaRef."""
        s = parse_schema("#! X\ncategory: -> Category")
        ref = s.fields[0]
        assert isinstance(ref, SchemaRef)
        assert ref.ref == "Category"
        assert ref.optional is False

    def test_optional_reference(self) -> None:
        """Test that an optional arrow reference sets optional on SchemaRef."""
        s = parse_schema("#! X\nwarehouse: -> Warehouse optional")
        ref = s.fields[0]
        assert isinstance(ref, SchemaRef)
        assert ref.optional is True

    def test_array_reference(self) -> None:
        """Test that a []-> reference is parsed as a SchemaArray of refs."""
        s = parse_schema("#! X\nitems: []-> OrderItem")
        arr = s.fields[0]
        assert isinstance(arr, SchemaArray)
        assert arr.item_type == "ref"
        assert arr.item_ref == "OrderItem"
        assert arr.optional is False

    def test_array_reference_optional(self) -> None:
        """Test that an optional []-> reference sets optional on SchemaArray."""
        s = parse_schema("#! X\ntags: []-> Tag optional")
        arr = s.fields[0]
        assert isinstance(arr, SchemaArray)
        assert arr.item_ref == "Tag"
        assert arr.optional is True

    def test_nested_object(self) -> None:
        """Test that a level-2 heading creates a SchemaObject field."""
        s = parse_schema("#! X\n## address\ncity: string\nzip: string")
        obj = next(f for f in s.fields if f.key == "address")
        assert isinstance(obj, SchemaObject)
        assert any(f.key == "city" for f in obj.fields)

    def test_array_scalar(self) -> None:
        """Test that a tags[]: string field is parsed as a SchemaArray."""
        s = parse_schema("#! X\ntags[]: string")
        arr = s.fields[0]
        assert isinstance(arr, SchemaArray)
        assert arr.item_type == "string"

    def test_array_object(self) -> None:
        """Test that a ## items[]: object section is parsed as a SchemaArray."""
        s = parse_schema(
            "#! X\n## items[]: object\n- id: integer\n  name: string"
        )
        arr = next(f for f in s.fields if f.key == "items")
        assert isinstance(arr, SchemaArray)
        assert arr.item_type == "object"
        assert any(f.key == "id" for f in arr.item_fields)


class TestJsonSchemaExport:
    """Tests for JMDSchema.to_json_schema() JSON Schema output."""

    def test_basic_export(self) -> None:
        """Test that the exported JSON Schema has the correct title and type."""
        s = parse_schema("#! Order\nid: integer\nstatus: string")
        js = s.to_json_schema()
        assert js["title"] == "Order"
        assert js["type"] == "object"
        assert "id" in js["properties"]
        assert "status" in js["properties"]

    def test_required_fields(self) -> None:
        """Test that non-optional fields appear in the required list."""
        s = parse_schema("#! X\nid: integer\nnote: string optional")
        js = s.to_json_schema()
        assert "id" in js["required"]
        assert "note" not in js["required"]

    def test_bare_pipe_enum(self) -> None:
        """Test that a bare pipe-separated value is parsed as an enum."""
        s = parse_schema("#! X\nstatus: pending|active|shipped")
        field = s.fields[0]
        assert field.base_type == "string"
        assert field.enum_values == ["pending", "active", "shipped"]

    def test_bare_pipe_enum_with_optional(self) -> None:
        """Test that a bare pipe enum accepts the optional modifier."""
        s = parse_schema("#! X\nstatus: pending|active|shipped optional")
        field = s.fields[0]
        assert field.enum_values == ["pending", "active", "shipped"]
        assert field.optional is True

    def test_format_hint_email(self) -> None:
        """Test that the email format hint is parsed correctly."""
        s = parse_schema("#! X\nemail: string email")
        field = s.fields[0]
        assert field.base_type == "string"
        assert field.format_hint == "email"

    def test_format_hint_datetime(self) -> None:
        """Test that the datetime format hint and readonly can be combined."""
        s = parse_schema("#! X\ncreated_at: string datetime readonly")
        field = s.fields[0]
        assert field.format_hint == "datetime"
        assert field.readonly is True

    def test_format_hint_date(self) -> None:
        """Test that the date format hint is parsed correctly."""
        s = parse_schema("#! X\ndue_date: string date optional")
        field = s.fields[0]
        assert field.format_hint == "date"
        assert field.optional is True

    def test_format_hint_uri(self) -> None:
        """Test that the uri format hint is parsed correctly."""
        s = parse_schema("#! X\nwebsite: string uri optional")
        field = s.fields[0]
        assert field.format_hint == "uri"

    def test_default_value_string(self) -> None:
        """Test that a string default value is parsed correctly."""
        s = parse_schema("#! X\nstatus: pending|active|shipped = pending")
        field = s.fields[0]
        assert field.default == "pending"

    def test_default_value_integer(self) -> None:
        """Test that an integer default value is parsed as int."""
        s = parse_schema("#! X\nretries: integer = 3")
        field = s.fields[0]
        assert field.default == 3

    def test_default_value_boolean(self) -> None:
        """Test that a boolean default value is parsed as bool."""
        s = parse_schema("#! X\nactive: boolean = true")
        field = s.fields[0]
        assert field.default is True

    def test_frontmatter_skipped(self) -> None:
        """Test that frontmatter before the heading is excluded from fields."""
        s = parse_schema("version: 1\n\n#! Order\nid: integer")
        assert s.label == "Order"
        assert s.fields[0].key == "id"

    def test_invalid_marker_raises(self) -> None:
        """Test that a non-schema marker raises ValueError."""
        with pytest.raises(ValueError):
            parse_schema("# Order\nid: integer")

    def test_json_schema_format_hint(self) -> None:
        """Test that a format hint is exported as the format key."""
        s = parse_schema("#! X\nemail: string email")
        js = s.to_json_schema()
        assert js["properties"]["email"]["format"] == "email"

    def test_json_schema_default(self) -> None:
        """Test that a default value is exported in the JSON Schema property."""
        s = parse_schema("#! X\nretries: integer = 3")
        js = s.to_json_schema()
        assert js["properties"]["retries"]["default"] == 3

    def test_json_schema_enum(self) -> None:
        """Test that enum values are exported as the enum key in JSON Schema."""
        s = parse_schema("#! X\nstatus: pending|active|shipped")
        js = s.to_json_schema()
        assert (
            js["properties"]["status"]["enum"]
            == ["pending", "active", "shipped"]
        )

    def test_json_schema_array_ref(self) -> None:
        """Test that a []-> reference is exported as an array of $ref items."""
        s = parse_schema("#! X\nitems: []-> OrderItem")
        js = s.to_json_schema()
        assert js["properties"]["items"]["type"] == "array"
        assert js["properties"]["items"]["items"]["$ref"] == "OrderItem"
