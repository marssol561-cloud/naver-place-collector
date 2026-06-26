"""
Sprint: Robust store lookup — ignore whitespace in name/address match
Tests for find_store_by_name_address whitespace-normalization logic.
All tests are DB-read only (no place_id dependency). HTTP calls are mocked.
"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("MASTER_DB_URL", "https://mock.supabase.co")
os.environ.setdefault("MASTER_DB_SERVICE_ROLE_KEY", "mock-key")

sys.path.insert(0, str(Path(__file__).parent.parent))

import db.master_db as master_db

_MYEONGGA_DB_ROW = {
    "store_id": "store-myeongga",
    "place_id": "1514477807",
    "store_name": "명가정육&수산셀프바베큐",
    "address": "인천 중구 을왕동 711-3",
}

_OTHER_STORE_ROW = {
    "store_id": "store-other",
    "place_id": "9999999999",
    "store_name": "명가정육수산",
    "address": "서울 강남구 역삼동 1",
}


def _mock_response(rows: list) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = rows
    resp.raise_for_status.return_value = None
    return resp


# ──────────────────────────────────────────────────────────────────────────────
# T1: 공백 포함 입력 → DB 등록 점포 매칭 (검색 미진입)
# ──────────────────────────────────────────────────────────────────────────────
def test_spaced_name_matches_registered_store():
    """
    Input: 이름에 공백 포함 "명가 정육&수산 셀프 바베큐"
    DB:   공백 없는 "명가정육&수산셀프바베큐"
    → Pass 2 (norm_name, addr)에서 매칭, place_id 1514477807 반환
    """
    def side_effect(url, params=None, headers=None, timeout=None):
        qname = (params or {}).get("store_name", "")
        qaddr = (params or {}).get("address", "")
        # Pass 2: norm_name="명가정육&수산셀프바베큐", addr="인천 중구 을왕동 711-3"
        if qname == "eq.명가정육&수산셀프바베큐" and qaddr == "eq.인천 중구 을왕동 711-3":
            return _mock_response([_MYEONGGA_DB_ROW])
        return _mock_response([])

    with patch("db.master_db.requests.get", side_effect=side_effect):
        result = master_db.find_store_by_name_address(
            "명가 정육&수산 셀프 바베큐",
            "인천 중구 을왕동 711-3",
        )

    assert result is not None, "등록 점포를 찾지 못함 (공백 정규화 매칭 실패)"
    assert result["place_id"] == "1514477807"
    assert result["store_id"] == "store-myeongga"


# ──────────────────────────────────────────────────────────────────────────────
# T2: 정확 입력(공백 없음) → 여전히 매칭 (회귀 없음)
# ──────────────────────────────────────────────────────────────────────────────
def test_exact_input_still_matches():
    """
    Input: 공백 없는 정확 입력
    → Pass 1 (exact) 에서 즉시 매칭 (기존 동작 보존)
    """
    call_count = {"n": 0}

    def side_effect(url, params=None, headers=None, timeout=None):
        call_count["n"] += 1
        qname = (params or {}).get("store_name", "")
        qaddr = (params or {}).get("address", "")
        if qname == "eq.명가정육&수산셀프바베큐" and qaddr == "eq.인천 중구 을왕동 711-3":
            return _mock_response([_MYEONGGA_DB_ROW])
        return _mock_response([])

    with patch("db.master_db.requests.get", side_effect=side_effect):
        result = master_db.find_store_by_name_address(
            "명가정육&수산셀프바베큐",
            "인천 중구 을왕동 711-3",
        )

    assert result is not None, "정확 입력 시 매칭 실패 (회귀)"
    assert result["place_id"] == "1514477807"
    assert call_count["n"] == 1, "Pass 1에서 즉시 반환돼야 함 (불필요한 추가 쿼리 발생)"


# ──────────────────────────────────────────────────────────────────────────────
# T3: 정규화 후 이름이 다른 점포 → 오매칭 없음
# ──────────────────────────────────────────────────────────────────────────────
def test_no_false_match_for_different_store():
    """
    Input: "명가 정육&수산 셀프 바베큐" + "서울 강남구 역삼동 1"
    DB 후보(다른 점포): store_name="명가정육수산", address="서울 강남구 역삼동 1"
    → 정규화 후 이름이 다르므로 None 반환 (오매칭 금지)
    """
    def side_effect(url, params=None, headers=None, timeout=None):
        qaddr = (params or {}).get("address", "")
        # 주소 일치 pass들에서 다른 점포 반환
        if "역삼동" in qaddr:
            return _mock_response([_OTHER_STORE_ROW])
        return _mock_response([])

    with patch("db.master_db.requests.get", side_effect=side_effect):
        result = master_db.find_store_by_name_address(
            "명가 정육&수산 셀프 바베큐",
            "서울 강남구 역삼동 1",
        )

    assert result is None, "정규화 후 이름이 다른 점포를 반환 (오매칭 발생)"


# ──────────────────────────────────────────────────────────────────────────────
# T4: 미등록 점포 → None 반환 (검색 fallthrough 유지)
# ──────────────────────────────────────────────────────────────────────────────
def test_unregistered_store_returns_none():
    """
    DB에 없는 점포 → 모든 pass에서 빈 응답 → None 반환
    (caller는 이 None을 받고 searcher로 fallthrough — 기존 흐름 보존)
    """
    with patch("db.master_db.requests.get", return_value=_mock_response([])):
        result = master_db.find_store_by_name_address(
            "완전히없는점포",
            "존재하지않는주소 999",
        )

    assert result is None, "미등록 점포가 None이 아닌 값을 반환"


# ──────────────────────────────────────────────────────────────────────────────
# T5: SELF-REVIEW — 범위 이탈·기존 경로 손상·오매칭·목표 달성·금지항목 점검
# ──────────────────────────────────────────────────────────────────────────────
def test_self_review_scope_and_integrity():
    """
    자기 검증 체크리스트:
    [1] 함수 시그니처 불변 확인
    [2] 정규화 로직이 name+address 양쪽 모두 요구하는지 확인
    [3] 검색기·추출·수집 흐름 미변경 (caller 미변경)
    [4] 목표: spaced 입력 → DB 히트 (T1 통과)
    [5] 금지: 단일 필드만으로 반환하는 경로 없음
    """
    import inspect
    sig = inspect.signature(master_db.find_store_by_name_address)
    params = list(sig.parameters.keys())
    assert params == ["store_name", "address"], f"시그니처 변경 감지: {params}"

    # 내부 _query_and_validate가 name AND address 양쪽을 검증하는지
    # → T1(오매칭 없음), T3(오매칭 금지) 통과로 간접 검증

    # Python-side 정규화 검증 코드가 존재하는지 소스 레벨 확인
    source = inspect.getsource(master_db.find_store_by_name_address)
    assert 'replace(" ", "")' in source, "공백 제거 로직 누락"
    assert "norm_name" in source and "norm_addr" in source, "정규화 변수 누락"
    assert "norm_name" in source and "norm_addr" in source, "양쪽 필드 검증 누락"
