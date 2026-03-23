"""Tests for JMD Query by Example (spec § 13)."""

from typing import Any

from jmd import JMDQueryExecutor, JMDQueryParser

RECORDS = [
    {"id": 1, "status": "active",   "score": 90, "tag": "vip"},
    {"id": 2, "status": "inactive", "score": 45, "tag": "standard"},
    {"id": 3, "status": "active",   "score": 72, "tag": "vip"},
    {"id": 4, "status": "pending",  "score": 60, "tag": "standard"},
]


def query(
    source: str, records: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Parse and execute a query against the given records or RECORDS."""
    q = JMDQueryParser().parse(source)
    return JMDQueryExecutor().execute(q, records or RECORDS)


class TestQueryParsing:
    """Tests for JMDQueryParser condition parsing."""

    def test_label(self) -> None:
        """Test that the query label is parsed from the heading."""
        q = JMDQueryParser().parse("#? Order\nstatus: active")
        assert q.label == "Order"

    def test_equality_condition(self) -> None:
        """Test that a plain value produces an equality condition."""
        q = JMDQueryParser().parse("#? X\nstatus: active")
        assert q.fields[0].key == "status"
        assert q.fields[0].condition.op == "="

    def test_gt_condition(self) -> None:
        """Test that a >N value produces a greater-than condition."""
        q = JMDQueryParser().parse("#? X\nscore: >50")
        assert q.fields[0].condition.op == ">"

    def test_projection(self) -> None:
        """Test that a ? value produces a projection-only condition."""
        q = JMDQueryParser().parse("#? X\nid: ?")
        assert q.fields[0].condition.op == "?"

    def test_alternation(self) -> None:
        """Test that a pipe-separated value produces an alternation."""
        q = JMDQueryParser().parse("#? X\nstatus: active|pending")
        assert q.fields[0].condition.op == "|"
        assert "active" in q.fields[0].condition.values
        assert "pending" in q.fields[0].condition.values

    def test_negation_equality(self) -> None:
        """Test that !value produces a negated equality condition."""
        q = JMDQueryParser().parse("#? X\nstatus: !cancelled")
        cond = q.fields[0].condition
        assert cond.op == "!"
        assert cond.values[0].op == "="
        assert cond.values[0].values[0] == "cancelled"

    def test_negation_comparison(self) -> None:
        """Test that !>N produces a negated greater-than condition."""
        q = JMDQueryParser().parse("#? X\nscore: !>90")
        cond = q.fields[0].condition
        assert cond.op == "!"
        assert cond.values[0].op == ">"

    def test_negation_contains(self) -> None:
        """Test that !~value produces a negated contains condition."""
        q = JMDQueryParser().parse("#? X\nname: !~Corp")
        cond = q.fields[0].condition
        assert cond.op == "!"
        assert cond.values[0].op == "~"

    def test_regex_condition(self) -> None:
        """Test that a regex pattern produces a regex condition."""
        q = JMDQueryParser().parse("#? X\nsku: ^A\\d+")
        assert q.fields[0].condition.op == "regex"

    def test_regex_alternation_with_wildcard(self) -> None:
        """Test that an alternation with a wildcard produces a regex op."""
        q = JMDQueryParser().parse("#? X\nstatus: pending|active.*")
        assert q.fields[0].condition.op == "regex"

    def test_frontmatter_parsed(self) -> None:
        """Test that pagination frontmatter is stored in frontmatter."""
        p = JMDQueryParser()
        p.parse("page: 2\nsize: 50\n\n#? Order\nstatus: active")
        assert p.frontmatter["page"] == 2
        assert p.frontmatter["size"] == 50

    def test_bare_frontmatter_key(self) -> None:
        """Test that a bare frontmatter key is parsed as True."""
        p = JMDQueryParser()
        p.parse("count\n\n#? Order")
        assert p.frontmatter["count"] is True


class TestQueryExecution:
    """Tests for JMDQueryExecutor filter and projection logic."""

    def test_equality_filter(self) -> None:
        """Test that an equality condition filters to matching records."""
        results = query("#? X\nstatus: active")
        assert all(r["status"] == "active" for r in results)
        assert len(results) == 2

    def test_gt_filter(self) -> None:
        """Test that a greater-than condition filters records correctly."""
        results = query("#? X\nscore: >60")
        assert all(r["score"] > 60 for r in results)

    def test_gte_filter(self) -> None:
        """Test that a greater-than-or-equal condition filters correctly."""
        results = query("#? X\nscore: >=60")
        assert all(r["score"] >= 60 for r in results)

    def test_lt_filter(self) -> None:
        """Test that a less-than condition filters records correctly."""
        results = query("#? X\nscore: <60")
        assert all(r["score"] < 60 for r in results)

    def test_contains_filter(self) -> None:
        """Test that a contains condition filters records by substring match."""
        results = query("#? X\ntag: ~vip")
        assert all("vip" in r["tag"] for r in results)

    def test_alternation_filter(self) -> None:
        """Test that an alternation condition matches any listed value."""
        results = query("#? X\nstatus: active|pending")
        assert all(r["status"] in ("active", "pending") for r in results)

    def test_no_results(self) -> None:
        """Test that a condition matching no records returns an empty list."""
        results = query("#? X\nstatus: deleted")
        assert results == []

    def test_all_results(self) -> None:
        """Test that a projection-only condition returns all records."""
        results = query("#? X\nid: ?")
        assert len(results) == len(RECORDS)

    def test_combined_conditions(self) -> None:
        """Test that multiple conditions are combined with AND logic."""
        results = query("#? X\nstatus: active\nscore: >80")
        assert all(
            r["status"] == "active" and r["score"] > 80 for r in results
        )

    def test_projection_selects_fields(self) -> None:
        """Test that projection fields limit the keys in each result record."""
        results = query("#? X\nid: ?\nstatus: ?")
        for r in results:
            assert set(r.keys()) == {"id", "status"}

    def test_negation_equality_filter(self) -> None:
        """Test that a negated equality condition excludes matching records."""
        results = query("#? X\nstatus: !active")
        assert all(r["status"] != "active" for r in results)
        assert len(results) == 2

    def test_negation_comparison_filter(self) -> None:
        """Test that !>N returns records not matched by >N."""
        results = query("#? X\nscore: !>60")
        assert all(r["score"] <= 60 for r in results)

    def test_negation_contains_filter(self) -> None:
        """Test that a negated contains condition excludes matching records."""
        results = query("#? X\ntag: !~vip")
        assert all("vip" not in r["tag"] for r in results)

    def test_regex_filter(self) -> None:
        """Test that a regex condition filters records by pattern match."""
        records = [
            {"sku": "A001"}, {"sku": "B002"}, {"sku": "A123"}, {"sku": "C999"},
        ]
        results = query("#? X\nsku: ^A\\d+", records)
        assert all(r["sku"].startswith("A") for r in results)
        assert len(results) == 2

    def test_regex_case_sensitive(self) -> None:
        """Test that regex matching is case-sensitive by default."""
        records = [
            {"name": "ACME Corp"}, {"name": "acme corp"}, {"name": "Beta Ltd"}
        ]
        results = query("#? X\nname: .*Corp.*", records)
        assert len(results) == 1
        assert results[0]["name"] == "ACME Corp"

    def test_negation_regex_filter(self) -> None:
        """Test that a negated regex excludes pattern-matching records."""
        records = [
            {"sku": "LEGACY_001"}, {"sku": "A001"}, {"sku": "LEGACY_002"}
        ]
        results = query("#? X\nsku: !^LEGACY.*", records)
        assert len(results) == 1
        assert results[0]["sku"] == "A001"
