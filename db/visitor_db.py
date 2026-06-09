import requests

from db.master_db import find_store_by_place_id, find_store_by_id, MASTER_DB_URL, _auth_headers


def upsert_visitor_aggregate(place_id, agg):
    """Merge visitor aggregate keys into stores.crawl_data for place_id.
    Returns store_id on success, None if the store is not found.
    Does NOT overwrite other crawl_data keys (read-modify-write merge)."""
    store = find_store_by_place_id(place_id)
    if not store:
        return None
    store_id = store["store_id"]
    cur = find_store_by_id(store_id, columns=["crawl_data"])
    cd = dict((cur or {}).get("crawl_data") or {})
    cd["visitor_review_total_count"] = agg["total_count"]
    cd["visitor_first_review_date"] = agg["first_review_date"]
    resp = requests.patch(
        f"{MASTER_DB_URL}/rest/v1/stores?store_id=eq.{store_id}",
        headers=_auth_headers(),
        json={"crawl_data": cd},
        timeout=15,
    )
    resp.raise_for_status()
    return store_id
