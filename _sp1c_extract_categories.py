"""SP-1c: stores.crawl_data->>'category' distinct values + counts. READ-ONLY."""
import os, sys
from collections import Counter
from dotenv import load_dotenv
import requests

load_dotenv()
sys.stdout.reconfigure(encoding='utf-8')

URL = os.getenv("MASTER_DB_URL")
KEY = os.getenv("MASTER_DB_SERVICE_ROLE_KEY")
assert URL and KEY, "env 누락"

headers = {
    "apikey": KEY,
    "Authorization": f"Bearer {KEY}",
    "Range-Unit": "items",
    "Range": "0-999",
}

resp = requests.get(
    f"{URL}/rest/v1/stores",
    params={"select": "crawl_data", "limit": 1000},
    headers=headers,
    timeout=30,
)
resp.raise_for_status()
stores = resp.json()

total_stores = len(stores)
category_values = []
null_or_empty = 0

for row in stores:
    cd = row.get("crawl_data") or {}
    cat = cd.get("category")
    if cat and str(cat).strip():
        category_values.append(str(cat).strip())
    else:
        null_or_empty += 1

cnt = Counter(category_values)
print(f"total_stores: {total_stores}")
print(f"null_or_empty_category: {null_or_empty}")
print(f"distinct_categories: {len(cnt)}")
print(f"stores_with_category: {len(category_values)}")
print()
print("--- distinct naver categories (count desc) ---")
for cat, c in sorted(cnt.items(), key=lambda x: -x[1]):
    print(f"{c}\t{cat}")
