# Session Log — 백필 배치2 수집 + retry (2026-06-23)

## 목표
배치2 (11~20번 점포) 수집, retry, 최종 PASS 집계. PASS 기준: industry + gpv + menu_mentions + total_reviews (rating 제외).

---

## 스프린트 선행 작업 (배치1 관련)
- searcher.py 쿼리 빌더 fix (Sprint2) — `_clean_address()` 추가, 괄호·층·호·상가동 suffix 제거
- Railway 프로덕션 배포 (Sprint3) — git push → auto-deploy 확인
- 배치1 수집 후 검증 완료 (별도 session_log_backfill_batch1_20260623.md 참조)

---

## 배치2 1차 수집 결과 (점포 11~20)

| # | 점포명 | 1차 결과 | place_id |
|---|--------|---------|----------|
| 11 | 그래요거 시흥거북섬점 | place_not_found | — |
| 12 | 그리고카페 | place_not_found | — |
| 13 | 깡통막창 | place_not_found | — |
| 14 | 꼬꼬마래집 | place_not_found | — |
| 15 | 꼬꼬마찬 | place_not_found | — |
| 16 | 꼼숯 | error(timeout 90.9s) | — |
| 17 | 냉삼회관 부천역점 | collected | 1401357400 |
| 18 | 다이닝야경 구월점 | collected | 1562156925 |
| 19 | 닥터빈티지 본점 | error(CRAWL_INCOMPLETE) | — |
| 20 | 단골 | collected | 2096969104 |

---

## Retry 대상 분류 및 결과

### CRAWL_INCOMPLETE retry (동일 주소 1회)
| 점포명 | retry 결과 | 비고 |
|--------|-----------|------|
| 꼼숯 | refreshed (place_id=2089726807) ✅ | timeout 1회 → retry 성공 |
| 닥터빈티지 본점 | CRAWL_INCOMPLETE 재발 ❌ | 2회 연속 불완전 렌더, crawl fix 필요 |

### QUERY_FORMAT retry (도로명주소 전환)
| 점포명 | CSV 지번주소 | retry 도로명주소 | retry 결과 |
|--------|------------|----------------|-----------|
| 깡통막창 | 상동 532-3 | 길주로77번길 55-25 | collected (place_id=1202844309) ✅ |
| 그래요거 시흥거북섬점 | 정왕동 2709 보니타가 2동 | 거북섬중앙로 1 | place_not_found ❌ |

### TRULY_ABSENT 판정 (웹 검색 증거 없음)
- **그리고카페** — 인천 옹진군 영흥면 내리 1622. 다이닝코드·구글·다음 어디에도 해당 점포 미발견
- **꼬꼬마래집** — 경기 부천시 도당동 180-6. 웹 검색 증거 없음
- **꼬꼬마찬** — 경기 부천시 중동 595-9. 웹 검색 증거 없음

---

## 배치2 최종 PASS 집계 (5/10)

| # | 점포명 | 최종 상태 | industry | gpv | menu | reviews | PASS |
|---|--------|---------|---------|-----|------|---------|------|
| 11 | 그래요거 시흥거북섬점 | undetermined | — | — | — | — | ❌ |
| 12 | 그리고카페 | TRULY_ABSENT | — | — | — | — | ❌ |
| 13 | 깡통막창 | PASS (retry) | 곱창,막창,양 | Y | Y | 1262 | ✅ |
| 14 | 꼬꼬마래집 | TRULY_ABSENT | — | — | — | — | ❌ |
| 15 | 꼬꼬마찬 | TRULY_ABSENT | — | — | — | — | ❌ |
| 16 | 꼼숯 | PASS (retry) | 장어,먹장어요리 | Y | Y | 336 | ✅ |
| 17 | 냉삼회관 부천역점 | PASS | 돼지고기구이 | Y | Y | 1006 | ✅ |
| 18 | 다이닝야경 구월점 | PASS | 술집 | Y | Y | 956 | ✅ |
| 19 | 닥터빈티지 본점 | CRAWL_INCOMPLETE | — | — | — | — | ❌ |
| 20 | 단골 | PASS | 술집 | Y | Y | 181 | ✅ |

**PASS: 5/10**

---

## CEO 미스리스트 (TRULY_ABSENT 3건)
- 그리고카페 (인천 옹진군 영흥면 내리 1622)
- 꼬꼬마래집 (경기 부천시 원미구 도당동 180-6)
- 꼬꼬마찬 (경기 부천시 중동 595-9)

---

## 미결 사항 (다음 스프린트로 이월)
1. **닥터빈티지 본점** — CRAWL_INCOMPLETE 2회. Playwright 렌더 타임아웃 원인 분석 필요 (특정 place_id 패턴 vs 일시적 서버 부하)
2. **그래요거 시흥거북섬점** — 다이닝코드에는 존재 (주소: 거북섬중앙로 1 2동 128호). Naver Place에서 상호명 또는 세부 상가 정보가 다를 가능성. place_id 직접 조회 또는 상호명 변형 시도 검토

---

## 주요 기술 발견
- `_DETAIL_SUFFIX_RE` 미처리 패턴 2종 발견:
  - `보니타가 2동` — `상가\S*동` 패턴에 미해당 (상가 이름이 고유명사)
  - `1.2.4층` — 점(`.`)이 `[가-힣A-Za-z\d]` 클래스 외
  → 배치3 진입 전 regex 보완 검토 대상
- CSV 지번주소가 Naver 도로명주소와 다른 경우 place_not_found 빈발 (깡통막창 사례)
  → CSV 수집 단계에서 도로명주소 병행 확보 권장
