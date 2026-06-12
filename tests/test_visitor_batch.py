import json
from datetime import date

from collector.visitor_batch import collect_visitor_reviews, run_batch
from collector.visitor_review_aggregate import aggregate_visitor_reviews, compute_daily_average_reviews

_items = [
    {"representativeVisitDateTime": "2023-01-01T12:00:00", "visitCount": 1, "originType": "영수증", "has_owner_reply": True},
    {"representativeVisitDateTime": "2023-01-01T18:00:00", "visitCount": 2, "originType": "영수증", "has_owner_reply": False},
    {"representativeVisitDateTime": "2023-01-02T12:00:00", "visitCount": 3, "originType": "영수증", "has_owner_reply": True},
    {"representativeVisitDateTime": "2023-01-03T12:00:00", "visitCount": 1, "originType": "블로그", "has_owner_reply": False},
    {"representativeVisitDateTime": "2023-01-03T20:00:00", "visitCount": 2, "originType": "영수증", "has_owner_reply": True},
]


def fake_collector(place_id):
    return _items


def test_run_batch_pipeline():
    agg = run_batch("1709413013", collector=fake_collector)
    assert agg["total_count"] == 5
    assert agg["first_review_date"] == "2023-01-01"
    assert agg["distinct_review_days"] == 3
    assert agg["revisit_count"] == 3
    assert round(agg["revisit_ratio"] * 100, 1) == 60.0
    assert agg["receipt_count"] == 4
    assert round(agg["receipt_ratio"] * 100, 1) == 80.0


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
    assert result["total_count"] == 5


def test_run_batch_use_cache_false():
    result = run_batch("1709413013", collector=fake_collector, use_cache=False)
    assert result["total_count"] == 5


def test_refresh_peek_gt_stored_triggers_recrawl(monkeypatch):
    """cached complete + peek > stored → triggers re-crawl + upsert."""
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

    def _sentinel(place_id, since_date=None):
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


def test_compute_daily_average_lifecycle():
    """어반정원 실제 수치: 1076건, 2022-04-02~2026-06-12.
    span = (2026-06-12 - 2022-04-02).days + 1 = 1533 (inclusive both ends).
    1076/1533 ≈ 0.7019."""
    result = compute_daily_average_reviews(1076, "2022-04-02", as_of_date=date(2026, 6, 12))
    assert abs(result - (1076 / 1533)) < 1e-9


def test_aggregate_uses_lifecycle_denominator():
    """aggregate는 lifecycle_days를 사용해야 하며, distinct_review_days와 달라야 한다."""
    items = [
        {"representativeVisitDateTime": "2023-01-01T12:00:00", "visitCount": 1,
         "originType": "영수증", "has_owner_reply": False},
        {"representativeVisitDateTime": "2023-01-03T12:00:00", "visitCount": 1,
         "originType": "영수증", "has_owner_reply": False},
    ]
    # distinct_review_days = 2, lifecycle_days (2023-01-01 → 2023-01-10) = 10
    r = aggregate_visitor_reviews(items, as_of_date=date(2023, 1, 10))
    assert r["distinct_review_days"] == 2
    assert abs(r["daily_average_reviews"] - (2 / 10)) < 1e-9


def test_cached_return_recomputes_daily_average(monkeypatch):
    """캐시된 stale daily_average_reviews가 lifecycle 값으로 재계산되어 반환되어야 한다."""
    import db.master_db
    import db.visitor_db
    import collector.visitor_batch as vb
    import collector.visitor_collect as vc

    CACHED = {
        "total_count": 1076,
        "first_review_date": "2022-04-02",
        "source_total_count": 1076,
        "daily_average_reviews": 2.72,  # stale active-days value
    }

    monkeypatch.setattr(db.master_db, "find_store_by_place_id", lambda pid: {"store_id": "S1"})
    monkeypatch.setattr(db.visitor_db, "check_visitor_reviews_complete", lambda sid: True)
    monkeypatch.setattr(db.visitor_db, "get_visitor_reviews", lambda sid: CACHED)
    monkeypatch.setattr(vc, "peek_total_count", lambda pid: None)

    result = run_batch("1709413013")
    assert result["daily_average_reviews"] != 2.72
    # lifecycle value must be < 1.0 (1076 / many lifecycle days)
    assert result["daily_average_reviews"] < 1.0


def test_compute_daily_average_guards():
    """first_review_date=None → 0.0; as_of earlier than first → 0.0."""
    assert compute_daily_average_reviews(100, None, as_of_date=date(2026, 1, 1)) == 0.0
    assert compute_daily_average_reviews(100, "2026-06-01", as_of_date=date(2026, 1, 1)) == 0.0


def test_no_cache_full_crawl_live_path(monkeypatch):
    """no cache (complete=False) → full crawl, collector called."""
    import db.master_db
    import db.visitor_db
    import collector.visitor_batch as vb

    monkeypatch.setattr(db.master_db, "find_store_by_place_id", lambda pid: {"store_id": "S1"})
    monkeypatch.setattr(db.visitor_db, "check_visitor_reviews_complete", lambda sid: False)
    monkeypatch.setattr(db.visitor_db, "upsert_visitor_reviews", lambda pid, agg: None)

    collector_called = []

    def _sentinel(place_id, since_date=None):
        collector_called.append(place_id)
        return []

    monkeypatch.setattr(vb, "collect_visitor_reviews", _sentinel)

    result = run_batch("1709413013")
    assert collector_called == ["1709413013"]
    assert result["source_total_count"] is None


# ──────────────────────────── S2b new tests ────────────────────────────

def test_since_date_from_stored():
    from collector.visitor_batch import _since_date_from_stored

    # max date "2026-06-01", overlap 30 → "2026-05-02"
    stored = {"daily_counts": {"2026-06-01": 5, "2026-05-15": 3}}
    assert _since_date_from_stored(stored) == "2026-05-02"

    # Empty daily_counts → None
    assert _since_date_from_stored({"daily_counts": {}}) is None

    # No stored → None
    assert _since_date_from_stored(None) is None

    # No daily_counts key → None
    assert _since_date_from_stored({}) is None


def test_incremental_merge_accumulates(monkeypatch):
    """Partial incremental crawl merges with stored history; old dates preserved, new dates added."""
    import db.master_db
    import db.visitor_db
    import collector.visitor_batch as vb
    from db.visitor_db import _merge_aggregates

    STORED = {
        "total_count": 5,
        "source_total_count": 5,
        "first_review_date": "2022-01-15",
        "distinct_review_days": 3,
        "daily_average_reviews": 0.01,
        "revisit_count": 0,
        "revisit_ratio": 0.0,
        "revisit_distribution": {},
        "reply_count": 0,
        "owner_receipt_reply_rate": 0.0,
        "daily_counts": {"2022-01-15": 2, "2022-01-20": 2, "2026-06-01": 1},
        "receipt_count": 0,
    }

    partial_items = [
        {"representativeVisitDateTime": "2026-06-02T10:00:00", "visitCount": 1,
         "originType": "영수증", "has_owner_reply": False},
        {"representativeVisitDateTime": "2026-06-03T10:00:00", "visitCount": 1,
         "originType": "영수증", "has_owner_reply": False},
    ]

    monkeypatch.setattr(db.master_db, "find_store_by_place_id", lambda pid: {"store_id": "S1"})
    monkeypatch.setattr(db.visitor_db, "get_visitor_reviews", lambda sid: STORED)

    captured_merges = []

    def _capture_upsert(pid, agg):
        merged = _merge_aggregates(STORED, agg)
        captured_merges.append(merged)

    monkeypatch.setattr(db.visitor_db, "upsert_visitor_reviews", _capture_upsert)

    def _stub_collect(place_id, since_date=None):
        return {"items": partial_items, "source_total_count": 7}

    monkeypatch.setattr(vb, "collect_visitor_reviews", _stub_collect)

    run_batch("1709413013", mode="incremental")

    assert len(captured_merges) == 1
    dc = captured_merges[0]["daily_counts"]

    # Old 2022 dates preserved
    assert "2022-01-15" in dc
    assert "2022-01-20" in dc
    assert "2026-06-01" in dc

    # New 2026-06 dates added
    assert "2026-06-02" in dc
    assert "2026-06-03" in dc

    # total_count does not shrink
    assert captured_merges[0]["total_count"] >= 5


def test_full_mode_unchanged(monkeypatch):
    """mode='full' always passes since_date=None to the live collector."""
    import collector.visitor_batch as vb

    calls = []

    def _stub_collect(place_id, since_date=None):
        calls.append(since_date)
        return {"items": [], "source_total_count": 5}

    monkeypatch.setattr(vb, "collect_visitor_reviews", _stub_collect)

    run_batch("1709413013", mode="full")

    assert calls == [None]
