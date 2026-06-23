# 세션로그 — 백필 배치 1 (2026-06-23)

## 현재 작업
스프린트: 리붐맛집추천 미수집 점포 백필 (109개, 10개 단위 CEO 승인)

## 완료 항목
- [x] backfill_csv_stores.py 작성 (scripts/)
- [x] Railway CLI로 COLLECTOR_API_KEY 조회 → .env 갱신 → 인증 확인 (HTTP 200)
- [x] 배치 1 (10개) 수집 실행
- [x] 배치 1 DB 검증 완료
- [x] DB 스텁 상태 직접 확인

## 배치 1 결과 요약

### 수집 결과
| 점포명 | status | 사유 |
|--------|--------|------|
| 834쭈꾸미 선부점 | place_not_found | 네이버 검색 0건 |
| 가르텐비어 인천청천점 | CRAWL_INCOMPLETE | 검색됨 / 렌더 미완 |
| 가족식당 장곡갈비 | place_not_found | 네이버 검색 0건 |
| 감성쪽갈비 연수점 | place_not_found | 네이버 검색 0건 |
| 갯바다해물샤브칼국수 | place_not_found | 네이버 검색 0건 |
| 겹살이네 | CRAWL_INCOMPLETE | 검색됨 / 렌더 미완 |
| 고기서고기 | place_not_found | 네이버 검색 0건 |
| 광주원조불닭 | place_not_found | 네이버 검색 0건 |
| 구주 부천역점 | place_not_found | 네이버 검색 0건 |
| 국밥대장 검암1지구점 | CRAWL_INCOMPLETE | 검색됨 / 렌더 미완 |

### DB 확인
- 10개 모두 DB에 스텁으로 존재 (place_id=NULL, industry=NULL, crawl_data 비어있음)
- 최초 등록: 2026-05-18 (겹살이네만 2026-06-20)
- 검증 PASS: 0/10

## 미수정 파일 목록
- scripts/backfill_csv_stores.py (신규)
- scripts/_check_batch1_db.py (임시 진단용)
- scripts/backfill_csv_result.csv (배치 1 결과)
- .env (COLLECTOR_API_KEY 갱신)

## 발생 에러 및 상태
- place_not_found (7건): 네이버 플레이스 미등록 추정 → un-collectable 목록 대상
- CRAWL_INCOMPLETE (3건): 검색은 성공했으나 페이지 렌더 불완전 → 재시도 가능(CEO 결정)

## 다음 세션 첫 액션
1. CEO가 배치 2 승인 시: `python scripts/backfill_csv_stores.py --list-batch 2`
2. CEO가 3건 재시도 승인 시: `--run-batch 1` 재실행 (result CSV에서 배치1 행 삭제 후)
3. 환경변수는 매 세션 Railway CLI로 주입 필요:
   ```
   $vars = railway variables --json 2>&1 | ConvertFrom-Json
   $env:COLLECTOR_API_KEY = $vars.COLLECTOR_API_KEY
   $env:MASTER_DB_URL = $vars.MASTER_DB_URL
   $env:MASTER_DB_SERVICE_ROLE_KEY = $vars.MASTER_DB_SERVICE_ROLE_KEY
   $env:PYTHONIOENCODING = "utf-8"
   ```
