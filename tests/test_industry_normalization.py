"""
SP-3 unit tests — industry normalization in db/master_db.py.
All REST calls are mocked; zero live DB access.
"""
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Set env vars before master_db is imported (module checks them at import time)
os.environ.setdefault("MASTER_DB_URL", "https://mock.supabase.co")
os.environ.setdefault("MASTER_DB_SERVICE_ROLE_KEY", "mock-key")

sys.path.insert(0, str(Path(__file__).parent.parent))
import db.master_db as mdb


# ---------------------------------------------------------------------------
# T1 — hit: '일식당' → '일식', assert normalized value written
# ---------------------------------------------------------------------------
def test_normalize_industry_hit(monkeypatch):
    monkeypatch.setattr(mdb, "_naver_map", {"일식당": "일식"})
    monkeypatch.setattr(mdb, "_naver_map_loaded_at", time.monotonic())

    result, hit = mdb.normalize_industry("일식당")

    assert result == "일식"
    assert hit is True


# ---------------------------------------------------------------------------
# T2 — miss: raw preserved + log INSERT called once with source='crawler'
# ---------------------------------------------------------------------------
def test_normalize_industry_miss_logs(monkeypatch):
    monkeypatch.setattr(mdb, "_naver_map", {"일식당": "일식"})
    monkeypatch.setattr(mdb, "_naver_map_loaded_at", time.monotonic())

    result, hit = mdb.normalize_industry("주꾸미요리")

    assert result == "주꾸미요리"
    assert hit is False

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    with patch("db.master_db.requests.post", return_value=mock_resp) as mock_post:
        mdb._log_unclassified("주꾸미요리", None)

    mock_post.assert_called_once()
    _, kwargs = mock_post.call_args
    payload = kwargs["json"]
    assert payload["source"] == "crawler"
    assert payload["input_value"] == "주꾸미요리"
    assert payload["store_id"] is None


# ---------------------------------------------------------------------------
# T3 — empty/None raw: no lookup, no log call
# ---------------------------------------------------------------------------
def test_normalize_industry_empty_none(monkeypatch):
    monkeypatch.setattr(mdb, "_naver_map", {"일식당": "일식"})
    monkeypatch.setattr(mdb, "_naver_map_loaded_at", time.monotonic())

    with patch("db.master_db.requests.get") as mock_get, \
         patch("db.master_db.requests.post") as mock_post:

        result_none, hit_none = mdb.normalize_industry(None)
        result_empty, hit_empty = mdb.normalize_industry("")

    assert result_none is None
    assert hit_none is False
    assert result_empty == ""
    assert hit_empty is False
    mock_get.assert_not_called()
    mock_post.assert_not_called()
