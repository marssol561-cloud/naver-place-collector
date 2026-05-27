# -*- coding: utf-8 -*-
"""강남칼동태 직접 재수집 스크립트 (API 없이 직접 crawl + upsert)"""
import asyncio, json, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from collector import place_crawler
from db import master_db

PLACE_ID = "21375139"
STORE_NAME = "강남칼동태"
ADDRESS = "서울 강남구 역삼동 테헤란로 365 아크플레이스 206호"


async def main():
    print(f"[1] place_id={PLACE_ID} 크롤링 시작...")
    raw = await place_crawler.crawl_place_by_id(PLACE_ID)
    if raw is None:
        print("[ERROR] 크롤링 실패")
        return

    print(f"[2] 크롤링 완료. fields={len(raw)}")
    print(f"  category (raw): {repr(raw.get('category', ''))}")

    mapped = master_db.apply_field_mapping(raw)
    mapped = master_db.sanitize_crawl_data(mapped)
    print(f"  category (mapped): {repr(mapped.get('category', ''))}")

    region = master_db.extract_region(ADDRESS)
    print(f"  region: {region}")

    print("[3] DB upsert 시작...")
    store_id, is_new = master_db.upsert_store(PLACE_ID, STORE_NAME, ADDRESS, mapped, region)
    print(f"  store_id : {store_id}")
    print(f"  is_new   : {is_new}")

    # 4. 결과 검증
    print("[4] 저장 결과 검증...")
    import requests
    BASE = os.getenv("MASTER_DB_URL", "").rstrip("/")
    KEY  = os.getenv("MASTER_DB_SERVICE_ROLE_KEY", "")
    headers = {
        "apikey": KEY,
        "Authorization": f"Bearer {KEY}",
    }
    resp = requests.get(
        f"{BASE}/rest/v1/stores",
        params={
            "select": "store_id,place_id,industry,category,crawl_data",
            "store_id": f"eq.{store_id}",
        },
        headers=headers,
        timeout=10,
    )
    resp.raise_for_status()
    row = resp.json()[0]
    cd = row.get("crawl_data") or {}
    print(f"  place_id : {row['place_id']}")
    print(f"  industry : {repr(row['industry'])}")
    print(f"  category : {repr(row['category'])}")
    print(f"  crawl_data.category : {repr(cd.get('category', ''))}")

    # 중복 레코드 수 확인
    dup_resp = requests.get(
        f"{BASE}/rest/v1/stores",
        params={
            "select": "store_id,place_id",
            "store_name": f"eq.{STORE_NAME}",
        },
        headers=headers,
        timeout=10,
    )
    dup_resp.raise_for_status()
    dup_rows = dup_resp.json()
    print(f"  DB 내 강남칼동태 레코드 수: {len(dup_rows)}")
    for r in dup_rows:
        print(f"    store_id={r['store_id']} place_id={r['place_id']}")

    print("\n=== 검증 결과 ===")
    ok = True
    # Bug1: industry 저장
    if row["industry"] and "한식" in row["industry"]:
        print("[PASS] Bug1 — industry 저장됨:", repr(row["industry"]))
    else:
        print("[FAIL] Bug1 — industry:", repr(row["industry"]))
        ok = False
    # Bug2: 중복 없음
    if len(dup_rows) == 1:
        print("[PASS] Bug2 — 중복 없음 (1건)")
    else:
        print(f"[FAIL] Bug2 — 중복 있음 ({len(dup_rows)}건)")
        ok = False
    # Bug3: category 정상
    cd_cat = cd.get("category", "")
    if cd_cat and "페이지 닫기" not in cd_cat and "더보기" not in cd_cat and len(cd_cat) <= 30:
        print("[PASS] Bug3 — category 정상:", repr(cd_cat))
    else:
        print("[FAIL] Bug3 — category:", repr(cd_cat))
        ok = False

    sys.exit(0 if ok else 1)


asyncio.run(main())
