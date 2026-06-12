# -*- coding: utf-8 -*-
"""
recollect_stubs.py — 261 place_id=NULL stub 재수집 배치
  - Supabase REST로 NULL stub 목록 읽기 (read-only)
  - 라이브 Collector API 순차 호출 (force_refresh=true)
  - CSV 결과 누적 (재실행 시 기처리 행 자동 스킵 = 재개 가능)
  - LIMIT 플래그로 처음 N건만 처리 후 STOP

Usage:
  python scripts/recollect_stubs.py [--limit N]
  COLLECTOR_API_KEY=<prod_key> python scripts/recollect_stubs.py --limit 10

Env vars (reads .env automatically):
  MASTER_DB_URL           Supabase project URL
  MASTER_DB_SERVICE_ROLE_KEY
  COLLECTOR_API_KEY       Live collector Bearer token
  COLLECTOR_URL           (optional) collector base URL, defaults to Railway prod
"""
import argparse
import csv
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# ── config ────────────────────────────────────────────────────────────────────
DB_URL   = os.getenv("MASTER_DB_URL", "").rstrip("/")
DB_KEY   = os.getenv("MASTER_DB_SERVICE_ROLE_KEY", "")
COL_KEY  = os.getenv("COLLECTOR_API_KEY", "")
COL_URL  = os.getenv(
    "COLLECTOR_URL",
    "https://naver-place-collector-production.up.railway.app",
).rstrip("/")

RATE_SLEEP  = 5          # seconds between calls (Naver anti-block)
PER_TIMEOUT = 90         # seconds per collect request (each ~30-45s)

RESULT_CSV = Path(__file__).parent / f"recollect_result_{datetime.now().strftime('%Y%m%d')}.csv"
CSV_FIELDS = ["store_id", "store_name", "address", "status", "place_id_returned", "elapsed_s", "note"]


def _db_headers():
    return {
        "apikey": DB_KEY,
        "Authorization": f"Bearer {DB_KEY}",
    }


def _col_headers():
    return {
        "Authorization": f"Bearer {COL_KEY}",
        "Content-Type": "application/json",
    }


def fetch_null_stubs() -> list[dict]:
    """Supabase REST: place_id IS NULL인 stores 전체 읽기 (최대 1000)"""
    resp = requests.get(
        f"{DB_URL}/rest/v1/stores",
        params={
            "place_id": "is.null",
            "select": "store_id,store_name,address",
            "order": "created_at.asc",
            "limit": "1000",
        },
        headers=_db_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def load_done_ids() -> set[str]:
    """이미 처리된 store_id 집합 (CSV 재개용)"""
    if not RESULT_CSV.exists():
        return set()
    done = set()
    with open(RESULT_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            done.add(row["store_id"])
    return done


def append_result(row: dict):
    is_new = not RESULT_CSV.exists()
    with open(RESULT_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if is_new:
            w.writeheader()
        w.writerow(row)


def collect_one(store: dict) -> dict:
    """단일 점포 재수집 → 결과 dict 반환"""
    t0 = time.monotonic()
    body = {
        "store_name": store["store_name"],
        "address":    store["address"],
        "force_refresh": True,
    }
    try:
        resp = requests.post(
            f"{COL_URL}/api/v1/collect",
            json=body,
            headers=_col_headers(),
            timeout=PER_TIMEOUT,
        )
        elapsed = round(time.monotonic() - t0, 1)
        data = resp.json()
        status   = data.get("status", "unknown")
        place_id = data.get("place_id") or ""
        note     = data.get("message", "") or data.get("error_code", "")
    except requests.exceptions.Timeout:
        elapsed  = round(time.monotonic() - t0, 1)
        status   = "error"
        place_id = ""
        note     = "timeout"
    except Exception as exc:
        elapsed  = round(time.monotonic() - t0, 1)
        status   = "error"
        place_id = ""
        note     = str(exc)[:120]

    return {
        "store_id":          store["store_id"],
        "store_name":        store["store_name"],
        "address":           store["address"],
        "status":            status,
        "place_id_returned": place_id,
        "elapsed_s":         elapsed,
        "note":              note,
    }


def verify_recovered(store_id: str) -> dict | None:
    """복구 확인: DB에서 place_id 실제 값 조회"""
    resp = requests.get(
        f"{DB_URL}/rest/v1/stores",
        params={
            "store_id": f"eq.{store_id}",
            "select": "store_id,store_name,place_id,is_registered",
        },
        headers=_db_headers(),
        timeout=10,
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


def main():
    parser = argparse.ArgumentParser(description="Recollect NULL-stub stores")
    parser.add_argument("--limit", type=int, default=10,
                        help="처리할 최대 건수 (기본 10 = CEO 체크포인트)")
    args = parser.parse_args()

    # ── 사전 점검 ──────────────────────────────────────────────────────────────
    if not DB_URL or not DB_KEY:
        print("[ERROR] MASTER_DB_URL / MASTER_DB_SERVICE_ROLE_KEY 없음 — .env 확인")
        sys.exit(1)
    if not COL_KEY:
        print("[ERROR] COLLECTOR_API_KEY 없음 — env 설정 후 재실행")
        sys.exit(1)

    # ── stub 목록 읽기 ─────────────────────────────────────────────────────────
    stubs = fetch_null_stubs()
    print(f"[INFO] place_id=NULL 전체: {len(stubs)}건")

    done_ids = load_done_ids()
    if done_ids:
        print(f"[INFO] 기처리(CSV 재개): {len(done_ids)}건 스킵")
    pending = [s for s in stubs if s["store_id"] not in done_ids]
    target  = pending[:args.limit]
    print(f"[INFO] 이번 배치: {len(target)}건 (limit={args.limit})")
    print(f"[INFO] 결과 CSV: {RESULT_CSV}")
    print()

    # ── 배치 실행 ──────────────────────────────────────────────────────────────
    recovered = []
    unresolved = []
    errors = []

    for i, store in enumerate(target, 1):
        print(f"[{i:>2}/{len(target)}] {store['store_name']!r} / {store['address']!r}")
        result = collect_one(store)
        append_result(result)

        st = result["status"]
        pid = result["place_id_returned"]
        print(f"       → status={st}  place_id={pid or '-'}  elapsed={result['elapsed_s']}s")

        if st in ("collected", "refreshed") and pid:
            recovered.append(result)
        elif st == "place_not_found":
            unresolved.append(result)
        else:
            errors.append(result)

        if i < len(target):
            time.sleep(RATE_SLEEP)

    # ── 요약 ──────────────────────────────────────────────────────────────────
    total = len(target)
    print()
    print("=" * 60)
    print(f"[SUMMARY] 처리: {total}건  |  복구: {len(recovered)}  |  미등록: {len(unresolved)}  |  오류: {len(errors)}")
    print("=" * 60)

    # ── 복구 확인 (DB 실값) ────────────────────────────────────────────────────
    if recovered:
        sample = recovered[0]
        db_row = verify_recovered(sample["store_id"])
        print(f"\n[DB 복구 확인 샘플]")
        print(f"  store_id      : {db_row['store_id']}")
        print(f"  store_name    : {db_row['store_name']}")
        print(f"  place_id (DB) : {db_row['place_id']}")
        print(f"  is_registered : {db_row['is_registered']}")
    else:
        print("\n[DB 복구 확인] 이번 배치 복구 건 없음 — 미등록 점포거나 추가 확인 필요")

    # 미해소 샘플 검증 (row count 불변 확인)
    if unresolved:
        sample_u = unresolved[0]
        db_row_u = verify_recovered(sample_u["store_id"])
        print(f"\n[DB 미해소 확인 샘플]")
        print(f"  store_id      : {db_row_u['store_id']}")
        print(f"  store_name    : {db_row_u['store_name']}")
        print(f"  place_id (DB) : {db_row_u['place_id']} (null 유지 확인)")
        print(f"  is_registered : {db_row_u['is_registered']}")

    print(f"\n[STOP] first-{args.limit} 체크포인트 완료. CEO 승인 후 전체 배치 재실행.")
    print(f"  재실행 명령: COLLECTOR_API_KEY=<prod_key> python scripts/recollect_stubs.py --limit 251")


if __name__ == "__main__":
    main()
