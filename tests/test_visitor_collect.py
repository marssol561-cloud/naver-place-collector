from collector.visitor_collect import _should_stop_incremental, extract_batches


# ── S6: extraction-window regression test ────────────────────────────────────

def _make_gql_capture(op_name, gql_key, total, item_ids):
    """Build a synthetic all_captures entry matching the on_resp handler shape."""
    return {
        "url": "https://pcmap.place.naver.com/graphql",
        "post_data": f'{{"operationName":"{op_name}","variables":{{}}}}',
        "body_json": {"data": {gql_key: {"total": total, "items": [{"id": i} for i in item_ids]}}},
        "is_gql": True,
    }


def test_extract_batches_nav_pre_includes_fallback_capture():
    """Initial batch extracted from nav_pre captures GQL captured during
    anchor-fallback goto ([nav_pre, pre_sort)), while extracting from
    pre_sort misses it — verifying the S6 fix is effective."""
    op_name = "getVisitorReviews"
    gql_key = "visitorReviews"

    # Simulated all_captures layout:
    #  [0,1]       → pre-nav noise (before nav_pre=2)
    #  [2]         → fallback-goto GQL  ← the one the bug missed
    #  [3]         → non-GQL entry
    #  pre_sort=4  → everything from here onward is empty (sort failure)
    nav_pre = 2
    pre_sort = 4

    fallback_gql = _make_gql_capture(op_name, gql_key, total=5, item_ids=["r1", "r2", "r3", "r4", "r5"])
    all_captures = [
        {"url": "x", "post_data": None, "body_json": None, "is_gql": False},  # 0
        {"url": "x", "post_data": None, "body_json": None, "is_gql": False},  # 1
        fallback_gql,                                                          # 2 ← in [nav_pre, pre_sort)
        {"url": "x", "post_data": None, "body_json": None, "is_gql": False},  # 3
        # index 4 = pre_sort; nothing here (sort failure, no subsequent GQL)
    ]

    # Fixed behaviour: nav_pre includes the fallback GQL
    batches_nav = extract_batches(all_captures, nav_pre, op_name, gql_key)
    assert len(batches_nav) == 1, f"expected 1 batch from nav_pre, got {len(batches_nav)}"
    assert batches_nav[0]["total"] == 5
    assert len(batches_nav[0]["items"]) == 5

    # Bug behaviour: pre_sort would have missed it
    batches_pre = extract_batches(all_captures, pre_sort, op_name, gql_key)
    assert len(batches_pre) == 0, "pre_sort must miss the fallback GQL (confirms bug was real)"


def test_extract_batches_success_path_unchanged():
    """On the anchor-success path the GQL fires after the sort click (≥ pre_sort),
    so extracting from nav_pre still works: items from pre_sort+ are included."""
    op_name = "getVisitorReviews"
    gql_key = "visitorReviews"

    nav_pre = 1
    # sort-triggered GQL fires after pre_sort
    post_sort_gql = _make_gql_capture(op_name, gql_key, total=3, item_ids=["a", "b", "c"])
    all_captures = [
        {"url": "x", "post_data": None, "body_json": None, "is_gql": False},  # 0 pre-nav
        # pre_sort = 1 = nav_pre (nothing between them in success path)
        post_sort_gql,                                                          # 1 ← sort re-fetch
    ]

    batches = extract_batches(all_captures, nav_pre, op_name, gql_key)
    assert len(batches) == 1
    assert batches[0]["total"] == 3


# ── existing tests ────────────────────────────────────────────────────────────

def test_should_stop_incremental():
    # oldest strictly older than since → True
    assert _should_stop_incremental("2026-05-01", "2026-05-15") is True
    assert _should_stop_incremental("2022-01-01T10:00:00", "2026-05-15") is True

    # oldest equal to since → False (not strictly older)
    assert _should_stop_incremental("2026-05-15", "2026-05-15") is False

    # oldest newer than since → False
    assert _should_stop_incremental("2026-05-20", "2026-05-15") is False

    # None args → False
    assert _should_stop_incremental(None, "2026-05-15") is False
    assert _should_stop_incremental("2026-05-01", None) is False
    assert _should_stop_incremental(None, None) is False
