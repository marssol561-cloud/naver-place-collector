# Session Log — CRAWL_INCOMPLETE Fix (2026-06-23)

## 목표
CRAWL_INCOMPLETE 4개 점포 root cause 확정 + fix + 로컬 검증.
스프린트 금지 사항: deploy, git push, searcher.py 수정, 배치3+.

---

## STEP 1 — ROOT CAUSE

### 진단 결과 (scripts/_diag_crawl_incomplete.py)

| 점포 | place_id | frame_url_at_find | len_orig | len_patched | 내용 |
|------|----------|-------------------|---------|-------------|------|
| 가르텐비어 인천청천점 | 34017001 | pcmap…/restaurant/34017001/home | 392 | 392 | 실제 점포 데이터 ✓ |
| 겹살이네 | 1225221119 | pcmap…/restaurant/1225221119/home | 342 | 342 | 실제 점포 데이터 ✓ |
| 국밥대장 검암1지구점 | 1448066768 | pcmap…/restaurant/1448066768/home | 302 | 302 | 실제 점포 데이터 ✓ |
| 닥터빈티지 본점 | 1842154511 | pcmap…/**place**/1842154511/home | 398 | 398 | 구제의류 — 실제 점포 데이터 ✓ |

### 핵심 발견
1. frame URL = 완전 로딩된 Naver URL (about:blank 아님)
2. body_text = 실제 점포 데이터 (이름·업종·리뷰수·탭 포함) — **렌더 성공**
3. `wait_for_load_state("networkidle")` 적용해도 len 변화 없음 → **timing 문제 아님**
4. 302~398자: 500자 threshold 미달이나 128자 실패 패턴과 전혀 다름

### Root cause (place_crawler.py:70)

```
BODY_COMPLETENESS_THRESHOLD = 500  # 잘못된 임계값
```

`S2-FIX(2026-06-06)` 당시 "incomplete=128자, complete=1673자+"로 보수적으로 500자로 설정했으나,
일부 점포(구제의류·간결 메뉴 점포)는 유효 렌더임에도 302~398자에 그침 → false negative.

**gap 분석:** 128자(실패) → 302자(성공 최소) → 72자 여유 공간. 200자 = 안전한 임계값.

### 관련 코드 위치
- `collector/place_crawler.py:70` — `BODY_COMPLETENESS_THRESHOLD = 500`
- `collector/place_crawler.py:74-76` — `_is_render_complete()` 함수
- `collector/place_crawler.py:1483-1530` — 재시도 루프 (4회 시도)

---

## STEP 2 — FIX

**변경 파일:** `collector/place_crawler.py`
**변경 라인:** 70
**변경 내용:** `BODY_COMPLETENESS_THRESHOLD = 500` → `200`

```python
# Before
BODY_COMPLETENESS_THRESHOLD = 500

# After
# 2026-06-23 fix: 500→200. Compact stores (clothing/bar with few sections) render
# valid content at 302-398 chars — well above the 128-char failure pattern but
# below the original 500 threshold. 200 safely separates failures (≤128) from
# valid compact pages (≥302). Gap: 128→302, threshold at 200 gives 72-char margin.
BODY_COMPLETENESS_THRESHOLD = 200
```

다른 코드 변경 없음 (searcher.py, rec_*, rating 로직 전혀 미수정).

---

## STEP 3 — LOCAL 검증 (scripts/_direct_crawl_ci.py)

### 수집 결과

| 점포 | 시도 결과 | 비고 |
|------|---------|------|
| 가르텐비어 인천청천점 | 3회 128자 → **4회째 392자 → OK** | 128자 간헐 패턴은 재시도로 극복 |
| 겹살이네 | 1회째 342자 → OK | 즉시 성공 |
| 국밥대장 검암1지구점 | 1회째 302자 → OK | 즉시 성공 |
| 닥터빈티지 본점 | 1회째 398자 → OK | /place/ 경로, 구제의류 |

**중요 관찰:** `가르텐비어`에서 128자 (실제 실패 패턴) → 200자 threshold가 이를 여전히 정확히 걸러냄. 4회째 392자 도달 시 정상 통과. 구 threshold(500)에서는 392자도 거부했음 — 이것이 원래 CRAWL_INCOMPLETE의 직접 원인.

### DB 검증 결과 (PASS = industry + gpv + menu_mentions + total_reviews, rating 제외)

| # | 점포 | industry | gpv | menu | reviews | 판정 |
|---|------|---------|-----|------|---------|------|
| 1 | 가르텐비어 인천청천점 | 맥주,호프 ✓ | Y ✓ | Y ✓ | 238 ✓ | **PASS** |
| 2 | 겹살이네 | 돼지고기구이 ✓ | Y ✓ | Y ✓ | 335 ✓ | **PASS** |
| 3 | 국밥대장 검암1지구점 | 국밥 ✓ | Y ✓ | Y ✓ | 436 ✓ | **PASS** |
| 4 | 닥터빈티지 본점 | 구제의류 ✓ | Y ✓ | N — | 513 ✓ | PARTIAL(3/4) |

**닥터빈티지 설명:** 구제의류(빈티지 의류) 매장 — Naver Place에 `메뉴` 탭 없음. menu_mentions 없음은 크롤 버그가 아닌 정상 동작. industry/gpv/reviews 3개 필드 확보.

---

## 상태 요약

- **Fix 완료 (local):** `BODY_COMPLETENESS_THRESHOLD 500→200`
- **Deploy 보류:** CEO 승인 별도 필요 (스프린트 금지)
- **검증 완료:** 3/4 PASS, 1/4 PARTIAL (닥터빈티지 — 구조적 이유)

## 다음 단계
1. CEO 승인 후 Railway 배포 (`git push`)
2. 배포 후 4개 점포 프로덕션 재수집 실행
