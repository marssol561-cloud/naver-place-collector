import argparse, json, sys
from collector.visitor_review_aggregate import aggregate_visitor_reviews


def collect_visitor_reviews(place_id):
    """Headed live collection of visitor reviews for place_id (sync wrapper)."""
    import asyncio
    from collector.visitor_collect import collect_visitor_items
    return asyncio.run(collect_visitor_items(place_id))


def run_batch(place_id, collector=None):
    """Collect visitor review items for place_id, then aggregate. `collector`
    is injectable for offline testing; defaults to the live collector."""
    collect = collector or collect_visitor_reviews
    items = collect(place_id)
    return aggregate_visitor_reviews(items)


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
