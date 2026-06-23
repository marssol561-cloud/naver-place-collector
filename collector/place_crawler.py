import asyncio
import json
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
    "parking_description",
    "parking_is_free",
    "takeout",
    "facilities",
    "menu_list",
    "menu_image_registered",         # Apollo State Menu:{place_id}_{N}.images non-empty (S2)
    "keywords",
    "description",
    "ai_summary",
    "directions",
    "visitor_review_count",
    "blog_review_count",
    "naedon_blog_review_count",
    "naedon_blog_latest_date",
    "reservation_active",
    "naver_pay_active",
    "coupon_active",
    "talktalk_active",
    "naver_features",
    "phone_reservation_enabled",
    "smartcall_active",
    "latest_news_date",
    "total_reviews",           # 방문자리뷰 = 전체리뷰 (visitor_review_count 그대로)
    "receipt_review_ratio",    # (방문자리뷰 - 블로그리뷰) / 방문자리뷰 × 100
    "good_point_votes",        # GQL visitorReviewStats.analysis.votedKeyword.details → [{displayName,count}]
    "feature_mentions",        # GQL visitorReviewStats.analysis.themes[].count 합산 (S4: keywordList→themes)
    "feature_themes",          # GQL visitorReviewStats.analysis.themes → [{label,count}] (S4 신설)
    "menu_mentions",           # GQL visitorReviewStats.analysis.menus → [{label,count}]
    "rating",                  # HTML avgRating / GQL visitorReviewStats.review.avgRating
    "reply_rate",              # GQL visitorReviews.items reply.body 존재 비율 (0.00~1.00, 리뷰 0건→None)
]

ADDRESS_PREFIXES = (
    "서울", "경기", "인천", "부산", "대구", "광주", "대전", "울산", "세종",
    "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
)

# ── Render-completeness guard (S2-FIX, 2026-06-06) ───────────────────────────
# S2-VERIFY measured: incomplete body_text = 128 chars (2/3 runs),
# complete body_text = 1,673+ chars (Phase-1 confirmed).
# 2026-06-23 fix: 500→200. Compact stores (clothing/bar with few sections) render
# valid content at 302-398 chars — well above the 128-char failure pattern but
# below the original 500 threshold. 200 safely separates failures (≤128) from
# valid compact pages (≥302). Gap: 128→302, threshold at 200 gives 72-char margin.
BODY_COMPLETENESS_THRESHOLD = 200
CRAWL_INCOMPLETE = "CRAWL_INCOMPLETE"


def _is_render_complete(body_text: str) -> bool:
    """True if body_text length indicates a fully-rendered entryIframe."""
    return len(body_text) >= BODY_COMPLETENESS_THRESHOLD


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


def _extract_blog_review_count_from_html(html: str) -> str:
    """Apollo State 블로그 리뷰 수 추출. 신규 키 cafeBlogReviewsTotal 우선, 레거시 blogReviewCount 폴백. 0/미발견 시 ''."""
    if not html:
        return ""
    for pat in (r'"cafeBlogReviewsTotal"\s*:\s*(\d+)', r'"blogReviewCount"\s*:\s*(\d+)'):
        _m = re.search(pat, html)
        if _m and int(_m.group(1)) > 0:
            return _m.group(1)
    return ""


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
    stop_markers = ["라스트오더", "전화번호", "홈페이지", "인스타그램", "편의", "AI 브리핑", "펼쳐보기", "메뉴"]
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
    context = _context_after_marker(text, ["정기휴무", "연중무휴", "휴무", "매주"], 120)
    if not context:
        return ""
    stop_markers = ["접기", "메뉴", "전화번호", "홈페이지"]
    cut_points = [context.find(m) for m in stop_markers if context.find(m) > 0]
    if cut_points:
        context = context[: min(cut_points)].strip()
    return context[:120]


def _extract_business_hours_from_expanded(expanded_text: str) -> str:
    """펼쳐보기 클릭 후 요일별 영업시간 구조화 추출.
    요일+시간 패턴을 '월 HH:MM-HH:MM | 화 ... | 일 정기휴무(매주 일요일)' 형태로 반환.
    """
    DAYS_ORDER = ['월', '화', '수', '목', '금', '토', '일']
    compact = _compact_text(expanded_text)
    idx_h = compact.find('영업시간')
    if idx_h == -1:
        return ""
    idx_end = compact.find('접기', idx_h)
    section = compact[idx_h: idx_end] if idx_end != -1 else compact[idx_h: idx_h + 500]
    day_pat = re.compile(
        r'(매일|월|화|수|목|금|토|일)\s+(\d{1,2}:\d{2}\s*[-–]\s*\d{1,2}:\d{2}|정기휴무(?:\s*\([^)]+\))?)'
    )
    matches = day_pat.findall(section)
    if not matches:
        return ""
    day_order = {d: i for i, d in enumerate(DAYS_ORDER)}
    matches.sort(key=lambda x: day_order.get(x[0], 99))
    parts = []
    for day, hours in matches:
        normalized = re.sub(r'\s*[-–]\s*', '-', hours.strip())
        normalized = re.sub(r'\s+\(', '(', normalized)
        parts.append(f"{day} {normalized}")
    return " | ".join(parts)


def _extract_closed_days_from_expanded(expanded_text: str) -> str:
    """펼쳐보기 클릭 후 정기휴무 정보 추출 — 괄호 내 텍스트(매주 일요일)만 반환."""
    compact = _compact_text(expanded_text)
    m = re.search(r'정기휴무\s*\(([^)]+)\)', compact)
    if m:
        return m.group(1).strip()
    if '연중무휴' in compact:
        return '연중무휴'
    return ""


def _extract_description_from_info(info_text: str) -> str:
    """정보탭 body_text에서 소개글 추출 (소개 섹션 헤딩 이후 ~ 펼쳐보기 이전)."""
    for marker in ['\n소개\n', '소개\n']:
        idx = info_text.find(marker)
        if idx != -1:
            content = info_text[idx + len(marker):]
            stop_markers = ['펼쳐보기', '알고 계신', '정보 수정', '이용약관']
            cut_points = [content.find(m) for m in stop_markers if content.find(m) > 0]
            if cut_points:
                content = content[:min(cut_points)]
            return content.strip()
    return ""


_CATEGORY_NOISE = ("페이지 닫기", "더보기", "이전 페이지")


def _extract_category(text: str, place_name: str) -> str:
    """place_name 바로 뒤 업종을 추출한다.

    nav 영역(이전 페이지 > place_name > 페이지 닫기 > 더보기)이 먼저 등장하여
    regex가 잘못된 구간을 캡처하는 것을 막기 위해 '더보기' 이후 구간만 검색한다.
    """
    if not place_name:
        return ""
    compact = _compact_text(text)
    # nav 영역 이후 구간만 검색 (더보기 이후)
    moreidx = compact.find("더보기")
    search_text = compact[moreidx:] if moreidx != -1 else compact
    match = re.search(
        rf"{re.escape(place_name)}\s*([가-힣A-Za-z,&·\s]+?)\s*알림받기",
        search_text,
    )
    if not match:
        return ""
    category = match.group(1).strip()
    if len(category) > 30:
        return ""
    if place_name in category or any(w in category for w in _CATEGORY_NOISE):
        return ""
    return category


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
    """전체리뷰 = 방문자리뷰 + 블로그리뷰 (방문자/블로그는 별도 카테고리, 합산)
    동암상회 예시: 490 + 185 = 675
    """
    try:
        v = int((visitor or "0").replace(",", ""))
        b = int((blog or "0").replace(",", ""))
        total = v + b
        return str(total) if total > 0 else ""
    except (ValueError, AttributeError):
        return ""


def compute_receipt_ratio(visitor: str, blog: str) -> str:
    """영수증리뷰비율 = visitor / (visitor + blog) × 100 (소수점 1자리)
    방문자 리뷰와 블로그 리뷰는 별도 카테고리. 포함 관계가 아님.
    동암상회 예시: 490 / (490 + 185) × 100 = 72.6%
    - visitor + blog = 0 이면 0 반환 (0 나누기 방지)
    """
    try:
        v = int((visitor or "0").replace(",", ""))
        b = int((blog or "0").replace(",", ""))
        total = v + b
        if total == 0:
            return "0"
        ratio = v / total * 100
        return str(round(ratio, 1))
    except (ValueError, AttributeError):
        return ""


def _extract_total_review_from_body(body_text: str) -> str:
    """body_text 상단 '카테고리리뷰 N' 에서 네이버 UI 표시 전체 리뷰 수 추출.
    '육류,고기요리리뷰 344' → '344'. 방문자/블로그 prefix 제외. 첫 200자 내 탐색."""
    head = body_text[:200]
    for m in re.finditer(r'([가-힣A-Za-z0-9,&·/]+)리뷰\s*([\d,]+)', head):
        if "방문자" in m.group(1) or "블로그" in m.group(1):
            continue
        val = m.group(2).replace(",", "")
        if val.isdigit() and int(val) > 0:
            return val
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


_PARKING_CODE_MAP = {
    "no_parking": "주차불가",
    "parking_available": "주차가능",
    "free_parking": "무료주차",
    "paid_parking": "유료주차",
    "accessible_parking": "장애인주차가능",
    "valet_parking": "발렛가능",
    "valet_parking_available": "발렛가능",
    "free_parking_available": "무료주차가능",
    "paid_parking_available": "유료주차가능",
}


def _extract_ai_summary_from_html(html: str) -> str:
    """Home Apollo State PlaceDetailBase.microReviews[0] — AI 요약 한 줄 텍스트.

    Apollo State JSON uses Unicode escapes (e.g. \\u002F for '/'); json.loads decodes
    them properly. Falls back to raw regex extraction if JSON parse fails.
    """
    if not html:
        return ""
    m = re.search(r'"microReviews"\s*:\s*\[([^\]]{1,500})\]', html)
    if not m:
        return ""
    try:
        items = json.loads(f'[{m.group(1)}]')
        return str(items[0]).strip() if items else ""
    except (json.JSONDecodeError, ValueError, IndexError):
        first = re.search(r'"([^"]{1,200})"', m.group(1))
        return first.group(1).strip() if first else ""


def _extract_conveniences_from_html(html: str, exclude: list | None = None) -> list:
    """Home Apollo State PlaceDetailBase.conveniences — 편의시설 레이블 배열.

    Apollo State JSON uses Unicode escapes (e.g. \\u002F for '/'); wrapping the
    captured bracket content in json.loads decodes all escapes correctly.
    Falls back to raw regex if JSON parse fails.

    NOTE: conveniences items are plain label strings — no fee/paid attribute is
    present here. '콜키지 가능(유료)' style subtags live in the InformationFacilities
    individual item objects (information tab Apollo State), not in conveniences.
    """
    if not html:
        return []
    m = re.search(r'"conveniences"\s*:\s*\[([^\]]{1,2000})\]', html)
    if not m:
        return []
    try:
        items = json.loads(f'[{m.group(1)}]')
    except (json.JSONDecodeError, ValueError):
        items = re.findall(r'"([^"]{1,50})"', m.group(1))
    items = [str(item) for item in items if isinstance(item, str)]
    if exclude:
        items = [item for item in items if item not in exclude]
    return items


def _extract_parking_from_html(html: str) -> str:
    """html_content Apollo State의 parkingInfo 구조에서 주차 정보 추출.
    body_text에서 주차 텍스트를 찾지 못했을 때 폴백으로 호출한다.
    - description: 점주가 직접 입력한 주차 안내 텍스트
    - basicParking: 코드값 (no_parking → 주차불가 등)
    - valetParking: 코드값 (발렛 여부)
    """
    if not html:
        return ""
    m = re.search(r'"parkingInfo"\s*:\s*\{([^}]{0,600})\}', html)
    if not m:
        return ""
    block = m.group(1)

    # description 우선 (직접 텍스트)
    desc_m = re.search(r'"description"\s*:\s*"([^"]{1,100})"', block)
    if desc_m and desc_m.group(1).strip():
        return desc_m.group(1).strip()

    # basicParking 코드 → 한국어
    basic_m = re.search(r'"basicParking"\s*:\s*"([^"]+)"', block)
    if basic_m:
        code = basic_m.group(1).strip()
        return _PARKING_CODE_MAP.get(code, code)

    # valetParking 코드 → 한국어
    valet_m = re.search(r'"valetParking"\s*:\s*"([^"]+)"', block)
    if valet_m:
        code = valet_m.group(1).strip()
        return _PARKING_CODE_MAP.get(code, f"발렛({code})")

    return ""


def _extract_parking_info_from_html(html: str) -> dict:
    """Extract nested parkingInfo block from /information tab Apollo State.

    Returns {description, is_free} where is_free is "Y"/"N"/"".
    Uses depth-tracking to handle the nested basicParking sub-object, which breaks
    the flat-block regex in _extract_parking_from_html.

    어반정원 example:
      description = "평일: 반달로14번길 월미공원 후문 무료주차 가능\\n주말: 공영주차장 이용"
      is_free     = "Y"  (basicParking.isFree: true)
    """
    if not html:
        return {}
    m = re.search(r'"parkingInfo"\s*:\s*\{', html)
    if not m:
        return {}
    start = m.end() - 1  # points at '{'
    depth, end_pos = 0, start
    for i in range(start, min(start + 5000, len(html))):
        if html[i] == '{':
            depth += 1
        elif html[i] == '}':
            depth -= 1
            if depth == 0:
                end_pos = i + 1
                break
    block_str = html[start:end_pos]
    try:
        block = json.loads(block_str)
    except (json.JSONDecodeError, ValueError):
        return {}

    out: dict = {}
    desc = block.get("description") or ""
    if isinstance(desc, str) and desc.strip():
        out["description"] = desc.strip()

    basic = block.get("basicParking") or {}
    is_free = basic.get("isFree") if isinstance(basic, dict) else None
    if is_free is True:
        out["is_free"] = "Y"
    elif is_free is False:
        out["is_free"] = "N"

    return out


def _fac_norm_key(s: str) -> str:
    """Normalize a facility label for dedup: strip trailing fee/note parenthetical, then collapse whitespace.
    "무선인터넷"=="무선 인터넷"; "콜키지 가능 (유료)"→"콜키지가능" matches "콜키지 가능"→"콜키지가능"."""
    return re.sub(r'\s+', '', re.sub(r'\s*\([^)]*\)\s*$', '', s))


def extract_facility_services(html: str) -> list:
    """Extract facility labels from the rendered '편의시설 및 서비스' section.

    Takes rendered body HTML from entry_frame.evaluate("document.body.outerHTML").
    Finds the section by the stable CSS class 'place_section_header_title', then
    extracts <li> text items scoped to that section only — excluding adjacent sections
    like '결제수단'/'간편결제' that share the same parent div.

    DOM structure (Naver pcmap, as of 2026-06-13):
      <div class="place_section ...">
        <h2 class="place_section_header">
          <div class="place_section_header_title">편의시설 및 서비스<em ...>N</em></div>
        </h2>
        <div class="place_section_content">
          <ul>
            <li><svg aria-hidden="true">...</svg><div>LABEL</div>[<em>fee_tag</em>]</li>
            ...
          </ul>
        </div>
      </div>

    SVG elements carry no text (aria-hidden, path-only).
    Fee-tagged items (e.g. "콜키지 가능 (유료)") have the fee rendered as a separate
    sibling element — li.get_text(separator="\\n") splits them; they are rejoined as
    "label (fee_text)" to preserve the Apollo-State-compatible format.
    Returns labels in display order. Returns [] if section not found.
    """
    if not html:
        return []
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    soup = BeautifulSoup(html, "html.parser")

    target_section = None
    for div in soup.find_all("div", class_="place_section_header_title"):
        if "편의시설 및 서비스" in div.get_text():
            node = div.parent
            while node and getattr(node, "name", None) not in (None, "body", "[document]"):
                if "place_section" in (node.get("class") or []):
                    target_section = node
                    break
                node = node.parent
            break

    if not target_section:
        return []

    labels = []
    _has_korean = re.compile(r'[가-힣]')
    for li in target_section.find_all("li"):
        # Use "\n" separator so fee-tag sub-elements appear as separate chunks.
        parts = [p for p in li.get_text(separator="\n", strip=True).split("\n") if p]
        # Keep only parts that contain Korean — filters out icon/CSS artifact chars (e.g. "\").
        korean_parts = [p for p in parts if _has_korean.search(p)]
        if not korean_parts:
            continue
        if len(korean_parts) == 1:
            labels.append(korean_parts[0])
        else:
            # First Korean part = main label, second = fee sub-label (e.g. "유료").
            labels.append(f"{korean_parts[0]} ({korean_parts[1]})")
    return labels


def _extract_facilities_from_info_html(html: str) -> list:
    """Extract facility labels from /information tab InformationFacilities objects.

    Unlike the home-tab conveniences array (plain labels), InformationFacilities.name
    includes fee subtags where applicable (e.g., '콜키지 가능 (유료)').
    Confirmed via 금도야지 루원시티본점 (place_id 1256925027): id=201 name='콜키지 가능 (유료)'.

    RC-2 (2026-06-06): numeric-ID filter (skips legacy string-keyed Apollo cache entries
    that partial/stale hydration can emit) + _fac_norm_key dedup (treats
    "무선인터넷"=="무선 인터넷"; prefers fee-tagged form, then longer form, then first seen).
    RC-2 rev (2026-06-06): camelCase string IDs added to pattern — accessibility entries
    (disabledFriendlyToilet, disabledFriendlyParking) use string IDs with all 4 fields
    intact and are not stale. Stale entries are filtered downstream by name presence check.

    Structure: {"__typename":"InformationFacilities","id":"201","name":"콜키지 가능 (유료)","i18nName":"..."}
    No separate fee attribute — fee info is embedded in the name string.
    """
    if not html:
        return []
    names_raw = []
    for m in re.finditer(r'"InformationFacilities:(\d+|[a-zA-Z][a-zA-Z0-9]*)"\s*:\s*\{', html):
        brace_start = m.end() - 1
        depth, end_pos = 0, brace_start
        for i in range(brace_start, min(brace_start + 1000, len(html))):
            if html[i] == '{':
                depth += 1
            elif html[i] == '}':
                depth -= 1
                if depth == 0:
                    end_pos = i + 1
                    break
        block_str = html[brace_start:end_pos]
        try:
            block = json.loads(block_str)
            name = block.get("name", "")
            if isinstance(name, str) and name.strip():
                names_raw.append(name.strip())
        except (json.JSONDecodeError, ValueError):
            nm = re.search(r'"name"\s*:\s*"([^"]{1,80})"', block_str)
            if nm:
                names_raw.append(nm.group(1).strip())
    seen: dict = {}
    for name in names_raw:
        nk = _fac_norm_key(name)
        if nk not in seen:
            seen[nk] = name
        else:
            existing = seen[nk]
            if '(' in name and '(' not in existing:
                seen[nk] = name  # fee-tagged beats plain
            elif '(' not in name and '(' not in existing and len(name) > len(existing):
                seen[nk] = name  # longer (spaced) form beats collapsed
    return list(seen.values())


# ── S3: Naver platform feature detection (DECLARATIVE) ───────────────────────

def _check_naver_reservation(html: str) -> bool:
    """Non-null naverBookingUrl in PlaceDetailNaverBooking (Naver table reservation).
    hasNaverReservation is always false even for active stores; naverBookingUrl is store-specific.
    Known miss: 호시카츠 서울역 본점 (1587635202) — bookingBusinessId=null, no naverBookingUrl.
    Uses non-Naver-Booking reservation (no Apollo State signal). Deferred per S3 Final."""
    return bool(re.search(r'"naverBookingUrl"\s*:\s*"[^"]+"', html))


def _naverorder_block(html: str) -> str:
    """Depth-extract naverOrder {...} block; returns '' if absent or null."""
    m = re.search(r'"naverOrder"\s*:\s*\{', html)
    if not m:
        return ""
    start = m.end() - 1
    depth, end_pos = 0, start
    for i in range(start, min(start + 2000, len(html))):
        if html[i] == '{':
            depth += 1
        elif html[i] == '}':
            depth -= 1
            if depth == 0:
                end_pos = i + 1
                break
    return html[start:end_pos]


def _check_naver_order(html: str) -> bool:
    """naverOrder has at least one active order type (isPickup/isTableOrder/isPreOrder = true).
    naverOrder exists for many stores with empty items; must confirm actual activity."""
    blk = _naverorder_block(html)
    return bool(re.search(r'"(?:isPickup|isTableOrder|isPreOrder)"\s*:\s*true', blk))


def _check_naver_delivery(html: str) -> bool:
    """naverOrder.isDelivery == true (delivery via Naver Order system)."""
    blk = _naverorder_block(html)
    return bool(re.search(r'"isDelivery"\s*:\s*true', blk))


def _check_talktalk(html: str) -> bool:
    """talktalkUrl is a non-null, non-empty string in Apollo State."""
    return bool(re.search(r'"talktalkUrl"\s*:\s*"[^"]+"', html))


# Declarative list — one entry per Naver feature. Add new entries here.
# 페이 (Naver Pay): npay Apollo object is a global page template, NOT store-specific.
# No reliable store-specific signal confirmed as of S3. Excluded until confirmed.
_NAVER_FEATURE_DETECTORS: list[tuple] = [
    ("예약", _check_naver_reservation),
    ("주문", _check_naver_order),
    ("배달", _check_naver_delivery),
    ("톡톡", _check_talktalk),
]


def _extract_naver_features(html: str) -> list[str]:
    """Return list of active Naver platform feature labels for the given home-tab HTML."""
    return [label for label, check in _NAVER_FEATURE_DETECTORS if check(html)]


def _extract_keywords_from_html(html: str) -> str:
    """HTML <script> JSON에서 점주 대표 키워드 추출 (최대 5개, 쉼표 구분 반환).
    탐색 순서:
      1. keywordList  — Apollo State informationTab.keywordList (단순 문자열 배열)
      2. representKeywords — 일부 플레이스 페이지 구조
      3. keywords / tags — 기타 폴백
    """
    if not html:
        return ""
    for pattern in [
        r'"keywordList"\s*:\s*\[([^\]]{1,500})\]',         # 실제 Apollo State 키
        r'"representKeywords"\s*:\s*\[([^\]]{1,500})\]',
        r'"keywords"\s*:\s*\[([^\]]{1,500})\]',
        r'"tags"\s*:\s*\[([^\]]{1,500})\]',
    ]:
        m = re.search(pattern, html)
        if m:
            block = m.group(1)
            # 객체 배열 ("name" 키 포함)
            names = re.findall(r'"name"\s*:\s*"([^"]{1,30})"', block)
            # 단순 문자열 배열 폴백 (keywordList 형식)
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
    _nav = list(re.finditer(r'홈\s*소식\s*메뉴\s*리뷰\s*사진\s*정보', compact))
    if _nav:
        previous_end = _nav[-1].end()
    for match in PRICE_PATTERN.finditer(compact, previous_end):
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
    # 1순위: /restaurant/ or /place/ URL 정확 매칭
    for frame in page.frames:
        if (
            "pcmap.place.naver.com" in frame.url
            and ("/restaurant/" in frame.url or "/place/" in frame.url)
        ):
            return frame
    # 2순위: pcmap.place.naver.com 포함 모든 프레임 (카테고리 path 무관)
    for frame in page.frames:
        if "pcmap.place.naver.com" in frame.url and frame.url not in ("", "about:blank"):
            return frame
    # 3순위: frame name 매칭
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


async def _extract_menu_list_from_frame(page, frame) -> tuple[str, str]:
    """Returns (menu_items_str, menu_frame_html). menu_html used for menu_image_registered (S2)."""
    try:
        if not await _click_menu_tab(frame):
            return "", ""
        await page.wait_for_timeout(3000)
        menu_frame = _find_entry_frame(page)
        if menu_frame is None:
            print("[메뉴] entryIframe 탐색 실패 (메뉴 탭 클릭 후)")
            return "", ""
        await menu_frame.locator("body").evaluate("el => el.scrollBy(0, 800)")
        await page.wait_for_timeout(1000)
        body_text = await menu_frame.locator("body").inner_text(timeout=5000)
        if "/menu" not in menu_frame.url and not PRICE_PATTERN.search(body_text):
            return "", ""
        try:
            menu_html = await menu_frame.content()
        except Exception:
            menu_html = ""
        return extract_menu_items(body_text), menu_html
    except Exception as exc:
        print(f"[메뉴] 추출 실패: {exc}")
        return "", ""


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
    visitor_review_total, rating_gql, reply_rate, category_gql 추출."""
    if not isinstance(data, dict):
        return

    # visitorReviewStats → good_point_votes, feature_mentions, menu_mentions, rating_gql
    stats = _deep_find_gql(data, "visitorReviewStats")
    if stats is not None and isinstance(stats, dict):
        _analysis = stats.get("analysis") or {}
        # good_point_votes: analysis.votedKeyword.details → [{"displayName":..,"count":..}]
        # GQL actual path confirmed 2026-06-05: visitorReviewStats.analysis.votedKeyword.details
        _vk_details = (_analysis.get("votedKeyword") or {}).get("details") or []
        if isinstance(_vk_details, list) and _vk_details:
            _gpv_arr = [
                {"displayName": it["displayName"], "count": it["count"]}
                for it in _vk_details
                if isinstance(it, dict)
                and isinstance(it.get("displayName"), str)
                and isinstance(it.get("count"), int)
            ]
            if _gpv_arr:
                out.setdefault("good_point_votes", json.dumps(_gpv_arr, ensure_ascii=False))
        # analysis.themes → feature_themes array + feature_mentions sum (S4)
        # Replaces keywordList aggregation (was mis-aggregating votedKeyword counts).
        _themes = _analysis.get("themes") or []
        if isinstance(_themes, list) and _themes:
            _th_arr = [
                {"label": it["label"], "count": it["count"]}
                for it in _themes
                if isinstance(it, dict)
                and isinstance(it.get("label"), str)
                and isinstance(it.get("count"), int)
            ]
            if _th_arr:
                out.setdefault("feature_themes", json.dumps(_th_arr, ensure_ascii=False))
                th_total = sum(t["count"] for t in _th_arr)
                if th_total > 0:
                    out.setdefault("feature_mentions", str(th_total))
        # menu_mentions: analysis.menus → [{"label":..,"count":..}]
        # GQL actual path confirmed 2026-06-05: visitorReviewStats.analysis.menus
        _mn_menus = _analysis.get("menus") or []
        if isinstance(_mn_menus, list) and _mn_menus:
            _mm_arr = [
                {"label": it["label"], "count": it["count"]}
                for it in _mn_menus
                if isinstance(it, dict)
                and isinstance(it.get("label"), str)
                and isinstance(it.get("count"), int)
            ]
            if _mm_arr:
                out.setdefault("menu_mentions", json.dumps(_mm_arr, ensure_ascii=False))
        # visitorReviewStats.review.avgRating → rating_gql (HTML 미추출 시 폴백)
        review_stat = stats.get("review") or {}
        if isinstance(review_stat, dict):
            avg = review_stat.get("avgRating")
            if avg is not None:
                try:
                    out.setdefault("rating_gql", str(float(avg)))
                except (TypeError, ValueError):
                    pass

    # visitorReviews → visitor_review_total, reply_rate
    vr = _deep_find_gql(data, "visitorReviews")
    if vr is not None and isinstance(vr, dict):
        if vr.get("total"):
            out.setdefault("visitor_review_total", str(vr["total"]))
        # reply_rate: items 배열 reply.body 존재 비율 (리뷰 0건 → None)
        items = vr.get("items")
        if isinstance(items, list):
            if items:
                replied = sum(
                    1 for r in items
                    if isinstance(r, dict)
                    and isinstance(r.get("reply"), dict)
                    and r["reply"].get("body")
                )
                out.setdefault("reply_rate", round(replied / len(items), 2))
            else:
                out.setdefault("reply_rate", None)

    # GQL category 탐색 (한국어 카테고리만 채택)
    cat = _deep_find_gql(data, "category")
    if isinstance(cat, str) and cat.strip() and re.search(r'[가-힣]', cat):
        out.setdefault("category_gql", cat.strip()[:30])


def _parse_gql_extras(gql_responses: list) -> dict:
    """GraphQL 응답 목록 → 보강 필드 반환
    visitor_review_total, good_point_votes, feature_themes, feature_mentions, menu_mentions,
    rating_gql, reply_rate, category_gql (save_count 수집 불가)
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


# ── Apollo State HTML 파싱 폴백 (GQL 미수신 시) ─────────────────────────────
# 홈 탭 Apollo State에서 good_point_votes / menu_mentions / feature_mentions 추출.
# GQL이 정상 동작하면 이 함수들은 호출되지 않는다.

def _extract_good_point_votes_from_html(html: str) -> str:
    """Apollo State votedKeyword.details → JSON 배열 문자열 반환.
    위치: VisitorReviewStatsResult:{place_id}.analysis.votedKeyword.details
    저장형태: '[{"displayName": "음식이 맛있어요", "count": 452}, ...]'

    GQL 기존 형태(positiveKeywordCount 단순 정수)와 다른 이유:
    Apollo State는 details 배열만 제공하며 totalCount와 구분되는 positiveKeywordCount를
    직접 노출하지 않음. 상세 배열이 더 완전한 데이터이므로 상세 형태 채택.

    구현 방식: depth-tracking으로 details 배열 끝을 탐색.
    "[^\\]]{0,8000}" 상한 방식은 항목이 많은 경우(30개 × ~350자 ≈ 10,500자) 실패하므로 사용하지 않음.
    """
    if not html:
        return ""
    # votedKeyword 위치 탐색
    vk_idx = html.find('"votedKeyword"')
    if vk_idx == -1:
        return ""
    # votedKeyword 이후 1500자 내에서 details 배열 시작([) 탐색
    search_window = html[vk_idx: vk_idx + 1500]
    det_m = re.search(r'"details"\s*:\s*\[', search_window)
    if not det_m:
        return ""
    # html 내 배열 내부 시작 위치 ([ 다음)
    arr_start = vk_idx + det_m.end()
    # depth-tracking으로 배열 끝(]) 탐색 (최대 50,000자)
    depth = 1
    pos = arr_start
    end = min(len(html), arr_start + 50_000)
    while pos < end and depth > 0:
        c = html[pos]
        if c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
        pos += 1
    block = html[arr_start: pos - 1]  # 닫는 ] 제외
    # displayName → count 순서
    items = re.findall(
        r'"displayName"\s*:\s*"([^"]+)"[^}]*?"count"\s*:\s*(\d+)',
        block,
        re.DOTALL,
    )
    if not items:
        # count → displayName 역순 대응
        rev = re.findall(
            r'"count"\s*:\s*(\d+)[^}]*?"displayName"\s*:\s*"([^"]+)"',
            block,
            re.DOTALL,
        )
        items = [(name, cnt) for cnt, name in rev]
    if not items:
        return ""
    return json.dumps(
        [{"displayName": name, "count": int(cnt)} for name, cnt in items],
        ensure_ascii=False,
    )


def _extract_menu_mentions_from_html(html: str) -> str:
    """Apollo State VisitorReviewStatsResult.analysis.menus → JSON 배열 문자열.
    저장형태: '[{"label": "고기", "count": 70}, ...]'

    주의: menus 키 중복 가능 → VisitorReviewStatsResult 블록 이후에서 탐색.
    GQL 기존 형태(menuList count 합산 정수)와 다른 이유:
    Apollo State menus 배열은 합산값이 아닌 개별 항목을 제공하므로 상세 형태 채택.
    """
    if not html:
        return ""
    vs_m = re.search(r'"VisitorReviewStatsResult:\d+"', html)
    if not vs_m:
        return ""
    section = html[vs_m.start(): vs_m.start() + 20000]
    m = re.search(r'"menus"\s*:\s*\[([^\]]{0,8000})\]', section)
    if not m:
        return ""
    block = m.group(1)
    items = re.findall(
        r'"label"\s*:\s*"([^"]+)"[^}]*?"count"\s*:\s*(\d+)',
        block,
        re.DOTALL,
    )
    if not items:
        rev = re.findall(
            r'"count"\s*:\s*(\d+)[^}]*?"label"\s*:\s*"([^"]+)"',
            block,
            re.DOTALL,
        )
        items = [(label, cnt) for cnt, label in rev]
    if not items:
        return ""
    return json.dumps(
        [{"label": label, "count": int(cnt)} for label, cnt in items],
        ensure_ascii=False,
    )


def _extract_feature_themes_from_html(html: str) -> str:
    """Apollo State VisitorReviewStatsResult.analysis.themes → JSON 배열 문자열.
    저장형태: '[{"label": "맛", "count": 717}, ...]'
    GQL analysis.themes 미수신 시 폴백. _extract_menu_mentions_from_html 패턴 미러.
    """
    if not html:
        return ""
    vs_m = re.search(r'"VisitorReviewStatsResult:\d+"', html)
    if not vs_m:
        return ""
    section = html[vs_m.start(): vs_m.start() + 20000]
    m = re.search(r'"themes"\s*:\s*\[([^\]]{0,8000})\]', section)
    if not m:
        return ""
    block = m.group(1)
    items = re.findall(
        r'"label"\s*:\s*"([^"]+)"[^}]*?"count"\s*:\s*(\d+)',
        block,
        re.DOTALL,
    )
    if not items:
        rev = re.findall(
            r'"count"\s*:\s*(\d+)[^}]*?"label"\s*:\s*"([^"]+)"',
            block,
            re.DOTALL,
        )
        items = [(label, cnt) for cnt, label in rev]
    if not items:
        return ""
    return json.dumps(
        [{"label": label, "count": int(cnt)} for label, cnt in items],
        ensure_ascii=False,
    )


def _extract_feature_mentions_from_html(html: str) -> str:
    """Apollo State VisitorReviewStatsResult.analysis.themes → count 합산.
    GQL analysis.themes 미수신 시 폴백. themes 배열 count 합산 → 정수 문자열 반환.
    S4: votedKeyword.details 합산에서 analysis.themes 합산으로 수정.
    """
    themes_json = _extract_feature_themes_from_html(html)
    if not themes_json:
        return ""
    try:
        themes = json.loads(themes_json)
        total = sum(t.get("count", 0) for t in themes if isinstance(t, dict))
        return str(total) if total > 0 else ""
    except Exception:
        return ""


def _extract_menu_image_flag_from_html(html: str, place_id: str) -> bool:
    """Apollo State Menu:{place_id}_{N}.images → True if any menu item has a photo URL, else False.

    Depth-tracks each Menu block's images array to find http/pstatic.net URLs.
    No-photo path: images arrays are all empty ([]) or the key is absent → returns False.
    Stored in crawl_data as JSON boolean true/false (sanitize_crawl_data passes booleans through).
    """
    if not html or not place_id:
        return False
    anchor_pat = re.compile(rf'"Menu:{re.escape(place_id)}_\d+"')
    for anchor_m in anchor_pat.finditer(html):
        section_start = anchor_m.start()
        section = html[section_start: section_start + 3000]
        img_key_m = re.search(r'"images"\s*:\s*\[', section)
        if not img_key_m:
            continue
        arr_open = section_start + img_key_m.end() - 1  # position of '['
        depth, pos = 1, arr_open + 1
        end = min(len(html), arr_open + 10000)
        while pos < end and depth > 0:
            c = html[pos]
            if c == '[':
                depth += 1
            elif c == ']':
                depth -= 1
            pos += 1
        arr_content = html[arr_open + 1: pos - 1]
        if re.search(r'https?://|pstatic\.net', arr_content):
            return True
    return False


def _extract_business_image_urls(html: str) -> tuple[list, object]:
    """PlaceDetailTopPhotoItem:business_<N> blocks → (top-5 origin URLs, count).
    count = total business TopPhotoItem blocks found (top-preview count; full 업체 total deferred).
    URL field: origin (raw ldb-phinf, \\u002F decoded). Array order = business_1, 2, …
    Returns ([], None) if no business blocks found.
    """
    if not html:
        return [], None
    pat = re.compile(r'"PlaceDetailTopPhotoItem:business_(\d+)"\s*:\s*\{')
    items: list[tuple[int, str]] = []
    for m in pat.finditer(html):
        n = int(m.group(1))
        # Each block is ~400 chars; 800 is ample for origin + photoType fields
        block = html[m.end() - 1: m.end() - 1 + 800]
        if '"photoType":"business"' not in block:
            continue
        origin_m = re.search(r'"origin"\s*:\s*"([^"]+)"', block)
        if not origin_m:
            continue
        raw = origin_m.group(1)
        url = re.sub(r'\\u([0-9a-fA-F]{4})', lambda x: chr(int(x.group(1), 16)), raw)
        url = url.replace('\\/', '/')
        if 'ldb-phinf' in url or 'pstatic.net' in url:
            items.append((n, url))
    if not items:
        return [], None
    items.sort(key=lambda x: x[0])
    urls = [u for _, u in items]
    return urls[:5], len(items)


async def _fetch_business_photo_total(
    page,
    entry_frame,
    place_id: str,
    ptype: str = "restaurant",
) -> int | None:
    """Fetch actual business photo total via getPhotoViewerItems(filter='업체').

    Navigates to /photo tab, clicks '업체' category, collects GQL responses,
    pages by scrolling until cursors[id='biz'].hasNext == False or hard cap reached.
    Returns total count (int >= 1) or None on any error/block.
    ISOLATION: never raises; all exceptions caught internally.
    Rate-limit safe: ≥3s between scroll pages; aborts on 429 (JSON parse fails → skipped).
    """
    _collected: list = []

    async def _on_response(response):
        if "graphql" not in response.url or "naver.com" not in response.url:
            return
        try:
            pd = response.request.post_data or ""
        except Exception:
            return
        if '"operationName":"getPhotoViewerItems"' not in pd or '"filter":"업체"' not in pd:
            return
        try:
            body = await response.json()
            _collected.append(body)
        except Exception:
            pass

    page.on("response", _on_response)
    try:
        photo_url = f"https://pcmap.place.naver.com/{ptype}/{place_id}/photo"
        try:
            await entry_frame.goto(photo_url, wait_until="networkidle", timeout=20_000)
        except Exception as _ge:
            print(f"[업체사진] /photo 탭 이동 실패: {_ge}")
            return None
        await page.wait_for_timeout(3000)

        # Click "업체" category tab
        clicked = False
        for _sel in ["a:has-text('업체')", "button:has-text('업체')", "span:has-text('업체')"]:
            try:
                _el = entry_frame.locator(_sel).first
                if await _el.count() > 0:
                    await _el.click()
                    clicked = True
                    break
            except Exception:
                pass
        if not clicked:
            print(f"[업체사진] '업체' 탭 클릭 실패 place_id={place_id}")
            return None

        try:
            await page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass
        await page.wait_for_timeout(3000)

        if not _collected:
            print(f"[업체사진] GQL 응답 없음 place_id={place_id}")
            return None

        def _parse_pv(resp):
            """Returns (photos_count, has_next) from a getPhotoViewerItems response."""
            items = resp if isinstance(resp, list) else [resp]
            for item in items:
                if not isinstance(item, dict) or "data" not in item:
                    continue
                pv = (item["data"].get("photoViewer") or {})
                photos = pv.get("photos") or []
                cursors = pv.get("cursors") or []
                biz = next((c for c in cursors if isinstance(c, dict) and c.get("id") == "biz"), {})
                return len(photos), bool(biz.get("hasNext"))
            return 0, False

        total = 0
        has_next = False
        for _r in _collected:
            _cnt, _hn = _parse_pv(_r)
            total += _cnt
            has_next = _hn

        _HARD_CAP = 50
        _scroll_n = 0
        while has_next and _scroll_n < _HARD_CAP:
            _scroll_n += 1
            _prev_len = len(_collected)
            await page.keyboard.press("End")
            await page.wait_for_timeout(3000)  # ≥3s mandatory between pages
            _new = _collected[_prev_len:]
            if not _new:
                break
            for _r in _new:
                _cnt, _hn = _parse_pv(_r)
                total += _cnt
                has_next = _hn

        return total if total > 0 else None

    except Exception as _e:
        print(f"[업체사진] 조회 오류 ({type(_e).__name__}): {str(_e)[:80]}")
        return None
    finally:
        page.remove_listener("response", _on_response)


def _extract_category_from_apollo(html: str) -> str:
    """Apollo State PlaceDetailResult / PlaceHomeResult 앵커 기준 2000자 이내에서 category 탐색.
    DOM → HTML → GQL 폴백 모두 실패 시 최종 단계로 호출된다.
    """
    if not html:
        return ""
    for anchor in ['"PlaceDetailResult', '"PlaceHomeResult', '"PlaceType']:
        idx = html.find(anchor)
        if idx != -1:
            window = html[idx: idx + 2000]
            for pat in [
                r'"category"\s*:\s*"([가-힣][가-힣a-zA-Z&·,\s/]{1,25})"',
                r'"categoryName"\s*:\s*"([가-힣][가-힣a-zA-Z&·,\s/]{1,25})"',
            ]:
                m = re.search(pat, window)
                if m:
                    val = m.group(1).strip()
                    if val:
                        return val
    return ""


def _parse_naedon_response(body) -> tuple:
    """getFsasReviews (buyWithMyMoneyType:true) GQL 응답 파싱.
    Returns (count: str, latest_date: str "YYYY-MM-DD"). 데이터 없으면 ("0", "").
    """
    root = body[0] if isinstance(body, list) else body
    fsas = (root.get("data") or {}).get("fsasReviews") or {}
    total = fsas.get("total", 0)
    items = fsas.get("items") or []
    if not total or not items:
        return "0", ""
    dates = [
        it["date"].rstrip(".").replace(".", "-")
        for it in items if it.get("date")
    ]
    return str(total), (max(dates) if dates else "")


async def _collect_naedon_blog_fields(
    page,
    entry_frame,
    place_id: str,
    ptype: str = "restaurant",
    timeout_ms: int = 15_000,
) -> tuple:
    """home 탭 이동 후 '블로그 리뷰 N' 링크 클릭 → getFsasReviews (buyWithMyMoneyType:true) 캡처.
    Returns (count: str, latest_date: str "YYYY-MM-DD"). 실패 시 ("", "").
    """
    matched: dict = {}
    got_event = asyncio.Event()

    async def _handler(response):
        if got_event.is_set():
            return
        if "graphql" not in response.url or "naver.com" not in response.url:
            return
        try:
            pd = response.request.post_data or ""
        except Exception:
            return
        if ('"operationName":"getFsasReviews"' not in pd
                or '"buyWithMyMoneyType":true' not in pd):
            return
        try:
            body = await response.json()
            matched["body"] = body
            got_event.set()
        except Exception:
            pass

    page.on("response", _handler)
    try:
        home_url = f"https://pcmap.place.naver.com/{ptype}/{place_id}/home"
        await entry_frame.goto(home_url, wait_until="networkidle", timeout=timeout_ms)
        await page.wait_for_timeout(1500)
        blog_link = entry_frame.locator('a:has-text("블로그 리뷰")')
        if await blog_link.count() == 0:
            return "", ""
        await blog_link.first.click()
        try:
            await asyncio.wait_for(got_event.wait(), timeout=12.0)
        except asyncio.TimeoutError:
            return "", ""
        body = matched.get("body")
        if not body:
            return "", ""
        return _parse_naedon_response(body)
    except Exception as _e:
        print(f"[naedon] 수집 실패 ({type(_e).__name__}): {str(_e)[:80]}")
        return "", ""
    finally:
        page.remove_listener("response", _handler)


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

                # iframe DOM은 존재하지만 frame URL 미로딩 대응 (Railway 환경 지연)
                entry_frame = _find_entry_frame(page)
                if entry_frame is None:
                    for _poll in range(12):  # 최대 6초 추가 대기
                        await page.wait_for_timeout(500)
                        entry_frame = _find_entry_frame(page)
                        if entry_frame:
                            break
                if entry_frame is None:
                    _frame_urls = [f.url for f in page.frames]
                    print(f"[오류] entryIframe 탐색 실패: {place_id!r} | frames={_frame_urls[:5]}")
                    return None

                try:
                    body_text = await entry_frame.locator("body").inner_text(timeout=5000)
                except PlaywrightTimeoutError:
                    print(f"[오류] body 텍스트 추출 타임아웃: {place_id!r}")
                    return None

                # ── Render-completeness retry + PRIMARY GUARD (S2-FIX, 2026-06-06) ──
                # S2-VERIFY measured: incomplete = 128 chars (2/3 runs), complete = 1,673+ chars.
                # On each incomplete attempt: re-navigate up to _MAX_RENDER_RETRIES times.
                # On persistent incomplete: return CRAWL_INCOMPLETE → caller performs NO upsert.
                _MAX_RENDER_RETRIES = 3
                for _retry_n in range(1, _MAX_RENDER_RETRIES + 1):
                    if _is_render_complete(body_text):
                        break
                    print(
                        f"[경고] 불완전 렌더 (재시도 {_retry_n}/{_MAX_RENDER_RETRIES}, "
                        f"{len(body_text)}자 < {BODY_COMPLETENESS_THRESHOLD}자): {place_id!r}"
                    )
                    gql_responses.clear()
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                    except PlaywrightTimeoutError:
                        print(f"[오류] 재시도 {_retry_n} 페이지 로드 타임아웃: {place_id!r}")
                        break
                    await page.wait_for_timeout(5000)
                    try:
                        await page.wait_for_selector("iframe#entryIframe", timeout=12_000)
                    except PlaywrightTimeoutError:
                        print(f"[오류] 재시도 {_retry_n} entryIframe 타임아웃: {place_id!r}")
                        break
                    entry_frame = _find_entry_frame(page)
                    if entry_frame is None:
                        for _poll in range(12):
                            await page.wait_for_timeout(500)
                            entry_frame = _find_entry_frame(page)
                            if entry_frame:
                                break
                    if entry_frame is None:
                        print(f"[오류] 재시도 {_retry_n} entryIframe 탐색 실패: {place_id!r}")
                        break
                    try:
                        body_text = await entry_frame.locator("body").inner_text(timeout=5000)
                    except PlaywrightTimeoutError:
                        print(f"[오류] 재시도 {_retry_n} body 텍스트 타임아웃: {place_id!r}")
                        break
                    print(f"[재시도 {_retry_n}] body_text 길이: {len(body_text)}자")

                if not _is_render_complete(body_text):
                    print(
                        f"[CRAWL_INCOMPLETE] {_MAX_RENDER_RETRIES + 1}회 시도 후 불완전 렌더 "
                        f"({len(body_text)}자 < {BODY_COMPLETENESS_THRESHOLD}자) — "
                        f"place_id={place_id!r}. DB 기존 데이터 보존"
                    )
                    return CRAWL_INCOMPLETE  # type: ignore[return-value]

                # HTML 전체 스캔 (place-revum/crawler naver_place.py _extract_from_html 참조)
                # <script> 태그 내 임베드 JSON 포함 — 봇 차단 시에도 데이터 보존됨
                try:
                    html_content = await entry_frame.content()
                except Exception:
                    html_content = ""

                button_link_text = await _collect_button_link_text(entry_frame)
                image_alt_text = await _collect_image_alts(entry_frame)
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
                # facilities: Apollo State conveniences array preferred (JSON array string,
                # excludes "주차" which is a separate 편의시설 섹션). Fall back to text-blob.
                _conv = _extract_conveniences_from_html(html_content, exclude=["주차"])
                if _conv:
                    result["facilities"] = json.dumps(_conv, ensure_ascii=False)
                else:
                    result["facilities"] = extract_facilities(body_text)
                # naver_features: Naver platform features from home-tab Apollo State.
                # Derived fields (reservation_active, talktalk_active, naver_pay_active)
                # come from naver_features membership to avoid text-match false positives.
                _nf = _extract_naver_features(html_content) if html_content else []
                result["naver_features"] = json.dumps(_nf, ensure_ascii=False)
                result["reservation_active"] = "Y" if "예약" in _nf else ""
                result["naver_pay_active"] = "Y" if "페이" in _nf else ""
                result["talktalk_active"] = "Y" if "톡톡" in _nf else ""
                result["coupon_active"] = extract_yes_no_keyword(combined_text, "쿠폰")
                result["directions"] = extract_directions(body_text)
                result["total_reviews"] = _extract_total_review_from_body(body_text)
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
                        _bc = _extract_blog_review_count_from_html(html_content)
                        if _bc:
                            result["blog_review_count"] = _bc

                    if not result["keywords"]:
                        result["keywords"] = _extract_keywords_from_html(html_content)

                    # parking: body_text에서 미추출 시 Apollo State parkingInfo 폴백
                    if not result["parking"]:
                        result["parking"] = _extract_parking_from_html(html_content)

                    # ai_summary: Home Apollo State microReviews[0]
                    if not result["ai_summary"]:
                        result["ai_summary"] = _extract_ai_summary_from_html(html_content)

                    # rating: HTML 임베드 JSON avgRating 추출 (신규 필드)
                    if not result["rating"]:
                        _m = re.search(r'"avgRating"\s*:\s*([\d.]+)', html_content)
                        if _m:
                            try:
                                result["rating"] = str(float(_m.group(1)))
                            except ValueError:
                                pass

                # ── GQL 탭 이동 (메뉴 추출 전 실행 — entry_frame 직접 사용) ─────────────
                # _find_entry_frame 재호출 없이 entry_frame 직접 사용 (메뉴 클릭 후 frame 상태 변경 방지)
                # 홈: visitorReviewStats → good_point_votes, feature_mentions, menu_mentions
                # 리뷰: visitorReviews.total → visitor_review_total (방문자 전용 카운트 폴백)
                _ptype = "restaurant"  # default; overridden below from entry_frame URL
                try:
                    _m_pt = re.search(r"pcmap\.place\.naver\.com/([a-z]+)/", entry_frame.url)
                    _ptype = _m_pt.group(1) if _m_pt else "restaurant"
                    _gql_base = f"https://pcmap.place.naver.com/{_ptype}/{place_id}"
                    await entry_frame.goto(f"{_gql_base}/home", wait_until="networkidle", timeout=15_000)
                    await page.wait_for_timeout(1500)
                    # 홈 탭 '펼쳐보기' 클릭 → 요일별 영업시간/정기휴무 전체 노출
                    # 초기 body_text에는 상단 요약("영업 전 11:00에 영업 시작")만 표시됨
                    try:
                        _expand_btn = entry_frame.get_by_role("button", name="펼쳐보기")
                        if await _expand_btn.count() > 0:
                            await _expand_btn.first.click(timeout=5000)
                            await page.wait_for_timeout(1000)
                            _expanded_text = await entry_frame.locator("body").inner_text(timeout=5000)
                            _hours_structured = _extract_business_hours_from_expanded(_expanded_text)
                            if _hours_structured:
                                result["business_hours"] = _hours_structured
                            if not result["closed_days"]:
                                result["closed_days"] = _extract_closed_days_from_expanded(_expanded_text)
                    except Exception:
                        pass
                    await entry_frame.goto(f"{_gql_base}/review", wait_until="networkidle", timeout=20_000)
                    await page.wait_for_timeout(3000)
                    # GQL 미수신 시 1회 재시도 (네트워크 지연 대응)
                    if not gql_responses:
                        await entry_frame.goto(f"{_gql_base}/home", wait_until="networkidle", timeout=15_000)
                        await page.wait_for_timeout(1000)
                        await entry_frame.goto(f"{_gql_base}/review", wait_until="networkidle", timeout=20_000)
                        await page.wait_for_timeout(3000)
                    # Root Cause B guard: visitorReviewStats-specific retry
                    # Mechanism (ii): the existing retry above guards total GQL absence only.
                    # In Railway/slow environments gql_responses can be non-empty (visitorReviews
                    # captured → visitor_review_count set) yet visitorReviewStats absent because
                    # the /home 1500ms settle was too short. One targeted /home re-navigation with
                    # a 3s settle fills this gap without touching working paths.
                    _vrs_found = False
                    for _r_b in gql_responses:
                        _items_b = _r_b if isinstance(_r_b, list) else [_r_b]
                        for _ib in _items_b:
                            _d_b = _ib.get("data") if isinstance(_ib, dict) else _ib
                            if _deep_find_gql(_d_b, "visitorReviewStats") is not None:
                                _vrs_found = True
                                break
                        if _vrs_found:
                            break
                    if not _vrs_found:
                        print("[경고] visitorReviewStats GQL 미수신 - /home 재시도 (3초 대기)")
                        await entry_frame.goto(
                            f"{_gql_base}/home", wait_until="networkidle", timeout=15_000
                        )
                        await page.wait_for_timeout(3000)
                    # 주차·휴무일·소개 + parking_description/parking_is_free/facilities:
                    # /information 탭에서 추출 (폴백 + 신규 필드).
                    # description은 항상 "" 상태로 시작 → 조건 항상 True → /information 항상 탐색.
                    # goto /info → /home 리다이렉트(SPA 미지원). goto /information 은 정상 동작.
                    if not result["parking"] or not result["closed_days"] or not result["description"]:
                        await entry_frame.goto(
                            f"{_gql_base}/information", wait_until="networkidle", timeout=15_000
                        )
                        await page.wait_for_timeout(2000)
                        _info_text = await entry_frame.locator("body").inner_text(timeout=5000)
                        _info_html = await entry_frame.content()
                        # ISSUE1 (2026-06-13): rendered body HTML for section-scoped extractor.
                        # content() returns un-hydrated HTML (Apollo State only); body.outerHTML
                        # has the React-rendered DOM needed by extract_facility_services.
                        _info_body_html = await entry_frame.evaluate("document.body.outerHTML")
                        if not result["parking"]:
                            result["parking"] = _extract_parking(_info_text)
                        if not result["closed_days"]:
                            result["closed_days"] = extract_closed_days(_info_text)
                        if not result["description"]:
                            result["description"] = _extract_description_from_info(_info_text)
                        # parking_description / parking_is_free (always — new fields)
                        _pinfo = _extract_parking_info_from_html(_info_html)
                        result["parking_description"] = _pinfo.get("description", "")
                        result["parking_is_free"] = _pinfo.get("is_free", "")
                        # ISSUE1 (2026-06-13): use rendered section extractor as primary;
                        # fall back to Apollo-parse + CONV merge (RC-3) when section not found.
                        _fac_rendered = extract_facility_services(_info_body_html)
                        if _fac_rendered:
                            result["facilities"] = json.dumps(_fac_rendered, ensure_ascii=False)
                        else:
                            # Fallback: RC-3 Apollo-parse merge
                            _info_fac = _extract_facilities_from_info_html(_info_html)
                            if _info_fac:
                                _info_d = {_fac_norm_key(x): x for x in _info_fac}
                                _merged, _seen_nk = [], set()
                                for _item in (_conv or []):
                                    _nk = _fac_norm_key(_item)
                                    if _nk not in _seen_nk:
                                        _merged.append(_info_d.get(_nk, _item))
                                        _seen_nk.add(_nk)
                                for _nk, _lbl in _info_d.items():
                                    if _nk not in _seen_nk:
                                        _merged.append(_lbl)
                                        _seen_nk.add(_nk)
                                result["facilities"] = json.dumps(_merged, ensure_ascii=False)

                    # naedon blog fields — getFsasReviews buyWithMyMoneyType capture
                    _naedon_count, _naedon_date = await _collect_naedon_blog_fields(
                        page, entry_frame, place_id, _ptype
                    )
                    result["naedon_blog_review_count"] = _naedon_count
                    result["naedon_blog_latest_date"] = _naedon_date
                except Exception as _e:
                    print(f"[GQL 탭 이동 실패] {type(_e).__name__}: {str(_e)[:100]}")

                # phone_reservation_enabled: "예약" in facilities list.
                # InformationFacilities id=1 ("예약") signals general reservation acceptance.
                # Falls back to home-tab conveniences if /information tab was skipped.
                if result.get("facilities"):
                    try:
                        result["phone_reservation_enabled"] = (
                            "Y" if "예약" in json.loads(result["facilities"]) else ""
                        )
                    except (json.JSONDecodeError, ValueError):
                        pass

                # GQL 보강 필드 추출 및 병합
                gql_extras = _parse_gql_extras(gql_responses)
                # visitor_review_count: GQL visitorReviews.total 항상 우선 (DOM/HTML 오래된 값 덮어쓰기)
                _gql_visitor = gql_extras.get("visitor_review_total", "")
                if _gql_visitor:
                    result["visitor_review_count"] = _gql_visitor
                # total_reviews: body_text 파싱값 유지 (합산 안 함); 미추출 시 GQL visitor 폴백
                if not result["total_reviews"]:
                    result["total_reviews"] = gql_extras.get("visitor_review_total", "")
                result["good_point_votes"] = gql_extras.get("good_point_votes", "")
                result["feature_themes"] = gql_extras.get("feature_themes", "")
                result["feature_mentions"] = gql_extras.get("feature_mentions", "")
                result["menu_mentions"] = gql_extras.get("menu_mentions", "")
                # reply_rate: GQL 집계 결과 병합 (float 또는 None — 키 존재 여부로 판별)
                if "reply_rate" in gql_extras:
                    result["reply_rate"] = gql_extras["reply_rate"]
                # category GQL 폴백 (DOM → HTML 폴백 → GQL)
                if not result["category"]:
                    result["category"] = gql_extras.get("category_gql", "")
                # rating GQL 폴백 (HTML → GQL)
                if not result["rating"]:
                    result["rating"] = gql_extras.get("rating_gql", "")

                # ── Apollo State HTML 폴백 (GQL 미수신 시) ────────────────────
                # html_content = 홈 탭 Apollo State (초기 로드 시 캡처, 세 필드 모두 포함 확인)
                if not result["good_point_votes"] and html_content:
                    result["good_point_votes"] = _extract_good_point_votes_from_html(html_content)
                if not result["menu_mentions"] and html_content:
                    result["menu_mentions"] = _extract_menu_mentions_from_html(html_content)
                if not result["feature_themes"] and html_content:
                    result["feature_themes"] = _extract_feature_themes_from_html(html_content)
                if not result["feature_mentions"] and html_content:
                    result["feature_mentions"] = _extract_feature_mentions_from_html(html_content)
                # category Apollo State 폴백 (DOM → HTML → GQL → Apollo State 최종 단계)
                if not result["category"] and html_content:
                    result["category"] = _extract_category_from_apollo(html_content)

                # 영수증리뷰비율 계산: visitor / (visitor + blog) × 100
                result["receipt_review_ratio"] = compute_receipt_ratio(
                    result["visitor_review_count"],
                    result["blog_review_count"],
                )

                # 메뉴 추출 (GQL 탭 이동 후 실행 — frame이 /review 상태, 메뉴 탭 클릭 가능)
                _menu_list, _menu_html = await _extract_menu_list_from_frame(page, entry_frame)
                result["menu_list"] = _menu_list
                result["menu_image_registered"] = _extract_menu_image_flag_from_html(_menu_html, place_id)

                _biz_urls, _biz_count = _extract_business_image_urls(html_content)
                result["business_image_urls"] = _biz_urls
                result["business_photo_count"] = _biz_count

                # Fetch actual business photo total via getPhotoViewerItems(filter='업체').
                # Overrides preview-based count when fetch succeeds; falls back on any error.
                try:
                    _real_biz_total = await _fetch_business_photo_total(
                        page, entry_frame, place_id, _ptype
                    )
                    if _real_biz_total is not None:
                        result["business_photo_count"] = _real_biz_total
                except Exception as _bpte:
                    print(
                        f"[업체사진] total 조회 실패 (폴백 유지) "
                        f"{type(_bpte).__name__}: {str(_bpte)[:80]}"
                    )

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
