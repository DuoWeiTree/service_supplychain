import datetime as dt

import pytest

from forecast.allocate import Batch, allocate, spread

D = dt.date
A, C = ("MSKU-A", "11072"), ("MSKU-C", "11094")
NOV = D(2026, 11, 1)


def batch(units, arrive=D(2026, 11, 14)):  # noqa: B008 -- date() 不可变，等同常量默认值
    return Batch("DCC1800264G1", D(2026, 10, 1), units, D(2026, 10, 15), D(2026, 10, 25), arrive)


def given(res, b):
    return {g.key: g.units for g in res[0][b]}


def test_gap_first_then_rest_by_expected_sales():
    """14 §3.1 缺口 ≤ 这批：先补缺口（C 缺 77），余量 123 按 11 月期望 120:44 摊 → 90 / 33。"""
    b = batch(200)
    res = allocate([b], [A, C], {A: {NOV: 44}, C: {NOV: -77}}, {A: {NOV: 120}, C: {NOV: 44}})
    assert given(res, b) == {A: 90, C: 110}
    gc = next(g for g in res[0][b] if g.key == C)
    assert (gc.gap_part, gc.weight_part, gc.pool) == (77, 33, 200)
    assert res[1] == []


def test_nobody_short_still_ships_everything_by_expected_sales():
    """14 §3「货照发」：按缺口卡会让货凭空消失（第一版把 105 件吞了）。"""
    b = batch(100)
    res = allocate([b], [A, C], {A: {NOV: 10}, C: {NOV: 5}}, {A: {NOV: 2}, C: {NOV: 1}})
    assert given(res, b) == {A: 67, C: 33}      # 66/33 余 1 给权重大的


def test_short_more_than_the_batch_goes_by_gap_ratio_remainder_to_biggest_gap():
    b = batch(7)
    res = allocate([b], [A, C], {A: {NOV: -30}, C: {NOV: -10}}, {A: {NOV: 1}, C: {NOV: 1}})
    assert given(res, b) == {A: 6, C: 1}        # 5.25→5, 1.75→1, 余 1 给缺口大的 A


def test_cumulative_gap_is_not_filled_twice():
    """14 §3.1：G 是累计缺口，要减已累计分给 s 的量 —— 否则重复补同一个窟窿。"""
    b1, b2 = batch(50, D(2026, 11, 3)), batch(40, D(2026, 11, 20))
    res = allocate([b2, b1], [A, C], {A: {NOV: 44}, C: {NOV: -77}},
                   {A: {NOV: 120}, C: {NOV: 44}})
    assert given(res, b1) == {C: 50}
    assert given(res, b2) == {A: 10, C: 30}     # C 还缺 27，余 13 → 9/3 余 1 给 A


def test_every_batch_is_fully_given_out():
    """★ spec §4.4 断言：Σ分到的 ≡ 这批件数。"""
    for units in (1, 7, 99, 200, 1001):
        b = batch(units)
        res = allocate([b], [A, C], {A: {NOV: -3}, C: {NOV: 4}}, {A: {NOV: 5}, C: {NOV: 7}})
        assert sum(g.units for g in res[0][b]) == units


def test_batch_with_unknown_gap_is_not_given_to_anyone():
    """OP-12 · Review Focus 2：缺口未知 → 整批不分、点名。"""
    b = batch(200)
    res = allocate([b], [A, C], {A: {NOV: 44}, C: {NOV: None}}, {A: {NOV: 120}, C: {NOV: None}})
    assert res[0] == {}
    assert res[1] == [{"sku": "DCC1800264G1", "order_period": "2026-10", "arrive": "2026-11-14",
                       "units": 200, "reason": "gap_unknown", "unknown": [["MSKU-C", "11094"]]}]


def test_batch_with_no_fba_msku_is_listed():
    b = batch(200)
    res = allocate([b], [], {}, {})
    assert res[1][0]["reason"] == "no_fba_msku"


def test_zero_weights_and_no_gap_split_evenly():
    """OP-11：谁都不缺、期望全 0 → 按个数均分，余数按顺序给前面的。"""
    b = batch(5)
    res = allocate([b], [A, C], {A: {NOV: 1}, C: {NOV: 1}}, {A: {NOV: 0}, C: {NOV: 0}})
    assert given(res, b) == {A: 3, C: 2}


def test_spread_keeps_the_total():
    assert spread(123, {A: 120, C: 44}, [A, C]) == {A: 90, C: 33}
    with pytest.raises(ValueError):
        spread(-1, {A: 1}, [A])
