import pytest

import db.visitor_db
from db.visitor_db import (
    upsert_visitor_reviews,
    get_visitor_reviews,
    check_visitor_reviews_complete,
)

KNOWN_PLACE_ID = "1709413013"
FAKE_STORE_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

FULL_AGG = {
    "total_count": 1073,
    "receipt_count": 1061,
    "first_review_date": "2022-04-02",
    "distinct_review_days": 393,
    "daily_average_reviews": 2.73,
    "revisit_count": 25,
    "revisit_ratio": 0.023,
    "revisit_distribution": {1: 1048, 2: 18, 3: 3, 4: 1, 5: 1, 6: 1, 7: 1},
    "reply_count": 0,
    "owner_receipt_reply_rate": 0.0,
    "daily_counts": {"2022-04-02": 1},
    "source_total_count": 1159,
}

FULL_ROW = {
    "place_id": KNOWN_PLACE_ID,
    "total_count": 1073,
    "receipt_count": 1061,
    "first_review_date": "2022-04-02",
    "distinct_review_days": 393,
    "daily_average_reviews": 2.73,
    "revisit_count": 25,
    "revisit_ratio": 0.023,
    "revisit_distribution": {1: 1048},
    "reply_count": 0,
    "owner_receipt_reply_rate": 0.0,
    "daily_counts": {"2022-04-02": 1},
    "source_total_count": 1159,
    "captured_at": "2026-06-10T00:00:00+00:00",
}


@pytest.fixture
def mocked(monkeypatch):
    """Monkeypatch find_store_by_place_id + get_visitor_reviews(None) + requests.post."""

    def fake_find_by_place_id(place_id):
        return {"store_id": FAKE_STORE_ID} if place_id == KNOWN_PLACE_ID else None

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["body"] = json
        return FakeResponse()

    monkeypatch.setattr(db.visitor_db, "find_store_by_place_id", fake_find_by_place_id)
    monkeypatch.setattr(db.visitor_db.requests, "post", fake_post)
    # no existing row → plain insert path
    monkeypatch.setattr(db.visitor_db, "get_visitor_reviews", lambda sid: None)
    return captured


@pytest.fixture
def make_upsert_env(monkeypatch):
    """Factory fixture: call with stored_row (or None) → returns captured dict."""
    def factory(stored_row):
        captured = {}

        monkeypatch.setattr(
            db.visitor_db, "find_store_by_place_id",
            lambda pid: {"store_id": FAKE_STORE_ID},
        )
        monkeypatch.setattr(
            db.visitor_db, "get_visitor_reviews",
            lambda sid: stored_row,
        )

        class FakePostResp:
            def raise_for_status(self):
                pass

        def fake_post(url, json=None, headers=None, timeout=None):
            captured["body"] = json
            return FakePostResp()

        monkeypatch.setattr(db.visitor_db.requests, "post", fake_post)
        return captured

    return factory


# ── Existing tests (unchanged) ─────────────────────────────────────────────

def test_upsert_writes_all_fields(mocked):
    result = upsert_visitor_reviews(KNOWN_PLACE_ID, FULL_AGG)
    body = mocked["body"]
    assert result == FAKE_STORE_ID
    assert body["store_id"] == FAKE_STORE_ID
    assert body["place_id"] == KNOWN_PLACE_ID
    assert "captured_at" in body
    for key in [
        "total_count", "receipt_count", "first_review_date", "distinct_review_days",
        "daily_average_reviews", "revisit_count", "revisit_ratio",
        "revisit_distribution", "reply_count", "owner_receipt_reply_rate", "daily_counts",
        "source_total_count",
    ]:
        assert key in body, f"missing column in POST body: {key}"


def test_upsert_store_not_found(mocked):
    result = upsert_visitor_reviews("UNKNOWN", FULL_AGG)
    assert result is None
    assert "url" not in mocked


def test_get_visitor_reviews_found(monkeypatch):
    class FakeGetResp:
        def raise_for_status(self):
            pass

        def json(self):
            return [FULL_ROW]

    monkeypatch.setattr(db.visitor_db.requests, "get", lambda *a, **kw: FakeGetResp())
    result = get_visitor_reviews(FAKE_STORE_ID)
    assert result == FULL_ROW


def test_get_visitor_reviews_not_found(monkeypatch):
    class FakeGetResp:
        def raise_for_status(self):
            pass

        def json(self):
            return []

    monkeypatch.setattr(db.visitor_db.requests, "get", lambda *a, **kw: FakeGetResp())
    result = get_visitor_reviews(FAKE_STORE_ID)
    assert result is None


def test_check_complete_all_fields(monkeypatch):
    monkeypatch.setattr(db.visitor_db, "get_visitor_reviews", lambda sid: dict(FULL_ROW))
    assert check_visitor_reviews_complete(FAKE_STORE_ID) is True


def test_check_complete_missing_field(monkeypatch):
    row = dict(FULL_ROW)
    row["daily_average_reviews"] = None
    monkeypatch.setattr(db.visitor_db, "get_visitor_reviews", lambda sid: row)
    assert check_visitor_reviews_complete(FAKE_STORE_ID) is False


def test_check_complete_no_row(monkeypatch):
    monkeypatch.setattr(db.visitor_db, "get_visitor_reviews", lambda sid: None)
    assert check_visitor_reviews_complete(FAKE_STORE_ID) is False


def test_check_complete_source_total_count_none(monkeypatch):
    """source_total_count removed from REQUIRED_FIELDS: row complete even when None."""
    row = {
        "place_id": KNOWN_PLACE_ID,
        "total_count": 1073,
        "first_review_date": "2022-04-02",
        "distinct_review_days": 393,
        "daily_average_reviews": 2.73,
        "revisit_count": 25,
        "revisit_ratio": 0.023,
        "revisit_distribution": {1: 1048},
        "reply_count": 0,
        "owner_receipt_reply_rate": 0.0,
        "daily_counts": {"2022-04-02": 1},
        "source_total_count": None,
        "captured_at": "2026-06-10T00:00:00+00:00",
    }
    monkeypatch.setattr(db.visitor_db, "get_visitor_reviews", lambda sid: row)
    assert check_visitor_reviews_complete(FAKE_STORE_ID) is True


# ── S2a merge tests ────────────────────────────────────────────────────────

def test_merge_never_shrinks_history(make_upsert_env):
    """Naver hides old reviews: live crawl returns fewer/later dates than cache.
    upsert must keep all old dates and total_count must not regress."""
    stored = {
        "total_count": 1076,
        "first_review_date": "2022-04-02",
        "distinct_review_days": 5,
        "daily_counts": {
            "2022-04-02": 1,
            "2022-06-15": 2,
            "2022-10-10": 3,
            "2023-03-01": 4,
            "2026-05-01": 5,
        },
        "daily_average_reviews": 0.7019,
        "revisit_count": 25,
        "revisit_ratio": 0.023,
        "revisit_distribution": {"1": 1045, "2": 18, "3": 3, "4": 1, "5": 1, "6": 1, "7": 1},
        "receipt_count": 1058,
        "reply_count": 380,
        "owner_receipt_reply_rate": 0.356,
        "source_total_count": 1076,
    }
    new = {
        "total_count": 1070,
        "first_review_date": "2022-11-08",
        "distinct_review_days": 4,
        "daily_counts": {
            "2022-11-08": 1,
            "2023-03-01": 4,
            "2026-05-01": 5,
            "2026-06-07": 1,
        },
        "daily_average_reviews": 0.815,
        "revisit_count": 24,
        "revisit_ratio": 0.022,
        "revisit_distribution": {"1": 1044, "2": 17, "3": 3, "4": 1, "5": 1, "6": 1, "7": 1},
        "receipt_count": 1055,
        "reply_count": 378,
        "owner_receipt_reply_rate": 0.350,
        "source_total_count": 1076,
    }

    captured = make_upsert_env(stored)
    upsert_visitor_reviews(KNOWN_PLACE_ID, new)
    body = captured["body"]

    assert body["total_count"] >= 1076
    assert body["first_review_date"] == "2022-04-02"
    dc = body["daily_counts"]
    assert "2022-04-02" in dc, "oldest stored date must be preserved"
    assert "2022-06-15" in dc, "hidden Naver date must be preserved"
    assert "2022-10-10" in dc, "hidden Naver date must be preserved"
    assert "2022-11-08" in dc, "new date must be present"
    assert "2026-06-07" in dc, "new date added by fresh crawl must appear"


def test_merge_adds_new_dates(make_upsert_env):
    """Fresh crawl with dates newer than stored max: merged contains them."""
    stored = {
        "total_count": 10,
        "first_review_date": "2023-01-01",
        "daily_counts": {"2023-01-01": 5, "2023-01-02": 5},
        "revisit_distribution": {"1": 9, "2": 1},
        "receipt_count": 8,
        "reply_count": 2,
        "owner_receipt_reply_rate": 0.25,
        "source_total_count": 10,
    }
    new = {
        "total_count": 13,
        "first_review_date": "2023-01-01",
        "daily_counts": {"2023-01-01": 5, "2023-01-02": 5, "2026-06-12": 3},
        "revisit_distribution": {"1": 11, "2": 2},
        "receipt_count": 10,
        "reply_count": 3,
        "owner_receipt_reply_rate": 0.30,
        "source_total_count": 13,
    }

    captured = make_upsert_env(stored)
    upsert_visitor_reviews(KNOWN_PLACE_ID, new)
    body = captured["body"]

    assert "2026-06-12" in body["daily_counts"]
    assert body["total_count"] >= 13
    assert body["source_total_count"] == 13


def test_per_date_max(make_upsert_env):
    """Per-date max rule: new>stored → take new; new<stored → keep stored."""
    stored = {
        "total_count": 9,
        "first_review_date": "2023-01-01",
        "daily_counts": {"2023-01-01": 5, "2023-01-02": 4},
        "revisit_distribution": {},
        "receipt_count": 5,
        "reply_count": 1,
        "owner_receipt_reply_rate": 0.2,
        "source_total_count": 9,
    }
    new = {
        "total_count": 10,
        "first_review_date": "2023-01-01",
        "daily_counts": {"2023-01-01": 3, "2023-01-02": 7},
        "revisit_distribution": {},
        "receipt_count": 6,
        "reply_count": 2,
        "owner_receipt_reply_rate": 0.3,
        "source_total_count": 10,
    }

    captured = make_upsert_env(stored)
    upsert_visitor_reviews(KNOWN_PLACE_ID, new)
    dc = captured["body"]["daily_counts"]

    assert dc["2023-01-01"] == 5, "stored value higher → keep stored"
    assert dc["2023-01-02"] == 7, "new value higher → take new"


def test_no_existing_row_inserts_plain(make_upsert_env):
    """No existing row → body equals incoming agg (no merge)."""
    captured = make_upsert_env(None)
    upsert_visitor_reviews(KNOWN_PLACE_ID, FULL_AGG)
    body = captured["body"]

    assert body["total_count"] == FULL_AGG["total_count"]
    assert body["first_review_date"] == FULL_AGG["first_review_date"]
    assert body["receipt_count"] == FULL_AGG["receipt_count"]


def test_distinct_and_first_derived(make_upsert_env):
    """After merge: distinct_review_days == len(merged dc); first == min key."""
    stored = {
        "total_count": 5,
        "first_review_date": "2022-01-01",
        "daily_counts": {"2022-01-01": 2, "2022-06-01": 3},
        "revisit_distribution": {},
        "receipt_count": 4,
        "reply_count": 1,
        "owner_receipt_reply_rate": 0.25,
        "source_total_count": 5,
    }
    new = {
        "total_count": 4,
        "first_review_date": "2022-06-01",
        "daily_counts": {"2022-06-01": 3, "2023-03-15": 1},
        "revisit_distribution": {},
        "receipt_count": 3,
        "reply_count": 1,
        "owner_receipt_reply_rate": 0.33,
        "source_total_count": 5,
    }

    captured = make_upsert_env(stored)
    upsert_visitor_reviews(KNOWN_PLACE_ID, new)
    body = captured["body"]

    merged_dc = body["daily_counts"]
    assert body["distinct_review_days"] == len(merged_dc)
    assert body["first_review_date"] == min(merged_dc.keys())
