# -*- coding: utf-8 -*-
"""
crawl_place_by_id() 직접 호출로 BODY_COMPLETENESS_THRESHOLD=200 fix 검증.
서버/인증 우회. place_id는 진단 스크립트에서 확보한 값.
수집 후 DB에 upsert하고 PASS 기준(industry + gpv + menu_mentions + total_reviews) 검증.
"""
import asyncio, sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import dotenv_values
from collector.place_crawler import crawl_place_by_id, CRAWL_INCOMPLETE
from db import master_db

vals = dotenv_values(Path(__file__).parent.parent / ".env", encoding="utf-8-sig")

TARGETS = [
    ("가르텐비어 인천청천점",  "인천 부평구 청천동 180-4",              "34017001"),
    ("겹살이네",              "인천 서구 석남동 451-44",               "1225221119"),
    ("국밥대장 검암1지구점",   "인천 서구 검암동 661-2",                "1448066768"),
    ("닥터빈티지 본점",        "경기 부천시 원미구 심곡동 386-4 1층,2층", "1842154511"),
]

RATE_SLEEP = 5

def verify_db(place_id: str, name: str) -> dict:
    import requests as req
    DB_URL = vals.get("MASTER_DB_URL", "").rstrip("/")
    DB_KEY = vals.get("MASTER_DB_SERVICE_ROLE_KEY", "")
    resp = req.get(
        f"{DB_URL}/rest/v1/stores",
        params={"place_id": f"eq.{place_id}",
                "select": "industry,rating,total_reviews,crawl_data"},
        headers={"apikey": DB_KEY, "Authorization": f"Bearer {DB_KEY}"},
        timeout=15,
    )
    rows = resp.json()
    if not rows:
        return {"verdict": "DB_MISS"}
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
    checks = [bool(industry), gpv_ok, mm_ok, bool(total_rev)]
    n_pass = sum(checks)
    return {
        "industry": industry or "NULL",
        "gpv": "Y" if gpv_ok else "N",
        "menu": "Y" if mm_ok else "N",
        "reviews": total_rev or "NULL",
        "n_pass": n_pass,
        "verdict": "PASS" if n_pass == 4 else f"PARTIAL({n_pass}/4)",
    }


async def main():
    print("=" * 64)
    print("[직접 crawl 검증] BODY_COMPLETENESS_THRESHOLD=200 fix")
    print("=" * 64)

    results = []
    for i, (name, addr, pid) in enumerate(TARGETS, 1):
        print(f"\n[{i}/4] {name}  place_id={pid}")
        try:
            raw = await crawl_place_by_id(pid)
        except Exception as e:
            print(f"  crawl 예외: {e}")
            results.append({"name": name, "pid": pid, "crawl": "EXCEPTION", "err": str(e)})
            continue

        if raw is CRAWL_INCOMPLETE:
            print(f"  crawl = CRAWL_INCOMPLETE (fix 미효과)")
            results.append({"name": name, "pid": pid, "crawl": "CRAWL_INCOMPLETE"})
        elif raw is None:
            print(f"  crawl = None (데이터 없음)")
            results.append({"name": name, "pid": pid, "crawl": "NONE"})
        else:
            # DB upsert
            try:
                mapped = master_db.apply_field_mapping(raw)
                mapped = master_db.sanitize_crawl_data(mapped)
                _biz_urls  = mapped.pop("business_image_urls", []) or []
                _biz_count = mapped.pop("business_photo_count", None)
                region = master_db.extract_region(addr)
                store_id, _ = master_db.upsert_store(pid, name, addr, mapped, region)
                if _biz_urls or _biz_count is not None:
                    master_db.upsert_business_images(store_id, pid, _biz_urls, _biz_count)
                print(f"  crawl = OK  store_id={store_id}")
                results.append({"name": name, "pid": pid, "crawl": "OK", "store_id": store_id})
            except Exception as e:
                print(f"  DB upsert 실패: {e}")
                results.append({"name": name, "pid": pid, "crawl": "DB_ERR", "err": str(e)})

        if i < len(TARGETS):
            await asyncio.sleep(RATE_SLEEP)

    print("\n" + "=" * 64)
    print("[DB 검증]")
    verify_results = []
    for r in results:
        name = r["name"]; pid = r["pid"]
        if r["crawl"] != "OK":
            print(f"  [{name}]  수집 실패 ({r['crawl']}) — DB 검증 스킵")
            verify_results.append({"name": name, "verdict": r["crawl"]})
            continue
        v = verify_db(pid, name)
        print(f"  [{name}]  industry={v.get('industry')}  gpv={v.get('gpv')}  "
              f"menu={v.get('menu')}  reviews={v.get('reviews')}  → {v['verdict']}")
        verify_results.append({"name": name, "verdict": v["verdict"]})

    print("\n" + "=" * 64)
    print("[최종 요약]")
    for r in verify_results:
        icon = "✅" if r.get("verdict") == "PASS" else "❌"
        print(f"  {icon} {r['name']:<22s}  {r.get('verdict', 'N/A')}")
    print("=" * 64)


asyncio.run(main())
