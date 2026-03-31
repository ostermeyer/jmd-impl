"""Tests for jmd.xml — JMD over XML mapping."""

from __future__ import annotations

import textwrap

import pytest
from lxml import etree

from jmd.xml import jmd_to_xml
from jmd.xml import xml_to_jmd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _xml(s: str) -> bytes:
    """Normalise whitespace and re-serialise XML for comparison."""
    root = etree.fromstring(s.encode())
    return etree.tostring(root, encoding="unicode").encode()


def _roundtrip(xml_src: str) -> bytes:
    """XML → JMD → XML, returns final XML bytes."""
    jmd = xml_to_jmd(xml_src)
    return jmd_to_xml(jmd)


# ---------------------------------------------------------------------------
# xml_to_jmd — individual mapping rules
# ---------------------------------------------------------------------------


class TestXmlToJmdElements:
    """Rule 2.1: Elements → Headings."""

    def test_root_element(self) -> None:
        jmd = xml_to_jmd("<root/>")
        assert "# root" in jmd

    def test_nested_elements(self) -> None:
        xml = "<a><b><c/></b></a>"
        jmd = xml_to_jmd(xml)
        assert "# a" in jmd
        assert "## b" in jmd
        assert "### c" in jmd

    def test_namespace_prefix_preserved(self) -> None:
        xml = (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main"/>'
        )
        jmd = xml_to_jmd(xml)
        assert "# w:document" in jmd

    def test_repeated_siblings_produce_repeated_headings(self) -> None:
        xml = "<root><item/><item/></root>"
        jmd = xml_to_jmd(xml)
        # Both items should appear as ## item headings
        assert jmd.count("## item") == 2

    def test_heterogeneous_siblings_preserve_order(self) -> None:
        xml = "<root><a/><b/><a/></root>"
        jmd = xml_to_jmd(xml)
        lines = [ln for ln in jmd.splitlines() if ln.startswith("##")]
        assert lines == ["## a", "## b", "## a"]


class TestXmlToJmdAttributes:
    """Rule 2.2: Attributes → Fields."""

    def test_simple_attribute(self) -> None:
        # "42" looks like a number, so it is quoted to preserve string type.
        jmd = xml_to_jmd('<item id="42"/>')
        assert 'id: "42"' in jmd

    def test_namespace_qualified_attribute_is_quoted(self) -> None:
        xml = (
            '<w:p xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main" w:rsidR="00B84FB8"/>'
        )
        jmd = xml_to_jmd(xml)
        assert '"w:rsidR": 00B84FB8' in jmd

    def test_xmlns_declarations_emitted_as_fields(self) -> None:
        xml = (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main"/>'
        )
        jmd = xml_to_jmd(xml)
        assert (
            '"xmlns:w": http://schemas.openxmlformats.org/'
            "wordprocessingml/2006/main"
        ) in jmd

    def test_xmlns_not_repeated_on_children(self) -> None:
        xml = (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main">'
            "<w:body/>"
            "</w:document>"
        )
        jmd = xml_to_jmd(xml)
        # xmlns:w should appear only once (on the root element)
        assert jmd.count('"xmlns:w"') == 1


class TestXmlToJmdTextContent:
    """Rule 2.3: Text Content → Implicit Attribute."""

    def test_text_only_compact_scalar_heading(self) -> None:
        jmd = xml_to_jmd("<name>Alice</name>")
        assert "# name: Alice" in jmd

    def test_text_with_attribute_uses_underscore(self) -> None:
        xml = (
            '<w:t xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main" xml:space="preserve"> World</w:t>'
        )
        jmd = xml_to_jmd(xml)
        assert '_: " World"' in jmd
        assert '"xml:space": preserve' in jmd

    def test_quoted_text_preserves_leading_space(self) -> None:
        xml = '<t xml:space="preserve"> hello</t>'
        jmd = xml_to_jmd(xml)
        assert '_: " hello"' in jmd

    def test_pure_whitespace_text_ignored(self) -> None:
        xml = "<root>\n  <child/>\n</root>"
        jmd = xml_to_jmd(xml)
        # No bare whitespace-only text should appear as a field
        assert "_:" not in jmd

    def test_underscore_attribute_disambiguated(self) -> None:
        # A literal XML attribute named "_" must be quoted in JMD to
        # distinguish it from the reserved bare "_" text-content key.
        xml = '<elem _="meta">text</elem>'
        jmd = xml_to_jmd(xml)
        assert '"_": meta' in jmd
        assert "\n_: text" in jmd


class TestXmlToJmdEmptyElements:
    """Rule 2.4: Empty Elements → Heading with no fields."""

    def test_empty_element(self) -> None:
        xml = (
            '<w:b xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main"/>'
        )
        jmd = xml_to_jmd(xml)
        assert "## w:b" not in jmd  # depth depends on nesting
        assert "# w:b" in jmd

    def test_empty_element_no_fields(self) -> None:
        jmd = xml_to_jmd("<flag/>")
        lines = jmd.strip().splitlines()
        # Only the heading line, no fields
        non_blank = [ln for ln in lines if ln.strip()]
        assert non_blank == ["# flag"]


# ---------------------------------------------------------------------------
# jmd_to_xml — parsing and reconstruction
# ---------------------------------------------------------------------------


class TestJmdToXml:
    """JMD → XML reconstruction."""

    def test_simple_element(self) -> None:
        jmd = "# root\n"
        xml = jmd_to_xml(jmd)
        root = etree.fromstring(xml)
        assert root.tag == "root"

    def test_attribute_reconstruction(self) -> None:
        jmd = "# item\nid: 42\n"
        xml = jmd_to_xml(jmd)
        root = etree.fromstring(xml)
        assert root.get("id") == "42"

    def test_namespace_declaration(self) -> None:
        jmd = textwrap.dedent("""\
            # w:document
            "xmlns:w": http://schemas.openxmlformats.org/wordprocessingml/2006/main
        """)
        xml = jmd_to_xml(jmd)
        root = etree.fromstring(xml)
        assert root.nsmap["w"] == (
            "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        )

    def test_text_content_underscore(self) -> None:
        jmd = "# greeting\n_: Hello\n"
        xml = jmd_to_xml(jmd)
        root = etree.fromstring(xml)
        assert root.text == "Hello"

    def test_text_content_scalar_heading(self) -> None:
        # No namespace prefix — plain element name works without declarations.
        jmd = "# root\n## title: Hello\n"
        xml = jmd_to_xml(jmd)
        root = etree.fromstring(xml)
        child = root[0]
        assert child.text == "Hello"

    def test_nested_elements(self) -> None:
        jmd = textwrap.dedent("""\
            # root

            ## child

            ### grandchild
        """)
        xml = jmd_to_xml(jmd)
        root = etree.fromstring(xml)
        assert root[0].tag == "child"
        assert root[0][0].tag == "grandchild"


# ---------------------------------------------------------------------------
# Lossless roundtrip tests
# ---------------------------------------------------------------------------


class TestRoundtrip:
    """XML → JMD → XML lossless roundtrip."""

    def test_simple_element(self) -> None:
        xml = "<root/>"
        assert _roundtrip(xml) == _xml(xml)

    def test_attributes(self) -> None:
        xml = '<item id="1" name="Alice"/>'
        assert _roundtrip(xml) == _xml(xml)

    def test_text_content(self) -> None:
        xml = "<greeting>Hello</greeting>"
        assert _roundtrip(xml) == _xml(xml)

    def test_text_with_spaces(self) -> None:
        xml = '<t xml:space="preserve"> World</t>'
        assert _roundtrip(xml) == _xml(xml)

    def test_nested(self) -> None:
        xml = "<a><b><c/></b></a>"
        assert _roundtrip(xml) == _xml(xml)

    def test_repeated_siblings(self) -> None:
        xml = "<root><item id='1'/><item id='2'/></root>"
        assert _roundtrip(xml) == _xml(xml)

    def test_heterogeneous_siblings(self) -> None:
        xml = "<body><p id='1'/><tbl/><p id='2'/></body>"
        assert _roundtrip(xml) == _xml(xml)

    def test_namespaces(self) -> None:
        xml = (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main">'
            '<w:body><w:p w:rsidR="00B84FB8"/></w:body>'
            "</w:document>"
        )
        assert _roundtrip(xml) == _xml(xml)

    def test_deep_nesting(self) -> None:
        """Elements nested beyond heading depth 6 (######) must round-trip."""
        xml = "<a><b><c><d><e><f><g><h>deep</h></g></f></e></d></c></b></a>"
        assert _roundtrip(xml) == _xml(xml)

    def test_namespace_on_child_element(self) -> None:
        """Namespace declared on a non-root element must be preserved."""
        xml = (
            '<root xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main">'
            '<w:body>'
            '<inner xmlns:c="http://schemas.openxmlformats.org/'
            'drawingml/2006/chart" c:val="x"/>'
            "</w:body>"
            "</root>"
        )
        assert _roundtrip(xml) == _xml(xml)

    def test_redundant_namespace_redeclaration_not_preserved(self) -> None:
        """Redundant namespace re-declarations on child elements are dropped.

        A namespace declared on the root and redundantly re-declared on a
        descendant is semantically equivalent without the re-declaration.
        The roundtrip preserves the XML infoset but not redundant declarations.
        """
        import io
        from lxml import etree as _etree

        xml = (
            '<root xmlns:r="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships">'
            # r: is redundantly re-declared on this child
            '<child xmlns:r="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships" r:id="rId1"/>'
            "</root>"
        )
        result = _roundtrip(xml)
        # C14N must be identical (same infoset)
        def c14n(b: bytes) -> bytes:
            buf = io.BytesIO()
            _etree.fromstring(b).getroottree().write_c14n(buf)
            return buf.getvalue()

        assert c14n(result) == c14n(_xml(xml))
        # The redundant re-declaration is gone
        assert b'xmlns:r="' not in result.split(b">", 1)[1]

    def test_ooxml_fragment(self) -> None:
        """Full OOXML fragment from the companion specification."""
        xml = (
            '<w:document'
            ' xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main"'
            ' xmlns:r="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships">'
            "<w:body>"
            '<w:p w:rsidR="00B84FB8">'
            "<w:pPr>"
            '<w:pStyle w:val="Normal"/>'
            '<w:jc w:val="center"/>'
            "</w:pPr>"
            "<w:r>"
            "<w:rPr>"
            "<w:b/>"
            '<w:color w:val="FF0000"/>'
            "</w:rPr>"
            "<w:t>Hello</w:t>"
            "</w:r>"
            "<w:r>"
            '<w:t xml:space="preserve"> World</w:t>'
            "</w:r>"
            "</w:p>"
            "<w:tbl/>"
            '<w:p w:rsidR="00B84FB9"/>'
            "</w:body>"
            "</w:document>"
        )
        assert _roundtrip(xml) == _xml(xml)
