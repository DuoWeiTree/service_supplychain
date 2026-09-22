"""monthly_estimate —— 系统预估销量的 seed 值。

★ 移植自老原型 store.js `rebuildCells`：预估序列**按位置**对齐到计划月份，
  用完了就沿用最后一个值并标 system_extrapolated（M-13 / 14 §5）。
"""
import datetime as dt

import pytest

from forecast.estimate import HistoryGap, InsufficientHistory, monthly_estimate


def h(*pairs):
    return [(dt.date(y, m, 1), u) for y, m, u in pairs]


def test_history_longer_than_plan_uses_the_most_recent_months():
    got = monthly_estimate(h((2026, 5, 10), (2026, 6, 20), (2026, 7, 30), (2026, 8, 40)), 3)
    assert [x.units for x in got] == [20, 30, 40]
    assert [x.system_extrapolated for x in got] == [False, False, False]


def test_beyond_the_series_carries_the_last_value_and_says_so():
    """★ 原型 test_forecast.js §9：第 7 个月必须标外推。"""
    got = monthly_estimate(h((2026, 7, 100), (2026, 8, 120), (2026, 9, 90)), 7)
    assert [x.units for x in got] == [100, 120, 90, 90, 90, 90, 90]
    assert [x.system_extrapolated for x in got] == [False] * 3 + [True] * 4


def test_the_flag_is_not_always_true():
    """★ 原型的原话：否则这个标记等于恒真，白标。"""
    got = monthly_estimate(h((2026, 7, 100), (2026, 8, 120), (2026, 9, 90)), 3)
    assert not any(x.system_extrapolated for x in got)


def test_empty_history_is_not_zero():
    """★ 没有历史 ≠ 预估 0。调用方要拿到一个硬失败，并把这个 msku 点名。"""
    with pytest.raises(InsufficientHistory):
        monthly_estimate([], 3)


def test_a_gap_in_history_is_refused_and_named():
    """★ 采集缺一天/缺一月是常态，把缺口当 0 会把预估压低而没有任何回声。"""
    with pytest.raises(HistoryGap) as ei:
        monthly_estimate(h((2026, 6, 10), (2026, 8, 20)), 3)
    assert "2026-07" in str(ei.value)


def test_negative_history_is_refused():
    with pytest.raises(ValueError):
        monthly_estimate(h((2026, 8, -1)), 1)


def test_the_same_month_twice_is_refused():
    """★ 同月两行会把按位置消费的序列整体错位 —— 与缺月同一类的静默丢失。"""
    with pytest.raises(ValueError) as ei:
        monthly_estimate(h((2026, 7, 10), (2026, 7, 20), (2026, 8, 30)), 3)
    assert "2026-07" in str(ei.value)


def test_months_must_be_a_positive_int():
    for bad in (0, -1, 3.5):
        with pytest.raises(ValueError):
            monthly_estimate(h((2026, 8, 10)), bad)
