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
