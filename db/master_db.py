import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

_SIDO_MAP = {
    "서울": "서울", "서울특별시": "서울",
    "경기": "경기", "경기도": "경기",
    "인천": "인천", "인천광역시": "인천",
    "부산": "부산", "부산광역시": "부산",
    "대구": "대구", "대구광역시": "대구",
    "광주": "광주", "광주광역시": "광주",
    "대전": "대전", "대전광역시": "대전",
    "울산": "울산", "울산광역시": "울산",
    "세종": "세종", "세종특별자치시": "세종",
    "강원": "강원", "강원도": "강원", "강원특별자치도": "강원",
    "충북": "충북", "충청북도": "충북",
    "충남": "충남", "충청남도": "충남",
    "전북": "전북", "전라북도": "전북", "전북특별자치도": "전북",
    "전남": "전남", "전라남도": "전남",
    "경북": "경북", "경상북도": "경북",
    "경남": "경남", "경상남도": "경남",
    "제주": "제주", "제주특별자치도": "제주",
}
_METRO_SIDOS = frozenset({"서울", "인천", "부산", "대구", "광주", "대전", "울산"})
_DO_SIDOS = frozenset({"경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남"})

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collector import searcher, place_crawler

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

MASTER_DB_URL = os.getenv("MASTER_DB_URL")
MASTER_DB_SERVICE_ROLE_KEY = os.getenv("MASTER_DB_SERVICE_ROLE_KEY")

if not MASTER_DB_URL or not MASTER_DB_SERVICE_ROLE_KEY:
    log.error("[master_db] MASTER_DB_URL 또는 MASTER_DB_SERVICE_ROLE_KEY 환경변수 누락")
    raise EnvironmentError("MASTER_DB_URL, MASTER_DB_SERVICE_ROLE_KEY 필수")

_MAPPING_PATH = Path(__file__).parent.parent / "config" / "field_mapping.json"
with open(_MAPPING_PATH, encoding="utf-8") as _f:
    _FIELD_MAPPING: dict = json.load(_f).get("mapping", {})


def extract_region(address: str) -> str | None:
    """address → 시도+구군 추출. 실패 시 None."""
    if not address:
        return None
    tokens = address.split()
    if not tokens:
        return None
    sido = _SIDO_MAP.get(tokens[0])
    if sido is None:
        return None
    if sido == "세종":
        return "세종"
    if sido == "제주":
        return f"제주 {tokens[1]}" if len(tokens) > 1 and tokens[1].endswith(("시", "군")) else "제주"
    if sido in _METRO_SIDOS:
        return f"{sido} {tokens[1]}" if len(tokens) > 1 and tokens[1].endswith(("구", "군")) else None
    if sido in _DO_SIDOS:
        if len(tokens) > 1 and tokens[1].endswith(("시", "군")):
            if len(tokens) > 2 and tokens[2].endswith("구"):
                return f"{sido} {tokens[1]} {tokens[2]}"
            return f"{sido} {tokens[1]}"
    return None


def find_store_by_place_id(place_id: str) -> dict | None:
    """place_id로 stores 조회. 없으면 None."""
    resp = requests.get(
        f"{MASTER_DB_URL}/rest/v1/stores",
        params={
            "select": "store_id,place_id,store_name,address",
            "place_id": f"eq.{place_id}",
        },
        headers=_auth_headers(),
        timeout=10,
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


def find_store_by_name_address(store_name: str, address: str) -> dict | None:
    """store_name+address로 stores 조회. 없으면 None."""
    resp = requests.get(
        f"{MASTER_DB_URL}/rest/v1/stores",
        params={
            "select": "store_id,place_id,store_name,address",
            "store_name": f"eq.{store_name}",
            "address": f"eq.{address}",
        },
        headers=_auth_headers(),
        timeout=10,
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


_STORE_ALLOWED_COLUMNS = frozenset({
    "store_id", "place_id", "store_name", "address", "region", "is_registered",
    "category", "rating", "total_reviews", "save_count", "reply_rate",
    "receipt_review_ratio", "crawl_data", "crawled_at", "created_at", "updated_at",
})

_DEFAULT_GET_COLUMNS = [
    "store_id", "place_id", "store_name", "address",
    "crawl_data", "crawled_at",
]


def find_store_by_id(store_id: str, columns: list[str] | None = None) -> dict | None:
    """store_id(UUID)로 stores 조회. 없으면 None. columns 미지정 시 기본 칼럼 반환."""
    if columns:
        safe_cols = [c for c in columns if c in _STORE_ALLOWED_COLUMNS]
        select_expr = ",".join(safe_cols) if safe_cols else ",".join(_DEFAULT_GET_COLUMNS)
    else:
        select_expr = ",".join(_DEFAULT_GET_COLUMNS)

    resp = requests.get(
        f"{MASTER_DB_URL}/rest/v1/stores",
        params={
            "select": select_expr,
            "store_id": f"eq.{store_id}",
        },
        headers=_auth_headers(),
        timeout=10,
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


def apply_field_mapping(raw: dict) -> dict:
    """place_crawler 원본 key → field_dictionary 표준 key 변환. 미매핑 key는 원본 유지."""
    return {_FIELD_MAPPING.get(k, k): v for k, v in raw.items()}


def sanitize_crawl_data(data: dict) -> dict:
    """빈 문자열 → None, 쉼표 포함 숫자 문자열 → int/float 변환. 변환 불가 값은 원본 유지."""
    result = {}
    for key, value in data.items():
        if isinstance(value, str):
            if value == "":
                result[key] = None
            elif "," in value:
                cleaned = value.replace(",", "")
                try:
                    result[key] = int(cleaned)
                except ValueError:
                    try:
                        result[key] = float(cleaned)
                    except ValueError:
                        result[key] = value
            else:
                result[key] = value
        else:
            result[key] = value
    return result


def _auth_headers() -> dict:
    return {
        "apikey": MASTER_DB_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {MASTER_DB_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def upsert_store(
    place_id: str | None,
    store_name: str,
    address: str,
    crawl_data: dict,
    region: str | None = None,
) -> tuple[str, bool]:
    """stores 테이블 upsert → (store_id, is_new) 반환"""
    base = f"{MASTER_DB_URL}/rest/v1/stores"
    headers = _auth_headers()

    if place_id:
        resp = requests.get(
            f"{base}?select=store_id&place_id=eq.{place_id}",
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        rows = resp.json()

        if rows:
            # 기존 레코드 → crawl_data만 UPDATE (트리거가 병합 + 핵심 6개 추출)
            store_id = rows[0]["store_id"]
            patch_body: dict = {"crawl_data": crawl_data}
            if region:
                patch_body["region"] = region
            patch = requests.patch(
                f"{base}?place_id=eq.{place_id}",
                json=patch_body,
                headers=headers,
                timeout=10,
            )
            patch.raise_for_status()
            patched = patch.json()
            if patched:
                store_id = patched[0]["store_id"]
            log.info("UPDATE stores store_id=%s place_id=%s", store_id, place_id)
            return store_id, False
        else:
            # 신규 → INSERT
            body: dict = {
                "place_id": place_id,
                "store_name": store_name,
                "address": address,
                "is_registered": True,
                "crawl_data": crawl_data,
            }
            if region:
                body["region"] = region
            ins = requests.post(base, json=body, headers=headers, timeout=10)
            ins.raise_for_status()
            store_id = ins.json()[0]["store_id"]
            log.info("INSERT stores store_id=%s place_id=%s", store_id, place_id)
            return store_id, True
    else:
        # place_id 없음 → 미등록 점포 INSERT
        body = {
            "store_name": store_name,
            "address": address,
            "is_registered": False,
            "crawl_data": crawl_data,
        }
        if region:
            body["region"] = region
        ins = requests.post(base, json=body, headers=headers, timeout=10)
        ins.raise_for_status()
        store_id = ins.json()[0]["store_id"]
        log.info("INSERT unregistered store store_id=%s", store_id)
        return store_id, True


async def save_to_master_db(store_name: str, address: str) -> dict:
    """searcher + place_crawler + key 변환 + upsert 전체 흐름"""
    place_id = await searcher.search_place_id(store_name, address)

    if place_id is None:
        log.info("place_id 없음 → 미등록 점포 INSERT: %s", store_name)
        store_id, is_new = upsert_store(None, store_name, address, {})
        return {"store_id": store_id, "place_id": None, "is_new": is_new}

    raw = await place_crawler.crawl_place_by_id(place_id)
    if raw is None:
        log.warning("place_crawler 실패 place_id=%s → 빈 crawl_data로 저장", place_id)
        raw = {}

    mapped = apply_field_mapping(raw)
    mapped = sanitize_crawl_data(mapped)
    store_id, is_new = upsert_store(place_id, store_name, address, mapped)
    return {"store_id": store_id, "place_id": place_id, "is_new": is_new}


if __name__ == "__main__":
    async def _main():
        headers = _auth_headers()
        base = f"{MASTER_DB_URL}/rest/v1/stores"

        # ── 테스트 1: 실제 점포 ──────────────────────────────────────────────
        print("=== 테스트 1: 실제 점포 (스타벅스 강남구 역삼동) ===")
        r1 = await save_to_master_db("스타벅스", "서울 강남구 역삼동")
        print(f"store_id : {r1['store_id']}")
        print(f"place_id : {r1['place_id']}")
        print(f"is_new   : {r1['is_new']}")

        # 마스터DB 조회 — 핵심 칼럼 자동 추출 확인
        vr = requests.get(
            f"{base}"
            f"?select=store_id,place_id,store_name,category,rating"
            f",total_reviews,save_count,reply_rate,receipt_review_ratio,crawl_data,crawled_at"
            f"&store_id=eq.{r1['store_id']}",
            headers=headers,
            timeout=10,
        )
        vr.raise_for_status()
        rows = vr.json()
        if rows:
            row = rows[0]
            print("\n[마스터DB 조회 결과]")
            print(f"  store_name           : {row.get('store_name')}")
            print(f"  category             : {row.get('category')}")
            print(f"  rating               : {row.get('rating')}")
            print(f"  total_reviews        : {row.get('total_reviews')}")
            print(f"  save_count           : {row.get('save_count')}")
            print(f"  reply_rate           : {row.get('reply_rate')}")
            print(f"  receipt_review_ratio : {row.get('receipt_review_ratio')}")
            print(f"  crawled_at           : {row.get('crawled_at')}")
            cd = row.get("crawl_data") or {}
            print(f"  crawl_data keys ({len(cd)}): {list(cd.keys())}")
        else:
            print("[오류] 마스터DB 조회 결과 없음")

        # field_mapping 변환 예시 출력
        print("\n[field_mapping 변환 예시]")
        sample_keys = ["rating", "total_reviews", "save_count", "place_name", "reservation_active"]
        for k in sample_keys:
            mapped_k = _FIELD_MAPPING.get(k, k)
            if k != mapped_k:
                print(f"  {k!r} → {mapped_k!r}")

        # ── 테스트 2: 존재하지 않는 점포 ─────────────────────────────────────
        print("\n=== 테스트 2: 존재하지 않는 점포 ===")
        r2 = await save_to_master_db("없는점포12345", "서울 강남구")
        print(f"store_id    : {r2['store_id']}")
        print(f"place_id    : {r2['place_id']}")
        print(f"is_new      : {r2['is_new']}")

        vr2 = requests.get(
            f"{base}"
            f"?select=store_id,place_id,is_registered"
            f"&store_id=eq.{r2['store_id']}",
            headers=headers,
            timeout=10,
        )
        vr2.raise_for_status()
        rows2 = vr2.json()
        if rows2:
            r2row = rows2[0]
            print(f"  place_id      : {r2row.get('place_id')}")
            print(f"  is_registered : {r2row.get('is_registered')}")

    asyncio.run(_main())
