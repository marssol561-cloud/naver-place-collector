"""Pure aggregation function for getVisitorReviews item dicts."""

from datetime import date, datetime
from zoneinfo import ZoneInfo


def _lifecycle_days(first_review_date, as_of_date):
    """Calendar days from first_review_date to as_of_date, inclusive."""
    if not first_review_date:
        return None
    try:
        first = date.fromisoformat(str(first_review_date)[:10])
    except Exception:
        return None
    span = (as_of_date - first).days + 1
    return span if span >= 1 else None


def compute_daily_average_reviews(total_count, first_review_date, as_of_date=None):
    """Reviews per calendar day across lifecycle (first_review_date → as_of_date)."""
    if as_of_date is None:
        as_of_date = datetime.now(ZoneInfo("Asia/Seoul")).date()
    days = _lifecycle_days(first_review_date, as_of_date)
    if not days:
        return 0.0
    return total_count / days


def aggregate_visitor_reviews(items, as_of_date=None):
    """items: list[dict], each a getVisitorReviews item. Returns dict (keys below)."""
    total_count = len(items)

    daily_counts = {}
    for it in items:
        rvdt = it.get("representativeVisitDateTime")
        if rvdt and isinstance(rvdt, str) and len(rvdt) >= 10:
            date_str = rvdt[:10]
            daily_counts[date_str] = daily_counts.get(date_str, 0) + 1

    distinct_review_days = len(daily_counts)
    first_review_date = min(daily_counts.keys()) if daily_counts else None

    revisit_count = sum(
        1 for it in items if int(it.get("visitCount") or 0) >= 2
    )
    revisit_ratio = revisit_count / total_count if total_count else 0.0

    receipt_count = sum(
        1 for it in items if it.get("originType") == "영수증"
    )
    receipt_ratio = receipt_count / total_count if total_count else 0.0

    daily_average_reviews = compute_daily_average_reviews(total_count, first_review_date, as_of_date)

    revisit_distribution = {}
    for it in items:
        vc = int(it.get("visitCount") or 0)
        revisit_distribution[vc] = revisit_distribution.get(vc, 0) + 1

    reply_count = sum(1 for it in items if it.get("has_owner_reply"))

    receipt_reply_count = sum(
        1 for it in items
        if it.get("originType") == "영수증" and it.get("has_owner_reply")
    )
    owner_receipt_reply_rate = receipt_reply_count / receipt_count if receipt_count > 0 else 0.0

    return {
        "total_count": total_count,
        "daily_counts": daily_counts,
        "distinct_review_days": distinct_review_days,
        "first_review_date": first_review_date,
        "revisit_count": revisit_count,
        "revisit_ratio": revisit_ratio,
        "receipt_count": receipt_count,
        "receipt_ratio": receipt_ratio,
        "daily_average_reviews": daily_average_reviews,
        "revisit_distribution": revisit_distribution,
        "reply_count": reply_count,
        "owner_receipt_reply_rate": owner_receipt_reply_rate,
    }
