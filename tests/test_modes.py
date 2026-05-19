# SPDX-License-Identifier: Apache-2.0
"""Tests for document mode detection and top-level API (spec §§ 3.6, 19)."""

from jmd import Envelope, jmd_mode, parse, serialize


class TestModeDetection:
    """Tests for jmd_mode document type detection."""

    def test_data_mode(self) -> None:
        """Test that a standard heading is detected as data mode."""
        assert jmd_mode("# Order\nid: 42") == "data"

    def test_query_mode(self) -> None:
        """Test that a #? heading is detected as query mode."""
        assert jmd_mode("#? Order\nstatus: active") == "query"

    def test_schema_mode(self) -> None:
        """Test that a #! heading is detected as schema mode."""
        assert jmd_mode("#! Order\nid: integer") == "schema"

    def test_delete_mode(self) -> None:
        """Test that a #- heading is detected as delete mode."""
        assert jmd_mode("#- Order\nid: 42") == "delete"

    def test_error_is_data_mode(self) -> None:
        """Test that an error document is detected as data mode."""
        # Error documents are standard data documents with reserved label
        assert jmd_mode("# Error\nstatus: 404") == "data"

    def test_leading_blank_lines(self) -> None:
        """Test that leading blank lines do not affect mode detection."""
        assert jmd_mode("\n\n# Order\nid: 1") == "data"

    def test_frontmatter_before_heading(self) -> None:
        """Test that frontmatter before the heading does not affect mode."""
        assert jmd_mode("confidence: 0.9\n# Order\nid: 1") == "data"


class TestTopLevelAPI:
    """Tests for the top-level parse and serialize convenience functions."""

    def test_parse_returns_envelope(self) -> None:
        """Test that parse returns a canonical Envelope (§3.6)."""
        env = parse("# X\nk: v")
        assert isinstance(env, Envelope)
        assert env.mode == "data"
        assert env.label == "X"
        assert env.value == {"k": "v"}
        assert env.frontmatter == {}

    def test_serialize_returns_string(self) -> None:
        """Test that serialize returns a JMD string with the heading."""
        out = serialize({"k": "v"}, label="X")
        assert out.startswith("# X\n")
        assert "k: v" in out

    def test_parse_serialize_roundtrip(self) -> None:
        """Test that parse and serialize are inverse operations."""
        data = {"id": 1, "name": "Test", "active": True}
        assert parse(serialize(data, label="X")).value == data
