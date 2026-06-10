"""
Unit tests for GET /api/v1/stores/{store_id}/visitor-reviews.
All external calls (run_batch, find_store_by_id) are monkeypatched — no live DB or network.
"""
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("MASTER_DB_URL", "https://mock.supabase.co")
os.environ.setdefault("MASTER_DB_SERVICE_ROLE_KEY", "mock-key")
os.environ.setdefault("COLLECTOR_API_KEY", "mock-key")

sys.path.insert(0, str(Path(__file__).parent.parent))

import db.master_db as _master_db
import collector.visitor_batch as _visitor_batch
from api.server import app

_AUTH = {"Authorization": "Bearer mock-key"}

_FAKE_AGG = {
    "total_count": 50,
    "daily_average_reviews": 1.5,
    "revisit_ratio": 0.1,
    "revisit_distribution": {},
    "reply_count": 10,
    "owner_receipt_reply_rate": 0.356,
    "receipt_count": 45,
    "receipt_ratio": 0.9,
    "distinct_review_days": 30,
    "first_review_date": "2023-01-01",
    "revisit_count": 5,
    "daily_counts": [],
}


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


# T1: Happy path — 200, visitor_reviews contains owner_receipt_reply_rate
def test_happy_path(client, monkeypatch):
    monkeypatch.setattr(
        _master_db,
        "find_store_by_id",
        lambda sid, columns=None: {"store_id": sid, "place_id": "1234567890"},
    )
    monkeypatch.setattr(_visitor_batch, "run_batch", lambda pid: dict(_FAKE_AGG))

    resp = client.get("/api/v1/stores/abc123/visitor-reviews", headers=_AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["store_id"] == "abc123"
    assert body["place_id"] == "1234567890"
    assert "owner_receipt_reply_rate" in body["visitor_reviews"]
    assert body["visitor_reviews"]["owner_receipt_reply_rate"] == 0.356


# T2: Unknown store_id → 404 STORE_NOT_FOUND
def test_store_not_found(client, monkeypatch):
    monkeypatch.setattr(_master_db, "find_store_by_id", lambda sid, columns=None: None)

    resp = client.get("/api/v1/stores/unknown-id/visitor-reviews", headers=_AUTH)
    assert resp.status_code == 404
    body = resp.json()
    assert body["status"] == "error"
    assert body["error_code"] == "STORE_NOT_FOUND"
    assert "unknown-id" in body["message"]


# T3a: store with place_id=None → 200 PLACE_ID_MISSING
def test_place_id_none(client, monkeypatch):
    monkeypatch.setattr(
        _master_db,
        "find_store_by_id",
        lambda sid, columns=None: {"store_id": sid, "place_id": None},
    )

    resp = client.get("/api/v1/stores/abc123/visitor-reviews", headers=_AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
    assert body["error_code"] == "PLACE_ID_MISSING"
    assert body["message"] == "store has no place_id"


# T3b: store with place_id="" (empty string) → 200 PLACE_ID_MISSING
def test_place_id_empty_string(client, monkeypatch):
    monkeypatch.setattr(
        _master_db,
        "find_store_by_id",
        lambda sid, columns=None: {"store_id": sid, "place_id": ""},
    )

    resp = client.get("/api/v1/stores/abc123/visitor-reviews", headers=_AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
    assert body["error_code"] == "PLACE_ID_MISSING"


# T4: run_batch raises → 200 AGGREGATE_FAILED (no 500 crash)
def test_aggregate_failed(client, monkeypatch):
    monkeypatch.setattr(
        _master_db,
        "find_store_by_id",
        lambda sid, columns=None: {"store_id": sid, "place_id": "1234567890"},
    )

    def _raise(pid):
        raise RuntimeError("크롤 오류")

    monkeypatch.setattr(_visitor_batch, "run_batch", _raise)

    resp = client.get("/api/v1/stores/abc123/visitor-reviews", headers=_AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
    assert body["error_code"] == "AGGREGATE_FAILED"
    assert "크롤 오류" in body["message"]


# T5: Missing Authorization header → 401
def test_no_auth(client):
    resp = client.get("/api/v1/stores/abc123/visitor-reviews")
    assert resp.status_code == 401


# T6: Wrong Bearer token → 401
def test_wrong_token(client):
    resp = client.get(
        "/api/v1/stores/abc123/visitor-reviews",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401
