"""VRDATE-1: collection timestamp tests.

Tests:
  test_full_sets_full_ts        — full run sets agg.last_full_collected_at only
  test_incremental_sets_incr_ts — incremental run sets last_incremental_collected_at only
  test_merge_preserves_other_ts — merge keeps the stored ts that the new run did NOT set
  test_status_returns_both_ts   — _collection_status last_collected includes both fields
"""
import json
import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def _stub_collect(place_id, since_date=None):
    return {"items": [], "source_total_count": 0}


# ── T1: full path sets last_full_collected_at; NOT last_incremental ───────────

def test_full_sets_full_ts(monkeypatch):
    import db.master_db, db.visitor_db, collector.visitor_batch as vb

    monkeypatch.setattr(db.master_db, "find_store_by_place_id", lambda pid: {"store_id": "S1"})
    monkeypatch.setattr(db.visitor_db, "get_visitor_reviews", lambda sid: None)
    monkeypatch.setattr(vb, "collect_visitor_reviews", _stub_collect)

    captured = {}
    monkeypatch.setattr(db.visitor_db, "upsert_visitor_reviews",
                        lambda pid, agg: captured.update(agg))

    from collector.visitor_batch import run_batch
    run_batch("dummy", mode="full")

    assert "last_full_collected_at" in captured, "full run must set last_full_collected_at"
    assert captured["last_full_collected_at"] is not None
    assert "last_incremental_collected_at" not in captured, \
        "full run must NOT set last_incremental_collected_at"


# ── T2: incremental path sets last_incremental_collected_at; NOT full ─────────

def test_incremental_sets_incr_ts(monkeypatch):
    import db.master_db, db.visitor_db, collector.visitor_batch as vb

    # stored row with daily_counts so since_date resolves to non-None
    stored = {
        "daily_counts": {"2026-06-01": 5, "2026-05-15": 3},
        "total_count": 5, "first_review_date": "2026-05-15",
    }
    monkeypatch.setattr(db.master_db, "find_store_by_place_id", lambda pid: {"store_id": "S1"})
    monkeypatch.setattr(db.visitor_db, "get_visitor_reviews", lambda sid: stored)
    monkeypatch.setattr(vb, "collect_visitor_reviews", _stub_collect)

    captured = {}
    monkeypatch.setattr(db.visitor_db, "upsert_visitor_reviews",
                        lambda pid, agg: captured.update(agg))

    from collector.visitor_batch import run_batch
    run_batch("dummy", mode="incremental")

    assert "last_incremental_collected_at" in captured, \
        "incremental run must set last_incremental_collected_at"
    assert captured["last_incremental_collected_at"] is not None
    assert "last_full_collected_at" not in captured, \
        "incremental run must NOT set last_full_collected_at"


# ── T3: merge keeps the ts the new run did NOT write ─────────────────────────

def test_merge_preserves_other_ts():
    from db.visitor_db import _merge_aggregates

    T1 = "2026-06-12T10:00:00+00:00"
    T2 = "2026-06-13T09:00:00+00:00"

    stored = {
        "last_full_collected_at": T1,
        "last_incremental_collected_at": None,
        "total_count": 10,
        "first_review_date": "2026-06-01",
        "distinct_review_days": 1,
        "daily_counts": {"2026-06-01": 10},
        "daily_average_reviews": 1.0,
        "revisit_count": 0,
        "revisit_ratio": 0.0,
        "revisit_distribution": {},
        "receipt_count": 0,
        "reply_count": 0,
        "owner_receipt_reply_rate": 0.0,
        "source_total_count": 10,
    }

    # new (incremental) sets only last_incremental_collected_at
    new = {
        "last_incremental_collected_at": T2,
        # last_full_collected_at NOT present
        "total_count": 12,
        "first_review_date": "2026-06-01",
        "distinct_review_days": 2,
        "daily_counts": {"2026-06-01": 10, "2026-06-13": 2},
        "daily_average_reviews": 1.0,
        "revisit_count": 0,
        "revisit_ratio": 0.0,
        "revisit_distribution": {},
        "receipt_count": 0,
        "reply_count": 0,
        "owner_receipt_reply_rate": 0.0,
        "source_total_count": 12,
    }

    merged = _merge_aggregates(stored, new)

    assert merged["last_full_collected_at"] == T1, \
        "merge must preserve stored last_full_collected_at when new run did not set it"
    assert merged["last_incremental_collected_at"] == T2, \
        "merge must adopt new last_incremental_collected_at"


# ── T4: status endpoint includes both timestamps ──────────────────────────────

def test_status_returns_both_ts(monkeypatch):
    import db.visitor_db
    from api.server import _collection_status

    T_FULL = "2026-06-12T00:00:00+00:00"
    T_INCR = "2026-06-13T00:00:00+00:00"

    stored_row = {
        "captured_at": T_INCR,
        "total_count": 100,
        "first_review_date": "2023-01-01",
        "last_full_collected_at": T_FULL,
        "last_incremental_collected_at": T_INCR,
    }

    monkeypatch.setattr(db.visitor_db, "get_visitor_reviews", lambda sid: stored_row)

    resp = _collection_status("S1")
    body = json.loads(resp.body)

    last = body["last_collected"]
    assert last["last_full_collected_at"] == T_FULL
    assert last["last_incremental_collected_at"] == T_INCR
