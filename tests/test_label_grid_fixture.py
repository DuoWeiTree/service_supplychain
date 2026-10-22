"""前端标签测试网格（web/src/pages/__fixtures__/labelGrid*.json）由真推演产出。

★ 为什么要这条：手写的预测块曾把「10 月下单、11 月到货」写进 make 30 + ship 60 的网格 ——
  物理上不可能，而前端测试只核对透传，谁也不会失败。这里用同一组入参重跑 forecast_payload，
  与前端冻结的那份逐字相等；入参或推演一变，这条就红。
★ demand / purchase 也冻进同一个文件，labelGrid.ts 直接读：喂给推演的入参就是界面拿到的格子，
  两边不可能再各写一份（曾经 MSKU-W 的 system_basis 在前端是 null、在这里是「不适用」）。

三份：
  · labelGrid.json         —— 2026-10 ~ 11，Task 18 标签测试用
  · labelGridLong.json     —— 2026-10 ~ 2027-01：10 月下单 30 + 60 天 → 2027-01-13 到货，
                              只有这份能看到计划采购到货的分配依据（Task 19）
  · labelGridPartial.json  —— as_of 2026-10-10：9 月已过去、10 月只从 10-11 起算（Task 19）

重新生成：`.venv/bin/python -m tests.test_label_grid_fixture`
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from api.ui.grid_forecast import forecast_payload
from api.ui.lead_time import LeadTime
from forecast.baseline import data_windows, estimate_rate
from forecast.calendar import days_in_month, units_for_month
from forecast.month_in_transit import TransitItem
from usecase.plan_claim import system_basis

DIR = Path(__file__).resolve().parents[1] / "web" / "src" / "pages" / "__fixtures__"

A, B, W = ("MSKU-A", "11072"), ("MSKU-B", "11072"), ("MSKU-W", "90001")

#: 日销序列，相对估计锚点（as_of − 7）往回数第 i 天。★ 速率与依据都由 forecast/baseline.py 从它算出，
#: 不手写：A = 恒卖 4 件/天 120 天（test_forecast_baseline 的 OP-3 金标，→ 4.325/天）；
#: B = 锚点前第 2 天起每 3 天卖 1 件、共 56 天（设计取值 · 未实测，→ 约 0.319/天）
SALES = {A: lambda i: 4 if i < 120 else 0, B: lambda i: 1 if i < 56 and i % 3 == 2 else 0}

#: 沃尔玛店：认领时记下「没有销量取数源」（plan_claim._no_sales_source_basis + 逐月补的两键）
NO_SOURCE = {"reason": "not_applicable_no_sales_source", "cause": "non_amazon_platform"}


def claim_basis(k: tuple[str, str], as_of: dt.date) -> dict | None:
    """走 claim() 同一条路：estimate_rate → plan_claim.system_basis（逐月两键在 _cell 里补）。
    库存日全采到、无断货 —— 于是 censored / rejected / uncaptured 都是 0。"""
    if k not in SALES:
        return None
    win = data_windows(as_of)
    daily = {win.until - dt.timedelta(days=i): u for i in range(200) if (u := SALES[k](i)) > 0}
    days = [win.censor_from + dt.timedelta(days=i) for i in range((win.until - win.censor_from).days + 1)]
    stock = SimpleNamespace(censored=set(), captured=days, rejected=[])
    return system_basis(estimate_rate(daily, set(), as_of), stock, win)


def _cell(k: tuple[str, str], period: dt.date, expected: int, as_of: dt.date) -> dict:
    """grid() demand[] 的一格（键集同 api/ui/plans.py::_demand_row）。system_units 与 claim 同法算。"""
    n = days_in_month(period)
    basis = claim_basis(k, as_of)
    if basis is None:
        sysu, basis = None, {**NO_SOURCE, "days_in_month": n, "units_raw": None}
    else:
        rate = basis["base_rate"]
        sysu = units_for_month(rate, n)
        basis = {**basis, "days_in_month": n, "units_raw": rate * n}
    return {"seller_sku": k[0], "sid": k[1], "sku": "SKU-1", "period": period.strftime("%Y-%m"),
            "system_units": sysu, "system_extrapolated": False, "expected_units": expected,
            "effective_units": expected, "basis": "human", "system_basis": basis}


def build(periods: list[dt.date], expected: dict[tuple[str, str], list[int]],
          purchase: list[int | None], as_of: dt.date = dt.date(2026, 9, 21)) -> dict:
    demand = [_cell(k, p, e[i], as_of) for k, e in expected.items() for i, p in enumerate(periods)]
    rate = {k: claim_basis(k, as_of)["base_rate"] for k in SALES}
    fc = forecast_payload(
        as_of=as_of, periods=periods, demand_rows=demand,
        purchase_rows=[("SKU-1", p, u) for p, u in zip(periods, purchase, strict=True)],
        fba={A: True, B: True, W: False},
        onhand={A: 300, B: 13},
        base_rate=rate,
        # ★ PO-2026-0701 预计 7 月到、至今未到：已逾期那一形态（终审 I-2 · OQ-6）
        in_transit={"SKU-1": [SimpleNamespace(period="2026-07", units=40, ref="PO-2026-0701"),
                              SimpleNamespace(period="2026-10", units=50, ref="PO-2026-0918")]},
        lead=LeadTime("oversea_sea", 30, 60, 15, {}), embargo_days=7, plan_id=1,
        # spec 2026-10-14：调仓中 0、没有在途货件 —— 数不变，只让「期初两部分」的形状进冻结网格
        fba_inbound={A: [], B: []}, fc={A: 0, B: 0},
        # spec 2026-10-15：当月在途 —— 一张海外仓待收货备货单（11-05 预计到达），FBA 货件无
        fba_transit={"SKU-1": []},
        oversea_transit={"SKU-1": [TransitItem("oversea", "OWS-T1", 40, dt.date(2026, 11, 5),
                                               {"from_wid": 1, "to_wid": 2, "shipped_at": "2026-09-30"})]})
    return {"periods": [p.strftime("%Y-%m") for p in periods], "demand": demand,
            "purchase": [{"sku": "SKU-1", "period": p.strftime("%Y-%m"), "planned_units": u}
                         for p, u in zip(periods, purchase, strict=True)],
            **fc}


P09, P10, P11 = dt.date(2026, 9, 1), dt.date(2026, 10, 1), dt.date(2026, 11, 1)
P12, P01 = dt.date(2026, 12, 1), dt.date(2027, 1, 1)

FIXTURES = {
    "labelGrid.json": lambda: build(
        [P10, P11], {A: [100, 90], B: [5, 5], W: [60, 65]}, [200, 80]),
    # 12 / 1 月采购未填：「采购未填」要有东西可显示
    "labelGridLong.json": lambda: build(
        [P10, P11, P12, P01], {A: [100, 90, 110, 120], B: [5, 5, 5, 5], W: [60, 65, 70, 75]},
        [200, 80, None, None]),
    # as_of 落在计划月里：9 月已过去、10 月 partial（台账从 10-11 起）、11 月照常
    "labelGridPartial.json": lambda: build(
        [P09, P10, P11], {A: [120, 100, 90], B: [5, 5, 5], W: [60, 60, 65]}, [0, 0, 0],
        as_of=dt.date(2026, 10, 10)),
}


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_frozen_label_grid_is_what_the_forecast_produces(name):
    assert json.loads((DIR / name).read_text(encoding="utf-8")) == FIXTURES[name](), (
        f"{name} 与真推演不一致 —— 重新生成：.venv/bin/python -m tests.test_label_grid_fixture")


def test_partial_grid_has_elapsed_and_partial_months():
    """★ 这份存在的理由：as_of 在计划月里时，依据面板的「部分月」「已过去」要有东西可测。"""
    g = FIXTURES["labelGridPartial.json"]()
    states = {(f["seller_sku"], f["period"]): f["state"] for f in g["forecast"]}
    assert states[("MSKU-A", "2026-09")] == "elapsed" and states[("MSKU-A", "2026-10")] == "partial"


def test_rates_come_from_the_estimator():
    """A 的速率就是 OP-3 金标（claim 在同一份日销上会写的那个数），不是抄来的常数。"""
    assert claim_basis(A, dt.date(2026, 9, 21))["base_rate"] == pytest.approx(4.325139122267326, rel=1e-12)


def test_long_grid_has_a_plan_purchase_arrival():
    """★ 这份存在的理由：没有一笔到货落进周期，依据面板的「到货怎么分」就没东西可测。"""
    g = FIXTURES["labelGridLong.json"]()
    assert any(f["basis"]["arrivals"] for f in g["forecast"])


def test_grid_has_an_overdue_open_order():
    """★ 终审 I-2：依据面板的「已逾期」要有东西可测 —— 7 月预计到货、as_of 9 月仍未到。"""
    pl = FIXTURES["labelGrid.json"]()["forecast_sku"][0]["pipeline"]
    assert [s["overdue"] for s in pl["ordered_open_sources"]] == [True, False]
    assert pl["ordered_open_overdue"] == 40


if __name__ == "__main__":
    for name, make in FIXTURES.items():
        (DIR / name).write_text(json.dumps(make(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {DIR / name}")
