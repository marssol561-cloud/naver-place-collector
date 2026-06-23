# -*- coding: utf-8 -*-
"""배치2 retry 성공 2건 DB 검증."""
import sys, json, requests
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import dotenv_values

vals = dotenv_values(Path(__file__).parent.parent / ".env", encoding="utf-8-sig")
DB_URL = vals.get("MASTER_DB_URL", "").rstrip("/")
DB_KEY = vals.get("MASTER_DB_SERVICE_ROLE_KEY", "")

targets = [
    ("꼼숯",    "2089726807"),
    ("깡통막창", "1202844309"),
]

for name, place_id in targets:
    resp = requests.get(
        f"{DB_URL}/rest/v1/stores",
        params={"place_id": f"eq.{place_id}", "select": "industry,rating,total_reviews,crawl_data"},
        headers={"apikey": DB_KEY, "Authorization": f"Bearer {DB_KEY}"},
        timeout=15,
    )
    rows = resp.json()
    if not rows:
        print(f"[{name}] DB에 없음")
        continue
    row   = rows[0]
    crawl = row.get("crawl_data") or {}
    if isinstance(crawl, str):
        try: crawl = json.loads(crawl)
        except: crawl = {}

    industry  = row.get("industry") or ""
    total_rev = row.get("total_reviews") or crawl.get("total_reviews") or ""
    gpv_raw   = crawl.get("good_point_votes") or ""
    mm_raw    = crawl.get("menu_mentions") or ""

    gpv_ok = False
    if gpv_raw:
        try:
            p = json.loads(gpv_raw) if isinstance(gpv_raw, str) else gpv_raw
            gpv_ok = isinstance(p, list) and len(p) > 0
        except: pass

    mm_ok = bool(mm_raw and (isinstance(mm_raw, list) or
                 (isinstance(mm_raw, str) and mm_raw.strip() not in ("", "[]"))))

    # PASS = industry + gpv + menu_mentions + total_reviews (rating 제외)
    checks = [bool(industry), gpv_ok, mm_ok, bool(total_rev)]
    n_pass = sum(checks)
    verdict = "PASS" if n_pass == 4 else f"PARTIAL({n_pass}/4)"

    print(f"[{name}]  place_id={place_id}")
    print(f"  industry={industry or 'NULL'}  gpv={'Y' if gpv_ok else 'N'}  "
          f"menu={'Y' if mm_ok else 'N'}  reviews={total_rev or 'NULL'}")
    print(f"  → {verdict}")
    print()
