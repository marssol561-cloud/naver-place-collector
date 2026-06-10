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
    "captured_at": "2026-06-10T00:00:00+00:00",
}


@pytest.fixture
def mocked(monkeypatch):
    """Monkeypatch find_store_by_place_id + requests.post for upsert tests."""

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
    return captured


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
