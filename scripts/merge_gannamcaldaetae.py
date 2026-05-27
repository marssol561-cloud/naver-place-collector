"""
강남칼동태 중복 레코드 머지 스크립트

- 보존: 39cb7e6b-caff-4f53-9eb0-ddbd17b2a8a0 (menu_recommendations FK 2건 있음)
- 삭제: 98255792-5c31-4643-b9cb-30b890200759 (place_id + crawl_data 있음, FK 없음)

작업:
1. 98255792 레코드에서 place_id, crawl_data, category 읽기
2. 39cb7e6b 레코드에 place_id, crawl_data, industry, category, crawled_at PATCH
3. 98255792 레코드 DELETE
"""
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

MASTER_DB_URL = os.getenv("MASTER_DB_URL", "").rstrip("/")
MASTER_DB_KEY = os.getenv("MASTER_DB_SERVICE_ROLE_KEY", "")

if not MASTER_DB_URL or not MASTER_DB_KEY:
    print("[ERROR] MASTER_DB_URL 또는 MASTER_DB_SERVICE_ROLE_KEY 환경변수 누락")
    sys.exit(1)

BASE = f"{MASTER_DB_URL}/rest/v1/stores"
HEADERS = {
    "apikey": MASTER_DB_KEY,
    "Authorization": f"Bearer {MASTER_DB_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

KEEP_ID = "39cb7e6b-caff-4f53-9eb0-ddbd17b2a8a0"
DEL_ID  = "98255792-5c31-4643-b9cb-30b890200759"


def get_store(store_id: str) -> dict:
    resp = requests.get(
        BASE,
        params={
            "select": "store_id,place_id,store_name,industry,category,crawl_data,crawled_at",
            "store_id": f"eq.{store_id}",
        },
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        print(f"[ERROR] store_id={store_id} 조회 결과 없음")
        sys.exit(1)
    return rows[0]


def main():
    # 1. 두 레코드 현재 상태 확인
    src = get_store(DEL_ID)
    dst = get_store(KEEP_ID)

    print("=== 머지 전 상태 ===")
    print(f"[DEL] {DEL_ID}")
    print(f"  place_id   : {src['place_id']}")
    print(f"  industry   : {src['industry']}")
    print(f"  category   : {src['category']}")
    print(f"  crawled_at : {src['crawled_at']}")
    cd_keys = list((src.get("crawl_data") or {}).keys())
    print(f"  crawl_data keys ({len(cd_keys)}): {cd_keys}")

    print(f"\n[KEEP] {KEEP_ID}")
    print(f"  place_id   : {dst['place_id']}")
    print(f"  industry   : {dst['industry']}")
    print(f"  category   : {dst['category']}")
    print(f"  crawled_at : {dst['crawled_at']}")

    confirm = input("\n계속 진행? (y/N): ").strip().lower()
    if confirm != "y":
        print("중단.")
        sys.exit(0)

    # 2. 먼저 98255792 DELETE (place_id unique constraint 해제 필요)
    src_crawl = src.get("crawl_data") or {}
    src_category = src.get("category") or ""
    src_industry = src_category or (src_crawl.get("category") or "")
    src_place_id = src["place_id"]
    src_crawled_at = src["crawled_at"]

    print(f"\n[DELETE] {DEL_ID} 삭제 중 (place_id unique constraint 해제)...")
    del_resp = requests.delete(
        f"{BASE}?store_id=eq.{DEL_ID}",
        headers=HEADERS,
        timeout=10,
    )
    del_resp.raise_for_status()
    print(f"  DELETE HTTP {del_resp.status_code}")

    # 3. 39cb7e6b에 place_id + crawl_data + category + industry + crawled_at PATCH
    patch_body = {
        "place_id": src_place_id,
        "crawl_data": src_crawl,
        "crawled_at": src_crawled_at,
        "is_registered": True,
    }
    if src_category:
        patch_body["category"] = src_category
    if src_industry:
        patch_body["industry"] = src_industry

    print(f"\n[PATCH] {KEEP_ID} 업데이트 중...")
    patch_resp = requests.patch(
        f"{BASE}?store_id=eq.{KEEP_ID}",
        json=patch_body,
        headers=HEADERS,
        timeout=10,
    )
    patch_resp.raise_for_status()
    patched = patch_resp.json()
    print(f"  PATCH 결과: {json.dumps(patched[0] if patched else {}, ensure_ascii=False)[:200]}")

    # 4. 최종 상태 확인
    final = get_store(KEEP_ID)
    print("\n=== 머지 후 KEEP 레코드 ===")
    print(f"  store_id   : {final['store_id']}")
    print(f"  place_id   : {final['place_id']}")
    print(f"  industry   : {final['industry']}")
    print(f"  category   : {final['category']}")
    print(f"  crawled_at : {final['crawled_at']}")
    print("\n완료.")


if __name__ == "__main__":
    main()
