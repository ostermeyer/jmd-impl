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
