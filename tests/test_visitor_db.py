import pytest

import db.visitor_db
from db.visitor_db import upsert_visitor_aggregate

KNOWN_PLACE_ID = "1709413013"
FAKE_STORE_ID = "S1"

AGG = {"total_count": 1074, "first_review_date": "2022-04-02"}

EXISTING_CRAWL_DATA = {
    "existing_key": "keep",
    "naedon_blog_review_count": "2",
}


@pytest.fixture
def mocked(monkeypatch):
    """Monkeypatch all DB/network calls in db.visitor_db (no real network)."""

    def fake_find_by_place_id(place_id):
        return {"store_id": FAKE_STORE_ID} if place_id == KNOWN_PLACE_ID else None

    def fake_find_by_id(store_id, columns=None):
        return {"crawl_data": dict(EXISTING_CRAWL_DATA)}

    captured = {}

    class FakeResponse:
        ok = True

        def raise_for_status(self):
            pass

    def fake_patch(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr(db.visitor_db, "find_store_by_place_id", fake_find_by_place_id)
    monkeypatch.setattr(db.visitor_db, "find_store_by_id", fake_find_by_id)
    monkeypatch.setattr(db.visitor_db.requests, "patch", fake_patch)
    return captured


def test_merges_first_review_date_only(mocked):
    upsert_visitor_aggregate(KNOWN_PLACE_ID, AGG)
    cd = mocked["json"]["crawl_data"]
    assert cd["visitor_first_review_date"] == "2022-04-02"
    assert "visitor_review_total_count" not in cd


def test_preserves_existing_keys(mocked):
    upsert_visitor_aggregate(KNOWN_PLACE_ID, AGG)
    cd = mocked["json"]["crawl_data"]
    assert cd["existing_key"] == "keep"
    assert cd["naedon_blog_review_count"] == "2"


def test_url_targets_store(mocked):
    upsert_visitor_aggregate(KNOWN_PLACE_ID, AGG)
    assert "store_id=eq.S1" in mocked["url"]


def test_store_not_found_returns_none(mocked):
    result = upsert_visitor_aggregate("UNKNOWN_PLACE_ID", AGG)
    assert result is None
    assert "url" not in mocked  # requests.patch must NOT have been called
