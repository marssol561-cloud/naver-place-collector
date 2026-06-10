import argparse, json, sys
from collector.visitor_review_aggregate import aggregate_visitor_reviews


def collect_visitor_reviews(place_id):
    """Headed live collection of visitor reviews for place_id (sync wrapper)."""
    import asyncio
    from collector.visitor_collect import collect_visitor_items
    return asyncio.run(collect_visitor_items(place_id))


def run_batch(place_id, collector=None, use_cache=True):
    """Collect visitor review items for place_id, then aggregate.
    If use_cache=True and no custom collector, checks satellite table first
    and saves results after crawling."""
    # Dedup check — only when using live collector and cache enabled
    if use_cache and collector is None:
        try:
            from db.master_db import find_store_by_place_id
            from db.visitor_db import check_visitor_reviews_complete, get_visitor_reviews
            store = find_store_by_place_id(place_id)
            if store and check_visitor_reviews_complete(store["store_id"]):
                cached = get_visitor_reviews(store["store_id"])
                if cached is not None:
                    return cached
        except Exception:
            pass  # fall through to crawl

    collect = collector or collect_visitor_reviews
    items = collect(place_id)
    agg = aggregate_visitor_reviews(items)

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
    args = parser.parse_args(argv)
    agg = run_batch(args.place_id)
    text = json.dumps(agg, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
