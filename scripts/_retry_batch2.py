# -*- coding: utf-8 -*-
"""
배치2 실패 점포 1회 retry (스프린트 지침 준수)
- CRAWL_INCOMPLETE: 꼼숯, 닥터빈티지 본점 — 동일 주소 재시도
- QUERY_FORMAT: 깡통막창, 그래요거 시흥거북섬점 — 도로명주소로 전환 재시도
결과는 표준 출력으로만 표시 (result CSV 미수정 — 별도 보고용)
"""
import sys, time, json
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

import requests
from dotenv import dotenv_values

vals = dotenv_values(Path(__file__).parent.parent / ".env", encoding="utf-8-sig")
COL_URL = vals.get("COLLECTOR_URL", "https://naver-place-collector-production.up.railway.app").rstrip("/")
COL_KEY = vals.get("COLLECTOR_API_KEY", "")
HEADERS = {"Authorization": f"Bearer {COL_KEY}", "Content-Type": "application/json"}

RETRY_TARGETS = [
    # (점포명, 재시도 주소, 이유)
    ("꼼숯",          "경기 부천시 원미구 중동 1125-1 1층",    "CRAWL_INCOMPLETE — timeout 재시도"),
    ("닥터빈티지 본점", "경기 부천시 원미구 심곡동 386-4 1층,2층", "CRAWL_INCOMPLETE 재시도"),
    ("깡통막창",       "경기 부천시 원미구 길주로77번길 55-25",  "QUERY_FORMAT — 도로명주소 전환"),
    ("그래요거 시흥거북섬점", "경기 시흥시 거북섬중앙로 1",     "QUERY_FORMAT — 도로명주소 전환"),
]

RATE_SLEEP = 5

print("=" * 64)
print(f"[배치2 retry] {len(RETRY_TARGETS)}건")
print(f"수집기: {COL_URL}")
print("=" * 64)

results = []
for i, (name, addr, reason) in enumerate(RETRY_TARGETS, 1):
    print(f"\n[{i}/{len(RETRY_TARGETS)}] {name!r}")
    print(f"        주소: {addr!r}  ({reason})")
    t0 = time.monotonic()
    try:
        resp = requests.post(
            f"{COL_URL}/api/v1/collect",
            json={"store_name": name, "address": addr, "force_refresh": True},
            headers=HEADERS,
            timeout=120,
        )
        elapsed = round(time.monotonic() - t0, 1)
        data    = resp.json()
        status   = data.get("status", "unknown")
        place_id = data.get("place_id") or "-"
        note     = data.get("message") or data.get("error_code") or ""
    except requests.exceptions.Timeout:
        elapsed, status, place_id, note = round(time.monotonic() - t0, 1), "error", "-", "timeout"
    except Exception as exc:
        elapsed, status, place_id, note = round(time.monotonic() - t0, 1), "error", "-", str(exc)[:80]

    print(f"        → status={status}  place_id={place_id}  elapsed={elapsed}s")
    if note:
        print(f"          note: {note}")
    results.append({"name": name, "addr": addr, "reason": reason,
                    "status": status, "place_id": place_id, "elapsed": elapsed, "note": note})

    if i < len(RETRY_TARGETS):
        time.sleep(RATE_SLEEP)

print("\n" + "=" * 64)
print("[retry 요약]")
for r in results:
    icon = "✅" if r["status"] in ("collected", "refreshed", "already_exists") else "❌"
    print(f"  {icon} {r['name']:<22s} {r['status']:<16s} place_id={r['place_id']}")
print("=" * 64)
