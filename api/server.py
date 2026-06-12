import time
from fastapi import FastAPI, Depends, Request, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import COLLECTOR_API_KEY
from db import master_db
from collector import searcher, place_crawler
from collector import visitor_batch

app = FastAPI(title="Naver Place Collector API")


class AuthFailedException(Exception):
    pass


@app.exception_handler(AuthFailedException)
async def auth_failed_handler(request: Request, exc: AuthFailedException):
    return JSONResponse(
        status_code=401,
        content={"status": "error", "error_code": "AUTH_FAILED", "message": "인증 실패"},
    )


async def verify_api_key(request: Request):
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        raise AuthFailedException()
    token = auth[7:]
    if token != COLLECTOR_API_KEY:
        raise AuthFailedException()


class CollectRequest(BaseModel):
    store_name: Optional[str] = None
    address: Optional[str] = None
    place_id: Optional[str] = None
    force_refresh: bool = False


def _elapsed(start: float) -> float:
    return round(time.monotonic() - start, 1)


def _error_resp(error_code: str, message: str, start: float, http_status: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=http_status,
        content={
            "status": "error",
            "error_code": error_code,
            "message": message,
            "elapsed_seconds": _elapsed(start),
        },
    )


def _already_exists_resp(store_id: str, place_id: Optional[str], start: float) -> JSONResponse:
    return JSONResponse(content={
        "status": "already_exists",
        "store_id": store_id,
        "place_id": place_id,
        "elapsed_seconds": _elapsed(start),
    })


async def _do_crawl_and_save(
    store_name: str,
    address: str,
    place_id: str,
    start: float,
    is_refresh: bool = False,
) -> JSONResponse:
    try:
        raw = await place_crawler.crawl_place_by_id(place_id)
    except Exception as e:
        return _error_resp("CRAWL_FAILED", f"크롤링 오류: {e}", start)

    if raw == place_crawler.CRAWL_INCOMPLETE:
        return _error_resp("CRAWL_INCOMPLETE", "불완전 렌더 — DB 기존 데이터 보존", start)
    if raw is None:
        return _error_resp("CRAWL_FAILED", "크롤링 실패 (데이터 없음)", start)

    try:
        mapped = master_db.apply_field_mapping(raw)
        mapped = master_db.sanitize_crawl_data(mapped)
        _biz_urls  = mapped.pop("business_image_urls", []) or []
        _biz_count = mapped.pop("business_photo_count", None)
        # Backfill empty store_name/address from crawled data (place_id-only path)
        if not store_name:
            store_name = mapped.get("name") or ""
        if not address:
            address = (mapped.get("lot_address") or mapped.get("lot_address_fallback") or "")
        region = master_db.extract_region(address)
        store_id, _ = master_db.upsert_store(place_id, store_name, address, mapped, region)
        if _biz_urls or _biz_count is not None:
            master_db.upsert_business_images(store_id, place_id, _biz_urls, _biz_count)
    except Exception as e:
        return _error_resp("DB_ERROR", f"DB 저장 오류: {e}", start)

    fields_collected = sum(1 for v in raw.values() if v)
    return JSONResponse(content={
        "status": "refreshed" if is_refresh else "collected",
        "store_id": store_id,
        "place_id": place_id,
        "fields_collected": fields_collected,
        "elapsed_seconds": _elapsed(start),
    })


@app.get("/health")
async def health():
    return {"status": "ok", "service": "naver-place-collector", "version": "1.4.1"}


@app.get("/api/v1/stores/{store_id}", dependencies=[Depends(verify_api_key)])
async def get_store(
    store_id: str,
    fields: Optional[str] = Query(default=None, description="쉼표 구분 칼럼명"),
):
    columns = [f.strip() for f in fields.split(",")] if fields else None

    try:
        store = master_db.find_store_by_id(store_id, columns)
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error_code": "DB_ERROR", "message": f"DB 조회 오류: {e}"},
        )

    if store is None:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "error_code": "STORE_NOT_FOUND", "message": f"store_id={store_id} 없음"},
        )

    try:
        business_images = master_db.get_business_images(store_id)
    except Exception:
        business_images = None

    return JSONResponse(content={"status": "ok", "store": store, "business_images": business_images})


@app.get("/api/v1/stores/{store_id}/visitor-reviews", dependencies=[Depends(verify_api_key)])
async def get_visitor_reviews(store_id: str):
    store = master_db.find_store_by_id(store_id)
    if store is None:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "error_code": "STORE_NOT_FOUND", "message": f"store_id={store_id} 없음"},
        )
    place_id = store.get("place_id")
    if not place_id:
        return JSONResponse(
            content={"status": "error", "error_code": "PLACE_ID_MISSING", "message": "store has no place_id"},
        )
    try:
        agg = visitor_batch.run_batch(place_id)
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "error_code": "AGGREGATE_FAILED", "message": str(e)},
        )
    return JSONResponse(content={"status": "ok", "store_id": store_id, "place_id": place_id, "visitor_reviews": agg})


@app.post("/api/v1/collect", dependencies=[Depends(verify_api_key)])
async def collect(req: CollectRequest):
    start = time.monotonic()

    # 입력 검증
    if not req.place_id and (not req.store_name or not req.address):
        return _error_resp("INVALID_REQUEST", "place_id가 없으면 store_name + address 필수", start, http_status=400)

    store_name = req.store_name or ""
    address = req.address or ""

    try:
        if req.place_id:
            # UC-2: place_id 직접 입력
            existing = master_db.find_store_by_place_id(req.place_id)
            if existing:
                if not req.force_refresh:
                    return _already_exists_resp(existing["store_id"], req.place_id, start)
                # UC-3: 재수집
                sn = store_name or existing.get("store_name", "")
                addr = address or existing.get("address", "")
                return await _do_crawl_and_save(sn, addr, req.place_id, start, is_refresh=True)
            # place_id 있지만 DB에 없음 → 수집
            return await _do_crawl_and_save(store_name, address, req.place_id, start)

        else:
            # UC-1: place_id 없음
            # 1차 중복 검색: store_name + address
            existing = master_db.find_store_by_name_address(store_name, address)
            if existing:
                if not req.force_refresh:
                    return _already_exists_resp(existing["store_id"], existing.get("place_id"), start)
                # UC-3: 재수집 — 기존 place_id 사용
                pid = existing.get("place_id")
                if pid:
                    return await _do_crawl_and_save(store_name, address, pid, start, is_refresh=True)
                # 기존이 미등록 점포 → searcher 재시도 (하단 로직으로 fall-through)

            # searcher 실행
            try:
                place_id = await searcher.search_place_id(store_name, address)
            except Exception as e:
                return _error_resp("PLACE_NOT_FOUND", f"검색 오류: {e}", start)

            if place_id:
                # 2차 중복 검색: place_id
                existing2 = master_db.find_store_by_place_id(place_id)
                if existing2 and not req.force_refresh:
                    return _already_exists_resp(existing2["store_id"], place_id, start)
                return await _do_crawl_and_save(store_name, address, place_id, start)
            else:
                # 검색 미등록 → stub 저장 않음, PLACE_NOT_FOUND 반환
                return JSONResponse(content={
                    "status": "place_not_found",
                    "saved": False,
                    "message": "네이버 플레이스에서 점포를 찾지 못했습니다. 상호·주소를 확인하거나 place_id로 직접 입력하세요.",
                    "elapsed_seconds": _elapsed(start),
                })

    except Exception as e:
        return _error_resp("CRAWL_FAILED", f"처리 중 오류: {e}", start)
