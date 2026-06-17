"""BLOG-COUNT-FIX: _extract_blog_review_count_from_html unit tests (no network)."""
from collector.place_crawler import _extract_blog_review_count_from_html


def test_cafe_key_primary():
    html = '..."cafeBlogReviewsTotal":15,...'
    assert _extract_blog_review_count_from_html(html) == "15"


def test_legacy_fallback():
    html = '"blogReviewCount":7'
    assert _extract_blog_review_count_from_html(html) == "7"


def test_neither_present():
    html = '"someOtherKey":100'
    assert _extract_blog_review_count_from_html(html) == ""


def test_zero_guarded():
    html = '"cafeBlogReviewsTotal":0'
    assert _extract_blog_review_count_from_html(html) == ""


def test_empty_html():
    assert _extract_blog_review_count_from_html("") == ""
