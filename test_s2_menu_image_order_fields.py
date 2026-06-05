"""S2 unit tests — menu_image_registered and order_enabled extraction.

Tests the pure extraction helpers with synthetic Apollo State HTML (no network,
no DB write). Four cases for menu_image_registered; two for order_enabled.

Run: python test_s2_menu_image_order_fields.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collector.place_crawler import _extract_menu_image_flag_from_html, _extract_naver_features

PLACE_ID = "1709413013"


# ── menu_image_registered ─────────────────────────────────────────────────────

def test_menu_image_with_photo():
    html = (
        f'"Menu:{PLACE_ID}_0":{{"__typename":"Menu",'
        f'"images":[{{"__typename":"MenuImage","url":"https://ldb-phinf.pstatic.net/20230101_1/abc.jpg"}}]}}'
    )
    result = _extract_menu_image_flag_from_html(html, PLACE_ID)
    assert result is True, f"FAIL: expected True, got {result!r}"
    print(f"[menu_image] case A (with photo): {result!r}  PASS")


def test_menu_image_empty_array():
    html = f'"Menu:{PLACE_ID}_0":{{"__typename":"Menu","images":[]}}'
    result = _extract_menu_image_flag_from_html(html, PLACE_ID)
    assert result is False, f"FAIL: expected False, got {result!r}"
    print(f"[menu_image] case B (empty images): {result!r}  PASS")


def test_menu_image_no_menu_key():
    result = _extract_menu_image_flag_from_html("some irrelevant html", PLACE_ID)
    assert result is False, f"FAIL: expected False, got {result!r}"
    print(f"[menu_image] case C (no Menu key): {result!r}  PASS")


def test_menu_image_wrong_place_id():
    html = (
        f'"Menu:{PLACE_ID}_0":{{"__typename":"Menu",'
        f'"images":[{{"url":"https://pstatic.net/img.jpg"}}]}}'
    )
    result = _extract_menu_image_flag_from_html(html, "9999999")
    assert result is False, f"FAIL: expected False, got {result!r}"
    print(f"[menu_image] case D (wrong place_id): {result!r}  PASS")


# ── order_enabled ─────────────────────────────────────────────────────────────

def test_order_enabled_present():
    # Minimal Apollo State with naverOrder.isPickup = true
    html = (
        '"naverOrder":{"__typename":"NaverOrder","isPickup":true,'
        '"isTableOrder":false,"isPreOrder":false,"isDelivery":false}'
    )
    features = _extract_naver_features(html)
    order_enabled = "주문" in features
    assert order_enabled is True, f"FAIL: expected True, got {order_enabled!r}"
    print(f"[order_enabled] case E (주문 active): features={features!r}  PASS")


def test_order_enabled_absent():
    # No naverOrder block → 주문 absent
    html = '"talktalkUrl":"https://talk.naver.com/abc123"'
    features = _extract_naver_features(html)
    order_enabled = "주문" in features
    assert order_enabled is False, f"FAIL: expected False, got {order_enabled!r}"
    print(f"[order_enabled] case F (주문 absent): features={features!r}  PASS")


# ── runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_menu_image_with_photo,
        test_menu_image_empty_array,
        test_menu_image_no_menu_key,
        test_menu_image_wrong_place_id,
        test_order_enabled_present,
        test_order_enabled_absent,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  {e}")
            failed += 1
    print(f"\n{'ALL PASS' if not failed else f'{failed} FAILED'} ({len(tests)} tests)")
    sys.exit(failed)
