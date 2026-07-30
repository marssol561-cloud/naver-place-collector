import asyncio
import html as _html
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
# Strips parenthetical dong annotation added by Naver road-address display: "(선부동)"
_PAREN_RE = re.compile(r"\s*\([^)]*\)")
# Strips floor/unit/commercial-block suffixes that poison the search query:
#   층 variants: 1층, 2층, B1층, 지하1층
#   호 variants: 101호, B07호, 가101호
#   상가동 variants: 상가동, 상가A동 (commercial block name, NOT administrative dong)
_DETAIL_SUFFIX_RE = re.compile(
    r"\s+(?:[가-힣A-Za-z\d]+층|[가-힣A-Za-z]*\d+호|상가\S*동).*$"
)
# Matches /entry/place/{id}, /place/{id}, /restaurant/{id} in the final page URL
# when Naver redirects directly to a place detail page (no searchIframe available)
_DIRECT_URL_RE = re.compile(r"/(?:entry/place|restaurant|place)/(\d{6,})")
# search_place_info 전용: Apollo cache JSON에서 상호명·도로명주소 추출
_NAME_RE = re.compile(r'"name"\s*:\s*"([^"]+)"')
_ROAD_ADDR_RE = re.compile(r'"roadAddress"\s*:\s*"([^"]+)"')
# direct-entry 상세 페이지 <title> 태그에서 점포명 추출
_TITLE_RE = re.compile(r'<title[^>]*>([^<]+)</title>')


def _clean_address(address: str) -> str:
    """주소에서 검색 노이즈 제거: 괄호 병기·층·호·상가동 등 상세 주소 suffix 삭제."""
    addr = _PAREN_RE.sub("", address)        # "(선부동)" 제거
    addr = _DETAIL_SUFFIX_RE.sub("", addr)  # "상가동 1층 102호", "1층" 등 제거
    return addr.strip()


async def search_place_id(store_name: str, address: str) -> str | None:
    """
    점포명+주소로 네이버 플레이스 place_id를 검색한다.
    검색 성공: place_id 문자열 반환
    검색 실패(플레이스 미등록 등): None 반환
    """
    address_clean = _clean_address(address)
    address_for_fallback = _ADDR_NUMBER_SUFFIX_RE.sub("", address_clean).strip()
    parts = address_for_fallback.split()

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


async def search_place_info(store_name: str, address: str) -> dict | None:
    """
    점포명+주소로 네이버 플레이스 검색 → {place_id, name, address} 반환.
    검색 실패 시 None. search_place_id 함수는 그대로 보존(호환성 유지).
    """
    address_clean = _clean_address(address)
    address_for_fallback = _ADDR_NUMBER_SUFFIX_RE.sub("", address_clean).strip()
    parts = address_for_fallback.split()

    queries = [f"{store_name} {address_clean}"]
    if len(parts) > 1:
        queries.append(f"{store_name} {parts[-1]}")

    for query in queries:
        print(f"[검색] 쿼리 시도(info): {query!r}")
        result = await _search_single_query_info(query, store_name)
        if result is not None:
            if result.get("name"):
                result["name"] = _html.unescape(result["name"])
            if result.get("address"):
                result["address"] = _html.unescape(result["address"])
            return result

    print(f"[검색 실패] 모든 쿼리 소진(info): {store_name!r}")
    return None


async def _search_single_query_info(query: str, store_name: str) -> dict | None:
    """search_place_id 내부 로직 재사용, {place_id, name, address} 반환."""

    def is_unsafe_name(name: str | None) -> bool:
        """확신할 수 없는 이름은 None으로 취급한다.
        '장소'는 네이버 SPA가 실제 상호명으로 아직 갱신되지 않은 상태의 제네릭
        플레이스홀더 타이틀이다 (실측: 2026-07-30, RESUME-4/STEP0 — t+1~2s
        구간에서만 일시적으로 나타나고 t+4s 이내 항상 실제 이름으로 바뀜)."""
        return not name or name.strip() == "장소"

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
                    print(f"[오류] 페이지 로드 타임아웃(info): {store_name!r}")
                    return None

                try:
                    await page.wait_for_load_state("networkidle", timeout=8_000)
                except PlaywrightTimeoutError:
                    print(f"[검색] networkidle 미달(info): {store_name!r}")

                # Shape 1: 상단 URL이 /place/{id} 또는 /entry/place/{id}로 리디렉트되는 경우
                m_direct = _DIRECT_URL_RE.search(page.url)
                if m_direct and _NUMERIC_RE.match(m_direct.group(1)):
                    pid = m_direct.group(1)
                    print(f"[검색] direct-entry 리디렉트(info) place_id={pid}: {store_name!r}")
                    try:
                        page_html = await page.content()
                        # 1순위: <title> 태그에서 점포명 추출 (direct-entry 상세 페이지 최신 구조)
                        # 예: "삼겹연구소 본오본점 - 네이버지도" → "삼겹연구소 본오본점"
                        naver_name = None
                        m_title = _TITLE_RE.search(page_html)
                        if m_title:
                            _title_raw = m_title.group(1).strip()
                            _name_candidate = re.split(r'\s*[-:]\s*네이버', _title_raw)[0].strip()
                            if _name_candidate:
                                naver_name = _name_candidate
                        # 2순위: Apollo cache PlaceListBusinessesItem 항목 정의에서 name 추출
                        if naver_name is None:
                            _item_pos = page_html.find(f'"PlaceListBusinessesItem:{pid}"')
                            _snip = page_html[_item_pos:_item_pos + 1000] if _item_pos >= 0 else page_html
                            m_name = _NAME_RE.search(_snip)
                            naver_name = m_name.group(1) if m_name else None
                        # address: Apollo cache roadAddress
                        _item_pos2 = page_html.find(f'"PlaceListBusinessesItem:{pid}"')
                        _snip2 = page_html[_item_pos2:_item_pos2 + 1000] if _item_pos2 >= 0 else page_html
                        m_addr = _ROAD_ADDR_RE.search(_snip2)
                        naver_addr = m_addr.group(1) if m_addr else None
                    except Exception:
                        naver_name = None
                        naver_addr = None

                    if is_unsafe_name(naver_name):
                        # 제네릭 플레이스홀더('장소')로 의심 → 1회 한정 재확인(최대 3초 대기 후 title 재읽기)
                        print(f"[검색] 이름 불확실 재확인(info) 1차값={naver_name!r}: {store_name!r}")
                        await page.wait_for_timeout(3_000)
                        try:
                            page_html_retry = await page.content()
                            m_title_retry = _TITLE_RE.search(page_html_retry)
                            retry_name = None
                            if m_title_retry:
                                _retry_raw = re.split(r'\s*[-:]\s*네이버', m_title_retry.group(1).strip())[0].strip()
                                if _retry_raw:
                                    retry_name = _retry_raw
                            naver_name = None if is_unsafe_name(retry_name) else retry_name
                        except Exception:
                            naver_name = None

                    print(f"[검색(info)] direct 결과 name={naver_name!r} addr={naver_addr!r}")
                    return {"place_id": pid, "name": naver_name, "address": naver_addr}

                # Shape 2: 상단 URL은 검색 페이지 그대로이나, 네이버가 단일 강매치로 판단해
                # iframe#entryIframe에 상세 페이지를 자동 임베드한 경우. 이 경우
                # iframe#searchIframe은 생성되지 않는다 (실측: 2026-07-30, RESUME-4/STEP0).
                try:
                    entry_el = page.locator("iframe#entryIframe")
                    await entry_el.wait_for(state="attached", timeout=3_000)
                except PlaywrightTimeoutError:
                    entry_el = None

                if entry_el is not None:
                    entry_src = await entry_el.get_attribute("src") or ""
                    m_entry = _DIRECT_URL_RE.search(entry_src)
                    if m_entry and _NUMERIC_RE.match(m_entry.group(1)):
                        pid = m_entry.group(1)
                        print(f"[검색] entryIframe 단일매치(info) place_id={pid}: {store_name!r}")

                        entry_frame = None
                        for _f in page.frames:
                            if _f.name == "entryIframe":
                                entry_frame = _f
                                break

                        naver_name = None
                        naver_addr = None
                        if entry_frame is not None:
                            try:
                                h1 = entry_frame.locator("h1").first
                                if await h1.count():
                                    _h1_text = (await h1.inner_text(timeout=3_000)).strip()
                                    if not is_unsafe_name(_h1_text):
                                        naver_name = _h1_text
                                if naver_name is None:
                                    # 1회 한정 재확인(최대 3초 대기 후 h1 재읽기)
                                    await page.wait_for_timeout(3_000)
                                    h1 = entry_frame.locator("h1").first
                                    if await h1.count():
                                        _h1_text = (await h1.inner_text(timeout=3_000)).strip()
                                        if not is_unsafe_name(_h1_text):
                                            naver_name = _h1_text

                                frame_html = await entry_frame.content()
                                m_addr = _ROAD_ADDR_RE.search(frame_html)
                                naver_addr = m_addr.group(1) if m_addr else None
                            except Exception:
                                pass
                        else:
                            print(f"[검색(info)] entryIframe 프레임 접근 실패 place_id={pid}: {store_name!r}")

                        print(f"[검색(info)] entryIframe 결과 name={naver_name!r} addr={naver_addr!r}")
                        return {"place_id": pid, "name": naver_name, "address": naver_addr}

                # Shape 3: 검색 결과 목록이 iframe#searchIframe 안에 표시되는 경우
                try:
                    await page.wait_for_selector("iframe#searchIframe", timeout=12_000)
                except PlaywrightTimeoutError:
                    try:
                        _iframe_ids = await page.eval_on_selector_all(
                            "iframe", "els => els.map(e => e.id || e.name || '(no id)')"
                        )
                    except Exception:
                        _iframe_ids = ["<조회 실패>"]
                    print(
                        f"[검색 실패] 페이지 형태 미인식(info) url={page.url} "
                        f"iframes={_iframe_ids}: {store_name!r}"
                    )
                    return None

                search_frame = await _wait_for_frame(page, "searchIframe", timeout=8.0)
                if search_frame is None:
                    print(f"[오류] searchIframe 없음(info): {store_name!r}")
                    return None

                try:
                    await search_frame.wait_for_selector("li", state="attached", timeout=15_000)
                except PlaywrightTimeoutError:
                    print(f"[검색 실패] li 없음(info): {store_name!r}")
                    return None

                if await search_frame.locator("li").count() == 0:
                    print(f"[검색 실패] 결과 0건(info): {store_name!r}")
                    return None

                await page.wait_for_timeout(800)

                html = await search_frame.content()
                m = _ITEMS_RE.search(html)
                if not (m and _NUMERIC_RE.match(m.group(1))):
                    print(f"[오류] place_id 추출 실패(info): {store_name!r}")
                    return None

                place_id = m.group(1)

                # place_id 특정 item 정의 위치에서 name/address 추출 (전체 html.search 보다 안정적)
                _item_pos = html.find(f'"PlaceListBusinessesItem:{place_id}"')
                if _item_pos >= 0:
                    _snip = html[_item_pos:_item_pos + 1000]
                else:
                    _snip = html
                m_name = _NAME_RE.search(_snip)
                naver_name = m_name.group(1) if m_name else None
                if naver_name is None:
                    print(f"[검색(info)] name 필드 payload에 없음 place_id={place_id}: {store_name!r}")

                m_addr = _ROAD_ADDR_RE.search(_snip)
                naver_addr = m_addr.group(1) if m_addr else None
                if naver_addr is None:
                    print(f"[검색(info)] roadAddress 필드 payload에 없음 place_id={place_id}: {store_name!r}")

                return {"place_id": place_id, "name": naver_name, "address": naver_addr}

            finally:
                await browser.close()

    except Exception as exc:
        print(f"[오류] 브라우저 실행 실패(info): {store_name!r}: {exc}")
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
