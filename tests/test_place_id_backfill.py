"""
S4 unit test — place_id-only INSERT backfills store_name/address/region from crawl data.
Zero live network access — all crawl and DB calls are monkeypatched.
"""
import asyncio
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Must set env vars before importing server (config.py and master_db read them at import time)
os.environ.setdefault("MASTER_DB_URL", "https://mock.supabase.co")
os.environ.setdefault("MASTER_DB_SERVICE_ROLE_KEY", "mock-key")
os.environ.setdefault("COLLECTOR_API_KEY", "mock-key")

sys.path.insert(0, str(Path(__file__).parent.parent))

import collector.place_crawler as _place_crawler
import db.master_db as _master_db
from api.server import _do_crawl_and_save


# Raw crawl payload as place_crawler would return
_RAW_CRAWL = {
    "place_name": "테스트매장",
    "lot_address": "서울시 강남구 테헤란로 1",
    "category": "한식",
    "phone": "02-0000-0000",
}

# After apply_field_mapping: place_name → name
_MAPPED = {
    "name": "테스트매장",
    "lot_address": "서울시 강남구 테헤란로 1",
    "category": "한식",
    "phone": "02-0000-0000",
}


def _patch_crawl_chain(monkeypatch, mapped=None):
    """공통 monkeypatch: crawl → apply_field_mapping → sanitize → extract_region → upsert_store."""
    _mapped = dict(mapped or _MAPPED)
    captured = {}

    async def _mock_crawl(place_id):
        return dict(_RAW_CRAWL)

    def _mock_apply(raw):
        return dict(_mapped)

    def _mock_sanitize(m):
        return dict(m)

    def _mock_extract_region(addr):
        captured["region_input"] = addr
        return "서울" if addr else ""

    def _mock_upsert(place_id, store_name, address, crawl_data, region):
        captured["store_name"] = store_name
        captured["address"] = address
        captured["region"] = region
        return ("fake-store-id", True)

    def _mock_biz(*args, **kwargs):
        pass

    monkeypatch.setattr(_place_crawler, "crawl_place_by_id", _mock_crawl)
    monkeypatch.setattr(_master_db, "apply_field_mapping", _mock_apply)
    monkeypatch.setattr(_master_db, "sanitize_crawl_data", _mock_sanitize)
    monkeypatch.setattr(_master_db, "extract_region", _mock_extract_region)
    monkeypatch.setattr(_master_db, "upsert_store", _mock_upsert)
    monkeypatch.setattr(_master_db, "upsert_business_images", _mock_biz)

    return captured


# ── T1: place_id-only (store_name='', address='') → backfill from crawl ────────

def test_place_id_only_backfills_store_name_address(monkeypatch):
    """place_id-only INSERT: upsert_store에 non-empty store_name/address/region 전달됨"""
    captured = _patch_crawl_chain(monkeypatch)
    asyncio.run(_do_crawl_and_save("", "", "99887766", time.monotonic()))

    assert captured["store_name"] == "테스트매장", (
        f"store_name 백필 실패: got {captured.get('store_name')!r}"
    )
    assert captured["address"] == "서울시 강남구 테헤란로 1", (
        f"address 백필 실패: got {captured.get('address')!r}"
    )
    assert captured["region"] == "서울", (
        f"region 재계산 실패: got {captured.get('region')!r}"
    )


# ── T2: store_name/address 제공된 경우 crawl data로 덮어쓰지 않음 ────────────────

def test_provided_name_address_not_overwritten(monkeypatch):
    """store_name, address가 이미 있으면 crawl data 값으로 덮어쓰지 않는다"""
    captured = _patch_crawl_chain(monkeypatch)
    asyncio.run(_do_crawl_and_save(
        "제공된매장명", "인천시 남동구 구월동 1", "99887766", time.monotonic()
    ))

    assert captured["store_name"] == "제공된매장명", "제공된 store_name이 보존되어야 함"
    assert captured["address"] == "인천시 남동구 구월동 1", "제공된 address가 보존되어야 함"


# ── T3: lot_address 없고 lot_address_fallback만 있는 경우 ────────────────────────

def test_lot_address_fallback_used_when_no_lot_address(monkeypatch):
    """lot_address 없고 lot_address_fallback만 있으면 fallback으로 백필"""
    fallback_mapped = {
        "name": "폴백매장",
        "lot_address_fallback": "부산시 해운대구 해운대로 1",
        "category": "카페",
    }
    captured = _patch_crawl_chain(monkeypatch, mapped=fallback_mapped)
    asyncio.run(_do_crawl_and_save("", "", "11223344", time.monotonic()))

    assert captured["store_name"] == "폴백매장"
    assert captured["address"] == "부산시 해운대구 해운대로 1"
