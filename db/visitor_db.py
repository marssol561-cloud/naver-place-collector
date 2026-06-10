import requests
from datetime import datetime, timezone

from db.master_db import (
    find_store_by_place_id,
    MASTER_DB_URL,
    MASTER_DB_SERVICE_ROLE_KEY,
    _auth_headers,
)

REQUIRED_FIELDS = [
    "total_count",
    "first_review_date",
    "distinct_review_days",
    "daily_average_reviews",
    "revisit_count",
    "revisit_ratio",
    "revisit_distribution",
    "reply_count",
    "owner_receipt_reply_rate",
    "daily_counts",
]


def upsert_visitor_reviews(place_id: str, agg: dict) -> str | None:
    """Satellite upsert: store_visitor_reviews. Returns store_id on success, None if store not found."""
    store = find_store_by_place_id(place_id)
    if not store:
        return None
    store_id = store["store_id"]
    headers = {
        "apikey": MASTER_DB_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {MASTER_DB_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    body = {
        "store_id": store_id,
        "place_id": place_id,
        "total_count": agg.get("total_count"),
        "receipt_count": agg.get("receipt_count"),
        "first_review_date": agg.get("first_review_date"),
        "distinct_review_days": agg.get("distinct_review_days"),
        "daily_average_reviews": agg.get("daily_average_reviews"),
        "revisit_count": agg.get("revisit_count"),
        "revisit_ratio": agg.get("revisit_ratio"),
        "revisit_distribution": agg.get("revisit_distribution"),
        "reply_count": agg.get("reply_count"),
        "owner_receipt_reply_rate": agg.get("owner_receipt_reply_rate"),
        "daily_counts": agg.get("daily_counts"),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    resp = requests.post(
        f"{MASTER_DB_URL}/rest/v1/store_visitor_reviews",
        json=body, headers=headers, timeout=10,
    )
    resp.raise_for_status()
    return store_id


def get_visitor_reviews(store_id: str) -> dict | None:
    """store_id로 store_visitor_reviews 조회. 없으면 None."""
    resp = requests.get(
        f"{MASTER_DB_URL}/rest/v1/store_visitor_reviews",
        params={
            "select": (
                "place_id,total_count,receipt_count,first_review_date,"
                "distinct_review_days,daily_average_reviews,revisit_count,"
                "revisit_ratio,revisit_distribution,reply_count,"
                "owner_receipt_reply_rate,daily_counts,captured_at"
            ),
            "store_id": f"eq.{store_id}",
        },
        headers=_auth_headers(),
        timeout=10,
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


def check_visitor_reviews_complete(store_id: str) -> bool:
    """Returns True only if all REQUIRED_FIELDS are non-None in the stored row."""
    row = get_visitor_reviews(store_id)
    if row is None:
        return False
    return all(row.get(f) is not None for f in REQUIRED_FIELDS)
