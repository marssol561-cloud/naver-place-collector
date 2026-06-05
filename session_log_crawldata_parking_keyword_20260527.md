# naver-place-collector 세션 로그 — 주차/키워드 추출 수정 + receipt_review_ratio 계산식 변경

**날짜**: 2026-05-27
**작업**: 지시서 v2 — 주차(parking) /information 탭 추출, 키워드(keywords) keywordList 파싱, 계산식 변경
**대상 점포**: 동암상회 (store_id: 54953ca8, place_id: 1446535451)

---

## 원인 조사 결과

### HTML 구조 확인 (Playwright 실제 렌더링)

#### 주차(parking) 원인

**탐색 경로 vs 실제 위치 불일치**:

| 항목 | 내용 |
|------|------|
| 현재 함수 | `_extract_parking(body_text)` — 홈 탭 body_text에서 "주차" 텍스트 탐색 |
| 홈 탭 body_text | "주차" 텍스트 없음 → null 반환 |
| 실제 위치 | `정보 탭(/information)` → body_text에 "주차 / 주차 불가" 구조로 존재 |
| goto /info 시도 | `/home`으로 리다이렉트 (SPA 미지원) |
| goto /information | 정상 동작 → body_text에 "주차 불가" 포함 |

**정보 탭 body_text 구조**:
```
편의시설 및 서비스3
  예약
  단체 이용 가능
  무선 인터넷
주차          ← 섹션 헤더
주차 불가      ← 실제 값
결제수단5
  ...
```

- `_extract_parking("주차 주차 불가")` → `re.search(r"주차\s*불가", ...)` → "주차불가" ✅

#### 키워드(keywords) 원인

**탐색 경로 vs 실제 위치 불일치**:

| 현재 코드 탐색 패턴 | 실제 키 | 결과 |
|---------------------|---------|------|
| `representKeywords` | ❌ 없음 | 미매칭 |
| `keywords` | ❌ 없음 | 미매칭 |
| `tags` | ❌ 없음 | 미매칭 |
| **`keywordList`** | ✅ 있음 | **미탐색** ← Root cause |

- 실제 위치: `html_content` Apollo State `informationTab.keywordList`
- 값 구조: 단순 문자열 배열 `["인천냉삼", "동암역삼겹살", ...]`

#### receipt_review_ratio 계산식 오류

| 구분 | 식 | 동암상회 결과 |
|------|---|--------------|
| 기존 (틀림) | `(visitor - blog) / visitor × 100` | (490-185)/490 = **62.2%** |
| 변경 (정답) | `visitor / (visitor + blog) × 100` | 490/(490+185) = **72.6%** |

- 방문자 리뷰와 블로그 리뷰는 별도 카테고리 (포함 관계 아님)

#### total_reviews 확인

| 구분 | 식 | 동암상회 결과 |
|------|---|--------------|
| 기존 (틀림) | `visitor만` | **490** |
| 변경 (정답) | `visitor + blog` | **675** |

---

## 수정 내용

### 4-1. 주차(parking) — /information 탭 추출

**GQL try 블록 내 `/information` 이동 추가**:
```python
# GQL /review 완료 후, parking 미추출 시:
await entry_frame.goto(f"{_gql_base}/information", wait_until="networkidle", timeout=15_000)
await page.wait_for_timeout(2000)
_info_text = await entry_frame.locator("body").inner_text(timeout=5000)
result["parking"] = _extract_parking(_info_text)
```

추출 순서: `body_text(홈)` → `html_content(Apollo State)` → **`/information body_text`**

### 4-2. 키워드(keywords) — keywordList 패턴 추가

`_extract_keywords_from_html()` 탐색 패턴 1순위 추가:
```python
r'"keywordList"\s*:\s*\[([^\]]{1,500})\]'  # Apollo State 실제 키
```

### 4-3. receipt_review_ratio + total_reviews 계산식 변경

| 함수 | 변경 내용 |
|------|----------|
| `compute_total_reviews()` | `visitor + blog` (기존: visitor만) |
| `compute_receipt_ratio()` | `visitor / (visitor + blog) × 100` (기존: `(visitor - blog) / visitor`) |
| `crawl_place_by_id()` 초기 계산 | `blog` 인수 추가 |
| GQL 섹션 total_reviews 동기화 | `compute_total_reviews(visitor, blog)` 호출로 변경 |

---

## 검증 결과

### 단위 테스트

```
compute_total_reviews   7/7 PASS
compute_receipt_ratio   6/6 PASS
```

### Playwright 실제 재수집 (동암상회 force_refresh 동등)

| # | 기준 | 결과 | 값 |
|---|------|------|----|
| 1 | 주차 추출 원인 보고 | ✅ | /information 탭 미탐색이 원인 |
| 2 | 키워드 추출 원인 보고 | ✅ | keywordList 패턴 누락이 원인 |
| 3 | 주차 정상 추출 | ✅ | "주차불가" |
| 4 | 키워드 정상 추출 | ✅ | "인천냉삼, 동암역삼겹살, 인천삼겹살, 동암역냉삼, 동암역해산물" |
| 5 | receipt_review_ratio | ✅ | 72.6 |
| 6 | total_reviews | ✅ | 675 |
| 7 | 기존 필드 무영향 | ✅ | phone/visitor/blog/smartcall 모두 동일 |
| 8 | 세션로그 | ✅ | 본 파일 |

---

## 파일 수정 이력

| 파일 | 변경 내용 |
|------|----------|
| `collector/place_crawler.py` | `compute_total_reviews()` visitor+blog / `compute_receipt_ratio()` 식 변경 / `_extract_keywords_from_html()` keywordList 추가 / `_extract_parking_from_html()` 신규(HTML 폴백) / GQL try 블록에 `/information` parking 추출 추가 / initial total_reviews에 blog 인수 추가 / GQL total_reviews 동기화 수정 |

---

## DB 영향

| 컬럼 | 이전 값 | 변경 후 | 컬럼 타입 |
|------|---------|---------|----------|
| `total_reviews` | 490 | 675 | integer — 문제 없음 |
| `receipt_review_ratio` | 62.2 | 72.6 | numeric(5,1) — 문제 없음 |

Supabase 트리거 `fn_extract_core_crawl_fields`: top-level 컬럼 복사 로직 변경 불필요 (값만 갱신됨)

---

## 배포 (CEO 승인 후 진행)

- Railway: `railway up --detach`
- Vercel: git push → 자동 배포 (place_crawler.py만 변경, frontend 무관)

---

## 조사 결과 — good_point_votes / feature_mentions / menu_mentions

**날짜**: 2026-05-27 (지시서 v3 — 코드 수정 없이 조사만)
**대상**: 동암상회 (place_id: 1446535451)
**방법**: Playwright + Apollo State 분석 (`review_apollo_state.json`, `home_apollo_state.json`)

---

### 1. good_point_votes (이런 점이 좋았어요)

**위치**: `VisitorReviewStatsResult:{place_id}.analysis.votedKeyword.details`
**탭**: 홈 탭 Apollo State에 존재 (추가 탭 이동 불필요)
**__typename**: `VisitorReviewStatsAnalysisVoteKeywordDetail`

**구조**:
```json
"votedKeyword": {
  "totalCount": 1967,
  "reviewCount": 487,
  "userCount": 431,
  "details": [
    {"code": "food_good", "displayName": "음식이 맛있어요", "count": 452},
    {"code": "interior_cool", "displayName": "인테리어가 멋져요", "count": 323},
    {"code": "fresh", "displayName": "재료가 신선해요", "count": 229},
    {"code": "kind", "displayName": "친절해요", "count": 194},
    {"code": "special_menu", "displayName": "특별한 메뉴가 있어요", "count": 154},
    {"code": "price_cheap", "displayName": "가성비가 좋아요", "count": 148},
    {"code": "large", "displayName": "양이 많아요", "count": 93},
    {"code": "concept_unique", "displayName": "컨셉이 독특해요", "count": 64},
    {"code": "store_clean", "displayName": "매장이 청결해요", "count": 50},
    {"code": "music_good", "displayName": "음악이 좋아요", "count": 42},
    ... (더 있음)
  ]
}
```

**파싱 패턴**:
```python
r'"votedKeyword"\s*:\s*\{.*?"details"\s*:\s*\[([^\]]{0,5000})\]'
```
→ `displayName` + `count` 쌍 추출

**파싱 가능 여부**: ✅ 홈 탭 Apollo State에서 직접 추출 가능 (추가 탭 이동 불필요)

---

### 2. menu_mentions (메뉴 언급수)

**위치**: `VisitorReviewStatsResult:{place_id}.analysis.menus`
**탭**: 홈 탭 Apollo State에 존재
**__typename**: `VisitorReviewStatsAnalysisThemes`

**구조**:
```json
"menus": [
  {"label": "고기",           "count": 70},
  {"label": "갑오징어",       "count": 39},
  {"label": "삼겹살",         "count": 39},
  {"label": "찌개",           "count": 28},
  {"label": "문어",           "count": 27},
  {"label": "계란찜",         "count": 25},
  {"label": "고추장삼겹살",   "count": 22},
  {"label": "도시락",         "count": 22},
  ... (총 42개)
]
```

**파싱 패턴**: `menus` 배열 내 `label + count` 추출
```python
r'"menus"\s*:\s*\[([^\]]{0,5000})\]'  # VisitorReviewStatsResult 내부
```
→ `{"label": "메뉴명", "count": N}` 쌍 추출

**파싱 가능 여부**: ✅ 홈 탭 Apollo State에서 직접 추출 가능

**주의**: `menus` 키가 여러 곳에 존재할 수 있음 → `VisitorReviewStatsResult:{place_id}` 블록 내에서 찾아야 함

---

### 3. feature_mentions (편의시설)

**위치**: `InformationFacilities:{id}.name` — InformationTab.facilities 배열로 참조
**탭**: 홈 탭 Apollo State에 존재
**__typename**: `InformationFacilities`

**구조**:
```json
"InformationFacilities:1":  {"id": "1",  "name": "예약"},
"InformationFacilities:13": {"id": "13", "name": "단체 이용 가능"},
"InformationFacilities:7":  {"id": "7",  "name": "무선 인터넷"}
```
→ `InformationTab.facilities: [{"__ref": "InformationFacilities:1"}, ...]`로 참조됨

**동암상회 값**: ["예약", "단체 이용 가능", "무선 인터넷"] (3개)

**파싱 패턴**:
```python
re.findall(
    r'"InformationFacilities:\d+"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
    html_content,
)
```

**파싱 가능 여부**: ✅ 홈 탭 Apollo State에서 직접 추출 가능

---

### 요약 — 3개 필드 파싱 가능 여부

| 필드 | Apollo State 키 | 홈 탭 존재 | 파싱 가능 |
|------|----------------|------------|----------|
| `good_point_votes` | `VisitorReviewStatsResult.analysis.votedKeyword.details` | ✅ | ✅ |
| `menu_mentions` | `VisitorReviewStatsResult.analysis.menus` | ✅ | ✅ |
| `feature_mentions` | `InformationFacilities:{id}.name` | ✅ | ✅ |

**공통 추출 방식**: `html_content` (홈 탭 Apollo State) 한 번만 로드하면 세 필드 모두 추출 가능. 추가 탭 이동 불필요.

---

### 저장 파일 (조사 산출물)

| 파일 | 내용 |
|------|------|
| `scripts/review_apollo_state.json` | /review 탭 Apollo State (91,590자) |
| `scripts/home_apollo_state.json` | 홈 탭 Apollo State (91,590자, 동일 구조) |
| `scripts/review_body_text.txt` | /review 탭 body_text (접근 차단 페이지) |
| `scripts/review_html.html` | /review 탭 전체 HTML |
| `scripts/investigate_review_fields.py` | 조사 스크립트 |
| `scripts/check_home_apollo.py` | 홈 탭 검증 스크립트 |

---

## 구현 — good_point_votes / menu_mentions / feature_mentions

**날짜**: 2026-05-27 (지시서 v4 — HTML 파싱 폴백 구현)
**파일**: `collector/place_crawler.py`

---

### 구현 내용

#### import json 추가

상단에 `import json` 추가 (JSON 배열 직렬화용).

#### 3개 HTML 파싱 함수 신규 추가

| 함수 | 추출 위치 | 저장 형태 |
|------|-----------|----------|
| `_extract_good_point_votes_from_html(html)` | `votedKeyword.details` | JSON 배열 `[{"displayName":"...", "count":N}]` |
| `_extract_menu_mentions_from_html(html)` | `VisitorReviewStatsResult.analysis.menus` | JSON 배열 `[{"label":"...", "count":N}]` |
| `_extract_feature_mentions_from_html(html)` | `InformationFacilities:{id}.name` | 편의시설 개수 정수 문자열 |

**good_point_votes 핵심 구현 포인트**:
- `[^\]]{0,8000}` 상한 방식은 details 배열이 30개 항목 × ~350자 ≈ 10,500자로 실패
- depth-tracking 방식으로 교체: `[` / `]` 카운팅으로 배열 끝 탐색, 최대 50,000자

**GQL 형태 vs HTML 형태 차이 명시**:

| 필드 | GQL 기존 의도 | HTML 파싱 형태 | 차이 이유 |
|------|-------------|--------------|---------|
| `good_point_votes` | `str(positiveKeywordCount)` 단순 정수 | JSON 배열 (상세) | Apollo State가 totalCount 아닌 details 배열 제공; 상세 배열이 더 완전한 데이터 |
| `menu_mentions` | `str(sum(menuList counts))` 단순 정수 | JSON 배열 (상세) | Apollo State menus가 합산값 아닌 개별 항목 제공 |
| `feature_mentions` | `str(sum(keywordList counts))` 단순 정수 | 편의시설 개수 정수 | Apollo State에 횟수 없음, 이름만 제공. 지시서 "없으면 정수" 규칙 적용 |

#### crawl_place_by_id() HTML 폴백 블록 추가

GQL 병합 이후, 각 필드가 비어있을 때만 HTML 파싱 함수 호출:
```python
if not result["good_point_votes"] and html_content:
    result["good_point_votes"] = _extract_good_point_votes_from_html(html_content)
if not result["menu_mentions"] and html_content:
    result["menu_mentions"] = _extract_menu_mentions_from_html(html_content)
if not result["feature_mentions"] and html_content:
    result["feature_mentions"] = _extract_feature_mentions_from_html(html_content)
```

`html_content` = 홈 탭 Apollo State (초기 로드 시 캡처). 세 필드 모두 포함됨 확인.

---

### 검증 결과 — 동암상회 force_refresh 동등 (Playwright 실수집)

| # | 기준 | 결과 | 값 |
|---|------|------|----|
| 1 | good_point_votes 정상 추출 | ✅ PASS | JSON 30개 항목. 음식이 맛있어요=452 (CEO확인값 일치) |
| 2 | menu_mentions 정상 추출 | ✅ PASS | JSON 41개 항목. 고기=70 (CEO확인값 일치) |
| 3 | feature_mentions 정상 추출 | ✅ PASS | "3" (예약, 단체 이용 가능, 무선 인터넷 — CEO확인값 일치) |
| 4 | 기존 필드 무영향 | ✅ PASS | place_name/visitor_review_count/blog_review_count/total_reviews/receipt_review_ratio/parking 모두 동일 |
| 5 | 저장 형태 보고 | ✅ | 위 표 참조 |
| 6 | 세션로그 업데이트 | ✅ | 본 섹션 |

단위 테스트: 5/5 PASS (빈html·VisitorReviewStatsResult없음·depth-tracking 포함)

---

### 파일 수정 이력 (v4 추가분)

| 파일 | 변경 내용 |
|------|----------|
| `collector/place_crawler.py` | `import json` 추가 / `_extract_good_point_votes_from_html()` 신규 (depth-tracking) / `_extract_menu_mentions_from_html()` 신규 / `_extract_feature_mentions_from_html()` 신규 / `crawl_place_by_id()` GQL 병합 이후 HTML 폴백 블록 3개 추가 |

---

### 배포 (CEO 승인 후 진행)

- Railway: `railway up --detach`
- Vercel: git push → 자동 배포 (place_crawler.py만 변경, frontend 무관)
