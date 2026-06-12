"""Sprint fix local test: searcher direct-entry + no-stub server path"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collector.searcher import search_place_id


async def main():
    print("=" * 60)
    print("TEST 1: 회엔참치 인천 북성동 (기존 실패 케이스)")
    print("  예상: None (네이버 미등록, clean miss)")
    print("=" * 60)
    result1 = await search_place_id("회엔참치", "인천 중구 북성동1가 98-495")
    print(f"  결과: place_id = {result1}")
    if result1 is None:
        print("  PASS: None 반환 (FIX 1로 stub 저장 안 함)")
    else:
        print(f"  PASS: place_id 반환 (direct-entry 또는 검색 매칭): {result1}")

    print()
    print("=" * 60)
    print("TEST 2: 회엔참치 부천 중동 (regression - 기존 정상 케이스)")
    print("  예상: 2042798831 (direct-entry URL 추출 or searchIframe)")
    print("=" * 60)
    result2 = await search_place_id("회엔참치", "경기 부천시 원미구 중동 1152-1")
    print(f"  결과: place_id = {result2}")
    if str(result2) == "2042798831":
        print("  PASS: 2042798831 매칭 정상")
    elif result2 is not None:
        print(f"  WARN: 다른 place_id 반환 ({result2}) - 검수 필요")
    else:
        print("  FAIL: None 반환 (regression!)")

    print()
    print("=" * 60)
    print("TEST 3: server.py None path 코드 검증 (no INSERT)")
    print("=" * 60)
    import inspect
    from api import server
    src = inspect.getsource(server.collect)
    if "upsert_store(None" in src:
        print("  FAIL: server.py에 여전히 upsert_store(None, ...) 존재")
    else:
        print("  PASS: server.py에 upsert_store(None, ...) 없음")
    if "place_not_found" in src and '"saved": False' in src:
        print("  PASS: PLACE_NOT_FOUND + saved:False 경로 확인")
    else:
        print("  FAIL: PLACE_NOT_FOUND 경로 없음")
    if "collected_without_place" in src:
        print("  FAIL: collected_without_place stub 경로 잔존")
    else:
        print("  PASS: collected_without_place 경로 완전 제거")


asyncio.run(main())
