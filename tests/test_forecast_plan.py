import datetime as dt
import time

from forecast.plan_forecast import MskuInput, PlanForecastInput, forecast_plan

D = dt.date
OCT, NOV, DEC = D(2026, 10, 1), D(2026, 11, 1), D(2026, 12, 1)
P = [OCT, NOV, DEC]
SKU_A, SKU_B = "DCC1800264G1", "A4P-TOY-002"
A, C, DD = ("MSKU-A", "11072"), ("MSKU-C", "11094"), ("MSKU-D", "11072")


def m(key, sku, onhand, eff, rate, applicable=True, system=None):
    return MskuInput(key[0], key[1], sku, applicable, onhand, dict(zip(P, eff)),
                     dict(zip(P, system or eff)), rate)


def s1(make=30, ship=60, c_eff=(40, 44, 44), extra=()):
    return PlanForecastInput(
        as_of=D(2026, 9, 21), periods=P, make_days=make, ship_days=ship,
        mskus=[m(A, SKU_A, 300, (100, 120, 90), 4.0), m(C, SKU_A, 25, c_eff, 2.0),
               m(DD, SKU_B, 40, (7, 8, 6), 1.0), *extra],
        purchase={(SKU_A, OCT): 200, (SKU_A, NOV): None, (SKU_A, DEC): 0,
                  (SKU_B, OCT): None, (SKU_B, NOV): None, (SKU_B, DEC): None},
        ordered_open={SKU_A: [("2026-10", 50, "PO-2026-0912"), ("2026-10", 30, "PO-2026-0915"),
                              ("2026-11", 120, "PO-2026-0920")]})


def chain(rows):
    return [(r.opening, r.demand, r.inbound, r.closing) for r in rows]


def sku_rows(f, sku):
    return [r for r in f.sku if r.sku == sku]


def test_default_lead_time_lands_the_october_order_outside_the_plan():
    """spec §7：10 月下单 + 30 + 60 → 2027-01，周期外 → 逐笔列出（14 B3）。"""
    f = forecast_plan(s1())
    assert [r.closing for r in f.msku[A]] == [164, 44, -46]
    assert [r.closing for r in f.msku[C]] == [-33, -77, -121]
    assert f.beyond_horizon == [{"sku": SKU_A, "order_period": "2026-10", "units": 200,
                                 "placed": "2026-10-15", "made": "2026-11-14",
                                 "arrive": "2027-01-13", "reason": "arrives_after_horizon"}]
    rows = sku_rows(f, SKU_A)
    assert [(r.making, r.shipping) for r in rows] == [(200, 0), (0, 200), (0, 200)]


def test_short_lead_time_allocates_into_november():
    f = forecast_plan(s1(make=10, ship=20))
    assert chain(f.msku[A]) == [(264, 100, 0, 164), (164, 120, 90, 134), (134, 90, 0, 44)]
    assert chain(f.msku[C]) == [(7, 40, 0, -33), (-33, 44, 110, 33), (33, 44, 0, -11)]
    assert [r.stockout_date for r in f.msku[C]] == [D(2026, 10, 6), D(2026, 11, 1), None]
    note = f.msku[C][1].arrivals[0]
    assert (note["units"], note["gap_part"], note["weight_part"], note["pool"]) == (110, 77, 33, 200)
    assert f.beyond_horizon == [] and f.unallocated == []


def test_store_is_sum_of_msku_and_sku_is_sum_of_store():
    """spec §4.6 · 14 §1.1 ①：合计行不许独立算一遍。"""
    f = forecast_plan(s1(make=10, ship=20))
    rows = sku_rows(f, SKU_A)
    assert [(r.opening, r.stock_demand, r.inbound, r.closing) for r in rows] == [
        (271, 140, 0, 131), (131, 164, 200, 167), (167, 134, 0, 33)]
    assert [r.demand for r in rows] == [140, 164, 134]
    store_a = [s for s in f.store if (s.sku, s.sid) == (SKU_A, "11072")]
    assert [s.closing for s in store_a] == [r.closing for r in f.msku[A]]


def test_pipeline_is_month_end_snapshot_and_ordered_open_never_arrives():
    """spec §4.3：已下单未到不定到货日，只进货号行在途（OQ-6）。"""
    rows = sku_rows(forecast_plan(s1(make=10, ship=20)), SKU_A)
    assert [(r.making, r.shipping, r.ordered_open, r.in_transit) for r in rows] == [
        (0, 200, 200, 400), (0, 0, 200, 200), (0, 0, 200, 200)]
    assert [x["ref"] for x in rows[0].ordered_sources] == ["PO-2026-0912", "PO-2026-0915",
                                                          "PO-2026-0920"]


def test_unfilled_purchase_is_named_not_silent():
    """OP-13：空采购不产生批次，但逐月列出。"""
    rows = sku_rows(forecast_plan(s1()), SKU_A)
    assert rows[0].unfilled_purchase == ("2026-11",)


def test_not_applicable_store_is_excluded_from_sku_stock_but_not_from_demand():
    """spec §4.6：无 FBA 店 → 不适用、不进库存求和；期望 Σ 仍含它（所以货号行分 demand 与 stock_demand 两个数）。"""
    w = m(("MSKU-W", "90001"), SKU_B, None, (60, 65, 70), None, applicable=False)
    f = forecast_plan(s1(extra=(w,)))
    rows = sku_rows(f, SKU_B)
    assert [r.excluded_not_applicable for r in rows] == [1, 1, 1]
    assert [r.closing for r in rows] == [24, 16, 10]            # 只有 D
    assert [r.demand for r in rows] == [67, 73, 76]             # 7+60 · 8+65 · 6+70
    assert [s.state for s in f.store if s.sid == "90001"] == ["not_applicable"] * 3


def test_batch_with_unknown_gap_makes_its_msku_unknown_from_arrival():
    """Review Focus 2 · OP-12。"""
    f = forecast_plan(s1(make=10, ship=20, c_eff=(40, None, 44)))
    assert [u["reason"] for u in f.unallocated] == ["gap_unknown"]
    assert (f.msku[A][1].state, f.msku[A][1].reason) == ("unknown", "inbound_unknown")
    assert f.msku[A][0].closing == 164


def test_batch_with_no_fba_msku_is_listed():
    """Review Focus 2：货号下只有无 FBA 的店。"""
    w = m(("MSKU-W", "90001"), "SKU-X", None, (1, 1, 1), None, applicable=False)
    inp = s1(make=10, ship=20, extra=(w,))
    inp.purchase[("SKU-X", OCT)] = 50
    f = forecast_plan(inp)
    assert [(u["sku"], u["reason"]) for u in f.unallocated] == [("SKU-X", "no_fba_msku")]


def test_zero_lead_time_before_as_of_is_listed():
    """Review Focus 3：0/0 且下单月在 as_of 之前 → 到货日早于 as_of，逐笔列出不丢。"""
    inp = s1(make=0, ship=0)
    inp = PlanForecastInput(D(2026, 9, 21), [D(2026, 9, 1), *P], 0, 0, inp.mskus,
                            {(SKU_A, D(2026, 9, 1)): 10}, {})
    f = forecast_plan(inp)
    assert [(b["arrive"], b["reason"]) for b in f.beyond_horizon] == [
        ("2026-09-15", "arrives_before_as_of")]


def test_hundred_mskus_twelve_months_is_fast_enough():
    """Review Focus 5：阈值 5 s 是 **设计取值 · 未实测**（只防数量级退化）。"""
    months = [D(2026 + (9 + i) // 12, (9 + i) % 12 + 1, 1) for i in range(12)]
    mskus = [MskuInput(f"M{i}", "11072", f"S{i % 30}", True, 500, {p: 30 for p in months},
                       {p: 30 for p in months}, 1.0) for i in range(100)]
    inp = PlanForecastInput(D(2026, 9, 21), months, 30, 60, mskus,
                            {(f"S{j}", months[0]): 100 for j in range(30)}, {})
    t0 = time.perf_counter()
    forecast_plan(inp)
    assert time.perf_counter() - t0 < 5.0


def test_unknown_onhand_msku_arrivals_are_folded_into_unallocated(monkeypatch):
    """Task 6 接口：推演不了的 msku 拿到的到货走 unplaced_arrivals —— 上一层必须收进 unallocated，
    否则到了计划层就悄悄没了（CLAUDE.md「静默兜底是最坏的一种」）。"""
    import forecast.plan_forecast as pf
    from forecast.allocate import Give

    def fake_allocate(batches, keys, baseline, weights):
        return {b: [Give(keys[0], b.units, 0, b.units, "测试", b.units)] for b in batches}, []

    monkeypatch.setattr(pf, "allocate", fake_allocate)
    x = m(("MSKU-X", "11072"), "SKU-X", None, (5, 5, 5), 1.0)
    inp = s1(make=10, ship=20, extra=(x,))
    inp.purchase[("SKU-X", OCT)] = 30
    f = forecast_plan(inp)
    lost = [u for u in f.unallocated if u["sku"] == "SKU-X"]
    assert [(u["seller_sku"], u["sid"], u["units"], u["arrive"], u["reason"]) for u in lost] == [
        ("MSKU-X", "11072", 30, "2026-11-14", "unknown_onhand")]


def test_gap_unknown_batch_is_listed_once_even_with_an_unknown_onhand_msku():
    """整批分不出的已在批次级点名；不再借 unplaced_arrivals 再列一遍。"""
    x = m(("MSKU-X", "11094"), SKU_A, None, (5, 5, 5), 1.0)
    f = forecast_plan(s1(make=10, ship=20, extra=(x,)))
    assert [(u["sku"], u["reason"]) for u in f.unallocated] == [(SKU_A, "gap_unknown")]
    assert f.unallocated[0]["unknown"] == [["MSKU-X", "11094"]]
    assert (f.msku[A][1].state, f.msku[A][1].reason) == ("unknown", "inbound_unknown")


def test_negative_purchase_is_rejected_by_name():
    """负采购量不许静默跳过（CLAUDE.md「静默兜底是最坏的一种」）。"""
    import pytest

    inp = s1()
    inp.purchase[(SKU_A, NOV)] = -5
    with pytest.raises(ValueError, match=r"DCC1800264G1 2026-11 采购量为负：-5"):
        forecast_plan(inp)


def test_unknown_msku_makes_its_store_and_sku_unknown_from_that_month():
    """spec §4.6：任一 msku 未知 → 上层未知；同货号下别的店不受牵连。"""
    f = forecast_plan(s1(c_eff=(40, None, 44)))
    assert [(r.state, r.closing) for r in f.msku[C]] == [
        ("ok", -33), ("unknown", None), ("unknown", None)]
    store_c = [s for s in f.store if (s.sku, s.sid) == (SKU_A, "11094")]
    assert [(s.state, s.reason, s.closing) for s in store_c] == [
        ("ok", None, -33), ("unknown", "unknown_demand", None), ("unknown", "unknown_demand", None)]
    store_a = [s for s in f.store if (s.sku, s.sid) == (SKU_A, "11072")]
    assert [s.state for s in store_a] == ["ok", "ok", "ok"]
    rows = sku_rows(f, SKU_A)
    assert [(r.state, r.opening, r.closing) for r in rows] == [
        ("ok", 271, 131), ("unknown", 131, None), ("unknown", None, None)]


def test_store_with_mixed_fba_flags_is_rejected():
    """has_fba 是店铺级（02 §3.1a）：同店 msku 不一致 = 输入自相矛盾，报错而不是跟第一个走。"""
    import pytest

    y = m(("MSKU-Y", "11072"), SKU_A, None, (1, 1, 1), None, applicable=False)
    with pytest.raises(ValueError, match=r"DCC1800264G1 店铺 11072 下 msku 的 has_fba 不一致"):
        forecast_plan(s1(extra=(y,)))
