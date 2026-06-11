import argparse, json, sys
from collector.visitor_review_aggregate import aggregate_visitor_reviews, compute_daily_average_reviews


def collect_visitor_reviews(place_id):
    """Headed live collection of visitor reviews for place_id (sync wrapper)."""
    import asyncio
    from collector.visitor_collect import collect_visitor_items
    return asyncio.run(collect_visitor_items(place_id))


def run_batch(place_id, collector=None, use_cache=True):
    """Collect visitor review items for place_id, then aggregate.
    If use_cache=True and no custom collector, checks satellite table first
    and saves results after crawling. Refreshes when platform total increases."""
    # Dedup check — only when using live collector and cache enabled
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
                            pass  # fall through to re-crawl
                        else:
                            return cached
                    except Exception:
                        return cached  # non-fatal: peek error → use cache
        except Exception:
            pass  # fall through to crawl

    collect = collector or collect_visitor_reviews
    raw = collect(place_id)

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
            from db.visitor_db import upsert_visitor_reviews
            upsert_visitor_reviews(place_id, agg)
        except Exception:
            pass  # non-fatal

    return agg


def main(argv=None):
    parser = argparse.ArgumentParser(description="Visitor review batch aggregator")
    parser.add_argument("--place-id", required=True)
    parser.add_argument("--out", default=None, help="output JSON path; stdout if omitted")
    parser.add_argument("--no-cache", action="store_true", help="bypass cache, always crawl live")
    args = parser.parse_args(argv)
    agg = run_batch(args.place_id, use_cache=not args.no_cache)
    text = json.dumps(agg, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
