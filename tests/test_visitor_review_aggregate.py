"""Offline unit tests for aggregate_visitor_reviews using the inline fixture."""

import pytest
from datetime import date

from collector.visitor_review_aggregate import aggregate_visitor_reviews, compute_daily_average_reviews

_ITEMS = [
    {"representativeVisitDateTime": "2023-01-01T12:00:00", "visitCount": 1, "originType": "영수증", "has_owner_reply": True},
    {"representativeVisitDateTime": "2023-01-01T18:00:00", "visitCount": 2, "originType": "영수증", "has_owner_reply": False},
    {"representativeVisitDateTime": "2023-01-02T12:00:00", "visitCount": 3, "originType": "영수증", "has_owner_reply": True},
    {"representativeVisitDateTime": "2023-01-03T12:00:00", "visitCount": 1, "originType": "블로그", "has_owner_reply": False},
    {"representativeVisitDateTime": "2023-01-03T20:00:00", "visitCount": 2, "originType": "영수증", "has_owner_reply": True},
]


_AS_OF = date(2023, 1, 3)  # lifecycle_days = (2023-01-03 - 2023-01-01).days+1 = 3


@pytest.fixture(scope="module")
def result():
    return aggregate_visitor_reviews(_ITEMS, as_of_date=_AS_OF)


def test_total_count(result):
    assert result["total_count"] == 5


def test_first_review_date(result):
    assert result["first_review_date"] == "2023-01-01"


def test_distinct_review_days(result):
    assert result["distinct_review_days"] == 3


def test_revisit(result):
    assert result["revisit_count"] == 3
    assert round(result["revisit_ratio"] * 100, 1) == 60.0


def test_receipt(result):
    assert result["receipt_count"] == 4
    assert round(result["receipt_ratio"] * 100, 1) == 80.0


def test_daily_counts_sum(result):
    assert sum(result["daily_counts"].values()) == 5


def test_daily_average_reviews(result):
    # lifecycle_days = 3 (2023-01-01 → 2023-01-03), total=5 → 5/3 ≈ 1.67
    assert round(result["daily_average_reviews"], 2) == 1.67


def test_revisit_distribution(result):
    assert result["revisit_distribution"] == {1: 2, 2: 2, 3: 1}


def test_owner_receipt_reply_rate(result):
    assert result["reply_count"] == 3
    assert result["owner_receipt_reply_rate"] == 0.75


def test_empty_list_edge():
    r = aggregate_visitor_reviews([])
    assert r["total_count"] == 0
    assert r["first_review_date"] is None
    assert r["distinct_review_days"] == 0
    assert r["daily_counts"] == {}
    assert r["revisit_ratio"] == 0.0
    assert r["receipt_ratio"] == 0.0
    assert r["daily_average_reviews"] == 0.0
    assert r["revisit_distribution"] == {}
    assert r["reply_count"] == 0
    assert r["owner_receipt_reply_rate"] == 0.0
