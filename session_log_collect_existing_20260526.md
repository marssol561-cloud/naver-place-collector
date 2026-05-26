# naver-place-collector 세션 로그 — collect 기등록 점포 동작 조사

**날짜**: 2026-05-26  
**작업**: POST /api/v1/collect 기등록 점포 처리 로직 조사 (읽기 전용)  
**대상 파일**: api/server.py, db/master_db.py

---

## 조사 결과 요약

### Q1. 기등록 판정 기준
- **UC-2 (place_id 직접 입력)**: `place_id` 정확 매칭
  - `master_db.find_store_by_place_id(req.place_id)` → server.py:146, master_db.py:77-90
- **UC-1 (place_id 없음)**: 2단계 중복 검사
  - 1차: `store_name + address` 완전 일치 → server.py:160, master_db.py:93-107
  - 2차: searcher 실행 후 획득한 `place_id` 매칭 → server.py:178

### Q2. crawl_data 재수집 여부
- `force_refresh=False`(기본): `_already_exists_resp` 반환, 재수집 없음 → server.py:148-149, 162-163
- crawl_data=null 기등록 점포도 동일하게 already_exists 반환 (재수집 안 함)
- `force_refresh=True`: `_do_crawl_and_save(is_refresh=True)` 호출 → 실제 재수집 + UPDATE → server.py:152-153

### Q3. force 파라미터 존재 여부
- **있음**: `force_refresh: bool = False` — server.py:42 (CollectRequest)
- True 시 기등록 점포도 재수집 실행

### Q4. 삭제 엔드포인트 존재 여부
- **없음**: server.py에는 GET /health, GET /api/v1/stores/{store_id}, POST /api/v1/collect 3개만 존재
- 잘못 생성된 레코드는 DB(Supabase)에서 직접 DELETE 필요

### Q5. 매 호출마다 새 store_id 생성 여부
- **정상이 아님** — 중복 방지 로직 있으나 조건 불일치 시 우회됨
- 중복 방지 위치: server.py:160 (1차 store_name+address), server.py:178 (2차 place_id)
- **"동암상" 문제의 원인 추정**:
  - "동암상"(잘못된 이름) → searcher 실패 → place_id=NULL, crawl_data={} INSERT → store_id_1
  - "동암상회" 1차 호출 → store_name 다름 → 1차 중복 미감지 → searcher 실패 or place_id 없음 → 신규 INSERT → store_id_2
  - "동암상회" 2차 호출 → 같은 address라면 1차 중복 감지 후 already_exists OR searcher 재실패 → store_id_3

---

## 직전 세션 이어받기 (2026-05-24)
- GQL 파싱 버그 수정 완료 (visitorReviewStats/visitorReviews → _deep_find_gql)
- Railway 배포 완료, 스타벅스 역삼점 16필드 수집 확인

---

## 다음 액션 후보 (조사 결과 기반)
1. Supabase에서 "동암상회" 관련 레코드 3건 확인 (place_id 값 유무 확인)
2. place_id가 있는 레코드 → `force_refresh=true`로 재수집
3. place_id=NULL 잘못 생성 레코드 → Supabase에서 직접 DELETE
4. "동암상회" place_id를 searcher가 못 찾는 경우 → 네이버 플레이스 등록 여부 확인
