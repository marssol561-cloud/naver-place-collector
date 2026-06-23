# -*- coding: utf-8 -*-
"""
CRAWL_INCOMPLETE 진단:
1. searcher로 4개 점포 place_id 확보
2. Playwright 직접 실행 → body_text 길이 + frame URL + 첫 200자 출력
3. wait_for_load_state("networkidle") 추가 버전 비교
"""
import asyncio, sys, re, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from config import PROXY_URL
from collector.searcher import search_place_id

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_PLACE_URL = "https://map.naver.com/p/entry/place/{place_id}"
BODY_THRESHOLD = 500

TARGETS = [
    ("가르텐비어 인천청천점", "인천 부평구 청천동 180-4"),
    ("겹살이네",             "인천 서구 석남동 451-44"),
    ("국밥대장 검암1지구점",  "인천 서구 검암동 661-2"),
    ("닥터빈티지 본점",       "경기 부천시 원미구 심곡동 386-4 1층,2층"),
]


def _find_entry_frame(page):
    for frame in page.frames:
        if "pcmap.place.naver.com" in frame.url and (
            "/restaurant/" in frame.url or "/place/" in frame.url
        ):
            return frame
    for frame in page.frames:
        if "pcmap.place.naver.com" in frame.url and frame.url not in ("", "about:blank"):
            return frame
    for frame in page.frames:
        if frame.name == "entryIframe":
            return frame
    return None


async def probe(place_id: str, store_name: str) -> dict:
    url = _PLACE_URL.format(place_id=place_id)
    opts = {"headless": True, "args": ["--disable-blink-features=AutomationControlled"]}
    if PROXY_URL:
        opts["proxy"] = {"server": PROXY_URL}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**opts)
        ctx = await browser.new_context(
            user_agent=_UA, viewport={"width": 1280, "height": 720}
        )
        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(5000)

            try:
                await page.wait_for_selector("iframe#entryIframe", timeout=12_000)
            except PlaywrightTimeoutError:
                return {"err": "iframe_selector_timeout"}

            frame_urls = [f.url for f in page.frames]
            entry_frame = _find_entry_frame(page)
            if entry_frame is None:
                for _ in range(12):
                    await page.wait_for_timeout(500)
                    entry_frame = _find_entry_frame(page)
                    if entry_frame:
                        break
            if entry_frame is None:
                return {"err": "frame_not_found", "frame_urls": frame_urls[:5]}

            frame_url_before = entry_frame.url

            # === ORIGINAL: immediate inner_text ===
            try:
                body_text_orig = await entry_frame.locator("body").inner_text(timeout=5000)
            except PlaywrightTimeoutError:
                body_text_orig = ""
            len_orig = len(body_text_orig)

            # === PATCHED: wait_for_load_state("networkidle") first ===
            try:
                await entry_frame.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeoutError:
                pass
            try:
                body_text_patched = await entry_frame.locator("body").inner_text(timeout=10_000)
            except PlaywrightTimeoutError:
                body_text_patched = ""
            len_patched = len(body_text_patched)

            return {
                "place_id": place_id,
                "frame_url_at_find": frame_url_before,
                "all_frame_urls": frame_urls[:6],
                "len_orig": len_orig,
                "head_orig": body_text_orig[:200].replace("\n", "↵"),
                "len_patched": len_patched,
                "head_patched": body_text_patched[:200].replace("\n", "↵"),
                "orig_pass": len_orig >= BODY_THRESHOLD,
                "patched_pass": len_patched >= BODY_THRESHOLD,
            }
        finally:
            await browser.close()


async def main():
    print("=" * 70)
    print("CRAWL_INCOMPLETE 진단 — LOCAL 실행 (deploy 없음)")
    print("=" * 70)

    # Step 1: searcher로 place_id 확보
    place_ids = {}
    print("\n[STEP 1] place_id 검색...")
    for name, addr in TARGETS:
        try:
            pid = await search_place_id(name, addr)
        except Exception as e:
            pid = None
            print(f"  [{name}] 검색 오류: {e}")
        place_ids[name] = pid
        print(f"  [{name}] place_id={pid}")

    # Step 2: Playwright 직접 진단
    print("\n[STEP 2] 렌더 진단 (original vs patched)...")
    results = []
    for name, addr in TARGETS:
        pid = place_ids.get(name)
        if not pid:
            print(f"\n  [{name}] place_id 없음 — 스킵")
            results.append({"name": name, "pid": None})
            continue
        print(f"\n  [{name}]  place_id={pid}")
        try:
            r = await probe(pid, name)
            r["name"] = name
            results.append(r)
            print(f"    frame_url_at_find : {r.get('frame_url_at_find', 'N/A')}")
            print(f"    len_orig          : {r.get('len_orig', 'N/A')} chars  (pass={r.get('orig_pass')})")
            print(f"    len_patched       : {r.get('len_patched', 'N/A')} chars  (pass={r.get('patched_pass')})")
            print(f"    head_orig [200]   : {r.get('head_orig', '')[:120]!r}")
            print(f"    head_patched[200] : {r.get('head_patched', '')[:120]!r}")
        except Exception as e:
            print(f"    ERROR: {e}")
            results.append({"name": name, "pid": pid, "err": str(e)})

    # Summary
    print("\n" + "=" * 70)
    print("[진단 요약]")
    for r in results:
        if r.get("err"):
            print(f"  {r['name']:<22s}  ERROR: {r['err']}")
            continue
        if not r.get("pid"):
            print(f"  {r['name']:<22s}  place_id 없음")
            continue
        o = "PASS" if r.get("orig_pass") else f"FAIL({r.get('len_orig')}자)"
        p = "PASS" if r.get("patched_pass") else f"FAIL({r.get('len_patched')}자)"
        print(f"  {r['name']:<22s}  original={o}  patched={p}")
    print("=" * 70)


asyncio.run(main())
