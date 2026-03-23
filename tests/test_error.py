"""Tests for JMD Error Documents (spec § 17)."""

import pytest

from jmd import is_error_document, parse_error


class TestIsErrorDocument:
    """Tests for the is_error_document detection function."""

    def test_error_document(self) -> None:
        """Test that a document with the Error label is detected as an error."""
        assert is_error_document("# Error\nstatus: 404") is True

    def test_data_document(self) -> None:
        """Test that a regular data document is not detected as an error."""
        assert is_error_document("# Order\nid: 42") is False

    def test_error_with_leading_blank(self) -> None:
        """Test that leading blank lines do not prevent error detection."""
        assert is_error_document("\n# Error\nstatus: 404") is True

    def test_non_error_heading(self) -> None:
        """Test that a heading not exactly Error is not matched."""
        assert is_error_document("# ErrorLog\nid: 1") is False


class TestErrorParsing:
    """Tests for the parse_error function."""

    def test_minimal_error(self) -> None:
        """Test that a minimal error document is parsed with status and code."""
        err = parse_error("# Error\nstatus: 404\ncode: not_found")
        assert err.status == 404
        assert err.code == "not_found"

    def test_full_error(self) -> None:
        """Test that all standard error fields are parsed correctly."""
        err = parse_error(
            "# Error\n"
            "status: 422\n"
            "code: validation_failed\n"
            "message: Request failed\n"
            "suggestion: Check the input\n"
            "context: Validation ran against OrderItem schema\n"
        )
        assert err.status == 422
        assert err.code == "validation_failed"
        assert err.message == "Request failed"
        assert err.suggestion == "Check the input"
        assert err.context is not None
        assert "OrderItem" in err.context

    def test_errors_array(self) -> None:
        """Test that an errors sub-array is parsed into a list of objects."""
        src = (
            "# Error\n"
            "status: 422\n"
            "code: validation_failed\n"
            "message: Schema validation failed\n"
            "\n"
            "## errors[]\n"
            "- field: items[0].qty\n"
            "  reason: must be positive\n"
            "  value: \"-3\"\n"
            "- field: address.zip\n"
            "  reason: invalid format\n"
            "  value: abc\n"
        )
        err = parse_error(src)
        assert len(err.errors) == 2
        assert err.errors[0].field == "items[0].qty"
        assert err.errors[0].reason == "must be positive"
        assert err.errors[0].value == "-3"
        assert err.errors[1].field == "address.zip"

    def test_extra_fields_in_extra(self) -> None:
        """Test that unrecognised fields are collected in the extra dict."""
        err = parse_error(
            "# Error\nstatus: 500\ncode: internal\nrequest_id: abc123"
        )
        assert err.extra.get("request_id") == "abc123"

    def test_non_error_document_raises(self) -> None:
        """Test that parsing a non-error document raises ValueError."""
        with pytest.raises(ValueError):
            parse_error("# Order\nid: 42")

    def test_status_none_when_absent(self) -> None:
        """Test that status is None when the field is absent."""
        err = parse_error("# Error\ncode: not_found")
        assert err.status is None
