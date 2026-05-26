# naver-place-collector 세션 로그 — 동암상회 레코드 정리 + 재수집 확인

**날짜**: 2026-05-26  
**작업**: DB 잘못된 레코드 삭제 + 재수집 동작 확인  
**코드 수정**: 없음 (조사 + DB 조작 + API 호출만)

---

## 최종 DB 상태

```
store_id : 54953ca8-40aa-4dfc-973a-11da1145f970
store_name : 동암상회
address  : 인천 부평구 십정동 440-10
place_id : 1446535451
crawl_data : 10/26개 실제 값 있음
  (name, phone, menu_list, facilities, lot_address, photo_count,
   business_hours, booking_enabled, blog_review_count, visitor_review_count)
```

---

## STEP 1 — DB 조회 결과 (4건 발견)

| store_id | store_name | address | place_id | crawl_data | 처리 |
|----------|------------|---------|----------|------------|------|
| 54953ca8 | 동암상회 | 인천 부평구 **십정동** 440-10 | 1446535451 ✅ | 26키 실데이터 | 보존 |
| d8ebef5c | **동암상** | 인천 부평구 십정동 440-10 | null | {} | 삭제 |
| 85ddbce4 | 동암상회 | 인천 부평구 **삼정동** 440-10 | null | {} | 삭제 |
| a2fcb8b6 | 동암상회 | **인첸** 부평구 삼정동 440-10 | null | {} | 삭제 |

---

## STEP 2 — 삭제 완료

삭제 대상 3건 전부 DELETE 완료 (Supabase REST API, Prefer: return=representation 응답 확인)

---

## STEP 3 — 재수집 동작 확인

**"십정동" 주소 테스트 (Python UTF-8)**:
```
collect_status : already_exists ✅
store_id       : 54953ca8-40aa-4dfc-973a-11da1145f970
place_id       : 1446535451
crawl_data     : 26키 (기존 데이터 반환)
```
→ 시스템 정상. 1차 중복 검사(store_name + address)가 기존 레코드를 정확히 감지.

**"삼정동" 주소 테스트 (Python UTF-8)**:
```
collect_status : collected_without_place
place_id       : null
fields_collected : 0
elapsed_seconds  : 39.5
```
→ 시스템 정상. "삼정동"은 네이버 플레이스에 없는 주소이므로 searcher가 None 반환 → 정상 동작.

---

## 핵심 발견 — 원인 분석

### 문제의 원인: "삼정동 vs 십정동" 주소 오류
- **실제 주소**: 인천 부평구 **십정동** 440-10 (place_id=1446535451로 확인)
- **잘못 입력된 주소**: 인천 부평구 **삼정동** 440-10 (searcher 실패)
- 지시서의 "인천 부평구 삼정동 440-10"은 **오류 주소**임

### 부수 발견: curl 인코딩 이슈
- Windows 터미널에서 `curl -d '한글...'` 사용 시 한글이 깨져 DB에 저장됨
- `python urllib`로 `ensure_ascii=False` + `encode('utf-8')` 사용 시 정상
- 조사 중 curl 인코딩 오류 레코드 2건 생성 → 모두 삭제 완료

---

## STEP 4 — Railway 로그 (불필요)

STEP 3에서 시스템 정상 확인. Railway 에러 로그 조사 필요 없음.
searcher가 "삼정동" 주소로 place_id를 못 찾은 것은 주소가 틀렸기 때문이지 시스템 버그 아님.

---

## 다음 액션 (CEO 확인 필요)

1. **주소 확정**: "십정동 440-10" 이 실제 주소인지 CEO가 직접 확인
   - place_id=1446535451 기준 네이버 플레이스에서 확인 가능
   - (https://map.naver.com/p/entry/place/1446535451)
2. **관리자 UI 재사용**: itdalab.com/admin/place-collector에서 "십정동" 주소로 입력하면 `already_exists` 정상 반환
3. **기존 데이터 갱신 필요 시**: force_refresh=true 직접 호출 (Vercel proxy는 미지원 — 필요 시 기능 추가 검토)
