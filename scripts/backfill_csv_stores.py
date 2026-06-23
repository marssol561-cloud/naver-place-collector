# -*- coding: utf-8 -*-
"""
backfill_csv_stores.py — 리붐맛집추천 미수집 점포 백필 (10개 단위 CEO 승인 게이트)

Commands:
  --list-batch N    배치 N 목록 출력 (수집 안 함) → CEO 승인 요청용
  --run-batch  N    배치 N 수집 실행 (CEO 승인 후)
  --verify-batch N  배치 N DB 검증
  --status          전체 진행 현황

Env (.env):
  COLLECTOR_API_KEY           Railway 수집기 Bearer token (prod 키 필수)
  COLLECTOR_URL               (optional) 수집기 base URL
  MASTER_DB_URL               Supabase project URL
  MASTER_DB_SERVICE_ROLE_KEY  Supabase service role key
  BACKFILL_CSV                (optional) 입력 CSV 절대 경로 override
"""
import argparse
import csv
import json
import math
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

# ── paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
REPO_ROOT  = SCRIPT_DIR.parent
load_dotenv(REPO_ROOT / ".env")

CSV_INPUT = Path(os.getenv(
    "BACKFILL_CSV",
    r"C:\Users\marss\Downloads\프로젝트백업\0 내부도구\6 맞집추천도구\리붐맛집추천_미수집점포_수집대상_v1.1.csv",
))
RESULT_CSV = SCRIPT_DIR / "backfill_csv_result.csv"

# ── config ────────────────────────────────────────────────────────────────────
COL_URL  = os.getenv("COLLECTOR_URL", "https://naver-place-collector-production.up.railway.app").rstrip("/")
COL_KEY  = os.getenv("COLLECTOR_API_KEY", "")
DB_URL   = os.getenv("MASTER_DB_URL", "").rstrip("/")
DB_KEY   = os.getenv("MASTER_DB_SERVICE_ROLE_KEY", "")

BATCH_SIZE  = 10
RATE_SLEEP  = 5    # seconds between requests (Naver anti-block)
PER_TIMEOUT = 90   # seconds per collect request

CSV_FIELDS = [
    "batch_no", "store_name", "address",
    "status", "place_id", "elapsed_s", "note",
    "industry", "good_point_votes_ok", "menu_mentions_ok",
    "rating", "total_reviews", "verify_status",
]


# ── data helpers ──────────────────────────────────────────────────────────────
def load_targets() -> list[dict]:
    """CSV에서 분류='수집대상' 행만 로드, 순서 유지."""
    if not CSV_INPUT.exists():
        print(f"[ERROR] 입력 CSV 없음: {CSV_INPUT}")
        sys.exit(1)
    rows = []
    with open(CSV_INPUT, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("분류", "").strip() == "수집대상":
                rows.append({
                    "store_name": row["점포명"].strip(),
                    "address":    row["주소"].strip(),
                })
    return rows


def get_batch(targets: list[dict], batch_no: int) -> list[dict]:
    """배치 번호(1-indexed) → 해당 점포 슬라이스."""
    start = (batch_no - 1) * BATCH_SIZE
    return targets[start:start + BATCH_SIZE]


def total_batches(targets: list[dict]) -> int:
    return math.ceil(len(targets) / BATCH_SIZE)


# ── result CSV ────────────────────────────────────────────────────────────────
def load_result_rows() -> list[dict]:
    if not RESULT_CSV.exists():
        return []
    with open(RESULT_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_result_rows(rows: list[dict]):
    with open(RESULT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def append_result_rows(new_rows: list[dict]):
    """기존 CSV에 새 행 추가 (배치번호+점포명 키로 중복 방지)."""
    existing = load_result_rows()
    existing_keys = {(r["batch_no"], r["store_name"]) for r in existing}
    to_add = [r for r in new_rows if (r["batch_no"], r["store_name"]) not in existing_keys]
    save_result_rows(existing + to_add)


def update_result_rows(updated_rows: list[dict]):
    """특정 행 업데이트 (배치+점포명 키로 찾아 교체)."""
    existing = load_result_rows()
    update_map = {(r["batch_no"], r["store_name"]): r for r in updated_rows}
    merged = [update_map.get((r["batch_no"], r["store_name"]), r) for r in existing]
    save_result_rows(merged)


def _empty_verify() -> dict:
    return {
        "industry": "", "good_point_votes_ok": "", "menu_mentions_ok": "",
        "rating": "", "total_reviews": "", "verify_status": "",
    }


# ── collect ───────────────────────────────────────────────────────────────────
def collect_one(store: dict, batch_no: int) -> dict:
    t0 = time.monotonic()
    try:
        resp = requests.post(
            f"{COL_URL}/api/v1/collect",
            json={
                "store_name":    store["store_name"],
                "address":       store["address"],
                "force_refresh": True,
            },
            headers={"Authorization": f"Bearer {COL_KEY}", "Content-Type": "application/json"},
            timeout=PER_TIMEOUT,
        )
        elapsed  = round(time.monotonic() - t0, 1)
        data     = resp.json()
        status   = data.get("status", "unknown")
        place_id = data.get("place_id") or ""
        note     = data.get("message") or data.get("error_code") or ""
    except requests.exceptions.Timeout:
        elapsed, status, place_id, note = round(time.monotonic() - t0, 1), "error", "", "timeout"
    except Exception as exc:
        elapsed, status, place_id, note = round(time.monotonic() - t0, 1), "error", "", str(exc)[:120]

    return {
        "batch_no":   str(batch_no),
        "store_name": store["store_name"],
        "address":    store["address"],
        "status":     status,
        "place_id":   place_id,
        "elapsed_s":  str(elapsed),
        "note":       note,
        **_empty_verify(),
    }


def _is_blocked(row: dict) -> bool:
    note  = (row.get("note") or "").lower()
    keywords = ("captcha", "block", "403", "429", "robot", "차단", "캡차")
    return any(k in note for k in keywords)


# ── verify ────────────────────────────────────────────────────────────────────
def verify_store(place_id: str) -> dict:
    """place_id로 DB 조회 → 검증 필드 dict 반환."""
    if not place_id:
        return {
            "industry": "N/A", "good_point_votes_ok": "N/A",
            "menu_mentions_ok": "N/A", "rating": "N/A",
            "total_reviews": "N/A", "verify_status": "no_place_id",
        }
    try:
        resp = requests.get(
            f"{DB_URL}/rest/v1/stores",
            params={
                "place_id": f"eq.{place_id}",
                "select":   "industry,rating,total_reviews,crawl_data",
            },
            headers={"apikey": DB_KEY, "Authorization": f"Bearer {DB_KEY}"},
            timeout=15,
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception as exc:
        return {**_empty_verify(), "verify_status": f"db_error:{str(exc)[:80]}"}

    if not rows:
        return {
            "industry": "N/A", "good_point_votes_ok": "N/A",
            "menu_mentions_ok": "N/A", "rating": "N/A",
            "total_reviews": "N/A", "verify_status": "not_in_db",
        }

    row   = rows[0]
    crawl = row.get("crawl_data") or {}
    if isinstance(crawl, str):
        try:
            crawl = json.loads(crawl)
        except Exception:
            crawl = {}

    industry  = row.get("industry") or ""
    rating    = row.get("rating")   or crawl.get("rating")   or ""
    total_rev = row.get("total_reviews") or crawl.get("total_reviews") or ""

    gpv_raw = crawl.get("good_point_votes") or ""
    mm_raw  = crawl.get("menu_mentions")    or ""

    # good_point_votes: string-encoded JSON array
    gpv_ok = False
    if gpv_raw:
        try:
            parsed = json.loads(gpv_raw) if isinstance(gpv_raw, str) else gpv_raw
            gpv_ok = isinstance(parsed, list) and len(parsed) > 0
        except Exception:
            pass

    mm_ok = bool(
        mm_raw and (
            isinstance(mm_raw, list)
            or (isinstance(mm_raw, str) and mm_raw.strip() not in ("", "[]"))
        )
    )

    checks  = [bool(industry), gpv_ok, mm_ok, bool(rating), bool(total_rev)]
    n_pass  = sum(checks)
    v_status = "PASS" if n_pass == 5 else f"PARTIAL({n_pass}/5)"

    return {
        "industry":           industry or "NULL",
        "good_point_votes_ok": "Y" if gpv_ok else "N",
        "menu_mentions_ok":    "Y" if mm_ok  else "N",
        "rating":             str(rating)    if rating    else "NULL",
        "total_reviews":      str(total_rev) if total_rev else "NULL",
        "verify_status":      v_status,
    }


# ── commands ──────────────────────────────────────────────────────────────────
def cmd_list_batch(batch_no: int, targets: list[dict]):
    n_batches = total_batches(targets)
    batch     = get_batch(targets, batch_no)
    if not batch:
        print(f"[ERROR] 배치 {batch_no} 없음 (총 {n_batches} 배치)")
        sys.exit(1)

    n_done = (batch_no - 1) * BATCH_SIZE
    print("=" * 64)
    print(f"[배치 {batch_no}/{n_batches}]  수집 대상 {len(batch)}개  -- CEO 승인 요청")
    print(f"전체 {len(targets)}개 중 {n_done}개 완료 / 이번 {len(batch)}개")
    print("=" * 64)
    for i, s in enumerate(batch, 1):
        addr_note = "  ⚠ 주소 미확인" if s["address"] == "주소 미확인" else ""
        print(f"  {n_done + i:3d}. {s['store_name']:<30s}  {s['address']}{addr_note}")
    print()
    print("▶ 승인 후 실행 명령:")
    print(f"  python scripts/backfill_csv_stores.py --run-batch {batch_no}")


def cmd_run_batch(batch_no: int, targets: list[dict]):
    n_batches = total_batches(targets)
    batch     = get_batch(targets, batch_no)
    if not batch:
        print(f"[ERROR] 배치 {batch_no} 없음 (총 {n_batches} 배치)")
        sys.exit(1)

    # 중복 실행 방지
    existing     = load_result_rows()
    done_stores  = {
        r["store_name"] for r in existing
        if r.get("batch_no") == str(batch_no) and r.get("status") not in ("", None)
    }
    if len(done_stores) >= len(batch):
        print(f"[INFO] 배치 {batch_no} 이미 실행됨 ({len(done_stores)}건).")
        print("재실행 필요 시 result CSV에서 해당 행 삭제 후 재시도.")
        return

    if not COL_KEY:
        print("[ERROR] COLLECTOR_API_KEY 없음 — .env에 프로덕션 키 설정 필요")
        sys.exit(1)

    n_done = (batch_no - 1) * BATCH_SIZE
    print("=" * 64)
    print(f"[배치 {batch_no}/{n_batches}] 수집 시작  ({n_done + 1}~{n_done + len(batch)}번 점포)")
    print(f"수집기: {COL_URL}")
    print("=" * 64)

    results  = []
    stopped  = False
    for i, store in enumerate(batch, 1):
        print(f"\n[{i:2d}/{len(batch)}] {store['store_name']!r}")
        print(f"        주소: {store['address']!r}")
        row = collect_one(store, batch_no)
        results.append(row)

        st  = row["status"]
        pid = row["place_id"]
        print(f"        → status={st}  place_id={pid or '-'}  elapsed={row['elapsed_s']}s")
        if row["note"]:
            print(f"          note: {row['note']}")

        if _is_blocked(row):
            print(f"\n[⚠ STOP] 캡차/차단 감지 — 즉시 중단. 나머지 {len(batch) - i}건 미실행.")
            stopped = True
            break

        if i < len(batch):
            time.sleep(RATE_SLEEP)

    append_result_rows(results)

    ok  = sum(1 for r in results if r["status"] in ("collected", "refreshed", "already_exists"))
    nf  = sum(1 for r in results if r["status"] == "place_not_found")
    err = sum(1 for r in results if r["status"] not in ("collected", "refreshed", "already_exists", "place_not_found"))

    print("\n" + "=" * 64)
    print(f"[배치 {batch_no} 완료]  처리: {len(results)}건  수집OK: {ok}  미등록: {nf}  오류: {err}")
    if stopped:
        print("[주의] 차단 감지로 조기 종료. CEO 보고 후 재개 결정 필요.")
    print(f"결과 CSV: {RESULT_CSV}")
    print("=" * 64)
    print("\n▶ 검증 명령:")
    print(f"  python scripts/backfill_csv_stores.py --verify-batch {batch_no}")


def cmd_verify_batch(batch_no: int):
    if not DB_URL or not DB_KEY:
        print("[ERROR] MASTER_DB_URL / MASTER_DB_SERVICE_ROLE_KEY 없음 — .env 확인")
        sys.exit(1)

    rows       = load_result_rows()
    batch_rows = [r for r in rows if r.get("batch_no") == str(batch_no)]
    if not batch_rows:
        print(f"[ERROR] 배치 {batch_no} 결과 없음 — --run-batch {batch_no} 먼저 실행 필요")
        sys.exit(1)

    print("=" * 64)
    print(f"[배치 {batch_no}] DB 검증  ({len(batch_rows)}건)")
    print("=" * 64)

    updated = []
    for row in batch_rows:
        pid = row.get("place_id") or ""
        v   = verify_store(pid)
        updated.append({**row, **v})

        vs   = v.get("verify_status", "")
        icon = "✅" if vs == "PASS" else ("⚠" if vs.startswith("PARTIAL") else "❌")
        print(f"\n{icon} [{row['status']:>12s}] {row['store_name']}")
        print(f"    industry={v['industry']:<20s} gpv={v['good_point_votes_ok']}  "
              f"menu={v['menu_mentions_ok']}  rating={v['rating']}  reviews={v['total_reviews']}")
        print(f"    → {vs}")

    update_result_rows(updated)

    n_pass    = sum(1 for r in updated if r.get("verify_status") == "PASS")
    n_partial = sum(1 for r in updated if (r.get("verify_status") or "").startswith("PARTIAL"))
    n_other   = len(updated) - n_pass - n_partial
    failed    = [r["store_name"] for r in updated if r.get("verify_status") != "PASS"]

    print("\n" + "=" * 64)
    print(f"[검증 요약]  PASS: {n_pass}  PARTIAL: {n_partial}  기타(미등록 등): {n_other}")
    if failed:
        print(f"미통과 점포 ({len(failed)}건):")
        for name in failed:
            row = next(r for r in updated if r["store_name"] == name)
            print(f"  - {name}  [{row.get('verify_status')}]  place_id={row.get('place_id') or '-'}")
    print("=" * 64)

    if batch_no < total_batches(load_targets()):
        next_b = batch_no + 1
        print(f"\n▶ 다음 배치 목록 확인 (CEO 승인 요청):")
        print(f"  python scripts/backfill_csv_stores.py --list-batch {next_b}")


def cmd_status(targets: list[dict]):
    rows = load_result_rows()

    if not rows:
        print(f"[현황] 아직 처리된 배치 없음. 총 {len(targets)}개 / {total_batches(targets)} 배치")
        print(f"  시작 명령: python scripts/backfill_csv_stores.py --list-batch 1")
        return

    done_batches = sorted({int(r["batch_no"]) for r in rows})
    n_ok   = sum(1 for r in rows if r.get("status") in ("collected", "refreshed", "already_exists"))
    n_nf   = sum(1 for r in rows if r.get("status") == "place_not_found")
    n_err  = sum(1 for r in rows if r.get("status") not in ("collected", "refreshed", "already_exists", "place_not_found", ""))
    n_proc = len(rows)
    n_pass = sum(1 for r in rows if r.get("verify_status") == "PASS")
    next_b = (max(done_batches) + 1) if done_batches else 1

    print("=" * 64)
    print(f"[백필 현황]  총 {len(targets)}개 / {total_batches(targets)} 배치")
    print(f"  완료 배치: {done_batches}")
    print(f"  처리 건수: {n_proc}  수집OK: {n_ok}  미등록: {n_nf}  오류: {n_err}")
    print(f"  검증 PASS: {n_pass}/{n_proc}")
    print(f"  다음 배치: {next_b}")
    print("=" * 64)
    if next_b <= total_batches(targets):
        print(f"\n▶ 다음 배치 목록 확인:")
        print(f"  python scripts/backfill_csv_stores.py --list-batch {next_b}")


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="리붐맛집추천 미수집 점포 백필 (10개 단위 CEO 승인 게이트)"
    )
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--list-batch",   type=int, metavar="N", help="배치 N 목록 출력 (CEO 승인용)")
    grp.add_argument("--run-batch",    type=int, metavar="N", help="배치 N 수집 실행 (CEO 승인 후)")
    grp.add_argument("--verify-batch", type=int, metavar="N", help="배치 N DB 검증")
    grp.add_argument("--status",       action="store_true",   help="전체 진행 현황")
    args = parser.parse_args()

    targets = load_targets()

    if args.list_batch is not None:
        cmd_list_batch(args.list_batch, targets)
    elif args.run_batch is not None:
        cmd_run_batch(args.run_batch, targets)
    elif args.verify_batch is not None:
        cmd_verify_batch(args.verify_batch)
    elif args.status:
        cmd_status(targets)


if __name__ == "__main__":
    main()
