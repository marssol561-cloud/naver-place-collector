# 세션 로그 — Queue② Impl-3a: visitor_collect port (no live)
날짜: 2026-06-09
에이전트: itda1
지시서: Queue② Impl-3a — Extract step2 visitor collection into a place_id-parameterized module

## 작업 요약
collect_reviews_expand_step2.py의 visitor 수집 로직을 collector/visitor_collect.py로 추출.
place_id 파라미터화 (모듈 전역 PLACE_ID/HOME_URL 제거). 정적 검증만, 브라우저 실행 없음.

## 완료 기준
| 기준 | 판정 | 세부 |
|------|------|------|
| C-1 helpers + process_tab + bring-up 포팅 (guards 유지) | ✓ | 8 helpers + process_tab(place_id 파라미터 추가) + visitor bring-up 포팅 |
| C-2 collect_visitor_items(place_id) async, 반환 flattened list | ✓ | 서명 확인: IMPORT_OK True ['place_id'] |
| C-3 AST_OK | ✓ | python ast.parse 통과 |
| C-4 IMPORT_OK True ['place_id'] | ✓ | inspect 확인 완료 |
| C-5 모듈 전역 PLACE_ID 사용 없음 | ✓ | grep 2건 = 모두 docstring, 기능 코드 0건 |
| C-6 git: 신규 2파일만 스테이지, 단일 커밋, no push | ✓ | git add 명시적 경로, git status --short 확인 |
| C-7 세션 로그 작성 | ✓ | 본 파일 |

## 정적 검증 RAW 출력
```
AST_OK

IMPORT_OK True ['place_id']

(grep PLACE_ID — 기능 코드 0건, docstring 2건)
6:Blog path omitted; no module-level PLACE_ID/HOME_URL globals.
180:    place_id drives HOME_URL and fallback_url; no module-global PLACE_ID used.
```

## 포팅 요소
### 상수
- REF_TOTALS (step2 line 38)
- _UA (lines 40-44)
- _SCROLL_JS (lines 46-57)

### 헬퍼 함수 (8개)
- deep_find (lines 62-77)
- human_delay (lines 80-81)
- scroll_bottom (lines 84-91)
- find_expand_btn (lines 93-113)
- click_sort_latest (lines 116-129)
- extract_batches (lines 132-146)
- wait_new_gql (lines 149-168)
- log_gql_ops (lines 171-193)

### process_tab (lines 198-419)
- 원본 대비 유일한 변경: 시그니처에 place_id 추가
- 함수 내부 첫 줄에 HOME_URL 로컬 정의: `HOME_URL = f"https://pcmap.place.naver.com/restaurant/{place_id}/home"`
- lines 207/212의 PLACE_ID → place_id 치환
- 모든 guard/retry/dedup 로직 원본 그대로 보존

### bring-up (main의 visitor 경로, lines 436-497)
- all_captures + on_resp handler
- launch_opts (headless=False, args 동일, PROXY_URL 조건부)
- async_playwright → chromium.launch → new_context(_UA, 1280x900)
- ctx.on("response", on_resp) → new_page → process_tab("visitor") → page.close

### flatten 매핑 (lines 520-532)
- created / representativeVisitDateTime / visitCount / originType / author(nickname) / id

## 설계 결정
- process_tab 시그니처: `(page, tab_name, all_captures, log_fn, place_id)` — place_id를 인자로 추가하여 HOME_URL/fallback_url 내부 도출
- blog 경로: process_tab 내 blog 브랜치(if tab_name == "blog") 원본 유지 (faithful port), 단 collect_visitor_items에서는 "visitor"만 호출 (blog page_b 제외)
- log_fn: 로컬 리스트에 append하는 no-op 함수 (`logs = []; def log_fn(msg=""): logs.append(msg)`)
- sys.stdout.reconfigure, REPO_ROOT, sys.path.insert, OUT_* 상수: 스크립트 진입점 전용 — 모듈에서 제외

## 모호한 step2 라인
없음 (none)

## 수정 파일
| 파일 | 종류 |
|------|------|
| collector/visitor_collect.py | 신규 |
| session_log_20260609_visitor_collect_port.md | 신규 |

## 다음 세션 첫 액션
Impl-3b: visitor_batch.py의 collect_visitor_reviews STUB을 collect_visitor_items로 교체하여 run_batch에 연결 + 단일 live 확인 (place_id=1709413013).
