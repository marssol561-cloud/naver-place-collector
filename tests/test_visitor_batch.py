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
    import collector.visitor_collect as vc

    CACHED = {"total_count": 999, "first_review_date": "2022-01-01"}

    monkeypatch.setattr(db.master_db, "find_store_by_place_id", lambda pid: {"store_id": "S1"})
    monkeypatch.setattr(db.visitor_db, "check_visitor_reviews_complete", lambda sid: True)
    monkeypatch.setattr(db.visitor_db, "get_visitor_reviews", lambda sid: CACHED)
    monkeypatch.setattr(vc, "peek_total_count", lambda pid: None)

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


def test_refresh_peek_gt_stored_triggers_recrawl(monkeypatch):
    """cached complete + peek > stored → triggers full re-crawl + upsert."""
    import db.master_db
    import db.visitor_db
    import collector.visitor_batch as vb
    import collector.visitor_collect as vc

    CACHED = {
        "total_count": 100,
        "source_total_count": 100,
        "first_review_date": "2022-01-01",
        "distinct_review_days": 10,
        "daily_average_reviews": 1.0,
        "revisit_count": 2,
        "revisit_ratio": 0.02,
        "revisit_distribution": {},
        "reply_count": 1,
        "owner_receipt_reply_rate": 0.5,
        "daily_counts": {},
    }

    monkeypatch.setattr(db.master_db, "find_store_by_place_id", lambda pid: {"store_id": "S1"})
    monkeypatch.setattr(db.visitor_db, "check_visitor_reviews_complete", lambda sid: True)
    monkeypatch.setattr(db.visitor_db, "get_visitor_reviews", lambda sid: CACHED)
    monkeypatch.setattr(vc, "peek_total_count", lambda pid: 120)  # 120 > 100 → re-crawl

    collector_called = []
    upsert_called = []

    def _sentinel(place_id):
        collector_called.append(place_id)
        return []

    monkeypatch.setattr(vb, "collect_visitor_reviews", _sentinel)
    monkeypatch.setattr(db.visitor_db, "upsert_visitor_reviews", lambda pid, agg: upsert_called.append(pid))

    run_batch("1709413013")
    assert collector_called == ["1709413013"]
    assert upsert_called == ["1709413013"]


def test_refresh_peek_eq_stored_returns_cache(monkeypatch):
    """cached complete + peek == stored → returns cache, collector NOT called."""
    import db.master_db
    import db.visitor_db
    import collector.visitor_batch as vb
    import collector.visitor_collect as vc

    CACHED = {
        "total_count": 100,
        "source_total_count": 100,
        "first_review_date": "2022-01-01",
        "distinct_review_days": 10,
        "daily_average_reviews": 1.0,
        "revisit_count": 2,
        "revisit_ratio": 0.02,
        "revisit_distribution": {},
        "reply_count": 1,
        "owner_receipt_reply_rate": 0.5,
        "daily_counts": {},
    }

    monkeypatch.setattr(db.master_db, "find_store_by_place_id", lambda pid: {"store_id": "S1"})
    monkeypatch.setattr(db.visitor_db, "check_visitor_reviews_complete", lambda sid: True)
    monkeypatch.setattr(db.visitor_db, "get_visitor_reviews", lambda sid: CACHED)
    monkeypatch.setattr(vc, "peek_total_count", lambda pid: 100)  # equal → cache

    collector_called = []

    def _sentinel(place_id):
        collector_called.append(place_id)
        return []

    monkeypatch.setattr(vb, "collect_visitor_reviews", _sentinel)

    result = run_batch("1709413013")
    assert result == CACHED
    assert collector_called == []


def test_refresh_peek_none_returns_cache(monkeypatch):
    """cached complete + peek returns None → returns cache (non-fatal)."""
    import db.master_db
    import db.visitor_db
    import collector.visitor_batch as vb
    import collector.visitor_collect as vc

    CACHED = {
        "total_count": 100,
        "source_total_count": 100,
        "first_review_date": "2022-01-01",
        "distinct_review_days": 10,
        "daily_average_reviews": 1.0,
        "revisit_count": 2,
        "revisit_ratio": 0.02,
        "revisit_distribution": {},
        "reply_count": 1,
        "owner_receipt_reply_rate": 0.5,
        "daily_counts": {},
    }

    monkeypatch.setattr(db.master_db, "find_store_by_place_id", lambda pid: {"store_id": "S1"})
    monkeypatch.setattr(db.visitor_db, "check_visitor_reviews_complete", lambda sid: True)
    monkeypatch.setattr(db.visitor_db, "get_visitor_reviews", lambda sid: CACHED)
    monkeypatch.setattr(vc, "peek_total_count", lambda pid: None)

    collector_called = []

    def _sentinel(place_id):
        collector_called.append(place_id)
        return []

    monkeypatch.setattr(vb, "collect_visitor_reviews", _sentinel)

    result = run_batch("1709413013")
    assert result == CACHED
    assert collector_called == []


def test_refresh_peek_raises_returns_cache(monkeypatch):
    """cached complete + peek raises → returns cache (non-fatal)."""
    import db.master_db
    import db.visitor_db
    import collector.visitor_batch as vb
    import collector.visitor_collect as vc

    CACHED = {
        "total_count": 100,
        "source_total_count": 100,
        "first_review_date": "2022-01-01",
        "distinct_review_days": 10,
        "daily_average_reviews": 1.0,
        "revisit_count": 2,
        "revisit_ratio": 0.02,
        "revisit_distribution": {},
        "reply_count": 1,
        "owner_receipt_reply_rate": 0.5,
        "daily_counts": {},
    }

    monkeypatch.setattr(db.master_db, "find_store_by_place_id", lambda pid: {"store_id": "S1"})
    monkeypatch.setattr(db.visitor_db, "check_visitor_reviews_complete", lambda sid: True)
    monkeypatch.setattr(db.visitor_db, "get_visitor_reviews", lambda sid: CACHED)

    def _raises(pid):
        raise RuntimeError("network error")

    monkeypatch.setattr(vc, "peek_total_count", _raises)

    collector_called = []

    def _sentinel(place_id):
        collector_called.append(place_id)
        return []

    monkeypatch.setattr(vb, "collect_visitor_reviews", _sentinel)

    result = run_batch("1709413013")
    assert result == CACHED
    assert collector_called == []


def test_no_cache_full_crawl_live_path(monkeypatch):
    """no cache (complete=False) → full crawl, collector called."""
    import db.master_db
    import db.visitor_db
    import collector.visitor_batch as vb

    monkeypatch.setattr(db.master_db, "find_store_by_place_id", lambda pid: {"store_id": "S1"})
    monkeypatch.setattr(db.visitor_db, "check_visitor_reviews_complete", lambda sid: False)
    monkeypatch.setattr(db.visitor_db, "upsert_visitor_reviews", lambda pid, agg: None)

    collector_called = []

    def _sentinel(place_id):
        collector_called.append(place_id)
        return []

    monkeypatch.setattr(vb, "collect_visitor_reviews", _sentinel)

    result = run_batch("1709413013")
    assert collector_called == ["1709413013"]
    assert result["source_total_count"] is None
