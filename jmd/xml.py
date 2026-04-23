# SPDX-License-Identifier: Apache-2.0
"""XML ↔ JMD conversion — JMD over XML companion specification.

Implements a lossless mapping between data XML and JMD following the
rules defined in the companion document ``jmd-over-xml.md``:

- XML elements      → JMD headings  (qualified name, depth = nesting depth)
- XML attributes    → JMD fields    (quoted keys for namespace-qualified names)
- XML text content  → implicit ``_`` field (compact scalar heading when alone)
- XML empty element → JMD heading with no fields

Out of scope: mixed content (text nodes interleaved with element nodes).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from dataclasses import field as dc_field

from lxml import etree

from jmd._scalars import parse_scalar
from jmd._scalars import quote_key
from jmd._scalars import serialize_scalar
from jmd._tokenizer import tokenize

# The xml: namespace is predefined in XML and may not appear in nsmap.
_XML_NS = "http://www.w3.org/XML/1998/namespace"
_PREDEFINED_NS: dict[str, str] = {"xml": _XML_NS}

# Matches a scalar heading: "prefix:local: value" or "local: value".
# Group 1 = qualified name, Group 2 = scalar text.
_SCALAR_HDG_RE = re.compile(
    r"^([a-zA-Z_][a-zA-Z0-9_\-]*"
    r"(?::[a-zA-Z_][a-zA-Z0-9_\-]*)?):\s+(.+)$"
)

# Matches a quoted-key field: '"key": value'.
_QUOTED_FIELD_RE = re.compile(r'^"([^"\\]*)": *(.*)')


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def xml_to_jmd(
    source: str | bytes,
    max_depth: int | None = None,
) -> str:
    """Convert an XML document to a JMD over XML document.

    Args:
        source: XML source as a string or bytes.
        max_depth: Maximum heading depth to emit.  Elements at a depth
            greater than ``max_depth`` are omitted entirely.  Defaults to
            ``None`` (unlimited).

    Returns:
        JMD document string.
    """
    if isinstance(source, str):
        source = source.encode()
    root = etree.fromstring(source)
    lines: list[str] = []
    _element_to_jmd(root, 1, lines, {}, max_depth)
    # Remove any leading blank line artifact and ensure single trailing newline
    text = "\n".join(lines)
    return text.strip("\n") + "\n"


def jmd_to_xml(source: str) -> bytes:
    """Convert a JMD over XML document to XML bytes.

    Args:
        source: JMD document string produced by ``xml_to_jmd`` or an LLM.

    Returns:
        UTF-8 encoded XML bytes without XML declaration.
    """
    node = _parse_jmd_nodes(source)
    root = _node_to_element(node, None, {})
    return etree.tostring(root, encoding="unicode").encode()


# ---------------------------------------------------------------------------
# XML → JMD
# ---------------------------------------------------------------------------


def _serialize_xml_str(s: str) -> str:
    """Serialize an XML string value, quoting when necessary.

    Extends ``serialize_scalar`` to also quote strings with leading or
    trailing whitespace, which would be silently stripped by the tokenizer.

    Args:
        s: XML attribute value or text content.

    Returns:
        JMD scalar representation.
    """
    if not s or s != s.strip():
        return json.dumps(s, ensure_ascii=False)
    return serialize_scalar(s)


def _clark_to_qname(clark: str, nsmap: dict[str | None, str]) -> str:
    """Convert Clark notation ``{uri}local`` to ``prefix:local``.

    Args:
        clark: Clark-notation tag or attribute name.
        nsmap: Cumulative namespace map for the element (may include None
            key for the default namespace).

    Returns:
        Qualified name string with namespace prefix, or bare local name
        if no matching prefix is found.
    """
    if not clark.startswith("{"):
        return clark
    uri, local = clark[1:].split("}", 1)
    for prefix, ns_uri in nsmap.items():
        if ns_uri == uri and prefix is not None:
            return f"{prefix}:{local}"
    # Fall back to predefined namespaces (e.g. xml:)
    for prefix, ns_uri in _PREDEFINED_NS.items():
        if ns_uri == uri:
            return f"{prefix}:{local}"
    return local


def _new_ns_decls(
    element: etree._Element,
    parent_nsmap: dict[str | None, str],
) -> list[tuple[str, str]]:
    """Return namespace declarations that are new on this element.

    Args:
        element: lxml element.
        parent_nsmap: Cumulative nsmap of the parent element (empty dict
            for the root element).

    Returns:
        List of ``(key, uri)`` pairs for namespace declarations to emit
        as JMD fields on this element.
    """
    result: list[tuple[str, str]] = []
    for prefix, uri in element.nsmap.items():
        if parent_nsmap.get(prefix) != uri:
            key = "xmlns" if prefix is None else f"xmlns:{prefix}"
            result.append((key, uri))
    return result


def _element_to_jmd(
    element: etree._Element,
    depth: int,
    lines: list[str],
    parent_nsmap: dict[str | None, str],
    max_depth: int | None = None,
) -> None:
    """Recursively serialize an lxml element to JMD lines.

    Args:
        element: lxml element to serialize.
        depth: Current heading depth (1 = root, 2 = first child level, …).
        lines: Accumulator list; lines are appended in place.
        parent_nsmap: Namespace map of the parent element.
        max_depth: Maximum heading depth to emit.  Children of an element
            at ``max_depth`` are omitted.  ``None`` means unlimited.
    """
    qname = _clark_to_qname(element.tag, element.nsmap)
    hashes = "#" * depth

    # Namespace declarations new on this element
    ns_decls = _new_ns_decls(element, parent_nsmap)

    # Element attributes (Clark notation → qualified name)
    attrs = [
        (_clark_to_qname(k, element.nsmap), v)
        for k, v in element.attrib.items()
    ]

    # Text content (ignore pure-whitespace formatting text)
    raw_text = element.text or ""
    text = raw_text if raw_text.strip() else ""

    has_children = len(element) > 0

    # Compact scalar heading: element with text only, no attrs, no children.
    if not ns_decls and not attrs and text and not has_children:
        lines.append(f"{hashes} {qname}: {_serialize_xml_str(text)}")
        return

    lines.append(f"{hashes} {qname}")

    # Namespace declarations as fields
    for key, uri in ns_decls:
        lines.append(f"{quote_key(key)}: {_serialize_xml_str(uri)}")

    # Element attributes as fields.
    # The bare key "_" is reserved for text content, so a literal XML
    # attribute named "_" must always be quoted to avoid ambiguity.
    for key, val in attrs:
        key_str = '"_"' if key == "_" else quote_key(key)
        lines.append(f"{key_str}: {_serialize_xml_str(val)}")

    # Text content alongside attributes or when children follow
    if text and not has_children:
        lines.append(f"_: {_serialize_xml_str(text)}")

    # Recurse into children, separated by blank lines for readability
    if max_depth is None or depth < max_depth:
        for child in element:
            lines.append("")
            _element_to_jmd(child, depth + 1, lines, element.nsmap, max_depth)


# ---------------------------------------------------------------------------
# JMD → XML  (intermediate node tree)
# ---------------------------------------------------------------------------


@dataclass
class _XMLNode:
    """Intermediate XML tree node parsed from JMD over XML."""

    qname: str
    fields: list[tuple[str, str]] = dc_field(default_factory=list)
    children: list[_XMLNode] = dc_field(default_factory=list)


def _parse_heading_content(content: str) -> tuple[str, str | None]:
    """Split a heading label into ``(qname, scalar_value_or_None)``.

    A scalar heading encodes an element with text content directly in
    the heading line, e.g. ``w:t: Hello`` → ``('w:t', 'Hello')``.
    A plain element heading has no value: ``w:body`` → ``('w:body', None)``.

    Args:
        content: The heading label text (after ``#`` markers are stripped).

    Returns:
        Tuple of ``(qualified_name, raw_scalar_text_or_None)``.
    """
    m = _SCALAR_HDG_RE.match(content)
    if m:
        return m.group(1), m.group(2)
    return content.strip(), None


def _parse_field_line(content: str) -> tuple[str, str]:
    """Parse a ``key: value`` field line.

    Handles both quoted keys (``"xmlns:w": http://...``) and bare keys
    (``_: value``).

    Args:
        content: Stripped field line content.

    Returns:
        Tuple of ``(raw_key, raw_value)``.
    """
    m = _QUOTED_FIELD_RE.match(content)
    if m:
        return m.group(1), m.group(2)
    sep = content.find(": ")
    if sep >= 0:
        return content[:sep], content[sep + 2:]
    if content.endswith(":"):
        return content[:-1], ""
    return content, ""


def _jmd_scalar_to_str(raw: str) -> str:
    """Parse a JMD scalar value and return it as an XML string.

    XML has no type system at the infoset level; all values are strings.

    Args:
        raw: Raw JMD scalar text (may be quoted or bare).

    Returns:
        String representation for use as an XML attribute value or text
        content.
    """
    raw = raw.strip()
    if not raw:
        return ""
    val = parse_scalar(raw)
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "true" if val else "false"
    return str(val)


def _parse_jmd_nodes(source: str) -> _XMLNode:
    """Parse a JMD over XML document into an intermediate node tree.

    Args:
        source: JMD document string.

    Returns:
        Root ``_XMLNode`` representing the XML document structure.

    Raises:
        ValueError: If no root element heading is found.
    """
    lines = tokenize(source)
    n = len(lines)
    pos = 0

    # Skip frontmatter (lines before the first heading)
    while pos < n and lines[pos].heading_depth <= 0:
        pos += 1

    if pos >= n:
        raise ValueError("No root element heading found in JMD document.")

    root_line = lines[pos]
    pos += 1
    root_qname, root_text = _parse_heading_content(root_line.content)
    root_node = _XMLNode(qname=root_qname)
    if root_text is not None:
        root_node.fields.append(("_", root_text))

    # Stack of (depth, node) — drives the nesting structure
    stack: list[tuple[int, _XMLNode]] = [
        (root_line.heading_depth, root_node)
    ]

    while pos < n:
        line = lines[pos]
        pos += 1

        if line.heading_depth == -1:  # blank line — cosmetic, skip
            continue

        if line.heading_depth > 0:  # heading → child element
            # Pop until the parent is at a strictly shallower depth
            while (
                len(stack) > 1
                and stack[-1][0] >= line.heading_depth
            ):
                stack.pop()
            parent_node = stack[-1][1]
            qname, text = _parse_heading_content(line.content)
            node = _XMLNode(qname=qname)
            if text is not None:
                node.fields.append(("_", text))
            parent_node.children.append(node)
            stack.append((line.heading_depth, node))

        else:  # field line → attribute of current element
            key, raw_val = _parse_field_line(line.content)
            stack[-1][1].fields.append((key, raw_val))

    return root_node


# ---------------------------------------------------------------------------
# JMD → XML  (node tree → lxml elements)
# ---------------------------------------------------------------------------


def _qname_to_clark(qname: str, nsmap: dict[str | None, str]) -> str:
    """Convert ``prefix:local`` to Clark notation ``{uri}local``.

    Args:
        qname: Qualified name, optionally namespace-prefixed.
        nsmap: Active namespace map (prefix → URI).

    Returns:
        Clark-notation string, or the bare local name if no namespace
        prefix is present or if the prefix is not in the map.
    """
    if ":" not in qname or qname.startswith("{"):
        return qname
    prefix, local = qname.split(":", 1)
    uri = nsmap.get(prefix) or _PREDEFINED_NS.get(prefix)
    if uri:
        return f"{{{uri}}}{local}"
    return qname


def _node_to_element(
    node: _XMLNode,
    parent: etree._Element | None,
    inherited_nsmap: dict[str | None, str],
) -> etree._Element:
    """Convert an ``_XMLNode`` to an lxml element.

    Namespace declarations in the node's fields are collected first and
    passed to lxml so that the correct namespace URIs are used when
    resolving qualified attribute and element names.

    Args:
        node: Intermediate node to convert.
        parent: Parent lxml element, or ``None`` for the root.
        inherited_nsmap: Namespace map inherited from ancestor elements.

    Returns:
        The constructed lxml element (already attached to parent if given).
    """
    # Collect namespace declarations from xmlns fields before anything else.
    # Fields store (raw_key, raw_value) where raw_key preserves quoting.
    # A quoted key like '"xmlns:w"' unquotes to 'xmlns:w'; bare 'xmlns:w'
    # is identical after unquoting — both identify namespace declarations.
    local_nsmap: dict[str | None, str] = dict(inherited_nsmap)
    for raw_key, raw_val in node.fields:
        key = raw_key[1:-1] if raw_key.startswith('"') else raw_key
        if key == "xmlns":
            local_nsmap[None] = _jmd_scalar_to_str(raw_val)
        elif key.startswith("xmlns:"):
            local_nsmap[key[6:]] = _jmd_scalar_to_str(raw_val)

    clark = _qname_to_clark(node.qname, local_nsmap)

    # Namespaces that are new on this element (not inherited from parent).
    # These must be declared on the element so that lxml can resolve the
    # prefix when serializing — otherwise lxml invents ns0/ns1/... prefixes.
    new_ns = {
        k: v for k, v in local_nsmap.items()
        if inherited_nsmap.get(k) != v
    }

    if parent is None:
        element = etree.Element(clark, nsmap=local_nsmap)
    else:
        element = etree.SubElement(parent, clark, nsmap=new_ns or None)

    # Add attributes and text content from fields.
    # Bare "_" → XML text content.
    # Quoted "_" (raw_key == '"_"') → XML attribute literally named "_".
    for raw_key, raw_val in node.fields:
        key = raw_key[1:-1] if raw_key.startswith('"') else raw_key
        if key == "xmlns" or key.startswith("xmlns:"):
            continue  # Already handled via nsmap
        val = _jmd_scalar_to_str(raw_val)
        if raw_key == "_":  # bare _ → text content
            element.text = val
        else:
            attr_clark = _qname_to_clark(key, local_nsmap)
            element.set(attr_clark, val)

    # Recurse into children
    for child in node.children:
        _node_to_element(child, element, local_nsmap)

    return element
