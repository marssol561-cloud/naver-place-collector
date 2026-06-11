import asyncio
import re
import time
import urllib.parse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import PROXY_URL

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_SEARCH_URL = "https://map.naver.com/p/search/{query}"

_ITEMS_RE = re.compile(
    r'"items"\s*:\s*\[\s*\{"__ref"\s*:\s*"PlaceListBusinessesItem:(\d+)(?::\d+)?"'
)
_NUMERIC_RE = re.compile(r"^\d+$")
_ADDR_NUMBER_SUFFIX_RE = re.compile(r"\s+\d[\d\-]*\s*$")
# Matches /entry/place/{id}, /place/{id}, /restaurant/{id} in the final page URL
# when Naver redirects directly to a place detail page (no searchIframe available)
_DIRECT_URL_RE = re.compile(r"/(?:entry/place|restaurant|place)/(\d{6,})")


async def search_place_id(store_name: str, address: str) -> str | None:
    """
    점포명+주소로 네이버 플레이스 place_id를 검색한다.
    검색 성공: place_id 문자열 반환
    검색 실패(플레이스 미등록 등): None 반환
    """
    address_clean = _ADDR_NUMBER_SUFFIX_RE.sub("", address).strip()
    parts = address_clean.split()

    queries = [f"{store_name} {address_clean}"]
    if len(parts) > 1:
        queries.append(f"{store_name} {parts[-1]}")

    for query in queries:
        print(f"[검색] 쿼리 시도: {query!r}")
        result = await _search_single_query(query, store_name)
        if result is not None:
            return result

    print(f"[검색 실패] 모든 쿼리 소진: {store_name!r}")
    return None


async def _search_single_query(query: str, store_name: str) -> str | None:
    encoded = urllib.parse.quote(query)
    url = _SEARCH_URL.format(query=encoded)

    launch_options: dict = {
        "headless": True,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if PROXY_URL:
        launch_options["proxy"] = {"server": PROXY_URL}

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(**launch_options)
            try:
                ctx = await browser.new_context(
                    user_agent=_UA,
                    viewport={"width": 1280, "height": 720},
                )
                page = await ctx.new_page()

                try:
                    await page.goto(url, timeout=15_000)
                except PlaywrightTimeoutError:
                    print(f"[오류] 페이지 로드 타임아웃: {store_name!r}")
                    return None

                # Wait for any redirect to settle before reading the final URL
                try:
                    await page.wait_for_load_state("networkidle", timeout=8_000)
                except PlaywrightTimeoutError:
                    pass  # proceed even if not fully idle

                # FIX: detect direct-entry redirect (no searchIframe on detail pages)
                # Naver sometimes redirects search to /place/{id}, /entry/place/{id}, etc.
                m_direct = _DIRECT_URL_RE.search(page.url)
                if m_direct and _NUMERIC_RE.match(m_direct.group(1)):
                    print(f"[검색] direct-entry 리디렉트 감지, place_id={m_direct.group(1)}: {store_name!r}")
                    return m_direct.group(1)

                try:
                    await page.wait_for_selector("iframe#searchIframe", timeout=12_000)
                except PlaywrightTimeoutError:
                    print(f"[오류] searchIframe 로드 타임아웃: {store_name!r}")
                    return None

                search_frame = await _wait_for_frame(page, "searchIframe", timeout=8.0)
                if search_frame is None:
                    print(f"[오류] searchIframe 프레임을 찾을 수 없음: {store_name!r}")
                    return None

                try:
                    await search_frame.wait_for_selector("li", state="attached", timeout=15_000)
                except PlaywrightTimeoutError:
                    print(f"[검색 실패] li 요소 없음(검색 결과 0건): {store_name!r}")
                    return None

                if await search_frame.locator("li").count() == 0:
                    print(f"[검색 실패] 결과 0건: {store_name!r}")
                    return None

                await page.wait_for_timeout(800)

                html = await search_frame.content()
                m = _ITEMS_RE.search(html)
                if m and _NUMERIC_RE.match(m.group(1)):
                    return m.group(1)

                print(f"[오류] HTML에서 place_id 추출 실패: {store_name!r}")
                return None
            finally:
                await browser.close()

    except Exception as exc:
        print(f"[오류] 브라우저 실행 실패: {store_name!r}: {exc}")
        return None


async def _wait_for_frame(page, frame_name: str, timeout: float):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        frame = page.frame(name=frame_name)
        if frame is not None:
            return frame
        await page.wait_for_timeout(200)
    return None


if __name__ == "__main__":
    async def _main():
        print("=== 테스트 1: 실제 점포 ===")
        result = await search_place_id("스타벅스", "서울 강남구 역삼동")
        print(f"place_id: {result}\n")

        print("=== 테스트 2: 존재하지 않는 점포 ===")
        result2 = await search_place_id("존재하지않는가게12345", "서울 강남구 역삼동 999")
        print(f"place_id: {result2}")

    asyncio.run(_main())
