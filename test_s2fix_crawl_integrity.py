"""S2-FIX unit tests — crawl integrity guard and write isolation.

CC-3 (primary guard): incomplete render → CRAWL_INCOMPLETE → no upsert
CC-4 (secondary guard): upsert_store PATCH excludes None/empty-string fields

Measured baseline from S2-VERIFY (2026-06-06):
  incomplete body_text = 128 chars (2/3 runs)
  complete  body_text = 1,673+ chars (Phase-1 confirmed)
  BODY_COMPLETENESS_THRESHOLD = 500 (conservative midpoint)

Run: python test_s2fix_crawl_integrity.py
"""
import asyncio
import json
import os
import sys
from unittest.mock import MagicMock, patch

# Required env vars BEFORE any repo imports (master_db raises EnvironmentError otherwise)
os.environ.setdefault("MASTER_DB_URL", "http://fake-db.local")
os.environ.setdefault("MASTER_DB_SERVICE_ROLE_KEY", "fake-service-role-key")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collector.place_crawler import (
    BODY_COMPLETENESS_THRESHOLD,
    CRAWL_INCOMPLETE,
    _is_render_complete,
)


# ── CC-3 partial: completeness signal ────────────────────────────────────────

def test_incomplete_128_chars():
    """S2-VERIFY confirmed: 128-char body = incomplete render."""
    body = "x" * 128
    result = _is_render_complete(body)
    assert result is False, f"FAIL: 128자 body는 불완전이어야 함, got {result!r}"
    print(f"[완전성 신호] 128자 -> {result!r}  PASS")


def test_complete_1673_chars():
    """Phase-1 confirmed: 1,673-char body = complete render."""
    body = "x" * 1673
    result = _is_render_complete(body)
    assert result is True, f"FAIL: 1673자 body는 완전이어야 함, got {result!r}"
    print(f"[완전성 신호] 1673자 -> {result!r}  PASS")


def test_threshold_boundary():
    """Values at and just below threshold."""
    just_below = "x" * (BODY_COMPLETENESS_THRESHOLD - 1)
    at_threshold = "x" * BODY_COMPLETENESS_THRESHOLD
    assert not _is_render_complete(just_below), f"FAIL: {BODY_COMPLETENESS_THRESHOLD - 1}자 -> 불완전이어야 함"
    assert _is_render_complete(at_threshold), f"FAIL: {BODY_COMPLETENESS_THRESHOLD}자 -> 완전이어야 함"
    print(f"[완전성 신호] 경계값 (임계값={BODY_COMPLETENESS_THRESHOLD}자)  PASS")


# ── CC-3 full: CRAWL_INCOMPLETE -> no upsert in _do_crawl_and_save ───────────

def test_crawl_incomplete_no_upsert():
    """CRAWL_INCOMPLETE 반환 시 upsert_store가 호출되지 않아야 함."""
    import time
    import collector.place_crawler as pc
    import db.master_db as mdb
    from api.server import _do_crawl_and_save

    upsert_calls = []

    async def fake_crawl(place_id):
        return CRAWL_INCOMPLETE

    def fake_upsert(*a, **kw):
        upsert_calls.append((a, kw))
        return ("fake-id", False)

    with patch.object(pc, "crawl_place_by_id", side_effect=fake_crawl):
        with patch.object(mdb, "upsert_store", side_effect=fake_upsert):
            resp = asyncio.run(
                _do_crawl_and_save("어반정원", "인천 미추홀구", "1709413013", time.monotonic())
            )

    assert len(upsert_calls) == 0, f"FAIL: upsert_store가 {len(upsert_calls)}회 호출됨 (0이어야 함)"
    body = json.loads(resp.body)
    assert body.get("error_code") == "CRAWL_INCOMPLETE", (
        f"FAIL: error_code={body.get('error_code')!r} (CRAWL_INCOMPLETE 이어야 함)"
    )
    print(f"[CRAWL_INCOMPLETE -> no upsert] error_code={body['error_code']}  PASS")


# ── CC-4: upsert_store PATCH excludes None/empty-string fields ───────────────

def test_upsert_none_fields_excluded_from_patch():
    """PATCH body의 crawl_data에서 None/빈 문자열 필드가 제외되어야 함."""
    import db.master_db as mdb

    fake_store_id = "aaaaaaaa-0000-0000-0000-000000000000"
    crawl_data = {
        "visitor_review_count": 1154,   # valid -> included
        "blog_review_count": None,       # None  -> excluded
        "category": "",                  # ""    -> excluded
        "place_name": "어반정원",        # valid -> included
        "phone": None,                   # None  -> excluded
    }

    patched_payload = {}

    def fake_get(url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = [{"store_id": fake_store_id}]
        return resp

    def fake_patch(url, json=None, **kwargs):
        patched_payload.update(json or {})
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = [{"store_id": fake_store_id}]
        return resp

    with patch("db.master_db.requests.get", side_effect=fake_get):
        with patch("db.master_db.requests.patch", side_effect=fake_patch):
            mdb.upsert_store("1709413013", "어반정원", "인천 미추홀구", crawl_data)

    cd = patched_payload.get("crawl_data", {})
    assert "visitor_review_count" in cd, "FAIL: visitor_review_count는 포함되어야 함"
    assert "place_name" in cd, "FAIL: place_name은 포함되어야 함"
    assert "blog_review_count" not in cd, f"FAIL: blog_review_count(None)는 제외되어야 함, cd={cd}"
    assert "category" not in cd, f"FAIL: category('')는 제외되어야 함, cd={cd}"
    assert "phone" not in cd, f"FAIL: phone(None)은 제외되어야 함, cd={cd}"
    print(f"[upsert None 제외] PATCH crawl_data keys: {list(cd.keys())}  PASS")


# ── runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_incomplete_128_chars,
        test_complete_1673_chars,
        test_threshold_boundary,
        test_crawl_incomplete_no_upsert,
        test_upsert_none_fields_excluded_from_patch,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  {e}")
            failed += 1
        except Exception as e:
            print(f"  [{t.__name__}] UNEXPECTED ERROR: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{'ALL PASS' if not failed else f'{failed} FAILED'} ({len(tests)} tests)")
    sys.exit(failed)
