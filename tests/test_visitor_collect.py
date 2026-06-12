from collector.visitor_collect import _should_stop_incremental


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
