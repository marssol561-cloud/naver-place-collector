import asyncio
import re
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
_PLACE_URL = "https://map.naver.com/p/entry/place/{place_id}"

PLACE_FIELDS = [
    "place_name",
    "lot_address",
    "category",
    "phone",
    "business_hours",
    "break_time",
    "last_order",
    "closed_days",
    "parking",
    "takeout",
    "facilities",
    "menu_list",
    "keywords",
    "description",
    "directions",
    "photo_count",
    "visitor_review_count",
    "blog_review_count",
    "reservation_active",
    "naver_pay_active",
    "coupon_active",
    "talktalk_active",
    "smartcall_active",
    "latest_news_date",
    "total_reviews",           # 방문자리뷰 = 전체리뷰 (visitor_review_count 그대로)
    "receipt_review_ratio",    # (방문자리뷰 - 블로그리뷰) / 방문자리뷰 × 100
    "good_point_votes",        # GQL visitorReviewStats.positiveKeywordCount
    "feature_mentions",        # GQL visitorReviewStats.keywordList[].count 합산
    "menu_mentions",           # GQL visitorReviewStats.menuList[].count 합산
]

ADDRESS_PREFIXES = (
    "서울", "경기", "인천", "부산", "대구", "광주", "대전", "울산", "세종",
    "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
)

PRICE_PATTERN = re.compile(r"(?:(?:\d{1,3},)*\d{3,}|\d{4,})\s*원|변동")
MENU_NOISE_WORDS = [
    "펼쳐보기", "접기", "주문", "사진", "리뷰", "메뉴", "정보", "홈", "소식",
    "예약", "쿠폰", "저장", "거리뷰", "공유", "출발", "도착", "AI 요약", "방문자", "블로그",
]
MENU_DESCRIPTION_MARKERS = [
    "설명", "어쩌고", " 참나무", "48시간", " 과일", "야채", "특미간장",
    "비법양념", "식감", "감칠맛", " 5색", "고명", "메뉴입니다", "입니다",
]


# ── 순수 텍스트 추출 함수 (playwright 의존 없음) ──────────────────────────────

def _compact_text(value: str) -> str:
    return " ".join((value or "").split())


def _extract_phone(text: str) -> str:
    match = re.search(r"\b\d{2,4}-\d{3,4}-\d{4}\b", text)
    return match.group(0) if match else ""


def _extract_review_count(text: str, label: str) -> str:
    match = re.search(rf"{re.escape(label)}\s*리뷰\s*([\d,]+)", text)
    return match.group(1) if match else ""


def _extract_address(text: str) -> str:
    compact = _compact_text(text)
    prefixes = "|".join(re.escape(p) for p in ADDRESS_PREFIXES)
    pattern = (
        rf"((?:{prefixes})\s+[가-힣A-Za-z0-9\s.\-]+?"
        r"(?:로|길|대로|번길)\s*\d+(?:\s*\d*층?)?)"
    )
    match = re.search(pattern, compact)
    return match.group(1).strip() if match else ""


def _context_after_marker(text: str, markers: list[str], limit: int) -> str:
    compact = _compact_text(text)
    for marker in markers:
        index = compact.find(marker)
        if index != -1:
            return compact[index: index + limit].strip()
    return ""


def extract_business_hours(text: str) -> str:
    context = _context_after_marker(
        text, ["영업시간", "영업 중", "영업 종료", "영업 전", "곧 영업 시작"], 300
    )
    if not context:
        return ""
    stop_markers = ["라스트오더", "전화번호", "홈페이지", "인스타그램", "편의", "AI 브리핑"]
    cut_points = [context.find(m) for m in stop_markers if context.find(m) > 0]
    if cut_points:
        context = context[: min(cut_points)].strip()
    return context[:300]


def extract_last_order(text: str) -> str:
    compact = _compact_text(text)
    index = compact.find("라스트오더")
    if index == -1:
        return ""
    hours_index = compact.rfind("영업시간", 0, index)
    start = hours_index + len("영업시간") if hours_index != -1 else max(0, index - 40)
    stop_candidates = [
        compact.find(m, index + 1)
        for m in ["전화번호", "홈페이지", "인스타그램", "편의", "AI 브리핑"]
        if compact.find(m, index + 1) != -1
    ]
    end = min(stop_candidates) if stop_candidates else index + 80
    return compact[start:end].strip()[:120]


def extract_break_time(text: str) -> str:
    return _context_after_marker(text, ["브레이크타임", "브레이크 타임"], 120)


def extract_closed_days(text: str) -> str:
    return _context_after_marker(text, ["정기휴무", "연중무휴", "휴무", "매주"], 120)


def _extract_category(text: str, place_name: str) -> str:
    if not place_name:
        return ""
    compact = _compact_text(text)
    match = re.search(
        rf"{re.escape(place_name)}\s*([가-힣A-Za-z,&·\s]+?)\s*알림받기",
        compact,
    )
    if not match:
        return ""
    category = match.group(1).strip()
    return category if len(category) <= 30 else ""


def _extract_place_name_from_text(text: str) -> str:
    compact = _compact_text(text)
    match = re.search(r"이전 페이지\s+(.+?)\s+페이지 닫기", compact)
    return match.group(1).strip() if match else ""


def extract_yes_no_keyword(text: str, keyword: str) -> str:
    return "Y" if keyword in (text or "") else ""


def extract_facilities(text: str) -> str:
    compact = _compact_text(text)
    start = compact.find("편의")
    if start == -1:
        markers = ["단체 이용 가능", "포장", "배달", "무선 인터넷", "남/녀 화장실"]
        starts = [compact.find(m) for m in markers if compact.find(m) != -1]
        if not starts:
            return ""
        start = min(starts)
    end_candidates = [
        compact.find(m, start + 1)
        for m in ["정보 더보기", "AI 브리핑", "안내 다양한 리뷰", "리뷰를 종합"]
        if compact.find(m, start + 1) != -1
    ]
    end = min(end_candidates) if end_candidates else start + 300
    return compact[start:end].strip()[:300]


def extract_photo_count(text: str, img_count: int | None = None) -> str:
    for pattern in [r"이미지\s*갯수\s*([\d,]+\+?)", r"사진\s*([\d,]+\+?)"]:
        match = re.search(pattern, text or "")
        if match:
            return match.group(1)
    return str(img_count) if img_count else ""


def extract_directions(text: str) -> str:
    compact = _compact_text(text)
    for marker in ["찾아가는길", "찾아가는 길"]:
        index = compact.find(marker)
        if index != -1:
            end_candidates = [
                compact.find(m, index + 1)
                for m in ["영업시간", "전화번호", "편의", "정보 더보기"]
                if compact.find(m, index + 1) != -1
            ]
            end = min(end_candidates) if end_candidates else index + 300
            return compact[index:end].strip()[:300]
    return ""


def clean_menu_name(raw: str) -> str:
    text = _compact_text(raw)
    if not text:
        return ""
    is_representative = False
    if "대표" in text:
        is_representative = True
        text = text.rsplit("대표", 1)[-1].strip()
    for word in MENU_NOISE_WORDS:
        text = text.replace(word, " ")
    text = re.sub(r"메뉴\s*항목과\s*가격.*$", "", text).strip()
    text = re.sub(r"[^\w가-힣&·,\s]+", " ", text)
    for marker in MENU_DESCRIPTION_MARKERS:
        idx = text.find(marker)
        if idx > 0:
            text = text[:idx].strip()
            break
    words = [w for w in text.split() if w and not re.fullmatch(r"[\d,]+", w)]
    if not words:
        return ""
    name = " ".join(words[:4]).strip()
    if len(name) > 40:
        name = name[:40]
    return f"[대표] {name}" if is_representative else name


def compute_total_reviews(visitor: str, blog: str = "") -> str:
    """전체리뷰 = 방문자리뷰 (블로그리뷰는 별도 카운트, 합산하지 않음)"""
    try:
        v = int((visitor or "0").replace(",", ""))
        return str(v) if v > 0 else ""
    except (ValueError, AttributeError):
        return ""


def compute_receipt_ratio(visitor: str, blog: str) -> str:
    """영수증리뷰비율 = (방문자리뷰 - 블로그리뷰) / 방문자리뷰 × 100 (소수점 1자리)
    - 블로그리뷰가 0이면 100.0
    - visitor_review_count가 0이면 0 (0으로 나누기 방지)
    """
    try:
        v = int((visitor or "0").replace(",", ""))
        b = int((blog or "0").replace(",", ""))
        if v == 0:
            return "0"
        ratio = (v - b) / v * 100
        return str(round(ratio, 1))
    except (ValueError, AttributeError):
        return ""


def _extract_parking(text: str) -> str:
    """주차 정보 추출. '주차불가' / '주차가능' / 'Y'(언급만 있고 구분 불명) 반환"""
    compact = _compact_text(text)
    if re.search(r"주차\s*불가", compact):
        return "주차불가"
    if re.search(r"주차\s*가능|주차장?\s*(?:있|보유)|발렛\s*파킹", compact):
        return "주차가능"
    if "주차" in compact:
        return "Y"
    return ""


def _extract_keywords_from_html(html: str) -> str:
    """HTML <script> JSON에서 점주 대표 키워드 추출 (최대 5개, 쉼표 구분 반환)"""
    if not html:
        return ""
    for pattern in [
        r'"representKeywords"\s*:\s*\[([^\]]{1,500})\]',
        r'"keywords"\s*:\s*\[([^\]]{1,500})\]',
        r'"tags"\s*:\s*\[([^\]]{1,500})\]',
    ]:
        m = re.search(pattern, html)
        if m:
            block = m.group(1)
            names = re.findall(r'"name"\s*:\s*"([^"]{1,30})"', block)
            if not names:
                names = re.findall(r'"([가-힣a-zA-Z0-9 ]{1,20})"', block)
            names = [n.strip() for n in names if n.strip()][:5]
            if names:
                return ", ".join(names)
    return ""


def extract_menu_items(text: str) -> str:
    compact = _compact_text(text)
    if not compact or not PRICE_PATTERN.search(compact):
        return ""
    items = []
    previous_end = 0
    for match in PRICE_PATTERN.finditer(compact):
        raw_name = compact[previous_end: match.start()].strip()
        if len(raw_name) > 120:
            raw_name = raw_name[-120:]
        name = clean_menu_name(raw_name)
        price = match.group(0).strip()
        if name:
            items.append(f"{name} - {price}")
        previous_end = match.end()
        if len(items) >= 30:
            break
    return " | ".join(items)[:2000]


# ── Playwright 헬퍼 (async) ──────────────────────────────────────────────────

def _find_entry_frame(page):
    """entryIframe 탐색 (page.frames 는 동기 프로퍼티)."""
    for frame in page.frames:
        if (
            "pcmap.place.naver.com" in frame.url
            and ("/restaurant/" in frame.url or "/place/" in frame.url)
        ):
            return frame
    for frame in page.frames:
        if frame.name == "entryIframe":
            return frame
    return None


async def _collect_button_link_text(frame) -> str:
    texts = await frame.locator("button, a").evaluate_all(
        """els => els
            .map(el => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim())
            .filter(Boolean)
            .join('\\n')
        """
    )
    return texts or ""


async def _collect_image_alts(frame) -> str:
    alts = await frame.locator("img[alt]").evaluate_all(
        """els => els
            .map(el => (el.getAttribute('alt') || '').replace(/\\s+/g, ' ').trim())
            .filter(Boolean)
            .join('\\n')
        """
    )
    return alts or ""


async def _click_menu_tab(frame) -> bool:
    candidates = [
        lambda: frame.get_by_role("tab", name="메뉴"),
        lambda: frame.get_by_role("link", name="메뉴"),
        lambda: frame.get_by_role("button", name="메뉴"),
        lambda: frame.locator("a, button").filter(has_text="메뉴"),
        lambda: frame.get_by_text("메뉴", exact=True),
    ]
    for factory in candidates:
        try:
            candidate = factory()
            if await candidate.count() == 0:
                continue
            await candidate.first.click(timeout=5000)
            return True
        except Exception as exc:
            print(f"[메뉴탭] 시도 실패: {exc}")
            continue
    return False


async def _extract_menu_list_from_frame(page, frame) -> str:
    try:
        if not await _click_menu_tab(frame):
            return ""
        await page.wait_for_timeout(3000)
        menu_frame = _find_entry_frame(page)
        if menu_frame is None:
            print("[메뉴] entryIframe 탐색 실패 (메뉴 탭 클릭 후)")
            return ""
        await menu_frame.locator("body").evaluate("el => el.scrollBy(0, 800)")
        await page.wait_for_timeout(1000)
        body_text = await menu_frame.locator("body").inner_text(timeout=5000)
        if "/menu" not in menu_frame.url and not PRICE_PATTERN.search(body_text):
            return ""
        return extract_menu_items(body_text)
    except Exception as exc:
        print(f"[메뉴] 추출 실패: {exc}")
        return ""


# ── GraphQL 인터셉트 보강 (place-revum/crawler/graphql_interceptor.py 참조) ──

def _deep_find_gql(obj, key: str, depth: int = 0):
    """GraphQL 응답 딕셔너리 심층 키 탐색 (최대 6단계)"""
    if depth > 6:
        return None
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _deep_find_gql(v, key, depth + 1)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _deep_find_gql(item, key, depth + 1)
            if found is not None:
                return found
    return None


def _extract_gql_item(data: dict, out: dict):
    """단일 GraphQL data 항목에서 good_point_votes, feature_mentions, menu_mentions,
    visitor_review_total 추출. rating/save_count/reply_rate 는 수집 대상 제외."""
    if not isinstance(data, dict):
        return

    # visitorReviewStats → good_point_votes, feature_mentions, menu_mentions
    stats = _deep_find_gql(data, "visitorReviewStats")
    if stats is not None and isinstance(stats, dict):
        pkc = stats.get("positiveKeywordCount")
        if isinstance(pkc, int) and pkc > 0:
            out.setdefault("good_point_votes", str(pkc))
        kw_list = stats.get("keywordList") or []
        if isinstance(kw_list, list) and kw_list:
            kw_total = sum(item.get("count", 0) for item in kw_list if isinstance(item, dict))
            if kw_total > 0:
                out.setdefault("feature_mentions", str(kw_total))
        mn_list = stats.get("menuList") or []
        if isinstance(mn_list, list) and mn_list:
            mn_total = sum(item.get("count", 0) for item in mn_list if isinstance(item, dict))
            if mn_total > 0:
                out.setdefault("menu_mentions", str(mn_total))

    # visitorReviews.total → visitor_review_total (방문자 전용 카운트)
    vr = _deep_find_gql(data, "visitorReviews")
    if vr is not None and isinstance(vr, dict):
        if vr.get("total"):
            out.setdefault("visitor_review_total", str(vr["total"]))


def _parse_gql_extras(gql_responses: list) -> dict:
    """GraphQL 응답 목록 → 보강 필드 반환
    visitor_review_total, good_point_votes, feature_mentions, menu_mentions
    (rating / save_count / reply_rate / receipt_review_ratio 는 수집 대상 제외)
    """
    out: dict = {}

    for resp in gql_responses:
        if isinstance(resp, list):
            for item in resp:
                if isinstance(item, dict) and "data" in item:
                    _extract_gql_item(item["data"], out)
        elif isinstance(resp, dict):
            _extract_gql_item(resp, out)

    return out


# ── 메인 수집 함수 ────────────────────────────────────────────────────────────

async def crawl_place_by_id(place_id: str) -> dict | None:
    """
    place_id 로 네이버 플레이스 정보를 수집한다.
    성공: 원본 key 딕셔너리 반환 (PLACE_FIELDS 27개)
    실패: None 반환
    """
    url = _PLACE_URL.format(place_id=place_id)
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

                # GraphQL 응답 인터셉트 (place-revum/crawler/graphql_interceptor.py 참조)
                gql_responses: list = []

                async def _gql_handler(response):
                    if "graphql" in response.url and "naver.com" in response.url:
                        try:
                            gql_responses.append(await response.json())
                        except Exception:
                            pass

                page.on("response", _gql_handler)

                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                except PlaywrightTimeoutError:
                    print(f"[오류] 페이지 로드 타임아웃: {place_id!r}")
                    return None

                await page.wait_for_timeout(5000)

                try:
                    await page.wait_for_selector("iframe#entryIframe", timeout=12_000)
                except PlaywrightTimeoutError:
                    print(f"[오류] entryIframe 로드 타임아웃: {place_id!r}")
                    return None

                entry_frame = _find_entry_frame(page)
                if entry_frame is None:
                    print(f"[오류] entryIframe 탐색 실패: {place_id!r}")
                    return None

                try:
                    body_text = await entry_frame.locator("body").inner_text(timeout=5000)
                except PlaywrightTimeoutError:
                    print(f"[오류] body 텍스트 추출 타임아웃: {place_id!r}")
                    return None

                # HTML 전체 스캔 (place-revum/crawler naver_place.py _extract_from_html 참조)
                # <script> 태그 내 임베드 JSON 포함 — 봇 차단 시에도 데이터 보존됨
                try:
                    html_content = await entry_frame.content()
                except Exception:
                    html_content = ""

                button_link_text = await _collect_button_link_text(entry_frame)
                image_alt_text = await _collect_image_alts(entry_frame)
                img_count = await entry_frame.locator("img").count()
                combined_text = "\n".join([body_text, button_link_text, image_alt_text])

                h1_text = ""
                h1 = entry_frame.locator("h1").first
                if await h1.count():
                    try:
                        h1_text = (await h1.inner_text(timeout=3000)).strip()
                    except PlaywrightTimeoutError:
                        pass
                place_name = h1_text or _extract_place_name_from_text(body_text)

                result = {field: "" for field in PLACE_FIELDS}
                result["place_name"] = place_name
                result["category"] = _extract_category(body_text, place_name)
                result["lot_address"] = _extract_address(body_text)
                result["phone"] = _extract_phone(body_text)
                result["business_hours"] = extract_business_hours(body_text)
                result["last_order"] = extract_last_order(body_text)
                result["break_time"] = extract_break_time(body_text)
                result["closed_days"] = extract_closed_days(body_text)
                result["visitor_review_count"] = _extract_review_count(body_text, "방문자")
                result["blog_review_count"] = _extract_review_count(body_text, "블로그")
                result["parking"] = _extract_parking(body_text)
                result["takeout"] = extract_yes_no_keyword(body_text, "포장")
                result["facilities"] = extract_facilities(body_text)
                result["reservation_active"] = extract_yes_no_keyword(combined_text, "예약")
                result["coupon_active"] = extract_yes_no_keyword(combined_text, "쿠폰")
                result["photo_count"] = extract_photo_count(body_text, img_count)
                result["directions"] = extract_directions(body_text)
                result["total_reviews"] = compute_total_reviews(
                    result["visitor_review_count"],
                )
                # 스마트콜: 전화번호가 0507- 로 시작하면 Y, 아니면 N
                if result["phone"]:
                    result["smartcall_active"] = "Y" if result["phone"].startswith("0507-") else "N"

                # ── HTML 임베드 JSON 보강 (place-revum/crawler naver_place.py 참조) ──────
                # 봇 차단·DOM 패턴 불일치 시 <script> 태그 JSON에서 핵심 필드 추출
                if html_content:
                    if not result["lot_address"]:
                        for _p in [r'"roadAddress"\s*:\s*"([^"]{5,100})"',
                                   r'"address"\s*:\s*"([^"]{5,100})"']:
                            _m = re.search(_p, html_content)
                            if _m:
                                result["lot_address"] = _m.group(1)
                                break

                    if not result["phone"]:
                        for _p in [r'"tel"\s*:\s*"([0-9][0-9\-]{6,14})"',
                                   r'"phone"\s*:\s*"([0-9][0-9\-]{6,14})"']:
                            _m = re.search(_p, html_content)
                            if _m:
                                result["phone"] = _m.group(1)
                                # 스마트콜 재판단 (HTML에서 전화번호 획득한 경우)
                                result["smartcall_active"] = "Y" if result["phone"].startswith("0507-") else "N"
                                break

                    if not result["visitor_review_count"]:
                        _m = re.search(r'"visitorReviewCount"\s*:\s*(\d{2,})', html_content)
                        if _m and int(_m.group(1)) > 0:
                            result["visitor_review_count"] = _m.group(1)

                    if not result["category"]:
                        for _cat_p in [
                            r'"category"\s*:\s*"([가-힣][가-힣a-zA-Z&·,\s/]{0,28})"',
                            r'"categoryName"\s*:\s*"([가-힣][가-힣a-zA-Z&·,\s/]{0,28})"',
                            r'"businessCategory"\s*:\s*"([가-힣][가-힣a-zA-Z&·,\s/]{0,28})"',
                        ]:
                            _m = re.search(_cat_p, html_content)
                            if _m:
                                result["category"] = _m.group(1).strip()
                                break

                    if not result["blog_review_count"]:
                        _m = re.search(r'"blogReviewCount"\s*:\s*(\d+)', html_content)
                        if _m and int(_m.group(1)) > 0:
                            result["blog_review_count"] = _m.group(1)

                    if not result["photo_count"]:
                        for _pp in [r'"photoCount"\s*:\s*(\d+)',
                                    r'"totalPhotoCount"\s*:\s*(\d+)',
                                    r'"photoCnt"\s*:\s*(\d+)']:
                            _m = re.search(_pp, html_content)
                            if _m and int(_m.group(1)) > 0:
                                result["photo_count"] = _m.group(1)
                                break

                    if not result["keywords"]:
                        result["keywords"] = _extract_keywords_from_html(html_content)

                # ── GQL 탭 이동 (메뉴 추출 전 실행 — entry_frame 직접 사용) ─────────────
                # _find_entry_frame 재호출 없이 entry_frame 직접 사용 (메뉴 클릭 후 frame 상태 변경 방지)
                # 홈: visitorReviewStats → good_point_votes, feature_mentions, menu_mentions
                # 리뷰: visitorReviews.total → visitor_review_total (방문자 전용 카운트 폴백)
                try:
                    _m_pt = re.search(r"pcmap\.place\.naver\.com/([a-z]+)/", entry_frame.url)
                    _ptype = _m_pt.group(1) if _m_pt else "restaurant"
                    _gql_base = f"https://pcmap.place.naver.com/{_ptype}/{place_id}"
                    await entry_frame.goto(f"{_gql_base}/home", wait_until="networkidle", timeout=15_000)
                    await page.wait_for_timeout(1500)
                    await entry_frame.goto(f"{_gql_base}/review", wait_until="networkidle", timeout=20_000)
                    await page.wait_for_timeout(3000)
                    # GQL 미수신 시 1회 재시도 (네트워크 지연 대응)
                    if not gql_responses:
                        await entry_frame.goto(f"{_gql_base}/home", wait_until="networkidle", timeout=15_000)
                        await page.wait_for_timeout(1000)
                        await entry_frame.goto(f"{_gql_base}/review", wait_until="networkidle", timeout=20_000)
                        await page.wait_for_timeout(3000)
                except Exception as _e:
                    print(f"[GQL 탭 이동 실패] {type(_e).__name__}: {str(_e)[:100]}")

                # GQL 보강 필드 추출 및 병합
                gql_extras = _parse_gql_extras(gql_responses)
                # visitor_review_count GQL 폴백 (DOM 미추출 시)
                if not result["visitor_review_count"]:
                    result["visitor_review_count"] = gql_extras.get("visitor_review_total", "")
                # total_reviews = visitor_review_count 동기화 (GQL 폴백 포함)
                if result["visitor_review_count"]:
                    result["total_reviews"] = result["visitor_review_count"]
                elif not result["total_reviews"]:
                    result["total_reviews"] = gql_extras.get("visitor_review_total", "")
                result["good_point_votes"] = gql_extras.get("good_point_votes", "")
                result["feature_mentions"] = gql_extras.get("feature_mentions", "")
                result["menu_mentions"] = gql_extras.get("menu_mentions", "")

                # 영수증리뷰비율 계산: (방문자리뷰 - 블로그리뷰) / 방문자리뷰 × 100
                result["receipt_review_ratio"] = compute_receipt_ratio(
                    result["visitor_review_count"],
                    result["blog_review_count"],
                )

                # 메뉴 추출 (GQL 탭 이동 후 실행 — frame이 /review 상태, 메뉴 탭 클릭 가능)
                result["menu_list"] = await _extract_menu_list_from_frame(page, entry_frame)

                # 실제 점포 데이터가 없으면 실패로 처리
                has_data = any([
                    result["lot_address"],
                    result["phone"],
                    result["visitor_review_count"],
                    result["blog_review_count"],
                    result["menu_list"],
                    result["total_reviews"],
                ])
                if not has_data:
                    print(f"[검색 실패] 점포 데이터 없음 (미등록 place_id): {place_id!r}")
                    return None

                return result
            finally:
                await browser.close()
    except Exception as exc:
        print(f"[오류] 크롤링 실패: {place_id!r}: {exc}")
        return None


if __name__ == "__main__":
    async def _main():
        print("=== 테스트 1: 실제 점포 (스타벅스 역삼점) ===")
        result = await crawl_place_by_id("33647195")
        if result is not None:
            non_empty = sum(1 for v in result.values() if v)
            print(f"수집된 필드 수 (전체): {len(result)}")
            print(f"수집된 필드 수 (비어있지 않은 값): {non_empty}")
            print("--- 전체 key/value ---")
            for k, v in result.items():
                print(f"  {k}: {str(v)[:80]}")
        else:
            print("result: None")

        print("\n=== 테스트 2: 존재하지 않는 place_id ===")
        result2 = await crawl_place_by_id("9999999999")
        print(f"place_id: {result2}")

    asyncio.run(_main())
