import json as _json
import requests
from datetime import datetime, timezone

from db.master_db import (
    find_store_by_place_id,
    MASTER_DB_URL,
    MASTER_DB_SERVICE_ROLE_KEY,
    _auth_headers,
)
from collector.visitor_review_aggregate import compute_daily_average_reviews

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


def _parse_dict_field(v):
    """dict or JSON-string → dict; returns {} on None/invalid."""
    if v is None:
        return {}
    if isinstance(v, dict):
        return v
    try:
        return _json.loads(v)
    except Exception:
        return {}


def _merge_aggregates(stored: dict, new: dict) -> dict:
    """Merge new aggregate into stored so stored history never shrinks."""
    # daily_counts: union; per-date value = max(stored, new)
    stored_dc = _parse_dict_field(stored.get("daily_counts"))
    new_dc = _parse_dict_field(new.get("daily_counts"))
    merged_dc = dict(stored_dc)
    for d, cnt in new_dc.items():
        merged_dc[d] = max(merged_dc.get(d, 0), cnt)

    # first_review_date: earliest key in merged daily_counts
    first = min(merged_dc.keys()) if merged_dc else None
    if first is None:
        candidates = [x for x in [stored.get("first_review_date"), new.get("first_review_date")] if x]
        first = min(candidates) if candidates else None

    distinct = len(merged_dc)

    # total_count: max(stored, new, sum_of_daily)
    total = max(
        stored.get("total_count") or 0,
        new.get("total_count") or 0,
        sum(merged_dc.values()),
    )

    # revisit_distribution: union; per-bucket = max(stored, new)
    stored_rd = _parse_dict_field(stored.get("revisit_distribution"))
    new_rd = _parse_dict_field(new.get("revisit_distribution"))
    merged_rd = {int(k): v for k, v in stored_rd.items()}
    for k, v in new_rd.items():
        ik = int(k)
        merged_rd[ik] = max(merged_rd.get(ik, 0), v)

    revisit_count = sum(v for k, v in merged_rd.items() if k >= 2)
    revisit_ratio = revisit_count / total if total else 0.0

    receipt_count = max(stored.get("receipt_count") or 0, new.get("receipt_count") or 0)
    reply_count = max(stored.get("reply_count") or 0, new.get("reply_count") or 0)

    stored_rc = stored.get("receipt_count") or 0
    new_rc = new.get("receipt_count") or 0
    # owner_receipt_reply_rate: from the side with larger receipt_count
    # NOTE: approximate under aggregate-only merge; precise recompute deferred to S2b
    owner_receipt_reply_rate = (
        new.get("owner_receipt_reply_rate") or 0.0
        if new_rc >= stored_rc
        else stored.get("owner_receipt_reply_rate") or 0.0
    )

    daily_average = compute_daily_average_reviews(total, first)

    source_total_raw = max(
        stored.get("source_total_count") or 0,
        new.get("source_total_count") or 0,
    )
    source_total = source_total_raw if source_total_raw > 0 else None

    # Timestamps: take new value when set; otherwise keep stored (never overwrite with None)
    last_full = new.get("last_full_collected_at") or stored.get("last_full_collected_at")
    last_incr = new.get("last_incremental_collected_at") or stored.get("last_incremental_collected_at")

    return {
        "total_count": total,
        "first_review_date": first,
        "distinct_review_days": distinct,
        "daily_counts": merged_dc,
        "daily_average_reviews": daily_average,
        "revisit_count": revisit_count,
        "revisit_ratio": revisit_ratio,
        "revisit_distribution": merged_rd,
        "receipt_count": receipt_count,
        "reply_count": reply_count,
        "owner_receipt_reply_rate": owner_receipt_reply_rate,
        "source_total_count": source_total,
        "last_full_collected_at": last_full,
        "last_incremental_collected_at": last_incr,
    }


def upsert_visitor_reviews(place_id: str, agg: dict) -> str | None:
    """Satellite upsert: store_visitor_reviews. Merges with existing row; never shrinks history."""
    store = find_store_by_place_id(place_id)
    if not store:
        return None
    store_id = store["store_id"]

    existing = get_visitor_reviews(store_id)
    merged = _merge_aggregates(existing, agg) if existing is not None else agg

    headers = {
        "apikey": MASTER_DB_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {MASTER_DB_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    body = {
        "store_id": store_id,
        "place_id": place_id,
        "total_count": merged.get("total_count"),
        "receipt_count": merged.get("receipt_count"),
        "first_review_date": merged.get("first_review_date"),
        "distinct_review_days": merged.get("distinct_review_days"),
        "daily_average_reviews": merged.get("daily_average_reviews"),
        "revisit_count": merged.get("revisit_count"),
        "revisit_ratio": merged.get("revisit_ratio"),
        "revisit_distribution": merged.get("revisit_distribution"),
        "reply_count": merged.get("reply_count"),
        "owner_receipt_reply_rate": merged.get("owner_receipt_reply_rate"),
        "daily_counts": merged.get("daily_counts"),
        "source_total_count": merged.get("source_total_count"),
        "last_full_collected_at": merged.get("last_full_collected_at"),
        "last_incremental_collected_at": merged.get("last_incremental_collected_at"),
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
                "owner_receipt_reply_rate,daily_counts,source_total_count,"
                "last_full_collected_at,last_incremental_collected_at,captured_at"
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
