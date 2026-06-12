# SPDX-License-Identifier: Apache-2.0
"""Tests for the canonical parser envelope (spec §3.6).

Covers:

- Envelope shape examples from §22.2 (data, schema, query, delete,
  bulk-delete, frontmatter)
- §3.6.1 label normalization — mode-mark and ``[]`` sigil stripped
- §3.6.3 round-trip contract: ``parse(serialize(env)) ≡ env`` and
  ``serialize(parse(src)) ≡ src``
- §3.6 single-entry-point property: ``parse()`` returns the envelope
  with no information passed via side channels
- Envelope construction validates ``mode``
"""

from __future__ import annotations

import pytest

import jmd
from jmd import Envelope

# ---------------------------------------------------------------------------
# §22.2 envelope shape examples — verbatim from the spec
# ---------------------------------------------------------------------------


class TestEnvelopeShape:
    """§22.2 envelope-shape conformance bullets."""

    def test_data_envelope(self) -> None:
        r"""``# Order\nid: 42`` → data envelope with label ``Order``."""
        env = jmd.parse("# Order\nid: 42")
        assert env.mode == "data"
        assert env.label == "Order"
        assert env.frontmatter == {}
        assert env.value == {"id": 42}

    def test_schema_envelope(self) -> None:
        r"""``#! Order\nid: integer`` → schema envelope; raw-string values."""
        env = jmd.parse("#! Order\nid: integer")
        assert env.mode == "schema"
        assert env.label == "Order"
        assert env.frontmatter == {}
        # §14.2: schema values are raw strings, not parsed type ASTs.
        assert env.value == {"id": "integer"}

    def test_query_envelope(self) -> None:
        r"""``#? Order\nstatus: pending`` → query envelope; raw strings."""
        env = jmd.parse("#? Order\nstatus: pending")
        assert env.mode == "query"
        assert env.label == "Order"
        assert env.value == {"status": "pending"}

    def test_delete_envelope(self) -> None:
        r"""``#- Order\nid: 42`` → delete envelope."""
        env = jmd.parse("#- Order\nid: 42")
        assert env.mode == "delete"
        assert env.label == "Order"
        assert env.value == {"id": 42}

    def test_delete_bulk_anonymous(self) -> None:
        r"""``#- []`` strips both the mode-mark and the ``[]`` sigil.

        Spec §22.2: ``#- []\n- 42\n- 43`` returns
        ``{mode: "delete", label: "", frontmatter: {}, value: [42, 43]}``.
        """
        env = jmd.parse("#- []\n- 42\n- 43")
        assert env.mode == "delete"
        assert env.label == ""
        assert env.frontmatter == {}
        assert env.value == [42, 43]

    def test_envelope_with_frontmatter(self) -> None:
        """Frontmatter rides on the envelope, never in ``value``."""
        env = jmd.parse("page: 1\n\n#? Order\nstatus: pending")
        assert env.mode == "query"
        assert env.label == "Order"
        assert env.frontmatter == {"page": 1}
        assert env.value == {"status": "pending"}
        # Frontmatter must not leak into the body.
        assert "page" not in env.value

    def test_root_array_label_stripped(self) -> None:
        """``# Orders[]`` → label ``Orders``, value list (sigil stripped)."""
        env = jmd.parse("# Orders[]\n- id: 1\n- id: 2")
        assert env.mode == "data"
        assert env.label == "Orders"
        assert env.value == [{"id": 1}, {"id": 2}]

    def test_anonymous_data_root(self) -> None:
        """``# []`` → empty label, list value."""
        env = jmd.parse("# []\n- a\n- b")
        assert env.mode == "data"
        assert env.label == ""
        assert env.value == ["a", "b"]

    def test_empty_frontmatter_is_dict(self) -> None:
        """No frontmatter → ``{}``, never ``None`` (§3.6.1)."""
        env = jmd.parse("# X\nk: v")
        assert env.frontmatter == {}
        assert isinstance(env.frontmatter, dict)


# ---------------------------------------------------------------------------
# §3.6.3 round-trip contract
# ---------------------------------------------------------------------------


_ROUNDTRIP_CASES = [
    ("data, no frontmatter", "# Order\nid: 42\nstatus: pending"),
    ("data, root array", "# Orders[]\n- id: 1\n- id: 2"),
    ("data, anonymous root array", "# []\n- a\n- b"),
    (
        "data, with frontmatter",
        "confidence: high\nsource: db\n\n# Customer\nname: Müller",
    ),
    ("schema, plain", "#! Order\nid: integer\nstatus: string"),
    ("query, with operator value", "#? Order\nstatus: pending\ntotal: > 50"),
    ("delete, single id", "#- Order\nid: 42"),
    ("delete, bulk anonymous", "#- []\n- 42\n- 43"),
    (
        "data, with pagination response frontmatter",
        "total: 142\npage: 2\npages: 8\n\n# Orders\n\n## data[]\n"
        "- id: 1\n  status: pending",
    ),
]


class TestRoundTripContract:
    """§3.6.3: parse and serialize compose losslessly."""

    @pytest.mark.parametrize(
        ("label", "src"),
        _ROUNDTRIP_CASES,
        ids=[label for label, _ in _ROUNDTRIP_CASES],
    )
    def test_parse_serialize_parse(self, label: str, src: str) -> None:
        """``parse(serialize(parse(src)))`` equals ``parse(src)``."""
        del label
        env1 = jmd.parse(src)
        env2 = jmd.parse(jmd.serialize(env1))
        assert env2.mode == env1.mode
        assert env2.label == env1.label
        assert env2.value == env1.value
        assert env2.frontmatter == env1.frontmatter

    @pytest.mark.parametrize(
        ("label", "src"),
        _ROUNDTRIP_CASES,
        ids=[label for label, _ in _ROUNDTRIP_CASES],
    )
    def test_serialize_is_canonical(self, label: str, src: str) -> None:
        """A second parse → serialize cycle is a fixed point."""
        del label
        once = jmd.serialize(jmd.parse(src))
        twice = jmd.serialize(jmd.parse(once))
        assert once == twice


# ---------------------------------------------------------------------------
# Single entry point — no side channels (§3.6)
# ---------------------------------------------------------------------------


class TestNoSideChannels:
    """§3.6: the envelope is the single entry point of the parser API."""

    def test_envelope_carries_everything(self) -> None:
        """All four envelope fields are populated on a single parse call."""
        env = jmd.parse(
            "confidence: high\n\n#? Order\nstatus: pending\ntotal: > 50"
        )
        # Everything the spec promises is on the envelope alone.
        assert env.mode == "query"
        assert env.label == "Order"
        assert env.frontmatter == {"confidence": "high"}
        assert env.value == {"status": "pending", "total": "> 50"}

    def test_top_level_parse_matches_class_parser(self) -> None:
        """``jmd.parse`` and class parser return identical envelopes."""
        src = "page: 1\n\n# Order\nid: 42"
        top = jmd.parse(src)
        cls = jmd.JMDParser().parse(src)
        assert top == cls


# ---------------------------------------------------------------------------
# Serializer accepts both envelope and convenience form
# ---------------------------------------------------------------------------


class TestSerializerEntryPoints:
    """§3.6.3: serializer accepts envelope; convenience form coexists."""

    def test_envelope_form_matches_convenience_form(self) -> None:
        """``serialize(env)`` equals the equivalent convenience-form call."""
        env = Envelope(
            mode="query",
            label="Order",
            value={"status": "pending"},
            frontmatter={"page": 1},
        )
        canonical = jmd.serialize(env)
        convenience = jmd.serialize(
            env.value,
            label="? " + env.label,
            frontmatter=env.frontmatter,
        )
        assert canonical == convenience

    def test_envelope_form_ignores_extra_kwargs(self) -> None:
        """``label``/``frontmatter`` kwargs are ignored when obj is Envelope."""
        env = Envelope(
            mode="data",
            label="Order",
            value={"id": 1},
            frontmatter={},
        )
        # Passing bogus label/frontmatter alongside the envelope must not
        # affect the output — the envelope is authoritative.
        a = jmd.serialize(env)
        b = jmd.serialize(env, label="IGNORED", frontmatter={"also": "ignored"})
        assert a == b


# ---------------------------------------------------------------------------
# Envelope validation
# ---------------------------------------------------------------------------


class TestEnvelopeValidation:
    """Envelope construction rejects invalid modes."""

    def test_invalid_mode_rejected(self) -> None:
        """``mode`` outside the four spec modes raises ``ValueError``."""
        with pytest.raises(ValueError, match="mode must be one of"):
            Envelope(mode="bogus", label="X", value={})  # type: ignore[arg-type]

    def test_all_four_modes_accepted(self) -> None:
        """All four spec modes pass construction validation."""
        for mode in ("data", "schema", "query", "delete"):
            Envelope(mode=mode, label="X", value={})  # type: ignore[arg-type]
