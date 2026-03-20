"""Tests for JMD Query by Example (spec § 13)."""

import pytest
from jmd import JMDQueryParser, JMDQueryExecutor, JMDQuery


RECORDS = [
    {"id": 1, "status": "active",   "score": 90, "tag": "vip"},
    {"id": 2, "status": "inactive", "score": 45, "tag": "standard"},
    {"id": 3, "status": "active",   "score": 72, "tag": "vip"},
    {"id": 4, "status": "pending",  "score": 60, "tag": "standard"},
]


def query(source: str, records=None):
    q = JMDQueryParser().parse(source)
    return JMDQueryExecutor().execute(q, records or RECORDS)


class TestQueryParsing:
    def test_label(self):
        q = JMDQueryParser().parse("#? Order\nstatus: active")
        assert q.label == "Order"

    def test_equality_condition(self):
        q = JMDQueryParser().parse("#? X\nstatus: active")
        assert q.fields[0].key == "status"
        assert q.fields[0].condition.op == "="

    def test_gt_condition(self):
        q = JMDQueryParser().parse("#? X\nscore: >50")
        assert q.fields[0].condition.op == ">"

    def test_projection(self):
        q = JMDQueryParser().parse("#? X\nid: ?")
        assert q.fields[0].condition.op == "?"

    def test_alternation(self):
        q = JMDQueryParser().parse("#? X\nstatus: active|pending")
        assert q.fields[0].condition.op == "|"
        assert "active" in q.fields[0].condition.values
        assert "pending" in q.fields[0].condition.values

    def test_negation_equality(self):
        q = JMDQueryParser().parse("#? X\nstatus: !cancelled")
        cond = q.fields[0].condition
        assert cond.op == "!"
        assert cond.values[0].op == "="
        assert cond.values[0].values[0] == "cancelled"

    def test_negation_comparison(self):
        q = JMDQueryParser().parse("#? X\nscore: !>90")
        cond = q.fields[0].condition
        assert cond.op == "!"
        assert cond.values[0].op == ">"

    def test_negation_contains(self):
        q = JMDQueryParser().parse("#? X\nname: !~Corp")
        cond = q.fields[0].condition
        assert cond.op == "!"
        assert cond.values[0].op == "~"

    def test_regex_condition(self):
        q = JMDQueryParser().parse("#? X\nsku: ^A\\d+")
        assert q.fields[0].condition.op == "regex"

    def test_regex_alternation_with_wildcard(self):
        q = JMDQueryParser().parse("#? X\nstatus: pending|active.*")
        assert q.fields[0].condition.op == "regex"

    def test_frontmatter_parsed(self):
        p = JMDQueryParser()
        p.parse("page: 2\nsize: 50\n\n#? Order\nstatus: active")
        assert p.frontmatter["page"] == 2
        assert p.frontmatter["size"] == 50

    def test_bare_frontmatter_key(self):
        p = JMDQueryParser()
        p.parse("count\n\n#? Order")
        assert p.frontmatter["count"] is True


class TestQueryExecution:
    def test_equality_filter(self):
        results = query("#? X\nstatus: active")
        assert all(r["status"] == "active" for r in results)
        assert len(results) == 2

    def test_gt_filter(self):
        results = query("#? X\nscore: >60")
        assert all(r["score"] > 60 for r in results)

    def test_gte_filter(self):
        results = query("#? X\nscore: >=60")
        assert all(r["score"] >= 60 for r in results)

    def test_lt_filter(self):
        results = query("#? X\nscore: <60")
        assert all(r["score"] < 60 for r in results)

    def test_contains_filter(self):
        results = query("#? X\ntag: ~vip")
        assert all("vip" in r["tag"] for r in results)

    def test_alternation_filter(self):
        results = query("#? X\nstatus: active|pending")
        assert all(r["status"] in ("active", "pending") for r in results)

    def test_no_results(self):
        results = query("#? X\nstatus: deleted")
        assert results == []

    def test_all_results(self):
        results = query("#? X\nid: ?")
        assert len(results) == len(RECORDS)

    def test_combined_conditions(self):
        results = query("#? X\nstatus: active\nscore: >80")
        assert all(r["status"] == "active" and r["score"] > 80 for r in results)

    def test_projection_selects_fields(self):
        results = query("#? X\nid: ?\nstatus: ?")
        for r in results:
            assert set(r.keys()) == {"id", "status"}

    def test_negation_equality_filter(self):
        results = query("#? X\nstatus: !active")
        assert all(r["status"] != "active" for r in results)
        assert len(results) == 2

    def test_negation_comparison_filter(self):
        results = query("#? X\nscore: !>60")
        assert all(r["score"] <= 60 for r in results)

    def test_negation_contains_filter(self):
        results = query("#? X\ntag: !~vip")
        assert all("vip" not in r["tag"] for r in results)

    def test_regex_filter(self):
        records = [
            {"sku": "A001"}, {"sku": "B002"}, {"sku": "A123"}, {"sku": "C999"},
        ]
        results = query("#? X\nsku: ^A\\d+", records)
        assert all(r["sku"].startswith("A") for r in results)
        assert len(results) == 2

    def test_regex_case_sensitive(self):
        records = [{"name": "ACME Corp"}, {"name": "acme corp"}, {"name": "Beta Ltd"}]
        results = query("#? X\nname: .*Corp.*", records)
        assert len(results) == 1
        assert results[0]["name"] == "ACME Corp"

    def test_negation_regex_filter(self):
        records = [{"sku": "LEGACY_001"}, {"sku": "A001"}, {"sku": "LEGACY_002"}]
        results = query("#? X\nsku: !^LEGACY.*", records)
        assert len(results) == 1
        assert results[0]["sku"] == "A001"
