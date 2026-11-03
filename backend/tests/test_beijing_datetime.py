from datetime import datetime, timezone

from app.core.datetime_json import BEIJING, BeijingDateTime, ensure_beijing, now_beijing, to_beijing_iso


def test_now_beijing_is_asia_shanghai():
    n = now_beijing()
    assert n.tzinfo is not None
    assert n.utcoffset() == BEIJING.utcoffset(n)


def test_ensure_beijing_naive_assumes_wall_clock():
    naive = datetime(2026, 10, 23, 15, 47, 2)
    aware = ensure_beijing(naive)
    assert aware.tzinfo is not None
    assert aware.hour == 15
    assert aware.utcoffset() == BEIJING.utcoffset(aware)


def test_ensure_beijing_from_utc():
    utc = datetime(2026, 10, 23, 7, 47, 2, tzinfo=timezone.utc)
    bj = ensure_beijing(utc)
    assert bj.hour == 15


def test_to_beijing_iso_has_offset():
    s = to_beijing_iso(datetime(2026, 10, 23, 15, 47, 2, tzinfo=BEIJING))
    assert s.endswith("+08:00")
    assert "15:47:02" in s


def test_beijing_datetime_bind_and_result():
    col = BeijingDateTime()
    utc = datetime(2026, 10, 23, 7, 47, 2, tzinfo=timezone.utc)
    bound = col.process_bind_param(utc, None)
    assert bound is not None
    assert bound.tzinfo is None
    assert bound.hour == 15
    loaded = col.process_result_value(bound, None)
    assert loaded is not None
    assert loaded.tzinfo is not None
    assert loaded.hour == 15
