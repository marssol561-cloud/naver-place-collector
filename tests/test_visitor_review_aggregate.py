"""Offline unit tests for aggregate_visitor_reviews using the 1073-record fixture."""

import json
from pathlib import Path

import pytest

from collector.visitor_review_aggregate import aggregate_visitor_reviews

FIXTURE = Path(__file__).resolve().parents[1] / "reviews_expand_visitor_1709413013.json"


def _load_items():
    with open(FIXTURE, encoding="utf-8") as f:
        data = json.load(f)
    return data["items"]


@pytest.fixture(scope="module")
def result():
    return aggregate_visitor_reviews(_load_items())


def test_total_count(result):
    assert result["total_count"] == 1073


def test_first_review_date(result):
    assert result["first_review_date"] == "2022-04-02"


def test_distinct_review_days(result):
    assert result["distinct_review_days"] == 393


def test_revisit(result):
    assert result["revisit_count"] == 25
    assert round(result["revisit_ratio"] * 100, 1) == 2.3


def test_receipt(result):
    assert result["receipt_count"] == 1061
    assert round(result["receipt_ratio"] * 100, 1) == 98.9


def test_daily_counts_sum(result):
    assert sum(result["daily_counts"].values()) == 1073


def test_daily_average_reviews(result):
    assert round(result["daily_average_reviews"], 2) == 2.73


def test_revisit_distribution(result):
    assert result["revisit_distribution"] == {1: 1048, 2: 18, 3: 3, 4: 1, 5: 1, 6: 1, 7: 1}


def test_owner_receipt_reply_rate(result):
    assert result["reply_count"] == 0
    assert result["owner_receipt_reply_rate"] == 0.0


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
