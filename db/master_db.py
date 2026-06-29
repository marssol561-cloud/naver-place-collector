import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import time

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
    """store_name+address로 stores 조회. 공백 정규화 매칭 포함. 없으면 None."""
    norm_name = store_name.replace(" ", "")
    norm_addr = address.replace(" ", "")

    def _query_and_validate(qname: str, qaddr: str) -> dict | None:
        resp = requests.get(
            f"{MASTER_DB_URL}/rest/v1/stores",
            params={
                "select": "store_id,place_id,store_name,address",
                "store_name": f"eq.{qname}",
                "address": f"eq.{qaddr}",
            },
            headers=_auth_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return None
        row = rows[0]
        # 공백 정규화 후 양쪽 모두 일치해야 반환 — 오매칭 방지
        if (row["store_name"].replace(" ", "") == norm_name and
                row["address"].replace(" ", "") == norm_addr):
            return row
        return None

    for qname, qaddr in [
        (store_name, address),   # Pass 1: exact (기존 동작 보존)
        (norm_name, address),    # Pass 2: 이름 공백 제거
        (store_name, norm_addr), # Pass 3: 주소 공백 제거
        (norm_name, norm_addr),  # Pass 4: 양쪽 공백 제거
    ]:
        result = _query_and_validate(qname, qaddr)
        if result:
            return result

    return None


_STORE_ALLOWED_COLUMNS = frozenset({
    "store_id", "place_id", "store_name", "address", "region", "is_registered",
    "industry", "category", "rating", "total_reviews", "save_count", "reply_rate",
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


# ── Industry normalization (SP-3) ────────────────────────────────────────────
INDUSTRY_MAP_TTL_SECONDS = 86400  # 24 h

_naver_map: dict = {}
_naver_map_loaded_at: float = 0.0


def _load_naver_map() -> dict:
    """Fetch industry_naver_map from DB (TTL-cached 24 h). On failure: log warning, return empty dict."""
    global _naver_map, _naver_map_loaded_at
    now = time.monotonic()
    if _naver_map_loaded_at > 0 and (now - _naver_map_loaded_at) < INDUSTRY_MAP_TTL_SECONDS:
        return _naver_map
    try:
        resp = requests.get(
            f"{MASTER_DB_URL}/rest/v1/industry_naver_map",
            params={"select": "naver_category,industry"},
            headers=_auth_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        rows = resp.json()
        _naver_map = {row["naver_category"]: row["industry"] for row in rows}
        _naver_map_loaded_at = now
        log.info("[industry_naver_map] loaded %d entries", len(_naver_map))
    except Exception as exc:
        log.warning("[industry_naver_map] fetch failed — raw values pass through: %s", exc)
        _naver_map = {}
        # _naver_map_loaded_at intentionally NOT updated so the next call retries
    return _naver_map


def normalize_industry(raw: str | None) -> tuple[str | None, bool]:
    """Map raw Naver category to approved taxonomy via industry_naver_map.

    Returns (mapped, True) on hit, (raw, False) on miss or empty map.
    Empty/None raw → (raw, False) without lookup.
    """
    if not raw:
        return (raw, False)
    mapping = _load_naver_map()
    if not mapping:
        return (raw, False)
    matched = mapping.get(raw)
    if matched is not None:
        return (matched, True)
    return (raw, False)


def _log_unclassified(raw: str, store_id: str | None) -> None:
    """POST unmatched industry value to industry_unclassified_log. Never raises."""
    try:
        resp = requests.post(
            f"{MASTER_DB_URL}/rest/v1/industry_unclassified_log",
            json={"source": "crawler", "input_value": raw, "store_id": store_id},
            headers=_auth_headers(),
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as exc:
        log.warning("[industry_unclassified_log] INSERT failed: %s", exc)


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
            # 기존 레코드 → crawl_data + industry UPDATE
            store_id = rows[0]["store_id"]
            # Secondary guard (S2-FIX): exclude None/empty-string fields from PATCH so a
            # failed-crawl null can never overwrite an existing good value via the merge trigger.
            _safe_cd = {k: v for k, v in crawl_data.items() if v is not None and v != ""}
            patch_body: dict = {"crawl_data": _safe_cd}
            _naver_name = _safe_cd.get("name")
            if _naver_name:
                patch_body["store_name"] = _naver_name
            # Q1 FIX (2026-06-29): persist the user-typed 지번 into stores.address on re-collect,
            # but ONLY when non-empty — an empty address must never overwrite an existing good value.
            # server.py no longer backfills 도로명 into `address`, so this can only carry a real 지번.
            if address:
                patch_body["address"] = address
            if region:
                patch_body["region"] = region
            _raw_cat = _safe_cd.get("category")
            if _raw_cat:
                _norm_cat, _cat_hit = normalize_industry(_raw_cat)
                patch_body["industry"] = _norm_cat
                if not _cat_hit:
                    _log_unclassified(_raw_cat, store_id)
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
            # place_id 미등록 레코드가 이미 존재하면 INSERT 대신 UPDATE (중복 방지)
            null_resp = requests.get(
                base,
                params={
                    "select": "store_id",
                    "store_name": f"eq.{store_name}",
                    "place_id": "is.null",
                },
                headers=headers,
                timeout=10,
            )
            null_resp.raise_for_status()
            null_rows = null_resp.json()
            if null_rows:
                existing_id = null_rows[0]["store_id"]
                # Secondary guard: exclude None/empty-string fields from PATCH (S2-FIX)
                _safe_cd2 = {k: v for k, v in crawl_data.items() if v is not None and v != ""}
                _naver_name2 = _safe_cd2.get("name")
                upd: dict = {
                    "place_id": place_id,
                    "crawl_data": _safe_cd2,
                    "is_registered": True,
                }
                if _naver_name2:
                    upd["store_name"] = _naver_name2
                if region:
                    upd["region"] = region
                _raw_cat = _safe_cd2.get("category")
                if _raw_cat:
                    _norm_cat, _cat_hit = normalize_industry(_raw_cat)
                    upd["industry"] = _norm_cat
                    if not _cat_hit:
                        _log_unclassified(_raw_cat, existing_id)
                patch = requests.patch(
                    f"{base}?store_id=eq.{existing_id}",
                    json=upd,
                    headers=headers,
                    timeout=10,
                )
                patch.raise_for_status()
                log.info("UPDATE(null→place_id) stores store_id=%s place_id=%s", existing_id, place_id)
                return existing_id, False

            # 진짜 신규 → INSERT
            _naver_name_ins = crawl_data.get("name")
            body: dict = {
                "place_id": place_id,
                "store_name": _naver_name_ins if _naver_name_ins else store_name,
                "address": address,
                "is_registered": True,
                "crawl_data": crawl_data,
            }
            if region:
                body["region"] = region
            _raw_cat = crawl_data.get("category")
            if _raw_cat:
                _norm_cat, _cat_hit = normalize_industry(_raw_cat)
                body["industry"] = _norm_cat
                if not _cat_hit:
                    _log_unclassified(_raw_cat, None)  # store_id not yet assigned
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


def upsert_business_images(store_id: str, place_id: str, image_urls: list, business_photo_count) -> None:
    """Satellite upsert: store_business_images. Overwrites on conflict (PK=store_id)."""
    from datetime import datetime, timezone
    headers = {
        "apikey": MASTER_DB_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {MASTER_DB_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    body = {
        "store_id": store_id,
        "place_id": place_id,
        "image_urls": image_urls,
        "business_photo_count": business_photo_count,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    resp = requests.post(
        f"{MASTER_DB_URL}/rest/v1/store_business_images",
        json=body, headers=headers, timeout=10,
    )
    resp.raise_for_status()
    log.info("upsert_business_images store_id=%s urls=%d count=%s", store_id, len(image_urls), business_photo_count)


def get_business_images(store_id: str) -> dict | None:
    """store_id로 store_business_images 조회. 없으면 None."""
    resp = requests.get(
        f"{MASTER_DB_URL}/rest/v1/store_business_images",
        params={
            "select": "image_urls,business_photo_count,captured_at",
            "store_id": f"eq.{store_id}",
        },
        headers=_auth_headers(),
        timeout=10,
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


async def save_to_master_db(store_name: str, address: str) -> dict:
    """searcher + place_crawler + key 변환 + upsert 전체 흐름"""
    place_id = await searcher.search_place_id(store_name, address)

    if place_id is None:
        log.info("place_id 없음 → 미등록 점포 INSERT: %s", store_name)
        store_id, is_new = upsert_store(None, store_name, address, {})
        return {"store_id": store_id, "place_id": None, "is_new": is_new}

    raw = await place_crawler.crawl_place_by_id(place_id)
    if raw == place_crawler.CRAWL_INCOMPLETE:
        log.warning("불완전 렌더 place_id=%s — upsert 생략, DB 기존값 보존", place_id)
        existing = find_store_by_place_id(place_id)
        return {"store_id": existing["store_id"] if existing else None, "place_id": place_id, "is_new": False}
    if raw is None:
        log.warning("place_crawler 실패 place_id=%s → 빈 crawl_data로 저장", place_id)
        raw = {}

    mapped = apply_field_mapping(raw)
    mapped = sanitize_crawl_data(mapped)
    _biz_urls  = mapped.pop("business_image_urls", []) or []
    _biz_count = mapped.pop("business_photo_count", None)
    store_id, is_new = upsert_store(place_id, store_name, address, mapped)
    if _biz_urls or _biz_count is not None:
        upsert_business_images(store_id, place_id, _biz_urls, _biz_count)
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
