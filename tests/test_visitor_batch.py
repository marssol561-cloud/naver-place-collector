import json
from pathlib import Path

import pytest

from collector.visitor_batch import collect_visitor_reviews, run_batch

FIXTURE = Path(__file__).resolve().parents[1] / "reviews_expand_visitor_1709413013.json"
_items = json.load(open(FIXTURE, encoding="utf-8"))["items"]


def fake_collector(place_id):
    return _items


def test_run_batch_pipeline():
    agg = run_batch("1709413013", collector=fake_collector)
    assert agg["total_count"] == 1073
    assert agg["first_review_date"] == "2022-04-02"
    assert agg["distinct_review_days"] == 393
    assert agg["revisit_count"] == 25
    assert round(agg["revisit_ratio"] * 100, 1) == 2.3
    assert agg["receipt_count"] == 1061
    assert round(agg["receipt_ratio"] * 100, 1) == 98.9


def test_result_json_serializable():
    json.dumps(run_batch("1709413013", collector=fake_collector))


def test_live_collector_is_stub():
    with pytest.raises(NotImplementedError):
        collect_visitor_reviews("x")
