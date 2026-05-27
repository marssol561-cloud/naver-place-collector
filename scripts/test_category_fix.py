# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collector.place_crawler import _extract_category

cases = [
    # (text, place_name, expected)
    (
        "이전 페이지 강남칼동태 페이지 닫기 더보기 강남칼동태 한식 알림받기 전화번호",
        "강남칼동태",
        "한식",
    ),
    (
        "이전 페이지 스타벅스 페이지 닫기 더보기 스타벅스 카페 알림받기",
        "스타벅스",
        "카페",
    ),
    (
        "강남칼동태 한식 알림받기",
        "강남칼동태",
        "한식",
    ),
    (
        "no_match_here",
        "강남칼동태",
        "",
    ),
]

all_pass = True
for text, name, expected in cases:
    result = _extract_category(text, name)
    status = "PASS" if result == expected else "FAIL"
    if status == "FAIL":
        all_pass = False
    print(f"[{status}] place_name={name!r} → {result!r} (expected={expected!r})")

sys.exit(0 if all_pass else 1)
