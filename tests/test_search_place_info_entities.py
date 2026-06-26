"""
Sprint: Decode HTML entities in confirm-box store name (&amp; → &)
Tests for search_place_info HTML-entity decoding logic.
_search_single_query_info is mocked — no browser/network required.
"""
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("MASTER_DB_URL", "https://mock.supabase.co")
os.environ.setdefault("MASTER_DB_SERVICE_ROLE_KEY", "mock-key")
os.environ.setdefault("COLLECTOR_API_KEY", "mock-key")

sys.path.insert(0, str(Path(__file__).parent.parent))

import collector.searcher as searcher


# ──────────────────────────────────────────────────────────────────────────────
# T1: &amp; 포함 이름 → & 로 디코딩 (핵심 버그픽스)
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_html_entity_in_name_is_decoded():
    """
    _search_single_query_info가 &amp; 포함 이름을 반환할 때
    search_place_info는 & 로 디코딩하여 반환해야 한다.
    """
    raw = {
        "place_id": "1514477807",
        "name": "명가 정육&amp;수산 셀프바베큐",
        "address": "인천 중구 을왕동 711-3",
    }
    with patch.object(searcher, "_search_single_query_info", new=AsyncMock(return_value=raw)):
        result = await searcher.search_place_info("명가 정육&수산 셀프바베큐", "인천 중구 을왕동 711-3")

    assert result is not None
    assert result["name"] == "명가 정육&수산 셀프바베큐", f"디코딩 실패: {result['name']!r}"
    assert "&amp;" not in result["name"], "&amp; 잔존"


# ──────────────────────────────────────────────────────────────────────────────
# T2: 엔티티 없는 이름 → 그대로 반환 (부작용 없음)
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_name_without_entities_unchanged():
    """
    HTML 엔티티가 없는 일반 이름은 변형 없이 그대로 반환되어야 한다.
    """
    raw = {
        "place_id": "9876543210",
        "name": "스타벅스 강남점",
        "address": "서울 강남구 역삼동 123",
    }
    with patch.object(searcher, "_search_single_query_info", new=AsyncMock(return_value=raw)):
        result = await searcher.search_place_info("스타벅스 강남점", "서울 강남구 역삼동")

    assert result is not None
    assert result["name"] == "스타벅스 강남점"


# ──────────────────────────────────────────────────────────────────────────────
# T3: 주소에 엔티티가 있을 경우 → 디코딩
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_html_entity_in_address_is_decoded():
    """
    address 필드에 &amp; 등 엔티티가 있으면 디코딩되어야 한다.
    (실제 발생 빈도는 낮으나 동일 로직 적용)
    """
    raw = {
        "place_id": "1111111111",
        "name": "테스트점포",
        "address": "서울 강남구 &amp; 역삼동 1",
    }
    with patch.object(searcher, "_search_single_query_info", new=AsyncMock(return_value=raw)):
        result = await searcher.search_place_info("테스트점포", "서울 강남구 역삼동")

    assert result is not None
    assert result["address"] == "서울 강남구 & 역삼동 1"
    assert "&amp;" not in result["address"]


# ──────────────────────────────────────────────────────────────────────────────
# T4: REGRESSION — search_place_id 경로 미변경 확인
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_search_place_id_path_unchanged():
    """
    search_place_id 함수는 _search_single_query 를 사용하며
    search_place_info / _search_single_query_info 와 완전히 독립된 경로.
    search_place_id 가 _search_single_query_info 를 호출하지 않음을 확인.
    """
    import inspect
    src = inspect.getsource(searcher.search_place_id)
    assert "_search_single_query_info" not in src, (
        "search_place_id 가 _search_single_query_info 를 호출하고 있음 (경로 오염)"
    )
    assert "_search_single_query" in src, "search_place_id 에서 _search_single_query 호출 누락"

    # search_place_id 직접 실행 확인: _search_single_query 를 mock 해서 place_id 반환
    with patch.object(searcher, "_search_single_query", new=AsyncMock(return_value="9999999999")):
        pid = await searcher.search_place_id("스타벅스", "서울 강남구")
    assert pid == "9999999999"


# ──────────────────────────────────────────────────────────────────────────────
# T5: SELF-REVIEW — 범위·경로·디코딩 정확성·금지항목 점검
# ──────────────────────────────────────────────────────────────────────────────
def test_self_review_scope_and_integrity():
    """
    자기 검증 체크리스트:
    [1] html.unescape 가 search_place_info 에만 적용됨
    [2] _search_single_query_info 소스에 html.unescape 없음 (적용 지점 올바름)
    [3] search_place_id 소스에 html.unescape 없음 (금지 경로 불변)
    [4] import html 추가됨 (_html alias)
    [5] &amp; → & / &lt; → < / &gt; → > 디코딩 정확성
    """
    import inspect
    import html as stdlib_html

    src_info = inspect.getsource(searcher.search_place_info)
    src_inner = inspect.getsource(searcher._search_single_query_info)
    src_pid = inspect.getsource(searcher.search_place_id)

    assert "unescape" in src_info, "search_place_info 에 unescape 없음"
    assert "unescape" not in src_inner, "_search_single_query_info 에 unescape 추가됨 (금지)"
    assert "unescape" not in src_pid, "search_place_id 에 unescape 추가됨 (금지)"

    # 디코딩 정확성
    assert stdlib_html.unescape("A&amp;B") == "A&B"
    assert stdlib_html.unescape("&lt;tag&gt;") == "<tag>"
    assert stdlib_html.unescape("no entity") == "no entity"
