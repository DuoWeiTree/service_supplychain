import datetime as dt

import pytest

from forecast.plan_forecast import Arrival, MskuInput, project_msku

D = dt.date
OCT, NOV, DEC = D(2026, 10, 1), D(2026, 11, 1), D(2026, 12, 1)


def msku(onhand=200, eff=None, rate=None, applicable=True):
    eff = eff or {OCT: 100, NOV: 50}
    return MskuInput("MSKU-A", "11072", "DCC1800264G1", applicable, onhand, eff,
                     {p: None for p in eff}, rate)


def chain(rows):
    return [(r.opening, r.demand, r.inbound, r.closing) for r in rows]


def test_owner_example_opening_200_expected_100():
    """spec §4.5 负责人 09-24：10 月期望 100，期初 200 → 期末 100 → 11 月期初 100。"""
    rows = project_msku(msku(), D(2026, 9, 30), [OCT, NOV])
    assert chain(rows) == [(200, 100, 0, 100), (100, 50, 0, 50)]
    assert [r.state for r in rows] == ["ok", "ok"]
    assert rows[1].opening == rows[0].closing          # ★ 恒等式④


def test_bridge_days_are_consumed_by_forecast():
    """W-6（OP-7）：as_of 9/21，9/22~9/30 按预测扣：9 月 = ceil(4×30) = 120，× 0.3 = 36。"""
    rows = project_msku(msku(onhand=300, rate=4.0), D(2026, 9, 21), [OCT, NOV])
    assert chain(rows) == [(264, 100, 0, 164), (164, 50, 0, 114)]
    assert rows[0].bridge == {"from": "2026-09-22", "to": "2026-09-30", "units": 36,
                              "by": "forecast", "rate": 4.0, "month_totals": {"2026-09": 120},
                              "onhand_as_of": 300, "arrivals": [], "arrival_units": 0,
                              "stockout_date": None}
    assert rows[1].bridge is None


def test_bridge_across_two_months():
    """as_of 8/20：8/21~8/31 = ceil(62 × 11/32) = 22；9 月整月 60 → 共 82。"""
    rows = project_msku(msku(onhand=100, eff={OCT: 10}, rate=2.0), D(2026, 8, 20), [OCT])
    assert rows[0].bridge["units"] == 82
    assert rows[0].opening == 18
    assert rows[0].bridge["month_totals"] == {"2026-08": 62, "2026-09": 60}


def test_no_rate_means_the_whole_msku_is_unknown_not_zero():
    """OP-8：扣不了桥接段 → 未知，不当 0。"""
    rows = project_msku(msku(onhand=300, rate=None), D(2026, 9, 21), [OCT, NOV])
    assert [(r.state, r.reason, r.closing) for r in rows] == [
        ("unknown", "bridge_rate_unknown", None), ("unknown", "bridge_rate_unknown", None)]
    assert rows[0].unknown_since == OCT


def test_unknown_demand_propagates_forward():
    """spec §4.2 · 14 B1/B2：11 月空 → 11 月起未知；10 月照常。"""
    rows = project_msku(msku(eff={OCT: 100, NOV: None, DEC: 10}), D(2026, 9, 30), [OCT, NOV, DEC])
    assert rows[0].closing == 100
    assert [(r.state, r.reason, r.unknown_since) for r in rows[1:]] == [
        ("unknown", "unknown_demand", NOV), ("unknown", "unknown_demand", NOV)]
    assert rows[1].opening == 100          # 期初是已知的上月期末


def test_not_applicable_and_unknown_onhand_are_different():
    """02 §3.1a · 14 B5：无 FBA = 不适用；有 FBA 没取到 = 未知。两者不许长得一样。"""
    na = project_msku(msku(onhand=None, applicable=False), D(2026, 9, 30), [OCT])
    un = project_msku(msku(onhand=None), D(2026, 9, 30), [OCT])
    assert (na[0].state, na[0].reason) == ("not_applicable", "not_applicable")
    assert (un[0].state, un[0].reason) == ("unknown", "unknown_onhand")


def test_arrivals_enter_on_their_day_and_the_month_identity_holds():
    """spec §4.5 ③：期末 = 期初 − 期望 + 入库；断货日来自截断台账。"""
    m = msku(onhand=7, eff={OCT: 40, NOV: 44, DEC: 44})
    arr = [Arrival(D(2026, 11, 14), 110, {"order_period": "2026-10"})]
    rows = project_msku(m, D(2026, 9, 30), [OCT, NOV, DEC], arr)
    assert chain(rows) == [(7, 40, 0, -33), (-33, 44, 110, 33), (33, 44, 0, -11)]
    assert [r.stockout_date for r in rows] == [D(2026, 10, 6), D(2026, 11, 1), None]
    assert rows[1].arrivals == ({"date": "2026-11-14", "units": 110, "order_period": "2026-10"},)


def test_unknown_arrival_makes_the_rest_unknown():
    m = msku(onhand=100, eff={OCT: 10, NOV: 10})
    rows = project_msku(m, D(2026, 9, 30), [OCT, NOV],
                        [Arrival(D(2026, 11, 14), None, {"reason": "gap_unknown"})])
    assert rows[0].closing == 90
    assert (rows[1].state, rows[1].reason, rows[1].inbound) == ("unknown", "inbound_unknown", None)


def test_as_of_inside_first_month_starts_the_day_after():
    """Review Focus 1 · OP-9：as_of 10/10 → 10 月从 10/11 起扣剩余份额 ceil(30×2/3)=20，不给期初。"""
    rows = project_msku(msku(onhand=100, eff={OCT: 30, NOV: 30}), D(2026, 10, 10), [OCT, NOV])
    assert (rows[0].state, rows[0].reason, rows[0].opening, rows[0].demand, rows[0].closing) == (
        "partial", "as_of_inside_month", None, 20, 80)
    assert (rows[1].opening, rows[1].closing) == (80, 50)


def test_elapsed_month_is_named_not_computed():
    """Review Focus 1：月末 ≤ as_of 的月 → 已过去。"""
    rows = project_msku(msku(onhand=100, eff={OCT: 30, NOV: 30}), D(2026, 10, 31), [OCT, NOV])
    assert (rows[0].state, rows[0].closing) == ("elapsed", None)
    assert (rows[1].opening, rows[1].closing) == (100, 70)


def test_arrival_outside_the_window_is_a_caller_bug():
    with pytest.raises(ValueError, match="beyond_horizon"):
        project_msku(msku(), D(2026, 9, 30), [OCT], [Arrival(D(2027, 1, 13), 5, {})])


def test_arrival_in_the_bridge_window_is_named_in_the_opening_basis():
    """14 B3 · §6：as_of 与计划首月之间到的货不许悄悄并进期初 —— 期初 = 在仓 − 桥接销量 + 桥接到货。"""
    arr = [Arrival(D(2026, 9, 25), 50, {"order_period": "2026-06"})]
    rows = project_msku(msku(onhand=300, rate=4.0), D(2026, 9, 21), [OCT, NOV], arr)
    br = rows[0].bridge
    assert br["arrivals"] == [{"date": "2026-09-25", "units": 50, "order_period": "2026-06"}]
    assert br["arrival_units"] == 50
    assert rows[0].opening == br["onhand_as_of"] - br["units"] + br["arrival_units"] == 314
    assert rows[0].arrivals == ()


def test_unknown_arrival_in_the_bridge_window_is_named_too():
    arr = [Arrival(D(2026, 9, 25), None, {"reason": "gap_unknown"})]
    rows = project_msku(msku(onhand=300, rate=4.0), D(2026, 9, 21), [OCT, NOV], arr)
    assert rows[0].bridge["arrivals"] == [{"date": "2026-09-25", "units": None,
                                           "reason": "gap_unknown"}]
    assert rows[0].bridge["arrival_units"] is None
    assert [(r.state, r.reason, r.opening) for r in rows] == [
        ("unknown", "inbound_unknown", None), ("unknown", "inbound_unknown", None)]


@pytest.mark.parametrize("periods, bad", [
    ([], "为空"),
    ([D(2026, 10, 2)], "2026-10-02"),
    ([OCT, DEC], "2026-12-01"),
    ([NOV, OCT], "2026-10-01"),
])
def test_plan_months_must_be_sorted_contiguous_month_starts(periods, bad):
    with pytest.raises(ValueError, match=f"计划月 ?{bad}"):
        project_msku(msku(), D(2026, 9, 30), periods)


def test_no_rate_rows_still_show_expected_demand_and_name_the_bridge():
    """OP-8：未知的是头寸，不是期望销量 —— 期望销量照常回显，桥接依据点名 rate 缺失。"""
    rows = project_msku(msku(onhand=300, rate=None), D(2026, 9, 21), [OCT, NOV])
    assert [(r.opening, r.demand, r.inbound, r.closing) for r in rows] == [
        (None, 100, 0, None), (None, 50, 0, None)]
    assert rows[0].bridge == {"from": "2026-09-22", "to": "2026-09-30", "units": None,
                              "by": "forecast", "rate": None, "month_totals": {},
                              "onhand_as_of": 300, "arrivals": [], "arrival_units": 0,
                              "stockout_date": None}
    assert rows[1].bridge is None


def test_stockout_inside_the_bridge_is_reported_not_moved_to_the_first_plan_day():
    """spec §4.5：断货日 = 台账里第一个 daily_unmet > 0 的日子 —— 桥接段也是台账。
    onhand 10，9 月 120 件：9/22~9/24 = 3×4 = 12 > 10 → 9/24 断货。"""
    rows = project_msku(msku(onhand=10, rate=4.0), D(2026, 9, 21), [OCT, NOV])
    assert rows[0].opening == -26
    assert rows[0].bridge["stockout_date"] == "2026-09-24"
    assert rows.first_stockout_date == D(2026, 9, 24)
    assert [r.stockout_date for r in rows] == [D(2026, 10, 1), D(2026, 11, 1)]   # OP-18：月内


def test_first_stockout_date_is_msku_level_and_none_when_never_short():
    m = msku(onhand=7, eff={OCT: 40, NOV: 44, DEC: 44})
    rows = project_msku(m, D(2026, 9, 30), [OCT, NOV, DEC],
                        [Arrival(D(2026, 11, 14), 110, {})])
    assert rows.first_stockout_date == D(2026, 10, 6)
    assert project_msku(msku(), D(2026, 9, 30), [OCT, NOV]).first_stockout_date is None


def test_stockout_before_a_mid_month_unknown_is_kept():
    """10/6 已断货，11/14 到货未知 → 11 月未知，但 11/1 的断货照报。"""
    m = msku(onhand=7, eff={OCT: 40, NOV: 44})
    rows = project_msku(m, D(2026, 9, 30), [OCT, NOV], [Arrival(D(2026, 11, 14), None, {})])
    assert (rows[1].state, rows[1].closing, rows[1].stockout_date) == (
        "unknown", None, D(2026, 11, 1))


@pytest.mark.parametrize("applicable, reason", [(False, "not_applicable"),
                                                (True, "unknown_onhand")])
def test_arrivals_for_an_unprojectable_msku_are_listed_not_dropped(applicable, reason):
    arr = [Arrival(D(2026, 10, 5), 30, {"order_period": "2026-06"})]
    rows = project_msku(msku(onhand=None, applicable=applicable), D(2026, 9, 30), [OCT], arr)
    assert rows.unplaced_arrivals == (
        {"date": "2026-10-05", "units": 30, "order_period": "2026-06", "reason": reason},)
    assert rows.first_stockout_date is None


def test_arrivals_are_range_checked_on_every_path():
    old = [Arrival(D(2020, 1, 1), 5, {})]
    for m, as_of in [(msku(), D(2026, 10, 31)),                      # 全部已过去
                     (msku(onhand=None), D(2026, 9, 30)),
                     (msku(applicable=False), D(2026, 9, 30))]:
        with pytest.raises(ValueError, match="到货日 2020-01-01"):
            project_msku(m, as_of, [OCT], old)
    with pytest.raises(ValueError, match="到货日 2026-09-30"):       # as_of 当天不算推演窗
        project_msku(msku(), D(2026, 9, 30), [OCT], [Arrival(D(2026, 9, 30), 5, {})])


def test_unknown_onhand_rows_still_show_expected_demand():
    """同裁定 3：未知的是头寸，不是期望销量。已过去的月照样是已过去。"""
    rows = project_msku(msku(onhand=None, eff={OCT: 30, NOV: 50}), D(2026, 10, 10), [OCT, NOV])
    assert [(r.state, r.reason, r.demand, r.closing, r.unknown_since) for r in rows] == [
        ("unknown", "unknown_onhand", 20, None, OCT), ("unknown", "unknown_onhand", 50, None, OCT)]
    rows = project_msku(msku(onhand=None, eff={OCT: 30, NOV: 50}), D(2026, 10, 31), [OCT, NOV])
    assert [(r.state, r.demand, r.unknown_since) for r in rows] == [
        ("elapsed", None, None), ("unknown", 50, NOV)]
