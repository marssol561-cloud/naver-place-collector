"""Pure aggregation function for getVisitorReviews item dicts."""


def aggregate_visitor_reviews(items):
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

    return {
        "total_count": total_count,
        "daily_counts": daily_counts,
        "distinct_review_days": distinct_review_days,
        "first_review_date": first_review_date,
        "revisit_count": revisit_count,
        "revisit_ratio": revisit_ratio,
        "receipt_count": receipt_count,
        "receipt_ratio": receipt_ratio,
    }
