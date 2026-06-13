import argparse, json, sys
from collector.visitor_review_aggregate import aggregate_visitor_reviews, compute_daily_average_reviews


def collect_visitor_reviews(place_id, since_date=None):
    """Headed live collection of visitor reviews for place_id (sync wrapper)."""
    import asyncio
    from collector.visitor_collect import collect_visitor_items
    return asyncio.run(collect_visitor_items(place_id, since_date=since_date))


def _since_date_from_stored(stored_row):
    """Return since_date string from stored row's max daily_counts key minus overlap days.
    Returns None when no stored row or empty daily_counts."""
    from datetime import date, timedelta
    from collector.visitor_collect import INCREMENTAL_OVERLAP_DAYS

    if not stored_row:
        return None
    dc = stored_row.get("daily_counts")
    if not dc:
        return None
    if isinstance(dc, str):
        try:
            dc = json.loads(dc)
        except Exception:
            return None
    if not dc:
        return None
    try:
        max_d = max(dc.keys())
        since = date.fromisoformat(max_d) - timedelta(days=INCREMENTAL_OVERLAP_DAYS)
        return since.isoformat()
    except Exception:
        return None


def run_batch(place_id, collector=None, use_cache=True, mode="auto"):
    """Collect visitor review items for place_id, then aggregate.

    mode values:
      "auto"        — existing cache/peek logic; falls through to incremental when
                      platform total increased and a stored row exists, full otherwise.
      "full"        — always full crawl (since_date=None).
      "incremental" — always incremental crawl; since_date computed from stored row.
    """
    since_date = None

    if mode == "auto":
        if use_cache and collector is None:
            try:
                from db.master_db import find_store_by_place_id
                from db.visitor_db import check_visitor_reviews_complete, get_visitor_reviews
                store = find_store_by_place_id(place_id)
                if store and check_visitor_reviews_complete(store["store_id"]):
                    cached = get_visitor_reviews(store["store_id"])
                    if cached is not None:
                        cached["daily_average_reviews"] = compute_daily_average_reviews(
                            cached.get("total_count") or 0, cached.get("first_review_date"))
                        try:
                            from collector.visitor_collect import peek_total_count
                            peek = peek_total_count(place_id)
                            stored_total = cached.get("source_total_count")
                            if peek is not None and stored_total is not None and peek > stored_total:
                                # Fall through to re-crawl; use incremental when stored row has history
                                since_date = _since_date_from_stored(cached)
                            else:
                                return cached
                        except Exception:
                            return cached  # non-fatal: peek error → use cache
            except Exception:
                pass  # fall through to crawl

    elif mode == "incremental":
        if collector is None:
            try:
                from db.master_db import find_store_by_place_id
                from db.visitor_db import get_visitor_reviews
                store = find_store_by_place_id(place_id)
                if store:
                    stored = get_visitor_reviews(store["store_id"])
                    since_date = _since_date_from_stored(stored)
            except Exception:
                since_date = None
    # mode "full": since_date stays None

    if collector is not None:
        raw = collector(place_id)
    else:
        raw = collect_visitor_reviews(place_id, since_date=since_date)

    if isinstance(raw, dict):
        items = raw.get("items") or []
        source_total = raw.get("source_total_count")
    else:
        items = raw
        source_total = None

    agg = aggregate_visitor_reviews(items)
    agg["source_total_count"] = source_total

    # Save to satellite table — only when using live collector and cache enabled
    if use_cache and collector is None:
        try:
            from datetime import datetime, timezone
            from db.visitor_db import upsert_visitor_reviews
            _now = datetime.now(timezone.utc).isoformat()
            if since_date is None:
                agg["last_full_collected_at"] = _now
            else:
                agg["last_incremental_collected_at"] = _now
            upsert_visitor_reviews(place_id, agg)
        except Exception:
            pass  # non-fatal

    return agg


def main(argv=None):
    parser = argparse.ArgumentParser(description="Visitor review batch aggregator")
    parser.add_argument("--place-id", required=True)
    parser.add_argument("--out", default=None, help="output JSON path; stdout if omitted")
    parser.add_argument("--no-cache", action="store_true", help="bypass cache, always crawl live")
    parser.add_argument("--incremental", action="store_true", help="incremental crawl from stored watermark")
    args = parser.parse_args(argv)

    mode = "incremental" if args.incremental else "auto"
    agg = run_batch(args.place_id, use_cache=not args.no_cache, mode=mode)
    text = json.dumps(agg, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
