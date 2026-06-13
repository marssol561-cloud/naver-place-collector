"""
Unit tests for S3a/S3b-1:
  POST /api/v1/stores/{store_id}/collect-visitor-reviews
  GET  /api/v1/stores/{store_id}/visitor-collect-status
  POST /api/v1/places/{place_id}/collect-visitor-reviews
  GET  /api/v1/places/{place_id}/visitor-collect-status

All external calls (run_batch, find_store_by_id, find_store_by_place_id,
get_visitor_reviews) are monkeypatched — no live DB or network.
_LAUNCHER is replaced with an inline synchronous runner so the background
job completes before assertions run.
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
import db.visitor_db as _visitor_db
import collector.visitor_batch as _visitor_batch
import api.server as _server
from api.server import app

_AUTH = {"Authorization": "Bearer mock-key"}
_FAKE_STORE = {"store_id": "store-abc", "place_id": "1234567890"}
_FAKE_AGG = {"total_count": 42, "first_review_date": "2023-01-01"}
_FAKE_VR = {
    "captured_at": "2026-06-01T00:00:00+00:00",
    "total_count": 42,
    "first_review_date": "2023-01-01",
}


@pytest.fixture(autouse=True)
def reset_registry():
    """Clear job registry and install inline (synchronous) launcher before each test."""
    _server._job_registry.clear()
    original_launcher = _server._LAUNCHER
    _server._LAUNCHER = lambda target, args: target(*args)
    yield
    _server._LAUNCHER = original_launcher
    _server._job_registry.clear()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


# T1 — POST returns 202 "started"; registry reaches "done" after inline runner;
#       run_batch called with correct mode.
def test_trigger_starts_job(client, monkeypatch):
    batch_calls = []

    monkeypatch.setattr(
        _master_db, "find_store_by_id",
        lambda sid, columns=None: _FAKE_STORE,
    )
    monkeypatch.setattr(
        _visitor_batch, "run_batch",
        lambda pid, **kw: batch_calls.append((pid, kw)) or _FAKE_AGG,
    )

    resp = client.post(
        "/api/v1/stores/store-abc/collect-visitor-reviews?mode=incremental",
        headers=_AUTH,
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "started"
    assert body["store_id"] == "store-abc"
    assert body["mode"] == "incremental"

    assert _server._job_registry["store-abc"]["state"] == "done"
    assert batch_calls == [("1234567890", {"mode": "incremental"})]


# T2 — mode="xyz" → 400
def test_trigger_rejects_bad_mode(client, monkeypatch):
    monkeypatch.setattr(
        _master_db, "find_store_by_id",
        lambda sid, columns=None: _FAKE_STORE,
    )

    resp = client.post(
        "/api/v1/stores/store-abc/collect-visitor-reviews?mode=xyz",
        headers=_AUTH,
    )
    assert resp.status_code == 400


# T3 — second POST while state=="running" → 409 "already_running"
def test_trigger_conflict_when_running(client, monkeypatch):
    monkeypatch.setattr(
        _master_db, "find_store_by_id",
        lambda sid, columns=None: _FAKE_STORE,
    )

    _server._job_registry["store-abc"] = {
        "state": "running",
        "mode": "incremental",
        "started_at": "2026-06-12T00:00:00+00:00",
        "finished_at": None,
        "error": None,
        "summary": None,
    }

    resp = client.post(
        "/api/v1/stores/store-abc/collect-visitor-reviews?mode=incremental",
        headers=_AUTH,
    )
    assert resp.status_code == 409
    assert resp.json()["status"] == "already_running"


# T4 — find_store_by_id None → 404
def test_trigger_store_not_found(client, monkeypatch):
    monkeypatch.setattr(
        _master_db, "find_store_by_id",
        lambda sid, columns=None: None,
    )

    resp = client.post(
        "/api/v1/stores/unknown/collect-visitor-reviews",
        headers=_AUTH,
    )
    assert resp.status_code == 404


# T5 — GET returns job state + last_collected; idle when no job
def test_status_reports_job_and_last_collected(client, monkeypatch):
    monkeypatch.setattr(
        _visitor_db, "get_visitor_reviews",
        lambda sid: _FAKE_VR,
    )

    # No job yet → idle
    resp = client.get(
        "/api/v1/stores/store-abc/visitor-collect-status",
        headers=_AUTH,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["store_id"] == "store-abc"
    assert body["job"]["state"] == "idle"
    assert body["last_collected"]["captured_at"] == "2026-06-01T00:00:00+00:00"
    assert body["last_collected"]["total_count"] == 42
    assert body["last_collected"]["first_review_date"] == "2023-01-01"

    # With a done job in registry
    _server._job_registry["store-abc"] = {
        "state": "done",
        "mode": "incremental",
        "started_at": "2026-06-12T00:00:00+00:00",
        "finished_at": "2026-06-12T00:01:00+00:00",
        "error": None,
        "summary": {"total_count": 42, "captured_at": "2026-06-12T00:01:00+00:00"},
    }

    resp = client.get(
        "/api/v1/stores/store-abc/visitor-collect-status",
        headers=_AUTH,
    )
    body = resp.json()
    assert body["job"]["state"] == "done"
    assert body["last_collected"]["total_count"] == 42


# ── S3b-1: place_id-keyed endpoints ──────────────────────────────────────────

# T6 — POST /places/{place_id}/... resolves store and starts job
def test_place_trigger_resolves_and_starts(client, monkeypatch):
    batch_calls = []

    monkeypatch.setattr(
        _master_db, "find_store_by_place_id",
        lambda pid: _FAKE_STORE,
    )
    monkeypatch.setattr(
        _visitor_batch, "run_batch",
        lambda pid, **kw: batch_calls.append((pid, kw)) or _FAKE_AGG,
    )

    resp = client.post(
        "/api/v1/places/1234567890/collect-visitor-reviews?mode=full",
        headers=_AUTH,
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "started"
    assert body["store_id"] == "store-abc"
    assert body["mode"] == "full"

    # Registry keyed by resolved store_id
    assert _server._job_registry["store-abc"]["state"] == "done"
    assert batch_calls == [("1234567890", {"mode": "full"})]


# T7 — find_store_by_place_id None → 404
def test_place_trigger_not_found(client, monkeypatch):
    monkeypatch.setattr(
        _master_db, "find_store_by_place_id",
        lambda pid: None,
    )

    resp = client.post(
        "/api/v1/places/unknown-place/collect-visitor-reviews",
        headers=_AUTH,
    )
    assert resp.status_code == 404


# T8 — invalid mode → 400
def test_place_trigger_bad_mode(client, monkeypatch):
    monkeypatch.setattr(
        _master_db, "find_store_by_place_id",
        lambda pid: _FAKE_STORE,
    )

    resp = client.post(
        "/api/v1/places/1234567890/collect-visitor-reviews?mode=invalid",
        headers=_AUTH,
    )
    assert resp.status_code == 400


# T9 — GET /places/{place_id}/... returns job + last_collected; 404 when not found
def test_place_status_resolves(client, monkeypatch):
    monkeypatch.setattr(
        _master_db, "find_store_by_place_id",
        lambda pid: _FAKE_STORE,
    )
    monkeypatch.setattr(
        _visitor_db, "get_visitor_reviews",
        lambda sid: _FAKE_VR,
    )

    resp = client.get(
        "/api/v1/places/1234567890/visitor-collect-status",
        headers=_AUTH,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["store_id"] == "store-abc"
    assert body["job"]["state"] == "idle"
    assert body["last_collected"]["total_count"] == 42

    # 404 when place not found
    monkeypatch.setattr(
        _master_db, "find_store_by_place_id",
        lambda pid: None,
    )
    resp = client.get(
        "/api/v1/places/unknown-place/visitor-collect-status",
        headers=_AUTH,
    )
    assert resp.status_code == 404


# ── S4a: name+address trigger ────────────────────────────────────────────────

# T11 — POST resolves store, starts collection; 202 includes store_id+place_id; reaches "done"
def test_by_store_resolves_and_starts(client, monkeypatch):
    batch_calls = []
    monkeypatch.setattr(
        _master_db, "find_store_by_name_address",
        lambda name, addr: _FAKE_STORE,
    )
    monkeypatch.setattr(
        _visitor_batch, "run_batch",
        lambda pid, **kw: batch_calls.append((pid, kw)) or _FAKE_AGG,
    )

    resp = client.post(
        "/api/v1/collect-visitor-reviews-by-store",
        json={"store_name": "테스트점포", "address": "서울 강남구 역삼동", "mode": "full"},
        headers=_AUTH,
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "started"
    assert body["store_id"] == "store-abc"
    assert body["place_id"] == "1234567890"
    assert _server._job_registry["store-abc"]["state"] == "done"
    assert batch_calls == [("1234567890", {"mode": "full"})]


# T12 — find_store_by_name_address None → 404 STORE_NOT_FOUND
def test_by_store_not_found(client, monkeypatch):
    monkeypatch.setattr(
        _master_db, "find_store_by_name_address",
        lambda name, addr: None,
    )

    resp = client.post(
        "/api/v1/collect-visitor-reviews-by-store",
        json={"store_name": "없는점포", "address": "서울 강남구"},
        headers=_AUTH,
    )
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "STORE_NOT_FOUND"


# T13 — store has no place_id → 404 PLACE_ID_MISSING
def test_by_store_no_place_id(client, monkeypatch):
    monkeypatch.setattr(
        _master_db, "find_store_by_name_address",
        lambda name, addr: {"store_id": "store-abc", "place_id": None},
    )

    resp = client.post(
        "/api/v1/collect-visitor-reviews-by-store",
        json={"store_name": "테스트점포", "address": "서울 강남구"},
        headers=_AUTH,
    )
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "PLACE_ID_MISSING"


# T14 — invalid mode → 400
def test_by_store_bad_mode(client, monkeypatch):
    resp = client.post(
        "/api/v1/collect-visitor-reviews-by-store",
        json={"store_name": "테스트점포", "address": "서울 강남구", "mode": "invalid"},
        headers=_AUTH,
    )
    assert resp.status_code == 400


# T15 — second call while running → 409 already_running
def test_by_store_already_running(client, monkeypatch):
    monkeypatch.setattr(
        _master_db, "find_store_by_name_address",
        lambda name, addr: _FAKE_STORE,
    )
    _server._job_registry["store-abc"] = {
        "state": "running",
        "mode": "incremental",
        "started_at": "2026-06-13T00:00:00+00:00",
        "finished_at": None,
        "error": None,
        "summary": None,
    }

    resp = client.post(
        "/api/v1/collect-visitor-reviews-by-store",
        json={"store_name": "테스트점포", "address": "서울 강남구"},
        headers=_AUTH,
    )
    assert resp.status_code == 409
    assert resp.json()["status"] == "already_running"


# T10 — store_id endpoints behavior unchanged after S3b-1 refactor
def test_store_endpoints_unchanged(client, monkeypatch):
    batch_calls = []

    monkeypatch.setattr(
        _master_db, "find_store_by_id",
        lambda sid, columns=None: _FAKE_STORE,
    )
    monkeypatch.setattr(
        _visitor_batch, "run_batch",
        lambda pid, **kw: batch_calls.append((pid, kw)) or _FAKE_AGG,
    )
    monkeypatch.setattr(
        _visitor_db, "get_visitor_reviews",
        lambda sid: _FAKE_VR,
    )

    # POST store_id → 202, registry done
    resp = client.post(
        "/api/v1/stores/store-abc/collect-visitor-reviews?mode=incremental",
        headers=_AUTH,
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == "started"
    assert _server._job_registry["store-abc"]["state"] == "done"

    # GET store_id status → ok with last_collected
    resp = client.get(
        "/api/v1/stores/store-abc/visitor-collect-status",
        headers=_AUTH,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["job"]["state"] == "done"
    assert body["last_collected"]["total_count"] == 42
