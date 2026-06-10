import json
from pathlib import Path

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


def test_run_batch_cache_hit(monkeypatch):
    import db.master_db
    import db.visitor_db
    import collector.visitor_batch as vb

    CACHED = {"total_count": 999, "first_review_date": "2022-01-01"}

    monkeypatch.setattr(db.master_db, "find_store_by_place_id", lambda pid: {"store_id": "S1"})
    monkeypatch.setattr(db.visitor_db, "check_visitor_reviews_complete", lambda sid: True)
    monkeypatch.setattr(db.visitor_db, "get_visitor_reviews", lambda sid: CACHED)

    collector_called = []

    def _sentinel(place_id):
        collector_called.append(place_id)
        return []

    monkeypatch.setattr(vb, "collect_visitor_reviews", _sentinel)

    result = run_batch("1709413013")
    assert result == CACHED
    assert collector_called == []


def test_run_batch_cache_miss_falls_through():
    result = run_batch("1709413013", collector=fake_collector)
    assert result["total_count"] == 1073


def test_run_batch_use_cache_false():
    result = run_batch("1709413013", collector=fake_collector, use_cache=False)
    assert result["total_count"] == 1073
