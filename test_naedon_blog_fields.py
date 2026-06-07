"""Unit tests — naedon blog review fields (naedon_blog_review_count, naedon_blog_latest_date).

Fixture: recon_naedon_blog_filter_out.json (no network).
Ground truth (store 1709413013, 어반정원, 2026-06-07):
  naedon count = 2
  latest date  = 2026-02-23  (items[0]: "2026.02.23." / items[1]: "2026.01.16.")

Run: python test_naedon_blog_fields.py
"""
import json
import os
import sys

os.environ.setdefault("MASTER_DB_URL", "http://fake-db.local")
os.environ.setdefault("MASTER_DB_SERVICE_ROLE_KEY", "fake-service-role-key")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collector.place_crawler import _parse_naedon_response

_RECON_PATH = os.path.join(os.path.dirname(__file__), "recon_naedon_blog_filter_out.json")

with open(_RECON_PATH, encoding="utf-8") as _f:
    _recon = json.load(_f)

# Fixture: raw body_json from recon — same shape as response.json() in _collect_naedon_blog_fields
FIXTURE_BODY = _recon["naedon_graphql_response"]["body_json"]


# ── TC-1: count parse ────────────────────────────────────────────────────────

def test_count_parse():
    """body_json[0].data.fsasReviews.total == 2."""
    count, _ = _parse_naedon_response(FIXTURE_BODY)
    assert count == "2", f"FAIL: 기대 '2', 실제 {count!r}"
    print(f"[TC-1] count parse → {count!r}  PASS")


# ── TC-2: date normalization ─────────────────────────────────────────────────

def test_date_normalization():
    """'2026.02.23.' → '2026-02-23'."""
    raw = "2026.02.23."
    normalized = raw.rstrip(".").replace(".", "-")
    assert normalized == "2026-02-23", f"FAIL: 기대 '2026-02-23', 실제 {normalized!r}"
    print(f"[TC-2] date normalization → {normalized!r}  PASS")


# ── TC-3: max-date selection ─────────────────────────────────────────────────

def test_max_date_selection():
    """max of ['2026-02-23', '2026-01-16'] == '2026-02-23'."""
    _, latest = _parse_naedon_response(FIXTURE_BODY)
    assert latest == "2026-02-23", f"FAIL: 기대 '2026-02-23', 실제 {latest!r}"
    print(f"[TC-3] max-date selection → {latest!r}  PASS")


# ── TC-4: empty-items path ───────────────────────────────────────────────────

def test_empty_items():
    """total=0 or items=[] → count='0', date=''."""
    empty_body = [{"data": {"fsasReviews": {"total": 0, "items": []}}}]
    count, date = _parse_naedon_response(empty_body)
    assert count == "0", f"FAIL: 기대 '0', 실제 {count!r}"
    assert date == "", f"FAIL: 기대 '', 실제 {date!r}"
    print(f"[TC-4] empty-items → count={count!r}, date={date!r}  PASS")

    no_items_body = [{"data": {"fsasReviews": {"total": 5, "items": []}}}]
    count2, date2 = _parse_naedon_response(no_items_body)
    assert count2 == "0", f"FAIL empty items list: 기대 '0', 실제 {count2!r}"
    assert date2 == "", f"FAIL empty items list: 기대 '', 실제 {date2!r}"
    print(f"[TC-4b] total>0 but items=[] → count={count2!r}, date={date2!r}  PASS")


# ── runner ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [test_count_parse, test_date_normalization, test_max_date_selection, test_empty_items]
    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(str(e))
            failed += 1
        except Exception as e:
            print(f"[ERROR] {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n결과: {passed} passed / {failed} failed")
    if failed:
        sys.exit(1)
