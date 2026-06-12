# Session Log — 2026-06-12 recollect_stubs first-10 checkpoint

## 지시서
SPRINT: naver-place-collector — re-collect 261 unmatched stubs (recover direct-entry misses)

## 완료 항목 (first-10 체크포인트)
- [x] 핸드셰이크: /health 1.4.1, NULL 스텁 261건 확인
- [x] scripts/recollect_stubs.py 작성 (rate-limit 5s, sequential, resumable CSV)
- [x] first-10 배치 실행 완료
- [x] DB 복구 확인: 하하반점 place_id=21791085, is_registered=True
- [x] DB 미해소 확인: 갯바다해물샤브칼국수 place_id=null 유지
- [x] CSV: scripts/recollect_result_20260612.csv (10행)

## first-10 결과
| # | 점포명 | 상태 | place_id |
|---|--------|------|---------|
| 1 | 갯바다해물샤브칼국수 | place_not_found | - |
| 2 | 하하반점 | collected | 21791085 |
| 3 | 세아 뷰티 | place_not_found | - |
| 4 | 돈가면 | collected | 1132849462 |
| 5 | 팔천순대 구월모래내시장점 | place_not_found | - |
| 6 | 생생 손칼국수 | collected | 2035033098 |
| 7 | 포차연구소 | place_not_found | - |
| 8 | 든든한끼육개장 | collected | 1161954741 |
| 9 | 오브쉬 속눈썹 구월점 | place_not_found | - |
| 10 | 뼈다구집정해장 시흥거북섬점 | place_not_found | - |

**요약: 복구 4 / 미등록 6 / 오류 0**

## 미완료 (CEO 승인 대기)
- [ ] 나머지 251건 전체 배치
  재실행 명령: `COLLECTOR_API_KEY=<prod_key> python scripts/recollect_stubs.py --limit 251`
  (CSV 재개 지원: 기처리 10건 자동 스킵)

## 수정한 파일
- scripts/recollect_stubs.py (신규)
- scripts/recollect_result_20260612.csv (신규, first-10 결과)
- session_log_20260612_recollect_stubs.md (신규)

## 발생 에러 + 처리
- 없음

## 다음 세션 첫 액션
- CEO 승인 확인 후 위 재실행 명령으로 --limit 251 실행
- 완료 후 최종 요약 보고 (복구율, 미등록율)

---

## FULL RUN 완료 — 2026-06-12

### 최종 집계 (261건 전체)
| 구분 | 건수 | 비율 |
|------|------|------|
| 전체 처리 | 261 | 100% |
| 복구 (place_id 획득) | 118 | 45.2% |
| 미등록 (genuinely not on Naver) | 124 | 47.5% |
| 오류 | 19 | 7.3% |

### 오류 19건 분류
- CRAWL_INCOMPLETE 17건: searcher가 place_id를 찾았으나 크롤러 렌더 미완성 → stub 유지
- NO_RESPONSE 2건: Railway 일시 무응답 (형제직화석갈비, 춘천해장국)

### 결과 파일
- scripts/recollect_result_20260612.csv (261행)
- scripts/recollect_run_20260612.log

### 다음 액션 (CEO 결정 사항)
- 오류 19건 재시도 여부 (CRAWL_INCOMPLETE = place_id는 있으나 크롤 미완, 재시도 시 복구 가능성 높음)
- 미등록 124건 처리 방침 (현재 stub 유지 중)
