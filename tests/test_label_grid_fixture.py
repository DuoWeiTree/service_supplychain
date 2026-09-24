"""前端标签测试网格（web/src/pages/__fixtures__/labelGrid*.json）由真推演产出。

★ 为什么要这条：手写的预测块曾把「10 月下单、11 月到货」写进 make 30 + ship 60 的网格 ——
  物理上不可能，而前端测试只核对透传，谁也不会失败。这里用同一组入参重跑 forecast_payload，
  与前端冻结的那份逐字相等；入参或推演一变，这条就红。
★ demand / purchase 也冻进同一个文件，labelGrid.ts 直接读：喂给推演的入参就是界面拿到的格子，
  两边不可能再各写一份（曾经 MSKU-W 的 system_basis 在前端是 null、在这里是「不适用」）。

两份：
  · labelGrid.json      —— 2026-10 ~ 11，Task 18 标签测试用
  · labelGridLong.json  —— 2026-10 ~ 2027-01：10 月下单 30 + 60 天 → 2027-01-13 到货，
                           只有这份能看到计划采购到货的分配依据（Task 19）

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
from forecast.calendar import days_in_month, units_for_month

DIR = Path(__file__).resolve().parents[1] / "web" / "src" / "pages" / "__fixtures__"

A, B, W = ("MSKU-A", "11072"), ("MSKU-B", "11072"), ("MSKU-W", "90001")
#: tests/test_api_claims_forecast.py 里 msku_a 认领出来的速率；B 的是占位（设计取值 · 未实测）
RATE = {A: 4.325139122267326, B: 0.32}

#: 沃尔玛店：认领时记下「没有销量取数源」（plans._no_sales_source_basis + 逐月补的两键）
NO_SOURCE = {"reason": "not_applicable_no_sales_source", "cause": "non_amazon_platform"}

#: claim 在 as_of 2026-09-21 记下的依据（test_api_claims_forecast 同一组）。
#: ★ 估计器的中间量（raw_rate / n_eff / target_rate / winsor_cap）这里不编，给 null
_BASIS = {
    "method": "baseline_rate_v1", "as_of": "2026-09-21", "estimation_anchor": "2026-09-14",
    "embargo_days": 7, "window": {"from": "2026-07-21", "until": "2026-09-14"},
    "target_window": {"from": "2026-06-17", "until": "2026-09-14"},
    "censored_days": 0, "stock_days_rejected": 0, "stock_days_uncaptured": 0, "reason": None,
    "raw_rate": None, "n_eff": None, "target_rate": None, "winsor_cap": None, "first_seen": None,
    "constants": {"halflife_days": 14, "window_days": 56, "target_window_days": 90,
                  "shrink_prior": 3, "winsor_q": 0.95},
}


def _cell(k: tuple[str, str], period: dt.date, expected: int) -> dict:
    """grid() demand[] 的一格（键集同 api/ui/plans.py::_demand_row）。system_units 与 claim 同法算。"""
    n = days_in_month(period)
    rate = RATE.get(k)
    if rate is None:
        sysu, basis = None, {**NO_SOURCE, "days_in_month": n, "units_raw": None}
    else:
        sysu = units_for_month(rate, n)
        basis = {**_BASIS, "base_rate": rate, "days_in_month": n, "units_raw": rate * n}
    return {"seller_sku": k[0], "sid": k[1], "sku": "SKU-1", "period": period.strftime("%Y-%m"),
            "system_units": sysu, "system_extrapolated": False, "expected_units": expected,
            "effective_units": expected, "basis": "human", "system_basis": basis}


def build(periods: list[dt.date], expected: dict[tuple[str, str], list[int]],
          purchase: list[int | None]) -> dict:
    demand = [_cell(k, p, e[i]) for k, e in expected.items() for i, p in enumerate(periods)]
    fc = forecast_payload(
        as_of=dt.date(2026, 9, 21), periods=periods, demand_rows=demand,
        purchase_rows=[("SKU-1", p, u) for p, u in zip(periods, purchase, strict=True)],
        fba={A: True, B: True, W: False},
        onhand={A: 300, B: 13},
        base_rate=RATE,
        in_transit={"SKU-1": [SimpleNamespace(period="2026-10", units=50, ref="PO-2026-0918")]},
        lead=LeadTime("oversea_sea", 30, 60, 15, {}), embargo_days=7, plan_id=1)
    return {"periods": [p.strftime("%Y-%m") for p in periods], "demand": demand,
            "purchase": [{"sku": "SKU-1", "period": p.strftime("%Y-%m"), "planned_units": u}
                         for p, u in zip(periods, purchase, strict=True)],
            **fc}


P10, P11, P12, P01 = dt.date(2026, 10, 1), dt.date(2026, 11, 1), dt.date(2026, 12, 1), dt.date(2027, 1, 1)

FIXTURES = {
    "labelGrid.json": lambda: build(
        [P10, P11], {A: [100, 90], B: [5, 5], W: [60, 65]}, [200, 80]),
    # 12 / 1 月采购未填：「采购未填」要有东西可显示
    "labelGridLong.json": lambda: build(
        [P10, P11, P12, P01], {A: [100, 90, 110, 120], B: [5, 5, 5, 5], W: [60, 65, 70, 75]},
        [200, 80, None, None]),
}


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_frozen_label_grid_is_what_the_forecast_produces(name):
    assert json.loads((DIR / name).read_text(encoding="utf-8")) == FIXTURES[name](), (
        f"{name} 与真推演不一致 —— 重新生成：.venv/bin/python -m tests.test_label_grid_fixture")


def test_long_grid_has_a_plan_purchase_arrival():
    """★ 这份存在的理由：没有一笔到货落进周期，依据面板的「到货怎么分」就没东西可测。"""
    g = FIXTURES["labelGridLong.json"]()
    assert any(f["basis"]["arrivals"] for f in g["forecast"])


if __name__ == "__main__":
    for name, make in FIXTURES.items():
        (DIR / name).write_text(json.dumps(make(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {DIR / name}")
